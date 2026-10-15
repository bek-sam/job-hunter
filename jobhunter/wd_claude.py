"""Claude-driven Workday steps (owner, Oct 13 2026): Playwright opens the job, handles sign-in / account, uploads
the resume, clicks Save and Continue and (after the Review guards) Submit. CLAUDE decides every field on every step.
Every round is logged in a structured schema for later analysis -> data/answer_log/wd_claude.jsonl.
After WD_CLAUDE_LIMIT applications (default 40) the Workday adapter switches back to the Playwright rules.

Schema "jobapp.step.v1" (one JSON object per Claude round; based on unified-ATS models: Merge ScreeningQuestion types
DATE/FILE/SINGLE_SELECT/MULTI_SELECT/SINGLE_LINE_TEXT/MULTI_LINE_TEXT/NUMERIC/BOOLEAN with {label} options, Greenhouse's
separate "eeoc" category, Kombo-style option answers):
{ schema, at, app: {app_key, company, job_title, job_url, ats, tenant, mode},
  step: {name_raw, name_norm, round},
  questions: [{q_ref, field_id, label_raw, label_norm, topic, category: screening|eeoc|profile|entry|consent,
               type, widget, required, options: [{label}], value_before}],
  decision: {status: continue|complete|need_human, reason, answers: [{q_ref, value, source, why}], actions: [...]},
  results: [{op, q_ref, ok, note}], errors_before: [...], usage: {...} }
"""
import applicant as P
import datetime as dt, json, os, re
import config, formfill, llm, rescue

LOG = config.DATA / 'answer_log' / 'wd_claude.jsonl'
COUNT = config.DATA / 'eval' / 'wd_claude_count.json'
LIMIT = int(os.environ.get('WD_CLAUDE_LIMIT', '40'))
STEP_NORM = [('account', r'sign in|create account'), ('autofill', r'autofill|resume'), ('my_information', r'my information'),
             ('my_experience', r'experience'), ('application_questions', r'application questions|questions'),
             ('voluntary_disclosures', r'voluntary|disclosure'), ('self_identify', r'self.?identif|disabilit'), ('review', r'review')]

SCHEMA = {'type': 'object', 'required': ['status', 'answers', 'actions'], 'properties': {
    'status': {'type': 'string', 'enum': ['continue', 'complete', 'need_human']},
    'reason': {'type': 'string'},
    'answers': {'type': 'array', 'items': {'type': 'object', 'required': ['q_ref', 'value', 'source'], 'properties': {
        'q_ref': {'type': 'string'}, 'value': {'type': 'string'},
        'source': {'type': 'string', 'enum': ['answer_bank', 'resume', 'job_post', 'inferred', 'kept']},
        'why': {'type': 'string'}}}},
    'actions': rescue.SCHEMA['properties']['actions']}}

SYSTEM = (
    f"You fill ONE step of a Workday job application for {P.NAME}. You see every visible field with its label, "
    "type, whether it is required, its current value and (for dropdowns) all options. Decide the correct value for EVERY "
    "field that should have one, using ONLY the Answer Bank, the work/education history and resume facts given. Fields of type 'search_select' (Workday School, Field of Study, Skills...) take op 'select' with the text to search; if the result says no option matched, try the shorter name or an 'Other' option next round. "
    "Return: answers = one entry per field you decided (q_ref = the field id, value = exact option text for choices, "
    "source, why in a few words; use source 'kept' when the current value is already correct), and actions = what "
    "Playwright must do to make the page match (fill/select/check/uncheck; click only Add/Delete/Close buttons, one Add "
    "per round, then fill the new entry next round). Dates: Workday date sections take digits (MM, YYYY or MM/DD/YYYY). "
    "Work history and education: only the entries given; delete any other entry; role description = that job's resume "
    "bullets. Self-ID (EEO): exactly the Answer Bank values (he is NOT a veteran and has NO disability; never pick an "
    "option that says he identifies as a veteran). Consent boxes: tick them. Status: 'continue' if you returned actions and "
    "need to see the page again, 'complete' when every required field (and relevant optional ones) is correct and no "
    "action is needed, 'need_human' only if a required fact is missing from the facts (say which in reason). "
    "Never invent facts. Never touch SSN/ID/passport/bank fields.")


def active():
    if os.environ.get('WD_CLAUDE_MODE', '0') != '1': return False
    return used() < LIMIT


def used():
    try: return json.loads(COUNT.read_text()).get('count', 0)
    except Exception: return 0


def count_one(job):
    d = {'count': used() + 1, 'last': job.get('company'), 'at': dt.datetime.now().isoformat(timespec='seconds')}
    COUNT.parent.mkdir(exist_ok=True); COUNT.write_text(json.dumps(d))
    return d['count']


def norm_step(name):
    return next((k for k, rx in STEP_NORM if re.search(rx, name or '', re.I)), 'other')


def qtype(f):
    w = f.get('type') or ''
    if w == 'textarea': return 'MULTI_LINE_TEXT'
    if w in ('number',): return 'NUMERIC'
    if w in ('date',) or re.search(r'month|day|year|date', f.get('label') or '', re.I) and w in ('text', 'spinbutton'): return 'DATE'
    if w in ('radio',): return 'SINGLE_SELECT'
    if w in ('checkbox',): return 'BOOLEAN'
    if w in ('select', 'dropdown'): return 'MULTI_SELECT' if f.get('multi') else 'SINGLE_SELECT'
    return 'SINGLE_LINE_TEXT'


def category(label):
    l = (label or '').lower()
    if re.search(r'veteran|disabilit|gender|ethnic|race|hispanic|sexual orientation|pronoun', l): return 'eeoc'
    if formfill.CONSENT.search(l): return 'consent'
    if re.search(r'name|e-?mail|phone|address|city|postal|country|state|linkedin|website|url', l): return 'profile'
    if re.search(r'job title|company|role description|from|to\b|school|degree|field of study|gpa|skill|language', l): return 'entry'
    return 'screening'


def snapshot(page):
    """rescue.SNAP_JS + the options of every Workday dropdown (opened one by one; Workday only renders open lists)."""
    from ats import workday as wd
    snap = page.main_frame.evaluate(rescue.SNAP_JS, page.locator('body').element_handle())
    for f in snap['fields']:
        if f['type'] == 'dropdown':
            loc = page.locator(f'[data-rx="{f["id"]}"]').first
            try:
                loc.click(timeout=3000); page.wait_for_timeout(600)
                _, texts = wd._options(page)
                f['options'] = [t for t in dict.fromkeys(texts) if t and not re.match(r'^select one$', t, re.I)][:60]
                page.keyboard.press('Escape'); page.wait_for_timeout(250)
            except Exception:
                f['options'] = f.get('options') or []
    return snap


def log(job, t, step, rnd, snap, out, results, errors, usage=None):
    try:
        LOG.parent.mkdir(exist_ok=True)
        from qa_db import norm, topic
        qs = [{'q_ref': f['id'], 'field_id': None, 'label_raw': f['label'], 'label_norm': norm(f['label']),
               'topic': topic(norm(f['label'])), 'category': category(f['label']), 'type': qtype(f), 'widget': f['type'],
               'required': bool(f.get('required')), 'options': [{'label': o} for o in f.get('options') or []],
               'value_before': f.get('value')} for f in snap.get('fields', [])]
        rec = {'schema': 'jobapp.step.v1', 'at': dt.datetime.now().isoformat(timespec='seconds'),
               'app': {'app_key': f"{(job.get('company') or '').lower()}|{(job.get('title') or '').lower()}", 'company': job.get('company'),
                       'job_title': job.get('title'), 'job_url': job.get('url'), 'ats': 'workday', 'tenant': t, 'mode': 'claude'},
               'step': {'name_raw': step, 'name_norm': norm_step(step), 'round': rnd},
               'questions': qs, 'decision': out, 'results': results, 'errors_before': errors, 'usage': usage}
        with open(LOG, 'a') as fh: fh.write(json.dumps(rec, ensure_ascii=False) + '\n')
    except Exception as e:
        print('wd_claude log failed:', str(e)[:100])


def facts():
    from ats.workday_map import WORK, SCHOOL
    work = [dict(w, start=f"{w['start'][0]:02d}/{w['start'][1]}", end=(f"{w['end'][0]:02d}/{w['end'][1]}" if w['end'] else 'current'))
            for w in WORK]
    try:
        import answers
        rf = answers.resume_facts()
    except Exception:
        rf = ''
    from ats.workday import role_description
    for w in work: w['role_description'] = role_description(w['company'])
    return json.dumps({'work_history': work, 'education': SCHOOL}, ensure_ascii=False) + '\n\nRESUME FACTS:\n' + rf


def fill_step(page, job, step, t, errors=None, rounds=4):
    """Claude fills the current Workday step. Returns (done, unknown)."""
    bank = (config.REF / 'Job Hunter — Answer Bank.md').read_text()[:14000]
    fx = facts(); done, unknown = [], []; last_sig = None
    for rnd in range(1, rounds + 1):
        try:
            snap = snapshot(page)
        except Exception as e:
            return done, [f'Claude step: page snapshot failed ({str(e)[:60]})']
        gov = [f['label'] for f in snap['fields'] if formfill.GOV_ID.search(f.get('label') or '')]
        if gov: return done, ['GOV-ID asked: ' + gov[0][:120]]
        prompt = (f"ANSWER BANK:\n{bank}\n\nWORK / EDUCATION / RESUME:\n{fx}\n\n"
                  f"APPLICATION: {job.get('title')} at {job.get('company')} (Workday tenant {t})\nSTEP: {step} (round {rnd})\n"
                  + (f"WORKDAY ERRORS SHOWN: {json.dumps(errors or snap.get('errors'))}\n" if (errors or snap.get('errors')) else '')
                  + f"PAGE:\n{json.dumps(snap, ensure_ascii=False)[:30000]}")
        try:
            out = llm.ask(prompt, system=SYSTEM, schema=SCHEMA, job='wd_claude_step', timeout=300)
        except Exception as e:
            return done, [f'Claude step failed: {str(e)[:80]}']
        sig = json.dumps(out.get('actions') or [], sort_keys=True)
        if rnd > 1 and sig == last_sig:   # same actions again = they don't take -> stop instead of burning rounds
            log(job, t, step, rnd, snap, out, [], errors or snap.get('errors'))
            return done, [f"Claude step: the same changes did not stick ({(out.get('reason') or '')[:120]})"]
        last_sig = sig
        results = []
        for a in (out.get('actions') or [])[:40]:
            try: note = rescue._do(page.main_frame, page, snap['fields'], a); ok = not note.startswith(('refused', 'unknown'))
            except Exception as e: note, ok = f'failed: {str(e)[:60]}', False
            results.append({'op': a.get('op'), 'q_ref': a.get('id'), 'ok': ok, 'note': note})
        log(job, t, step, rnd, snap, out, results, errors or snap.get('errors'))
        done += [r['note'] for r in results if r['ok']]
        errors = None
        if out.get('status') == 'need_human':
            return done, [f"Claude: {out.get('reason') or 'missing fact'}"]
        if out.get('status') == 'complete' or not out.get('actions'):
            return done, []
        page.wait_for_timeout(900)
    return done, ['Claude step: not complete after 4 rounds']
