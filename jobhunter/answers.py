"""Answer an application's custom questions in ONE small LLM call, truthfully, from his real facts only.
Input: field descriptors the rule-based filler couldn't answer (label, kind, options, maxlength).
Output: fills the fields; returns the labels it answered. Anything not answerable from the facts stays empty
(the row goes to NEEDS YOU with that question) -- never guessed."""
import applicant as P
import json, re
import config, llm, jd as JD, packet
from gen_v5 import EDU, EXP, PROJ, AWARDS

GUESS = ("Owner rule (Oct 26 2026): GUESS when the facts don't say -- preference, opinion, familiarity, platform usage, how-heard, availability, interest, yes/no screening and essay questions always get the most plausible answer that helps the application. Still NEVER invent verifiable facts (employers, titles, dates, degrees, schools, GPA, test scores, certifications, metrics) or legal status (citizenship, immigration, export control, criminal record, clearance): those come only from the facts, else null.")

SYSTEM = (
    f"You fill job-application questions for {P.NAME} like a careful, strong applicant. "
    "Use ONLY the facts provided (Answer Bank, resume, project facts, JD). Never invent experience, employers, dates, "
    "metrics, users, deployments, referrals, people he knows, or prior interviews/applications. "
    f"{GUESS} "
    "Choice questions: answer with one option text copied EXACTLY from the given options, or null. "
    f"Years-of-experience questions: count only from the dated facts ({P.get('llm', 'experience_dates', default='see the work history')}); "
    "if the honest number is unclear, use the conservative whole number; if the "
    "skill has no evidence in the facts, answer 0 or the lowest option. "
    "Essays: specific to this company/team from the JD, 1-2 real projects mapped to their needs, plain polite English, no "
    "flattery, no clichés, first person, within the length limit (default 90-170 words). "
    "Work authorization / sponsorship questions inside a form: authorized = Yes, sponsorship = No. But NEVER mention "
    "work authorization, residency, green card, visa or citizenship inside essays or free-text answers. "
    "Form status questions (immigration status, citizenship country, U.S. person, export-control country lists, "
    "criminal record, government officials, pronouns, self-ID): answer from the Answer Bank section 'Application form facts'. "
    "Essays that say 'do not use AI' are authorized by him: write them from the facts like any essay. "
    "Legal agreements, consent, signatures, certifications: always null.")

SCHEMA = {'type': 'object', 'required': ['answers'], 'properties': {'answers': {'type': 'array', 'items': {
    'type': 'object', 'required': ['id', 'answer'], 'properties': {
        'id': {'type': 'integer'}, 'answer': {'type': ['string', 'null']},
        'note': {'type': 'string', 'description': 'if null: what he must answer himself (short)'}}}}}}

BANNED = re.compile(r'permanent resident|green card|visa\b|citizenship|sponsorship|authorized to work', re.I)


def resume_facts():
    out = ['EDUCATION'] + [f'- {s}, {p} ({r}): ' + '; '.join(l) for s, p, r, l in EDU]
    out += ['EXPERIENCE'] + [f'- {t} | {o} | {d}: ' + ' '.join(b) for t, o, d, b in EXP]
    out += ['PROJECTS'] + [f'- {n} ({s}, {d}): ' + ' '.join(b) for n, s, d, b in PROJ]
    out += ['AWARDS'] + [f'- {a}{b}' for a, b in AWARDS]
    return '\n'.join(out)


def _options(d):
    o = d.get('options') or []
    return [x['text'] if isinstance(x, dict) else x for x in o]


def _combobox_options(frame, loc):
    try:
        loc.click(); frame.wait_for_timeout(500)
        import formfill
        t = [x.strip() for x in formfill.options_of(frame, loc).all_inner_texts() if x.strip()]
        loc.press('Escape')
        return t[:80]
    except Exception:
        return []


def company_history(company):
    """His real history with this company from the Notion tracker (applications, OAs, interviews, rejections)."""
    if not company or company.startswith('('): return 'unknown (no company name)'
    try:
        import notion
        rows = notion.rows_for_company(company.split('(')[0].strip())
    except Exception as e:
        return f'unknown (tracker not reachable: {str(e)[:60]})'
    if not rows: return f'No earlier applications, assessments or interviews with {company} in his tracker.'
    lines = []
    for r in rows[:15]:
        lines.append(f"- {r.get('Position', '')[:70]} | Status {r.get('Status')} | applied {r.get('Date Applied') or '-'} | "
                     f"notes: {(r.get('Notes') or '')[:120]}")
    interviewed = any(r.get('Status') in ('Interview', 'Offer') or re.search(r'interview', r.get('Notes') or '', re.I) for r in rows)
    return (f"Interviewed with {company} before: {'YES' if interviewed else 'NO (no interview recorded)'}.\n" + '\n'.join(lines))


CACHE = config.DATA / 'answers_cache.json'


def _cache():
    try: return json.loads(CACHE.read_text())
    except Exception: return {}


def ask_cached(job, labels, lines):
    """One LLM call for the questions not answered before for this posting (Oct 13: rows are re-filled every hour, and
    each retry paid for the same answers again). Static context goes FIRST so the prompt cache is read, not re-written."""
    key = re.sub(r'[?#].*$', '', job.get('url') or '') or f"{job.get('company')}|{job.get('title')}"
    cache = _cache(); known = cache.get(key, {})
    todo = [i for i, l in enumerate(labels) if l not in known]
    out = {'answers': [{'id': i, 'answer': known[l]} for i, l in enumerate(labels) if l in known]}
    if todo:
        answer_bank = (config.REF / 'Job Hunter — Answer Bank.md').read_text()[:14000]
        # Oct 26: his own answers to questions the filler could not answer before (data/answer_log/needs_answers.md ->
        # profile.json owner_answers); authoritative for the same question in other words
        own = '\n'.join(f"- Q: {q}\n  A: {a}" for q, a in (P.get('owner_answers', default={}) or {}).items() if a and not q.startswith('_'))
        prompt = (f"ANSWER BANK:\n{answer_bank}\n\n"
                  + (f"HIS OWN ANSWERS (authoritative; use them whenever a question asks the same thing):\n{own}\n\n" if own else '')
                  + f"RESUME FACTS:\n{resume_facts()}\n\n"          # static -> cached prefix
                  f"Application: {job.get('title') or ''} at {job.get('company') or ''}\n\nJOB DESCRIPTION (trimmed):\n{job['jd_text']}\n\n"
                  f"PROJECT FACTS:\n{packet.project_facts(job.get('title') or '', job['jd_text'])[:5000]}\n\n"
                  f"HIS HISTORY WITH THIS COMPANY (from his application tracker — use it for 'applied/interviewed/worked here "
                  f"before' questions):\n{company_history(job.get('company') or '')}\n\n"
                  f"HIS COVER LETTER FOR THIS ROLE (for consistency):\n{(job.get('cover_text') or '')[:1500]}\n\n"
                  f"QUESTIONS:\n" + '\n'.join(lines[i] for i in todo) + "\n\nReturn one entry per question id.")
        new = llm.ask(prompt, system=SYSTEM, schema=SCHEMA, job='form answers').get('answers', [])
        for a in new:
            if isinstance(a.get('id'), int) and a['id'] in todo and a.get('answer') is not None:
                known[labels[a['id']]] = a['answer']
        out['answers'] += [a for a in new if isinstance(a.get('id'), int) and a['id'] in todo]
        cache[key] = known; CACHE.write_text(json.dumps(cache, ensure_ascii=False))
    return out


def answer_questions(questions, job):
    """Site-independent: questions = [dict(label, options=[...] or [], kind='choice'|'long text'|'short text',
    required=bool, maxlength=int|None)] -> list of answers (str or None, same order). One LLM call."""
    if not questions: return []
    if 'jd_text' not in job:
        try: job['jd_text'] = JD.fetch(job['url'], render=False).get('text', '')[:5000]
        except Exception: job['jd_text'] = ''
    lines = []
    for i, q in enumerate(questions):
        lim = f", max {q['maxlength']} chars" if q.get('maxlength') else ''
        lines.append(f"[{i}] ({q['kind']}{', required' if q.get('required') else ', optional'}{lim}) {q['label'][:400]}"
                     + (f"\n    options: {' | '.join(q['options'][:60])}" if q.get('options') else ''))
    out = ask_cached(job, [q['label'] for q in questions], lines)
    ans = [None] * len(questions)
    for a in out.get('answers', []):
        i, v = a.get('id'), a.get('answer')
        if isinstance(i, int) and 0 <= i < len(questions) and v is not None:
            v = v.strip(); q = questions[i]
            if not q.get('options') and BANNED.search(v) and not re.search(r'sponsor|authori', q['label'], re.I):
                continue   # hard rule: no work-auth wording in free text
            if q.get('maxlength'): v = v[:q['maxlength']]
            ans[i] = v
    return ans


def resolve(frame, pending, job):
    # de-duplicate (same label seen twice) and collect options for comboboxes
    qs, seen = [], set()
    for d in pending:
        key = (d.get('label') or '').strip()
        if not key or key in seen: continue
        seen.add(key)
        if d['kind'] == 'combobox' and not d.get('options'):
            d['options'] = _combobox_options(frame, frame.locator(f'[data-jh="{d["n"]}"]').first)
        qs.append(d)
    if not qs: return []
    if 'jd_text' not in job:
        # render=False: we are inside the apply script's sync Playwright -- a 2nd one can't start ("Sync API inside
        # asyncio loop", Oct 12) -> use the open application page's text instead
        try: job['jd_text'] = JD.fetch(job['url'], render=False).get('text', '')[:5000]
        except Exception: job['jd_text'] = ''
        if len(job['jd_text']) < 200:
            try: job['jd_text'] = frame.locator('body').inner_text(timeout=5000)[:5000]
            except Exception: pass
    lines = []
    for i, d in enumerate(qs):
        opts = _options(d)
        kind = 'choice' if opts else ('long text' if d['kind'] == 'textarea' else 'short text')
        lim = f", max {d['maxlength']} chars" if d.get('maxlength') else ''
        lines.append(f"[{i}] ({kind}{', required' if d.get('required') else ', optional'}{lim}) {d['label'][:400]}"
                     + (f"\n    options: {' | '.join(opts[:60])}" if opts else ''))
    out = ask_cached(job, [d['label'] for d in qs], lines)
    answered = []
    for a in out.get('answers', []):
        i, ans = a.get('id'), a.get('answer')
        if ans is None or not isinstance(i, int) or i >= len(qs): continue
        d = qs[i]; ans = ans.strip()
        if d['kind'] in ('textarea', 'input') and not _options(d) and BANNED.search(ans) and not re.search(r'sponsor|authori', d['label'], re.I):
            continue  # hard rule: no work-auth wording in free text
        if d.get('maxlength'): ans = ans[:d['maxlength']]
        if _apply(frame, d, ans): answered.append(d['label'])
    return answered


def _apply(f, d, ans):
    try:
        opts = _options(d)
        if d['kind'] in ('radio', 'buttons', 'checks'):
            o = next((x for x in d['options'] if x['text'].strip().lower() == ans.lower()), None)
            if not o: return False
            loc = f.locator(f'[data-jh="{o["n"]}"]').first
            loc.check(force=True) if d['kind'] in ('radio', 'checks') else loc.click()
            return True
        loc = f.locator(f'[data-jh="{d["n"]}"]').first
        if d['kind'] == 'select':
            o = next((x for x in opts if x.strip().lower() == ans.lower()), None)
            if not o: return False
            loc.select_option(label=o); return True
        if d['kind'] == 'combobox':
            loc.scroll_into_view_if_needed(); loc.click(); f.wait_for_timeout(400)
            import formfill
            o = formfill.options_of(f, loc)
            texts = [t.strip() for t in o.all_inner_texts()]
            if ans not in texts:
                loc.press_sequentially(ans[:40], delay=15); f.wait_for_timeout(800)
                texts = [t.strip() for t in o.all_inner_texts()]
            if ans in texts:
                o.nth(texts.index(ans)).click(); return True
            loc.press('Escape'); return False
        if (loc.get_attribute('type') or '').lower() == 'number':
            import formfill
            ans = formfill.numeric(ans, d.get('label'))
            if ans is None: return False
        loc.fill(ans); return True
    except Exception:
        return False
