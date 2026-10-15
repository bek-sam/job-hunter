"""Eightfold (careers.<co>.com/careers/job/<pid>, <co>.eightfold.ai; Microsoft, PayPal, AMD, Qualcomm, NetApp ...).
Apply -> /careers/apply?pid=... single page: resume upload, contact info, application questions (custom
comboboxes), EEO checkbox groups, privacy/NDA consent boxes, "Submit application". Stops before Submit.

The form is React and re-renders after every dropdown pick (fields get inserted, nodes reused), so tagging
fields once is unsafe: every action re-finds the field by its id and re-checks its label right before acting."""
import re
import applicant as P
import formfill
from ats.common import captcha, needs_login, outcome, result, click_text

MATCH = r'eightfold\.ai|/careers/(job|apply)\b.*pid=|/careers/job/\d{6,}|jobs\.careers\.microsoft\.com|careers\.(amd|qualcomm|ericsson|lamresearch)\.com'
HTML_MATCH = r'eightfold\.ai|eightfold-'

FIELDS_JS = r"""() => {
  const txt = e => (e ? (e.innerText || e.textContent || '') : '').replace(/\s+/g, ' ').trim();
  const out = [];
  for (const e of document.querySelectorAll('input, textarea')) {
    if (!e.id || !(e.offsetWidth || e.offsetHeight)) continue;
    const type = (e.type || '').toLowerCase();
    if (['hidden', 'file', 'submit', 'button', 'radio'].includes(type)) continue;
    const l = document.querySelector(`label[for="${CSS.escape(e.id)}"]`);
    let lab = txt(l) || e.getAttribute('aria-label') || '';
    const lb = e.getAttribute('aria-labelledby');
    if (!lab && lb) lab = lb.split(' ').map(id => txt(document.getElementById(id))).join(' ').trim();
    if (!lab) { let p = e.parentElement; for (let i = 0; i < 6 && p && !lab; i++, p = p.parentElement) {
      const q = p.querySelector('label, legend, [class*="label" i]'); if (q && !q.contains(e)) lab = txt(q); } }
    out.push({id: e.id, type, combo: e.getAttribute('role') === 'combobox', label: lab.slice(0, 300), value: e.value || '',
              checked: !!e.checked, required: e.required || e.getAttribute('aria-required') === 'true' || /\*\s*$/.test(lab)});
  }
  return out;
}"""


# Checked before formfill.RULES. (label regex, value, option regex) -- value None = leave empty on purpose
OVERRIDES = [
    (r'extension', None, None),
    (r'phone (country )?code|country (phone )?code|dial', 'United States', r'^(\W*\s*)?(\(\+1\)\s*)?united states( of america)?\b(?!\s*minor)'),
    (r'phone device|device type|phone type', 'Mobile', r'^mobile|^cell'),
    (r'previous(ly)? employee|former employee|previously (been )?(employed|worked)', 'No', r'^no\b'),
] + P.rules([(r'veteran', 'veteran')], 'eightfold_answers')   # self-ID answer from profile.json


def rule(label):
    l = (label or '').strip().lower()
    for rx, val, opt in OVERRIDES:
        if re.search(rx, l): return (val, opt, True)
    v, o = formfill.rule_for(label)
    return (v, o, False)


def _fields(page):
    return page.evaluate(FIELDS_JS)


def _label_now(page, fid):
    for f in _fields(page):
        if f['id'] == fid: return f
    return None


def _pick_combo(page, fid, val, opt_rx):
    loc = page.locator(f'[id="{fid}"]')
    loc.scroll_into_view_if_needed(timeout=5000); loc.click(timeout=5000); page.wait_for_timeout(700)
    opts = page.locator('[role="option"]:visible')
    texts = [t.strip() for t in opts.all_inner_texts()]
    o = formfill.pick(texts, opt_rx, val) if texts else None
    if o is None and val:
        loc.press_sequentially(val.split(' (')[0][:25], delay=30); page.wait_for_timeout(1000)
        texts = [t.strip() for t in opts.all_inner_texts()]
        o = formfill.pick(texts, opt_rx, val) if texts else None
    if o is not None:
        opts.nth(texts.index(o)).click(timeout=5000); page.wait_for_timeout(900); return o
    loc.press('Escape')
    return None


def _check_box(page, fid):
    b = page.locator(f'[id="{fid}"]')
    for how in ('label', 'click', 'js'):
        try:
            if b.is_checked(): return True
            if how == 'label':
                l = page.locator(f'label[for="{fid}"]')
                if not l.count(): continue
                l.first.click(timeout=3000)
            elif how == 'click': b.click(force=True, timeout=3000)
            else: b.evaluate('e => { e.click(); e.dispatchEvent(new Event("change", {bubbles: true})); }')
            page.wait_for_timeout(300)
        except Exception:
            continue
    try: return b.is_checked()
    except Exception: return False


def _combo_options(page, fid):
    try:
        loc = page.locator(f'[id="{fid}"]')
        loc.scroll_into_view_if_needed(timeout=5000); loc.click(timeout=5000); page.wait_for_timeout(700)
        t = [x.strip() for x in page.locator('[role="option"]:visible').all_inner_texts() if x.strip()]
        loc.press('Escape'); page.wait_for_timeout(300)
        return t[:60]
    except Exception:
        return []


def _llm(page, pending, job, done, unknown):
    """Custom questions the rules couldn't answer -> one batched truthful LLM call (answers.py)."""
    if not pending or job.get('no_llm'): return
    import answers
    qs = [dict(label=f['label'] or f['id'], options=(_combo_options(page, f['id']) if f['combo'] else []), required=f['required'],
               maxlength=None, kind='long text' if f['type'] == 'textarea' else
               ('number (digits only; for pay: his Answer Bank range, hourly if the role is an internship)' if f['type'] == 'number'
                else 'short text')) for f in pending]
    for q in qs:
        if q['options']: q['kind'] = 'choice'
    try:
        ans = answers.answer_questions(qs, job)
    except Exception as e:
        print('eightfold answer step failed:', str(e)[:200]); return
    for f, q, a in zip(pending, qs, ans):
        if not a: continue
        try:
            if f['combo']:
                if _pick_combo(page, f['id'], a, r'^' + re.escape(a) + r'$'): done.append(q['label']); unknown.pop(f['id'], None)
            else:
                page.locator(f'[id="{f["id"]}"]').fill(a, timeout=5000); done.append(q['label']); unknown.pop(f['id'], None)
        except Exception:
            continue


def _ethnicity(page, done):
    """EEO race checkbox group (Answer Bank: Asian)."""
    boxes = page.locator('input[type="checkbox"][name*="ETHNICITY" i], input[type="checkbox"][name*="race" i]')
    for i in range(boxes.count()):
        b = boxes.nth(i)
        lab = b.evaluate("e => { const l = e.id && document.querySelector(`label[for=\"${CSS.escape(e.id)}\"]`); return (l ? l.innerText : '').trim() }")
        if re.match(r'^asian\b', lab or '', re.I) or re.match(r'^asian\b', b.get_attribute('aria-label') or '', re.I):
            if not b.is_checked():
                lbl = page.locator(f'label[for="{b.get_attribute("id")}"]')
                (lbl.first if lbl.count() else b).click(timeout=5000); page.wait_for_timeout(400)
            if b.is_checked(): done.append('Race/ethnicity: Asian'); return True
            return False
    return False


def fill(page, job):
    done, unknown, handled = [], {}, set()
    pending = []
    for _ in range(3):                      # passes: new fields appear after some answers (e.g. State after Country)
        progressed = False
        for f in _fields(page):
            fid, lab = f['id'], f['label']
            if fid in handled: continue
            if f['type'] == 'checkbox':
                handled.add(fid)
                if re.search(r'ethnicity|race', f['id'] + ' ' + lab, re.I): continue
                if formfill.CONSENT.search(lab) and formfill.consent_on() and not f['checked']:
                    if _check_box(page, fid): done.append(lab + ' (consent)'); continue
                if formfill.CONSENT.search(lab) and f['required'] and not f['checked']: unknown[fid] = lab + ' (legal/consent — yours)'
                continue
            if f['value']:
                # values pre-filled by the resume parser are re-checked only for fields we know are often wrong
                v, o, forced = rule(lab)
                if not (forced and o and not re.search(o, f['value'], re.I)): handled.add(fid); continue
            if formfill.CONSENT.search(lab) and formfill.consent_on() and f['combo'] and not formfill.NEVER.search(lab):
                handled.add(fid)
                if _pick_combo(page, fid, 'Yes', formfill.AGREE.pattern): done.append(lab + ' (consent)'); continue
                if f['required']: unknown[fid] = lab + ' (legal/consent — yours)'
                continue
            if formfill.NEVER.search(lab) or formfill.CONSENT.search(lab):
                handled.add(fid)
                if f['required']: unknown[fid] = lab + (' (legal/consent — yours)' if formfill.CONSENT.search(lab) else '')
                continue
            if re.search(r'cover letter', lab, re.I) and f['type'] == 'textarea':
                if job.get('cover_text'): page.locator(f'[id="{fid}"]').fill(job['cover_text']); done.append('Cover letter')
                handled.add(fid); continue
            val, opt, forced = rule(lab)
            if f['type'] == 'number' and val is not None and not re.fullmatch(r'[\d.]+', str(val)):
                val = opt = None                        # numbers-only box: let the answer step pick an honest number
            if val is None and opt is None:
                handled.add(fid)
                if f['required'] and not forced: unknown[fid] = lab; pending.append(f)
                continue
            now = _label_now(page, fid)                 # re-check right before acting (React re-renders)
            if not now or now['label'] != lab: continue  # changed under us: retry next pass
            try:
                if f['combo']:
                    o = _pick_combo(page, fid, val, opt)
                    if o: done.append(lab); unknown.pop(fid, None)
                    elif f['required']: unknown[fid] = lab
                else:
                    page.locator(f'[id="{fid}"]').fill(val, timeout=5000); done.append(lab)
                handled.add(fid); progressed = True
            except Exception as e:
                handled.add(fid)
                if f['required']: unknown[fid] = f'{lab} (error: {str(e)[:50]})'
        if not progressed: break
    _llm(page, pending, job, done, unknown)
    return done, list(unknown.values())


def run(ctx, page, job):
    if '/apply' not in page.url:
        n = len(ctx.pages)
        if not (click_text(page, r'^\s*apply( now)?\s*$') or click_text(page, r'^\s*apply( now)?\s*$', role='link')):
            return result('NEEDS YOU', 'Eightfold: no Apply button found (tab open)', page=page)
        page.wait_for_timeout(4000)
        if len(ctx.pages) > n: page = ctx.pages[-1]; page.wait_for_load_state('domcontentloaded'); page.wait_for_timeout(3000)
    if needs_login(page): return result('NEEDS YOU', 'Eightfold: sign in or create account (tab open)', page=page)
    if captcha(page): return result('NEEDS YOU', 'Eightfold: CAPTCHA (tab open)', page=page)
    try:
        page.wait_for_selector('input[id*="firstname" i], input[type="email"], input[id^="Contact_Information"]', timeout=20000)
    except Exception:
        return result('NEEDS YOU', 'Eightfold: application form did not load (tab open)', page=page)
    page.wait_for_timeout(1500)
    done = []
    d, unknown = fill(page, job)
    done += d
    has_race = _ethnicity(page, done)
    # resume LAST: the upload opens a "Data Privacy Agreement ... I Agree" modal (consent -> his click)
    privacy = False
    if job.get('pdf'):
        f = page.locator('input[type="file"]')
        if f.count():
            f.first.set_input_files(job['pdf']); page.wait_for_timeout(5000); done.append('Resume')
            dlg = [x for x in page.locator('[role="dialog"][aria-modal="true"]').all() if x.is_visible()]
            if dlg and re.search(r'privacy|agree|terms', dlg[0].inner_text(), re.I):
                privacy = True
                if formfill.consent_on():
                    btn = dlg[0].get_by_role('button', name=re.compile(r'^\s*(i agree|agree|accept|i accept|confirm|ok|continue)\s*$', re.I))
                    if btn.count():
                        btn.first.click(timeout=5000); page.wait_for_timeout(2500); privacy = False
                        done.append('Data Privacy Agreement (consent)')
    # final sweep: every required field that is still empty must be reported (never claim FILLED with gaps)
    for f in _fields(page):
        if f['type'] == 'checkbox':
            if f['required'] and not f['checked'] and formfill.CONSENT.search(f['label']):
                if formfill.consent_on() and _check_box(page, f['id']): continue
                m = f['label'] + ' (legal/consent — yours)'
                if m not in unknown: unknown.append(m)
            continue
        if f['required'] and not f['value'] and not any(f['label'] and f['label'] in u for u in unknown):
            unknown.append(f['label'] or f['id'])
    if not has_race and page.locator('input[name*="ETHNICITY" i]').count(): unknown.append('Race/ethnicity')
    if privacy: unknown.insert(0, 'Data Privacy Agreement popup: click "I Agree" (legal/consent — yours) so the resume is attached')
    if not job.get('pdf'): unknown.append('resume PDF missing on the Notion page')
    return outcome(done, unknown, page)
