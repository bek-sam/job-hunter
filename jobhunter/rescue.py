"""Claude rescue for a stuck application page (owner, Oct 13 2026: "don't close the page midway because of a simple
error Claude can fix"). Playwright shows Claude the page as structured text (fields, values, options, errors, buttons);
Claude answers with actions; Playwright performs them; up to 3 rounds. The calling adapter then continues its flow.

Hard limits enforced HERE (not only in the prompt):
- never types into password / SSN / ID / bank / card / signature fields (formfill.NEVER) except the Workday self-ID
  name+date when AUTO_SELF_ID=1, never solves CAPTCHAs (caller stops before), never clicks Submit / Sign in /
  Create account / Next / Continue / Review (the adapter owns navigation and auth.submit owns the final click),
- consent boxes only with AUTO_CONSENT=1, guesses allowed except verifiable facts / legal status (answers.GUESS, owner Oct 26).
"""
import applicant as P
import json, os, re
import config, formfill, llm
from answers import GUESS

SNAP_JS = r"""
(root) => {
  const vis = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length) && getComputedStyle(e).visibility !== 'hidden';
  const txt = e => (e ? (e.innerText || e.textContent || '') : '').replace(/\s+/g, ' ').trim();
  const doc = root.ownerDocument || root;
  const labelOf = el => {
    if (el.getAttribute('aria-label')) return el.getAttribute('aria-label');
    if (el.id) { const l = doc.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) return txt(l); }
    const lb = el.getAttribute('aria-labelledby'); if (lb) return lb.split(/\s+/).map(i => txt(doc.getElementById(i))).join(' ');
    const ff = el.closest('[data-automation-id^="formField"], .field, .form-group, .application-question, fieldset, [role=group]');
    if (ff) { const l = ff.querySelector('label, legend, [data-automation-id="formLabel"]'); if (l) return txt(l); }
    return el.placeholder || el.name || '';
  };
  let n = 0; const fields = [], buttons = [];
  const sel = 'input:not([type=hidden]):not([type=file]), textarea, select, button[aria-haspopup="listbox"], [role=combobox], [role=checkbox], [role=radio]';
  for (const el of root.querySelectorAll(sel)) {
    if (!vis(el)) continue;
    const id = 'rx' + (++n); el.setAttribute('data-rx', id);
    const aid = el.getAttribute('data-automation-id') || '';
    const type = el.tagName === 'SELECT' ? 'select'
               : (aid === 'searchBox' || el.getAttribute('aria-autocomplete') === 'list' || /multiselect|searchbox/i.test(el.closest('[data-automation-id]')?.getAttribute('data-automation-id') || '')) ? 'search_select'
               : (el.getAttribute('aria-haspopup') === 'listbox' || el.getAttribute('role') === 'combobox') ? 'dropdown'
               : (el.getAttribute('role') || el.type || el.tagName.toLowerCase());
    const label = labelOf(el).slice(0, 300);
    const required = el.required || el.getAttribute('aria-required') === 'true' || /\*\s*$/.test(label);
    let value = (type === 'checkbox' || type === 'radio') ? (el.checked || el.getAttribute('aria-checked') === 'true')
              : type === 'dropdown' && el.tagName === 'BUTTON' ? txt(el) : (el.value || '');
    if (!value && (type === 'dropdown' || type === 'search_select')) {   // react-select shows its value in a sibling div
      const c = el.closest('[class*="select__control"], [class*="-control"], [class*="__control"]');
      const sv = c && c.querySelector('[class*="single-value"], [class*="singleValue"], [class*="multi-value__label"]');
      if (sv) value = txt(sv);
    }
    const f = {id, type, label, required, value: typeof value === 'string' ? value.slice(0, 120) : value};
    if (el.tagName === 'SELECT') f.options = [...el.options].map(o => o.text.trim()).slice(0, 60);
    if (el.getAttribute('aria-invalid') === 'true') f.invalid = true;
    fields.push(f);
  }
  for (const b of root.querySelectorAll('button, [role=button], a[role=button]')) {
    if (!vis(b) || b.getAttribute('data-rx')) continue;
    const t = (txt(b) || b.getAttribute('aria-label') || '').slice(0, 60); if (!t) continue;
    const id = 'rx' + (++n); b.setAttribute('data-rx', id); buttons.push({id, text: t});
  }
  const errors = [...root.querySelectorAll('[role=alert], [data-automation-id*="rror"], .error, .error-message, [aria-live=assertive]')]
                 .filter(vis).map(txt).filter(Boolean).slice(0, 15);
  const heads = [...root.querySelectorAll('h1,h2,h3,legend')].filter(vis).map(txt).filter(Boolean).slice(0, 12);
  return {fields: fields.slice(0, 120), buttons: buttons.slice(0, 40), errors, heads};
}
"""

BLOCK_CLICK = re.compile(r'submit|send application|apply now|^apply$|sign ?in|log ?in|create account|register|next|continue|review|'
                         r'save and continue|withdraw|delete (my )?account|captcha', re.I)
SCHEMA = {'type': 'object', 'required': ['status', 'actions'], 'properties': {
    'status': {'type': 'string', 'enum': ['continue', 'need_human']},
    'reason': {'type': 'string'},
    'actions': {'type': 'array', 'items': {'type': 'object', 'required': ['op', 'id'], 'properties': {
        'op': {'type': 'string', 'enum': ['fill', 'select', 'check', 'uncheck', 'click']},
        'id': {'type': 'string'}, 'value': {'type': 'string'}}}}}}
SYSTEM = (f"You repair a job-application page for {P.NAME} so the normal automation can continue. You see the "
          "page as data. Return the smallest set of actions that fixes the listed problems and fills every REQUIRED empty "
          "field truthfully from the Answer Bank / resume facts. Choice fields: 'value' must be one of the shown options "
          "(or, for dropdowns without listed options, the exact text to search). Use 'click' only for Add/Delete/Close/"
          "expand-type buttons (never Submit/Next/Sign in). " + GUESS + " Only a question in those never-invent groups with no "
          "answer in the facts gets status need_human (question in reason). Do not touch fields that are already correct.")


def snapshot(scope):
    return scope.evaluate(SNAP_JS, scope.locator('body').element_handle() if hasattr(scope, 'locator') else None)


def _scope(page):
    try:
        return formfill.form_frame(page)
    except Exception:
        return page.main_frame


OPT_SEL = '[role=option]:visible, [data-automation-id="promptOption"]:visible, [data-automation-id="promptLeafNode"]:visible'


def _pick_from(page, value):
    opts = page.locator(OPT_SEL)
    texts = [re.sub(r'\s+', ' ', t).strip() for t in opts.all_inner_texts()]
    v = value.strip().lower()
    for i, t in enumerate(texts):
        if t.lower() == v: opts.nth(i).click(); return t
    for i, t in enumerate(texts):
        if t and (v in t.lower() or t.lower() in v): opts.nth(i).click(); return t
    return None


def _choose(frame, page, loc, value, search=False):
    """Dropdown / combobox / Workday search-select: click the option matching value. Search boxes (Workday School,
    Field of Study, Skills...) need: type the text, Enter, wait for results, click the result (typing alone selects
    nothing -- Cadence Oct 13). Returns the picked option text or None."""
    loc.click(timeout=5000); frame.wait_for_timeout(700)
    got = None if search else _pick_from(page, value)
    if got: return got
    for q in dict.fromkeys([value, ' '.join(value.split()[:2]), value.split()[0] if value.split() else value]):
        try:
            loc.fill(''); loc.type(q[:40], delay=40); page.keyboard.press('Enter'); frame.wait_for_timeout(1800)
        except Exception:
            page.keyboard.type(q[:40], delay=40); page.keyboard.press('Enter'); frame.wait_for_timeout(1800)
        got = _pick_from(page, value) or _pick_from(page, q)
        if got: break
    page.keyboard.press('Escape'); frame.wait_for_timeout(300)
    return got


def _do(frame, page, fields, a):
    f = next((x for x in fields if x['id'] == a.get('id')), None)
    loc = frame.locator(f'[data-rx="{a.get("id")}"]').first
    op, val = a['op'], a.get('value') or ''
    if op == 'click':
        if not loc.count(): return 'unknown id'
        btn_txt = ' '.join(x for x in (loc.inner_text(), loc.get_attribute('aria-label'), loc.get_attribute('value'),
                                       loc.get_attribute('title'), loc.get_attribute('data-automation-id')) if x)
        # submit-type controls are refused even without text (icon buttons, <input type=submit>)
        if BLOCK_CLICK.search(btn_txt) or (loc.get_attribute('type') or '').lower() == 'submit' or not btn_txt.strip():
            return f'refused click "{btn_txt[:30]}"'
        loc.click(timeout=5000); frame.wait_for_timeout(1200); return 'clicked ' + btn_txt[:30]
    if f is None: return 'unknown id'
    lab = f.get('label') or ''
    selfid = os.environ.get('AUTO_SELF_ID') == '1' and re.search(r'^(name|date|today)', lab, re.I)
    if (formfill.NEVER.search(lab) and not selfid) or f.get('type') == 'password':
        return f'refused protected field "{lab[:40]}"'
    if op in ('check', 'uncheck'):
        if formfill.CONSENT.search(lab) and not formfill.consent_on(): return 'refused consent (AUTO_CONSENT off)'
        (loc.check if op == 'check' else loc.uncheck)(force=True); return f'{op} {lab[:40]}'
    if f['type'] in ('dropdown', 'search_select') and f.get('value') and not f.get('invalid'):
        return f'kept {lab[:30]} (already {str(f["value"])[:20]})'   # re-picking cleared SpaceX dropdowns (Oct 20)
    if op == 'select' or (op == 'fill' and f['type'] == 'search_select'):
        if f['type'] == 'select': loc.select_option(label=val); return f'selected {val[:30]}'
        if f['type'] in ('radio', 'checkbox'): loc.check(force=True); return f'chose {lab[:30]}'
        got = _choose(frame, page, loc, val, search=f['type'] == 'search_select')
        return f'chose {got[:40]} in {lab[:30]}' if got else f'unknown: no option matching {val[:30]!r} in {lab[:30]}'
    if op == 'fill':
        aid = (loc.get_attribute('data-automation-id') or '') + ' ' + (loc.get_attribute('id') or '')
        if 'dateSection' in aid or (loc.get_attribute('role') or '') == 'spinbutton':
            # Workday date/spin sections ignore fill(): click and type digits like a person (Cadence, Oct 13)
            loc.click(timeout=4000); page.keyboard.press('Meta+A'); page.keyboard.type(re.sub(r'\D', '', val), delay=80)
            page.keyboard.press('Tab'); return f'typed {lab[:30]}'
        t = (loc.get_attribute('type') or '').lower()
        if t == 'number': val = formfill.numeric(val, lab) or ''
        if t == 'date': val = formfill.iso_date(val) or ''
        loc.fill(val); loc.press('Tab'); return f'filled {lab[:40]}'
    return 'noop'


def _log_rescue(page, job, problems, snap, out):
    """data/answer_log/rescue.jsonl: what was stuck, what Claude saw (fields), what it decided -- training data."""
    try:
        d = config.DATA / 'answer_log'; d.mkdir(exist_ok=True)
        with open(d / 'rescue.jsonl', 'a') as fh:
            fh.write(json.dumps({'at': __import__('datetime').datetime.now().isoformat(timespec='seconds'), 'url': page.url,
                                 'company': job.get('company'), 'title': job.get('title'), 'problems': problems[:10],
                                 'fields': snap.get('fields', [])[:80], 'errors': snap.get('errors'), 'decision': out},
                                ensure_ascii=False) + '\n')
    except Exception:
        pass


def fix(page, job, problems, rounds=3):
    """Try to resolve `problems` (list of strings) on the current page. Returns (ok, log)."""
    if os.environ.get('RESCUE', '1') != '1': return False, ['rescue off']
    frame = _scope(page); log = []
    bank = (config.REF / 'Job Hunter — Answer Bank.md').read_text()[:14000]
    try:
        import answers
        facts = answers.resume_facts()
    except Exception:
        facts = ''
    for r in range(rounds):
        try:
            snap = snapshot(frame)
        except Exception as e:
            log.append(f'snapshot failed: {str(e)[:80]}'); return False, log
        prompt = (f"ANSWER BANK:\n{bank}\n\nRESUME FACTS:\n{facts}\n\n"
                  f"APPLICATION: {job.get('title') or ''} at {job.get('company') or ''}\nURL: {page.url}\n"
                  f"PROBLEMS REPORTED BY THE AUTOMATION:\n" + '\n'.join(f'- {p[:300]}' for p in problems) +
                  f"\n\nPAGE (round {r + 1}):\n{json.dumps(snap, ensure_ascii=False)[:24000]}")
        try:
            out = llm.ask(prompt, system=SYSTEM, schema=SCHEMA, job='rescue')
        except Exception as e:
            log.append(f'claude failed: {str(e)[:80]}'); return False, log
        if out.get('status') == 'need_human':
            log.append('need human: ' + (out.get('reason') or '')[:200]); return False, log
        acts = out.get('actions') or []
        _log_rescue(page, job, problems, snap, out)
        if not acts: return True, log + ['nothing left to fix']
        for a in acts[:25]:
            try: log.append(_do(frame, page, snap['fields'], a))
            except Exception as e: log.append(f"{a.get('op')} {a.get('id')} failed: {str(e)[:60]}")
        frame.wait_for_timeout(1000)
        problems = [f'still to check after round {r + 1}: required fields empty or errors shown']
    return True, log
