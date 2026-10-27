"""Workday (*.myworkdayjobs.com, *.myworkdaysite.com). Multi-step: Autofill with Resume -> My Information ->
My Experience -> Application Questions -> Voluntary Disclosures -> Self Identify -> Review.

Never types a password, never creates an account, never ticks the terms/consent box, never signs the
self-identification form unless AUTO_SELF_ID=1 (Oct 13), never clicks the final Submit. Stops on Review (FILLED) or on the first step it
cannot finish (NEEDS YOU with the exact questions). Requires an existing Workday session for the tenant in the
apply Chrome profile; otherwise returns NEEDS YOU "sign in or create account".
"""
import applicant as P
import re
import formfill
from ats.common import result, outcome, needs_login, captcha, safe_next, body_text, FINAL_SUBMIT
from ats.workday_map import id_rule, WORK, SCHOOL, work_for, norm

MATCH = r'myworkdayjobs\.com|myworkdaysite\.com'
NEXT_BTN = '[data-automation-id="bottom-navigation-next-button"], [data-automation-id="pageFooterNextButton"]'
MAX_STEPS = 10
CLAUDE_STEPS = ('my_experience', 'application_questions', 'voluntary_disclosures', 'self_identify', 'other')


def tenant(url):
    m = re.search(r'https?://([\w-]+)\.', url)
    return m.group(1) if m else 'this company'


def _vis(loc):
    try:
        return loc.count() > 0 and loc.first.is_visible()
    except Exception:
        return False


def _aid(page, aid):
    return page.locator(f'[data-automation-id="{aid}"]')


# ---------------------------------------------------------------- walls
def wall(page):
    """Sign-in / create-account wall (not the header 'Sign In' link that is always present)."""
    if needs_login(page): return True
    for aid in ('signInContent', 'createAccountContent', 'SignInWithEmailButton', 'signInSubmitButton',
                'createAccountSubmitButton', 'createAccountLink'):
        if _vis(_aid(page, aid)): return True
    if _vis(page.get_by_role('button', name=re.compile(r'^sign in with email$', re.I))): return True
    return False


def signed_in(page):
    try:
        t = ' '.join(page.locator('[data-automation-id="utilityMenuButton"], [data-automation-id="accountSettingsButton"]').all_inner_texts())
        return '@' in t
    except Exception:
        return False


def in_application(page):
    return _vis(_aid(page, 'progressBar')) or _vis(page.locator(NEXT_BTN)) or _vis(_aid(page, 'applyFlowPage'))


def step_name(page):
    for sel in ('[data-automation-id="progressBarActiveStep"]', '[data-automation-id="progressBar"] [aria-current="step"]'):
        try:
            loc = page.locator(sel)
            if loc.count():
                t = re.sub(r'(current step|(step )?\d+ of \d+)', '', loc.first.inner_text(), flags=re.I)
                return re.sub(r'\s+', ' ', t).strip()
        except Exception:
            pass
    try:
        h = page.locator('main h2, [data-automation-id="applyFlowPage"] h2').first.inner_text()
        return h.strip()
    except Exception:
        return ''


def errors(page):
    msgs = []
    for sel in ('[data-automation-id="errorMessage"]', '[data-automation-id="errorBanner"] li', '[data-automation-id="inputAlert"]',
                '[role="alert"]'):
        try:
            for t in page.locator(sel).all_inner_texts():
                t = re.sub(r'\s+', ' ', t).strip()
                if t and t not in msgs and not re.match(r'^errors? found$', t, re.I) \
                        and not re.search(r'successfully uploaded', t, re.I): msgs.append(t[:160])
        except Exception:
            pass
    return msgs


# ---------------------------------------------------------------- entry
def start(ctx, page, job):
    """Job page -> Apply -> (Autofill with Resume | Apply Manually). Returns the page of the apply flow."""
    if '/job/' not in page.url and not in_application(page):
        return page, 'not a Workday job posting URL'
    # already inside the apply flow (e.g. LinkedIn deep-links to .../apply/applyManually) or at the account wall
    at_wall = _vis(_aid(page, 'createAccountSubmitButton')) or _vis(_aid(page, 'signInSubmitButton')) or \
        _vis(_aid(page, 'signInLink')) and _vis(_aid(page, 'backToJobPosting'))
    chooser = _vis(_aid(page, 'autofillWithResume')) or _vis(_aid(page, 'applyManually')) or \
        _vis(page.get_by_role('button', name=re.compile(r'autofill with resume|apply manually|use my last application', re.I)))
    if at_wall or (re.search(r'/apply(/|\?|$)', page.url) and not chooser):
        return page, None
    if re.search(r'you applied for this job on|you have already applied', body_text(page, 6000), re.I):
        return page, 'already applied'
    if not in_application(page) and not chooser:
        try:   # the job page renders its Apply button a few seconds after load
            page.wait_for_selector('[data-automation-id="adventureButton"], [data-automation-id="applyButton"], '
                                   '[data-automation-id="continueButton"]', state='visible', timeout=20000)
        except Exception:
            pass
        btn = _aid(page, 'continueButton')   # an earlier run left a draft: "Continue Application" (SchoolsFirst Oct 12)
        if not _vis(btn): btn = _aid(page, 'adventureButton')
        if not _vis(btn): btn = _aid(page, 'applyButton')
        if not _vis(btn): btn = page.get_by_role('button', name=re.compile(r'^\s*apply\s*$', re.I))
        if not _vis(btn): btn = page.get_by_role('link', name=re.compile(r'^\s*apply\s*$', re.I))
        if not _vis(btn):
            if re.search(r'no longer (available|accepting)|job (has been )?closed|position (is )?(closed|filled)', body_text(page, 6000), re.I):
                return page, 'posting closed'
            return page, 'no Apply button found'
        n = len(ctx.pages)
        btn.first.click(); page.wait_for_timeout(2500)
        if len(ctx.pages) > n: page = ctx.pages[-1]; page.wait_for_load_state('domcontentloaded'); page.wait_for_timeout(2000)
    # apply-mode dialog
    auto, manual = _aid(page, 'autofillWithResume'), _aid(page, 'applyManually')
    if not _vis(auto): auto = page.get_by_role('button', name=re.compile(r'autofill with resume', re.I))
    if not _vis(manual): manual = page.get_by_role('button', name=re.compile(r'apply manually', re.I))
    if _vis(auto): auto.first.click(); page.wait_for_timeout(3000)
    elif _vis(manual): manual.first.click(); page.wait_for_timeout(3000)
    return page, None


def autofill_step(page, job):
    """'Autofill with Resume' step: upload the PDF and continue (Workday parses it into the next steps)."""
    fi = page.locator('input[type="file"]')
    if not fi.count(): return []
    if job.get('pdf'):
        fi.first.set_input_files(job['pdf'])
        for _ in range(20):
            page.wait_for_timeout(1000)
            if re.search(r'successfully uploaded|upload(ed)? successful', body_text(page, 6000), re.I) or \
               _vis(_aid(page, 'delete-file')): break
        return ['Resume (autofill)']
    return []


# ---------------------------------------------------------------- field-level helpers
FIELDS_JS = r"""
() => {
  const vis = e => !!(e && (e.offsetWidth || e.offsetHeight || e.getClientRects().length));
  const txt = e => (e ? (e.innerText || e.textContent || '') : '').replace(/\s+/g, ' ').trim();
  const out = []; let n = 0;
  const boxes = [...document.querySelectorAll('[data-automation-id^="formField-"], fieldset')].filter(vis);
  for (const box of boxes) {
    if (box.parentElement && box.parentElement.closest('[data-automation-id^="formField-"]')) continue;  // outermost only
    const lab = box.querySelector('label, legend');
    let label = txt(lab).replace(/\*$/, '').trim();
    const required = /\*/.test(txt(lab)) || !!box.querySelector('[aria-required="true"], [required]');
    const aid = (box.getAttribute('data-automation-id') || '').replace(/^formField-/, '');
    const tag = `wd${++n}`; box.setAttribute('data-jhwd', tag);
    let kind = 'other', value = '', inputId = '';
    const dd = box.querySelector('button[aria-haspopup="listbox"]');
    const ms = box.querySelector('[data-automation-id="multiselectInputContainer"], [data-uxi-widget-type="multiselect"] input, input[data-uxi-widget-type="selectinput"]');
    const radios = box.querySelectorAll('input[type="radio"]');
    const cbs = box.querySelectorAll('input[type="checkbox"]');
    const date = box.querySelector('[data-automation-id="dateSectionMonth-input"], [data-automation-id="dateSectionYear-input"]');
    const ta = box.querySelector('textarea');
    const inp = box.querySelector('input[type="text"], input[type="tel"], input[type="email"], input[type="number"], input:not([type])');
    if (dd) { kind = 'dropdown'; value = txt(dd); inputId = dd.id || dd.getAttribute('name') || ''; if (/^select one$/i.test(value)) value = ''; }
    else if (ms) { kind = 'multiselect'; value = [...box.querySelectorAll('[data-automation-id="selectedItem"]')].map(txt).join(' | '); }
    else if (radios.length) { kind = 'radio'; value = [...radios].some(r => r.checked) ? 'x' : '';
      const opts = [...radios].map(r => { const l = document.querySelector(`label[for="${CSS.escape(r.id)}"]`); return txt(l) || r.value; });
      out.push({tag, kind, label, required, aid, value, options: opts}); continue; }
    else if (date) { kind = 'date'; value = [...box.querySelectorAll('input')].map(i => i.value).join('/').replace(/^\/+|\/+$/g, ''); }
    else if (cbs.length > 1) {   // Oct 26: a checkbox GROUP is one question (Cohesity "Do any of the following apply?")
      value = [...cbs].some(c => c.checked) ? 'x' : '';
      const opts = [...cbs].map(c => { const l = document.querySelector(`label[for="${CSS.escape(c.id)}"]`); return txt(l) || c.value; });
      out.push({tag, kind: 'checks', label, required, aid, value, options: opts}); continue; }
    else if (cbs.length) { kind = 'checkbox'; value = [...cbs].some(c => c.checked) ? 'x' : ''; }
    else if (ta) { kind = 'textarea'; value = ta.value; inputId = ta.id; }
    else if (inp) { kind = 'text'; value = inp.value; inputId = inp.id || inp.name || ''; }
    out.push({tag, kind, label, required, aid, value, inputId});
  }
  return out;
}
"""


def answer_for(f):
    """(value, option_regex) from the Workday id map, then the generic Answer Bank label rules."""
    val, opt = id_rule(f.get('aid'))
    if val is None and opt is None: val, opt = id_rule(f.get('inputId'))
    if val is None and opt is None: val, opt = formfill.rule_for(f.get('label'))
    return val, opt


def _options(page):
    # only the popup that is open right now (several fields' option lists can exist in the DOM at once)
    active = page.locator('[data-automation-id="activeListContainer"]:visible')
    if active.count():
        loc = active.last.locator('[data-automation-id="promptOption"], [data-automation-id="promptLeafNode"], [role="option"]')
        try:
            if loc.count():
                return loc, [re.sub(r'\s+', ' ', t).strip() for t in loc.all_inner_texts()]
        except Exception:
            pass
    loc = page.locator('[role="listbox"]:visible [role="option"], [data-automation-id="promptOption"]:visible, [data-automation-id="promptLeafNode"]:visible')
    try:
        return loc, [re.sub(r'\s+', ' ', t).strip() for t in loc.all_inner_texts()]
    except Exception:
        return loc, []


def _close_popups(page):
    try:
        page.keyboard.press('Escape'); page.wait_for_timeout(200)
        if page.locator('[data-automation-id="activeListContainer"]:visible').count():
            page.mouse.click(5, 5); page.wait_for_timeout(300)
    except Exception:
        pass


LAST_OPTS = []   # options seen by the last failed pick -> shown in the NEEDS YOU note (and used to fix rules)
OPTS_SEEN = {}   # question label -> every option seen when its dropdown was opened (saved with the page record)


def _remember_opts(box, texts):
    try:
        lab = re.sub(r'\s+', ' ', box.locator('label').first.inner_text()).strip().rstrip('*').strip()
        if lab and texts: OPTS_SEEN[lab] = list(dict.fromkeys(t for t in texts if t))[:80]
    except Exception:
        pass


def pick_dropdown(page, box, val, opt):
    import auth
    _close_popups(page)    # an open search list (e.g. School) covers the next dropdown
    btn = box.locator('button[aria-haspopup="listbox"]').first
    try: btn.evaluate("e => e.scrollIntoView({block: 'center'})")
    except Exception: pass
    before = _options(page)[1]
    auth._wd_click(btn)
    for _ in range(6):    # this dropdown's own list (not the previous one's) must have rendered
        page.wait_for_timeout(500)
        loc, texts = _options(page)
        if texts and texts != before: break
    LAST_OPTS[:] = list(dict.fromkeys(texts))[:40]
    _remember_opts(box, texts)
    o = formfill.pick(texts, opt, val)
    if o is None:
        page.keyboard.press('Escape'); return False
    loc.nth(texts.index(o)).click(); page.wait_for_timeout(500)
    # Oct 13 eval (Caris "Degree is required"): the click can land while the list re-renders and nothing sticks.
    # Verify the button now shows the option; otherwise retry once by keyboard (open, type the start, Enter).
    def shows():
        try: return o.lower()[:20] in (btn.inner_text() or '').lower()
        except Exception: return True
    if shows(): return True
    try:
        _close_popups(page); btn.focus(); page.keyboard.press('Enter'); page.wait_for_timeout(700)
        page.keyboard.type(o[:12], delay=60); page.wait_for_timeout(700); page.keyboard.press('Enter'); page.wait_for_timeout(700)
    except Exception:
        pass
    if shows(): return True
    print(f"workday: '{o}' did not stick in {val!r} dropdown (button shows {(btn.inner_text() or '')[:40]!r})")
    return False


def pick_multiselect(page, box, val, opt):
    inp = box.locator('input').first
    inp.scroll_into_view_if_needed(); inp.click(); page.wait_for_timeout(600)
    try:   # a search left over from an earlier try (the school name -> "No Items.") hides every option
        if inp.input_value(): inp.fill(''); page.wait_for_timeout(500)
    except Exception:
        pass
    for attempt in range(2):
        loc, texts = _options(page)
        if texts: LAST_OPTS[:] = list(dict.fromkeys(texts))[:40]; _remember_opts(box, texts)
        o = formfill.pick(texts, opt, val) if texts else None
        if o is not None:
            loc.nth(texts.index(o)).click(); page.wait_for_timeout(900)
            # hierarchical prompts (e.g. "Career Site" -> "Company Website"): pick a leaf if a second level opened
            prev = texts
            for level in range(3):   # nested prompts: Websites -> LinkedIn; HPE: Career Site -> HPE Careers webpage -> ...
                loc2, texts2 = _options(page)
                if not texts2 or texts2 == prev: break
                print(f'workday: level-{level + 2} options', list(dict.fromkeys(texts2))[:30])
                uniq = list(dict.fromkeys(texts2))
                o2 = formfill.pick(texts2, opt, val) or (uniq[0] if len(uniq) == 1 else None)
                if not o2: break
                loc2.nth(texts2.index(o2)).click(); page.wait_for_timeout(700)
                prev = texts2
            page.keyboard.press('Escape')
            return True
        if attempt == 0:  # search
            inp.fill(val.split(' (')[0][:40]); inp.press('Enter'); page.wait_for_timeout(1500)
    page.keyboard.press('Escape')
    return False


def pick_radio(box, opts, val, opt):
    o = formfill.pick(opts, opt, val)
    if o is None: return False
    r = box.locator('input[type="radio"]').nth(opts.index(o))
    # Workday draws custom radios: a programmatic check often doesn't register -> click the label, then the input
    for how in ('label', 'click', 'check', 'js'):
        try:
            if how == 'label':
                rid = r.get_attribute('id')
                lab = box.locator(f'label[for="{rid}"]') if rid else box.get_by_text(o, exact=True)
                if not lab.count(): continue
                lab.first.click(timeout=4000)
            elif how == 'click': r.click(force=True, timeout=4000)
            elif how == 'check': r.check(force=True, timeout=4000)
            else: r.evaluate('e => { e.click(); e.dispatchEvent(new Event("change", {bubbles: true})); }')
        except Exception:
            continue
        try:
            if r.is_checked(): return True
        except Exception:
            return True
    return False


def _tick(page, box):
    cb = box.locator('input[type="checkbox"]').first
    for how in ('label', 'click', 'js'):
        try:
            if cb.is_checked(): return True
            if how == 'label':
                cid = cb.get_attribute('id')
                lab = page.locator(f'label[for="{cid}"]') if cid else None
                if lab is not None and lab.count(): lab.first.click(timeout=3000)
                else: continue
            elif how == 'click': cb.click(force=True, timeout=3000)
            else: cb.evaluate('e => { e.click(); e.dispatchEvent(new Event("change", {bubbles: true})); }')
            page.wait_for_timeout(300)
        except Exception:
            continue
    try: return cb.is_checked()
    except Exception: return False


def _tick_option(page, box, text):
    """Tick the checkbox of one group option by its label text."""
    for i, cb in enumerate(box.locator('input[type="checkbox"]').all()):
        cid = cb.get_attribute('id') or ''
        lab = page.locator(f'label[for="{cid}"]') if cid else None
        if lab is not None and lab.count() and (lab.first.inner_text() or '').strip() == text.strip():
            for how in ('label', 'js'):
                try:
                    if cb.is_checked(): return True
                    if how == 'label': lab.first.click(timeout=3000)
                    else: cb.evaluate('e => { e.click(); e.dispatchEvent(new Event("change", {bubbles: true})); }')
                    page.wait_for_timeout(300)
                except Exception:
                    continue
            try: return cb.is_checked()
            except Exception: return False
    return False


def _dropdown_options(page, box):
    try:
        btn = box.locator('button[aria-haspopup="listbox"]').first
        btn.scroll_into_view_if_needed(); btn.click(); page.wait_for_timeout(700)
        _, texts = _options(page)
        page.keyboard.press('Escape'); page.wait_for_timeout(300)
        _remember_opts(box, texts)
        return [t for t in texts if t and not re.match(r'^select one$', t, re.I)][:60]
    except Exception:
        return []


def llm_fill(page, pending, job):
    """Workday custom questions the rules couldn't answer -> one batched, truthful LLM call (answers.py)."""
    import answers
    qs = []
    for f in pending:
        box = page.locator(f'[data-jhwd="{f["tag"]}"]').first
        opts = f.get('options') or (_dropdown_options(page, box) if f['kind'] in ('dropdown', 'multiselect') else [])
        qs.append(dict(label=f.get('label') or '?', options=opts, required=f.get('required'), maxlength=None,
                       kind='choice' if opts else ('long text' if f['kind'] == 'textarea' else 'short text')))
    got = []
    for f, q, a in zip(pending, qs, answers.answer_questions(qs, job)):
        if not a: continue
        box = page.locator(f'[data-jhwd="{f["tag"]}"]').first
        try:
            ok = True
            exact = r'^' + re.escape(a) + r'$'
            if f['kind'] == 'dropdown': ok = pick_dropdown(page, box, a, exact)
            elif f['kind'] == 'multiselect': ok = pick_multiselect(page, box, a, exact)
            elif f['kind'] == 'radio': ok = pick_radio(box, f['options'], a, exact)
            elif f['kind'] == 'checks': ok = _tick_option(page, box, a)
            elif f['kind'] in ('text', 'textarea'):
                inp = box.locator('textarea' if f['kind'] == 'textarea' else 'input').first
                inp.fill(a); inp.press('Tab')
            else: ok = False
            if ok: got.append(q['label'])
        except Exception:
            continue
    return got


def fill_step(page, job):
    """Fill every visible Workday form field on the current step. Rules first, then one LLM call for the rest.
    Returns (done, unknown)."""
    done, unknown = [], []
    pending = []
    for f in page.evaluate(FIELDS_JS):
        label = f.get('label') or f.get('aid') or '?'
        box = page.locator(f'[data-jhwd="{f["tag"]}"]').first
        if formfill.GOV_ID.search(label):   # any government-ID request -> the application is cancelled (Oct 13)
            unknown.append('GOV-ID asked: ' + label[:120]); continue
        if formfill.NEVER.search(label):
            if f['required'] and not f['value']: unknown.append(label + ' (yours)')
            continue
        if re.search(r'upload a file|drop files|resume/?cv|^resume$', label, re.I):
            continue   # the resume upload is handled by autofill_step / the My Experience upload code
        if re.match(r'(workexperience|education|language|certification)-\d+', (f.get('inputId') or f.get('aid') or '').lower()):
            continue   # entries are handled by fill_work / fill_education (they know the real history)
        if f['kind'] == 'checks':   # one option of a checkbox group -- never "the first box" (that ticked "Current employee")
            if f['value']: continue
            val, opt = answer_for(f)
            o = formfill.choose(label, f['options'], opt, val)
            if o and _tick_option(page, box, o): done.append(label); continue
            if f['required']: unknown.append(label); pending.append(f)
            continue
        if f['kind'] == 'checkbox':
            if not f['value'] and formfill.consent_on() and (f['required'] or formfill.CONSENT.search(label)):
                if _tick(page, box): done.append(label + ' (consent)'); continue
            if f['required'] and not f['value']:
                unknown.append(label + ' (legal/consent — yours)')
            continue
        if f['value']:  # already set (resume parse / earlier run) -- keep, except the fields we know better
            if f['kind'] == 'text' and re.search(r'addressline1|city|postalcode|phonenumber--phonenumber', (f['aid'] or '').lower()):
                pass
            else:
                continue
        if formfill.CONSENT.search(label) and f['kind'] != 'dropdown' and not re.search(r'\bnumber\b|phone', label, re.I):
            if formfill.consent_on() and f['kind'] == 'radio':
                o = next((x for x in f.get('options') or [] if formfill.AGREE.search(x)), None)
                if o and pick_radio(box, f['options'], o, r'^' + re.escape(o) + r'$'):
                    done.append(label + ' (consent)'); continue
            if f['required']: unknown.append(label + ' (legal/consent — yours)')
            continue
        if formfill.CONSENT.search(label) and f['kind'] == 'dropdown' and formfill.consent_on():
            if pick_dropdown(page, box, 'Yes', formfill.AGREE.pattern):
                done.append(label + ' (consent)'); continue
        val, opt = answer_for(f)
        if val is None and opt is None:
            if f['required']: unknown.append(label); pending.append(f)
            continue
        try:
            ok = True
            if f['kind'] == 'dropdown': ok = pick_dropdown(page, box, val, opt)
            elif f['kind'] == 'multiselect': ok = pick_multiselect(page, box, val, opt)
            elif f['kind'] == 'radio': ok = pick_radio(box, f['options'], val, opt)
            elif f['kind'] in ('text', 'textarea'):
                inp = box.locator('textarea' if f['kind'] == 'textarea' else 'input').first
                inp.fill(val); inp.press('Tab')
            elif f['kind'] == 'date':
                ok = False  # dates only inside work/education entries (handled there)
            if ok: done.append(label)
            elif f['required']:
                print(f'workday: no option for {label!r} (wanted {val!r}); saw {LAST_OPTS}')
                unknown.append(label); pending.append(f)
        except Exception as e:
            if f['required']: unknown.append(f'{label} (error: {str(e)[:60]})')
    if pending and not job.get('no_llm'):
        try:
            got = llm_fill(page, pending, job)
            done += got
            unknown = [u for u in unknown if u not in got]
        except Exception as e:
            print('workday answer step failed:', str(e)[:200])
    return done, unknown


# ---------------------------------------------------------------- My Experience
def _set(page, sel, val):
    loc = page.locator(sel)
    if not loc.count(): return False
    loc.first.fill(str(val)); loc.first.press('Tab'); return True


def _type_spin(page, sel, text):
    """Workday month/year boxes are spinbuttons that ignore fill(): click, clear, type digits like a person."""
    loc = page.locator(sel)
    if not loc.count(): return False
    el = loc.first
    try:
        el.scroll_into_view_if_needed(timeout=3000)
    except Exception:
        pass
    try:
        el.click(timeout=2000)
    except Exception:
        el.focus()          # the real input sits under a display overlay -> focus it directly
    page.keyboard.press('Meta+A'); page.keyboard.press('Backspace')
    page.keyboard.type(str(text), delay=60)
    page.wait_for_timeout(200)
    return True


def pick_today(page, scope_sel, when):
    """Open the Workday date widget's calendar icon and click today's date. True if a day was clicked."""
    icon = None
    for sel in (f'{scope_sel} [data-automation-id="dateIcon"]', f'{scope_sel} button[aria-label*="calendar" i]',
                '[data-automation-id="dateIcon"]', 'button[aria-label*="calendar" i]'):
        loc = page.locator(sel)
        if loc.count() and loc.first.is_visible(): icon = loc.first; break
    if icon is None: return False
    try:
        icon.scroll_into_view_if_needed(timeout=3000); icon.click(timeout=4000); page.wait_for_timeout(900)
    except Exception:
        return False
    long_label = f'{when:%B} {when.day}, {when:%Y}'          # e.g. "October 13, 2026"
    for sel in ('[data-automation-id="datePickerSelectedToday"]', '[data-automation-id="datePickerToday"]',
                f'[role="gridcell"][aria-label*="{long_label}"]', f'button[aria-label*="{long_label}"]',
                '[aria-current="date"]', '[data-automation-id="datePickerDay"][aria-selected="true"]'):
        day = page.locator(sel)
        try:
            if day.count() and day.first.is_visible():
                day.first.click(timeout=3000); page.wait_for_timeout(700); return True
        except Exception:
            continue
    page.keyboard.press('Escape')
    return False


def typed_date(page, scope_sel, when):
    """Type a full date into a Workday date widget as keystrokes (MMDDYYYY into the month section)."""
    for sel in (f'{scope_sel} [data-automation-id="dateSectionMonth-display"]', f'{scope_sel}[data-automation-id="dateSectionMonth-input"]',
                '[data-automation-id="dateSectionMonth-display"]', '[data-automation-id="dateSectionMonth-input"]'):
        loc = page.locator(sel)
        if not loc.count(): continue
        try:
            loc.first.scroll_into_view_if_needed(timeout=3000); loc.first.click(timeout=3000)
        except Exception:
            try: loc.first.focus()
            except Exception: continue
        page.keyboard.type(f'{when:%m%d%Y}', delay=80); page.keyboard.press('Tab'); page.wait_for_timeout(600)
        return True
    return False


def _date(page, prefix, mm_yyyy):
    if not mm_yyyy: return
    m, y = mm_yyyy
    msel = f'[id="{prefix}-dateSectionMonth-input"], [id^="{prefix}"][data-automation-id="dateSectionMonth-input"]'
    ysel = f'[id="{prefix}-dateSectionYear-input"], [id^="{prefix}"][data-automation-id="dateSectionYear-input"]'
    _type_spin(page, msel, f'{m:02d}')
    _type_spin(page, ysel, y)
    page.keyboard.press('Tab')
    # verify; retry once by typing MMYYYY from the month box (Workday auto-advances to the year)
    try:
        mv = page.locator(msel).first.input_value(); yv = page.locator(ysel).first.input_value()
        if int(mv or 0) != m or str(yv) != str(y):
            _type_spin(page, msel, f'{m:02d}{y}'); page.keyboard.press('Tab')
    except Exception:
        pass


def _entries(page, kind):
    """ids like workExperience-11 / education-4 currently on the page."""
    ids = page.evaluate(f"""() => [...new Set([...document.querySelectorAll('[id^="{kind}-"]')]
        .map(e => (e.id.match(/^({kind}-\\d+)--/) || [])[1]).filter(Boolean))]""")
    return ids


def _delete_entry(page, eid):
    """Delete a parsed entry that is not part of the real resume (removing an invented entry is allowed)."""
    try:
        panel = page.locator(f'[id^="{eid}--"]').first.locator('xpath=ancestor::div[@role="group" or @data-automation-id][1]')
        btn = panel.get_by_role('button', name=re.compile(r'^delete', re.I))
        if not btn.count():
            btn = page.locator(f'[id^="{eid}--"]').first.locator('xpath=ancestor::*[.//button[starts-with(normalize-space(.),"Delete")]][1]').get_by_role('button', name=re.compile('^delete', re.I))
        if btn.count(): btn.first.click(); page.wait_for_timeout(800); return True
    except Exception:
        pass
    return False


def _add_entry(page, section_rx):
    """Click the 'Add' / 'Add Another' button of a section (Work Experience / Education / Languages).
    Oct 13 eval (Unity): current Workday names the button "Add Work Experience" (aria-label) while it shows "Add",
    so the old exact '^add$' name never matched -> every work/education entry failed on every tenant."""
    name_rx = re.compile(r'^add( another)?( ' + section_rx + r')?\s*$', re.I)
    try:   # 1) Workday's standard add-button, labelled with the section name
        for b in page.locator('button[data-automation-id="add-button"], button[data-automation-id="addButton"]').all():
            lab = (b.get_attribute('aria-label') or '') + ' ' + (b.inner_text() or '')
            if re.search(section_rx, lab, re.I) and b.is_visible():
                auth_click(b); page.wait_for_timeout(1500); return True
    except Exception:
        pass
    try:   # 2) the section that owns an Add button (older layouts / unlabelled buttons)
        sec = page.locator('[role="group"], section, fieldset, div[data-automation-id]').filter(has_text=re.compile(section_rx, re.I))
        for i in reversed(range(sec.count())):  # innermost section that actually owns an Add button
            btn = sec.nth(i).get_by_role('button', name=name_rx)
            if not btn.count(): btn = sec.nth(i).locator('button[data-automation-id="add-button"]')
            if btn.count() and btn.last.is_visible():
                auth_click(btn.last); page.wait_for_timeout(1500); return True
    except Exception:
        pass
    return False


def auth_click(loc):
    import auth
    auth._wd_click(loc)


def has_section(page, rx):
    """Does this tenant's My Experience step contain the section at all? (Unity has only Resume/Websites/Social URLs.)"""
    try:
        heads = page.evaluate("() => [...document.querySelectorAll('h2,h3,h4,legend,[role=heading],[role=group]')]"
                              ".filter(e => e.offsetWidth).map(e => (e.getAttribute('aria-labelledby') || '') + ' ' + (e.innerText || '').split('\\n')[0])")
        return any(re.search(rx, h, re.I) for h in heads)
    except Exception:
        return True


def fill_work(page):
    done, unknown = [], []
    ids = _entries(page, 'workExperience')
    if not ids and not has_section(page, r'work experience|employment history|work history'):
        return done, unknown            # Oct 13 eval: this tenant doesn't ask for work history
    used = set()
    for eid in ids:
        co = page.locator(f'[id="{eid}--companyName"]')
        w = work_for(co.first.input_value() if co.count() else '')
        title = page.locator(f'[id="{eid}--jobTitle"]')
        if not w and title.count(): w = next((x for x in WORK if norm(x['title']) == norm(title.first.input_value())), None)
        if not w or w['company'] in used:
            if _delete_entry(page, eid): done.append('removed parsed job not on resume')
            else: unknown.append(f'{eid}: parsed job entry not on your resume — delete it')
            continue
        used.add(w['company'])
        _fill_job(page, eid, w); done.append(f"Work: {w['company']}")
    for w in WORK:
        if w['company'] in used: continue
        before = set(_entries(page, 'workExperience'))
        if not _add_entry(page, r'work experience'):
            unknown.append(f"add work entry {w['company']}"); continue
        new = [e for e in _entries(page, 'workExperience') if e not in before]
        if not new: unknown.append(f"add work entry {w['company']}"); continue
        _fill_job(page, new[0], w); used.add(w['company']); done.append(f"Work: {w['company']}")
    return done, unknown


def role_description(company):
    """Clean role description from the pinned resume bullets in Notion (via data/resume_facts.json)."""
    try:
        import gen_v6
        pins = gen_v6.pins(gen_v6._facts(), company)
        return '\n'.join('• ' + b['text'] for b in pins[:3])
    except Exception:
        return ''


def _fill_job(page, eid, w):
    _set(page, f'[id="{eid}--jobTitle"]', w['title'])
    _set(page, f'[id="{eid}--companyName"]', w['company'])
    _set(page, f'[id="{eid}--location"]', w['location'])
    # Oct 13 (qa_db): Workday's resume parse put mixed text (incl. 'PROJECTS / <project> | github…') into Role Description
    # and it was never replaced -> always overwrite with this job's real resume bullets
    desc = role_description(w['company'])
    rd = page.locator(f'[id="{eid}--roleDescription"], textarea[id^="{eid}--roleDescription"]')
    if desc and rd.count():
        try: rd.first.fill(desc); rd.first.press('Tab')
        except Exception: pass
    cur = page.locator(f'[id="{eid}--currentlyWorkHere"]')
    if cur.count():
        want = w['end'] is None
        for how in ('label', 'click', 'js'):            # custom checkbox: make sure the state really changes
            if cur.first.is_checked() == want: break
            try:
                if how == 'label':
                    lab = page.locator(f'label[for="{eid}--currentlyWorkHere"]')
                    if lab.count(): lab.first.click(timeout=3000)
                elif how == 'click': cur.first.click(force=True, timeout=3000)
                else: cur.first.evaluate('e => { e.click(); e.dispatchEvent(new Event("change", {bubbles: true})); }')
            except Exception:
                pass
            page.wait_for_timeout(400)
    _date(page, f'{eid}--startDate', w['start'])
    if w['end']: _date(page, f'{eid}--endDate', w['end'])


def fill_education(page):
    done, unknown = [], []
    ids = _entries(page, 'education')
    if not ids and not has_section(page, r'^\s*education|education-section|educational background'):
        return done, unknown            # this tenant doesn't ask for education
    keep = None
    for eid in ids:
        txt = ' '.join(page.locator(f'[id^="{eid}--"]').all_inner_texts() + [i for i in page.locator(f'input[id^="{eid}--"]').evaluate_all('es => es.map(e => e.value)')])
        if keep is None and (re.search(r'north american', txt, re.I) or not re.sub(r'\W', '', txt)):
            keep = eid; continue
        if keep is None and len(ids) == 1:
            keep = eid; continue
        if _delete_entry(page, eid): done.append('removed parsed education not on resume')
        else: unknown.append(f'{eid}: extra education entry — check it')
    if keep is None:
        if _add_entry(page, r'education'):
            new = _entries(page, 'education'); keep = new[-1] if new else None
    if keep is None:
        return done, unknown + [f"Education: add {SCHOOL['name']}"]
    box = lambda sfx: page.locator(f'[id^="{keep}--{sfx}"]').first.locator('xpath=ancestor::*[starts-with(@data-automation-id,"formField-")][1]')
    # school (multiselect search). Not listed -> "Other" if offered.
    try:
        sch = page.locator(f'[id^="{keep}--school"]')
        plain = page.locator(f'input[id="{keep}--schoolName"]:not([data-automation-id="searchBox"])')
        if plain.count() and plain.first.get_attribute('role') not in ('combobox', 'searchbox'):
            # some tenants use a plain text box: type the real school name (never "Other")
            plain.first.fill(SCHOOL['name']); plain.first.press('Tab'); done.append('School')
        elif sch.count():
            b = box('school')
            if not re.search(SCHOOL['rx'] + r'|other', b.inner_text(), re.I):
                # Oct 13 (Symbotic, General Motors, Sentry): the tenant's list lacks the school -> shorter search, then any
                # "Other"-style entry ("Other", "Other - Not Listed", "Other Institution", "School Not Listed")
                if not pick_multiselect(page, b, SCHOOL['name'], SCHOOL['rx']) and \
                   not pick_multiselect(page, b, SCHOOL.get('short', SCHOOL['name']), SCHOOL['rx']) and \
                   not pick_multiselect(page, b, 'Other', r'^other\b|not listed|other (school|university|college|institution)') and \
                   not pick_multiselect(page, b, 'Not Listed', r'not listed|^other\b'):
                    unknown.append(f"Education: school ({SCHOOL['name']} not in list)")
            done.append('School')
    except Exception as e:
        unknown.append(f'Education: school ({str(e)[:50]})')
    try:
        if page.locator(f'[id^="{keep}--degree"]').count():
            if pick_dropdown(page, box('degree'), "Bachelor's Degree", SCHOOL['degree_rx']): done.append('Degree')
            else: unknown.append('Education: degree')
    except Exception:
        unknown.append('Education: degree')
    try:
        if page.locator(f'[id^="{keep}--fieldOfStudy"]').count():
            b = box('fieldOfStudy')
            if not re.search(r'computer science', b.inner_text(), re.I):
                if pick_multiselect(page, b, SCHOOL['field'], r'^computer science$|computer science'): done.append('Field of study')
    except Exception:
        pass
    _set(page, f'[id="{keep}--gradeAverage"]', SCHOOL['gpa'])
    _set(page, f'[id^="{keep}--firstYearAttended"][data-automation-id="dateSectionYear-input"], [id="{keep}--firstYearAttended-dateSectionYear-input"]', SCHOOL['first_year'])
    _set(page, f'[id^="{keep}--lastYearAttended"][data-automation-id="dateSectionYear-input"], [id="{keep}--lastYearAttended-dateSectionYear-input"]', SCHOOL['last_year'])
    return done, unknown


def prune_skills(page):
    """Remove auto-suggested skill pills he has no evidence for (keeps real ones)."""
    try:
        from score import EVIDENCE
        from gen_v5 import CANON
    except Exception:
        return []
    removed = []
    for _ in range(30):
        pills = page.locator('[data-automation-id="selectedItem"]').filter(has_text=re.compile(r'\(suggested\)', re.I))
        target = None
        for i in range(pills.count()):
            name = re.sub(r'\(suggested\)', '', pills.nth(i).inner_text(), flags=re.I).strip()
            canon = CANON.get(name.lower()) or name
            if canon not in EVIDENCE and name not in EVIDENCE:
                target = (pills.nth(i), name); break
        if not target: break
        x = target[0].locator('[data-automation-id="DELETE_charm"], [aria-label*="delete" i], [aria-label*="remove" i]')
        if not x.count(): break
        x.first.click(); page.wait_for_timeout(400); removed.append(target[1])
    return [f'removed suggested skill {r}' for r in removed]


# ---------------------------------------------------------------- self identify
def self_identify(page):
    """Pick the disability answer from the Answer Bank; the name/date signature is his to do."""
    done, unknown = [], []
    # Oct 13 audit: the old code ticked the FIRST box inside the block that contains the "No" text, which can be
    # "Yes, I have a disability". Now: match each box by ITS OWN label, tick only "No", untick the others.
    try:
        boxes = page.evaluate(r'''() => [...document.querySelectorAll('input[type=checkbox], input[type=radio]')].filter(e => e.offsetWidth || e.offsetHeight || e.getClientRects().length).map((e, i) => {
            const l = (e.id && document.querySelector(`label[for="${CSS.escape(e.id)}"]`)) || e.closest('label');
            e.setAttribute('data-jhdis', String(i));
            return {i, label: (l ? l.innerText : (e.getAttribute('aria-label') || '')).replace(/\s+/g, ' ').trim(), checked: e.checked}; })''')
        dis = [b for b in boxes if re.search(r'disabilit', b['label'], re.I)]
        want = next((b for b in dis if re.match(r"\s*no,? i do(n.t| not) have a disability", b['label'], re.I)), None)
        if want:
            for b in dis:
                loc = page.locator(f'[data-jhdis="{b["i"]}"]').first
                if b is want and not loc.is_checked(): loc.check(force=True)
                elif b is not want and loc.is_checked(): loc.uncheck(force=True)
            done.append('Disability: No')
    except Exception:
        pass
    # Oct 13 audit: this used to be added ALWAYS, so every tenant with a Self Identify page stopped one step before
    # Review and the tab was closed. Only stop when an empty Name / Date signature field is actually on the page.
    sig = page.locator('[data-automation-id="name"] input, input[data-automation-id="name"], input[id*="selfIdentifiedDisabilityData--name"], '
                       '[data-automation-id="dateSectionMonth-input"], input[id*="selfIdentifiedDisabilityData--dateSignedOn"]')
    empty = False
    for i in range(sig.count()):
        try:
            if sig.nth(i).is_visible() and not (sig.nth(i).input_value() or '').strip(): empty = True
        except Exception:
            continue
    if empty and __import__('os').environ.get('AUTO_SELF_ID') == '1':
        # authorized by the owner Oct 13 2026 (AUTO_SELF_ID=1): sign the voluntary disability self-ID with the profile name + today's date
        import datetime as _dt
        from zoneinfo import ZoneInfo
        today = _dt.datetime.now(ZoneInfo('America/Chicago'))
        try:
            nm = page.locator('input[id*="selfIdentifiedDisabilityData--name"], [data-automation-id="name"] input, input[data-automation-id="name"]')
            if nm.count() and not (nm.first.input_value() or '').strip(): nm.first.fill(P.get('identity', 'workday_self_id_name') or P.NAME); nm.first.press('Tab')
            # Workday date sections only take real keystrokes (fill() shows the digits but the form stays empty ->
            # "Enter today's date", Cadence Oct 13): click the month section and type MMDDYYYY; Workday auto-advances
            # owner's tip (Oct 13): the calendar icon next to the date picks today's date reliably; typing is the fallback
            if not pick_today(page, '[id*="dateSignedOn"]', today):
                typed_date(page, '[id*="dateSignedOn"]', today)
            single = page.locator('input[id*="dateSignedOn"]:not([data-automation-id^="dateSection"])')
            if single.count() and not (single.first.input_value() or '').strip(): single.first.fill(f'{today:%m/%d/%Y}')
            page.wait_for_timeout(800)
            still = [i for i in range(sig.count()) if sig.nth(i).is_visible() and not (sig.nth(i).input_value() or '').strip()]
            if still: unknown.append('Self-Identify: signature fields did not take the name/date — check them')
            else: done.append('Self-Identify: signed (name + today)')
        except Exception as e:
            unknown.append(f'Self-Identify: signing failed ({str(e)[:60]})')
    elif empty:
        unknown.append('Self-Identify: name + date signature (legal/consent — yours; set AUTO_SELF_ID=1 to sign)')
    return done, unknown


# ---------------------------------------------------------------- main
def run(ctx, page, job):
    t = tenant(page.url)
    if captcha(page): return result('NEEDS YOU', 'CAPTCHA (tab open)', page=page)
    page, why = start(ctx, page, job)
    if why: return result('NEEDS YOU', f'Workday: {why} (tab open)', page=page)
    if wall(page) or (not in_application(page) and not signed_in(page)):
        page.wait_for_timeout(2000)
        if wall(page) or not in_application(page):
            return result('NEEDS YOU', f'Workday: sign in or create account for {t} (tab open)', page=page)
    done, unknown = [], []
    seen = []
    import wd_claude
    use_claude = wd_claude.active()
    if use_claude: print(f'workday: CLAUDE mode (application {wd_claude.count_one(job)}/{wd_claude.LIMIT})')
    for _ in range(MAX_STEPS):
        page.wait_for_timeout(1500)
        # Workday renders the step title first and the form + footer buttons seconds later: wait for them
        try:
            page.wait_for_selector(f'{NEXT_BTN}, [data-automation-id="signInSubmitButton"], '
                                   f'[data-automation-id="createAccountSubmitButton"]', state='visible', timeout=25000)
            page.wait_for_load_state('networkidle', timeout=10000)
        except Exception:
            pass
        page.wait_for_timeout(1000)
        for _r in range(2):   # Workday glitch page: "Something went wrong. Please refresh the page and then try again."
            if not re.search(r'something went wrong.{0,80}refresh', body_text(page, 4000), re.I | re.S): break
            page.reload(wait_until='domcontentloaded'); page.wait_for_timeout(5000)
            try: page.wait_for_selector(NEXT_BTN, state='visible', timeout=20000)
            except Exception: pass
        if captcha(page): return result('NEEDS YOU', 'CAPTCHA (tab open)', done, unknown, page)
        if wall(page) or re.search(r'sign in|create account', step_name(page), re.I):
            return result('NEEDS YOU', f'Workday: sign in or create account for {t} (tab open)', done, unknown, page)
        step = step_name(page)
        seen.append(step)
        if re.search(r'\breview\b', step, re.I) or _final_only(page):
            record(page, job, t, step or 'Review', done, unknown)
            m = WRONG_SELF_ID.search(body_text(page, 60000))   # last check before auth.submit (Oct 13 audit)
            why = m.group(0) if m else ('disability box "Yes" is ticked' if wrong_disability_checked(page) else '')
            if why:
                unknown.append(f'Review shows a self-ID answer that contradicts the Answer Bank: "{why[:80]}" — NOT submitting')
            return outcome(done, unknown, page, f'Workday {t}: stopped on Review — click Submit')
        step_unknown = []
        if re.search(r'autofill|resume', step, re.I) and page.locator('input[type="file"]').count():
            done += autofill_step(page, job)
        elif use_claude and wd_claude.norm_step(step) in CLAUDE_STEPS:
            # Oct 13 (owner): Claude fills everything after My Information; Playwright only uploads files
            d, u = wd_claude.fill_step(page, job, step, t); done += d; step_unknown += u
            if re.search(r'experience', step, re.I):
                fi = page.locator('input[type="file"]')
                if fi.count() and job.get('pdf') and not re.search(r'successfully uploaded', body_text(page, 20000), re.I):
                    try: fi.first.set_input_files(job['pdf']); done.append('Resume'); page.wait_for_timeout(3000)
                    except Exception: pass
        else:
            d, u = fill_step(page, job); done += d; step_unknown += u
            page.wait_for_timeout(1200)          # 2nd pass: fields that appear after earlier answers (e.g. SMS number)
            d2, u2 = fill_step(page, job); done += [x for x in d2 if x not in done]
            step_unknown = [x for x in step_unknown if x not in d2] + [x for x in u2 if x not in step_unknown and x not in done]
            if re.search(r'experience', step, re.I):
                d, u = fill_work(page); done += d; step_unknown += u
                d, u = fill_education(page); done += d; step_unknown += u
                # generic-pass flags for entry fields that fill_work / fill_education have now filled
                filled = ' '.join(done).lower()
                step_unknown = [x for x in step_unknown if not (
                    re.search(r'school|university', x, re.I) and 'school' in filled or
                    re.search(r'degree', x, re.I) and 'degree' in filled or
                    re.search(r'field of study', x, re.I) and 'field' in filled or
                    re.search(r'job title|company|^from|^to\b|location', x, re.I) and 'work:' in filled)]
                done += prune_skills(page)
                fi = page.locator('input[type="file"]')
                if fi.count() and job.get('pdf') and not re.search(r'successfully uploaded', body_text(page, 20000), re.I):
                    try: fi.first.set_input_files(job['pdf']); done.append('Resume'); page.wait_for_timeout(3000)
                    except Exception: pass
            if re.search(r'self.?identif|disabilit', step, re.I):
                d, u = self_identify(page); done += d; step_unknown += u
                if any('Self-Identify: signed' in x for x in d):   # the generic scan flagged Name/Date before signing
                    step_unknown = [x for x in step_unknown if not re.search(r'^(name|date|today.?s date|signature)\b', x, re.I)]
        record(page, job, t, step, done, step_unknown)
        if step_unknown and not (use_claude and wd_claude.norm_step(step) in CLAUDE_STEPS):
            step_unknown = _rescue(page, job, step_unknown, done)   # Oct 13: Claude repairs the page instead of closing it
        if step_unknown:
            unknown += [f'[{step or "step"}] {u}' for u in step_unknown]
            return outcome(done, unknown, page, f'(Workday {t}, stopped on "{step}")')
        if not _next(page):
            unknown.append(f'[{step}] could not find Save and Continue')
            return outcome(done, unknown, page)
        page.wait_for_timeout(2500)
        if not _recover_glitch(page, job):   # Oct 13 (Caris): "Something went wrong" right after Save and Continue
            unknown.append(f'[{step}] Workday kept showing "Something went wrong" after 3 reloads')
            return outcome(done, unknown, page)
        errs = errors(page)
        if errs: record(page, job, t, step, [], [], errs, after_next=True)
        if errs and step_name(page) == step:
            if use_claude and wd_claude.norm_step(step) in CLAUDE_STEPS:
                d, u = wd_claude.fill_step(page, job, step, t, errors=errs); done += d
                fixed = not u
            else:
                fixed = not _rescue(page, job, errs, done)
            if fixed and _next(page):   # repaired -> try Save and Continue again
                page.wait_for_timeout(2500)
                if not errors(page) or step_name(page) != step:
                    continue
            errs = errors(page) or errs
            unknown += [f'[{step}] {e}' for e in errs]
            return outcome(done, unknown, page, f'(Workday {t}, validation errors on "{step}")')
        if len(seen) >= 3 and seen[-1] == seen[-2] == seen[-3]:
            unknown.append(f'[{step}] stuck on this step')
            return outcome(done, unknown, page)
    unknown.append('too many steps')
    return outcome(done, unknown, page)


# selected-answer text that contradicts the Answer Bank (Review shows only the chosen value for these dropdowns).
# Disability is NOT checked by text: Review prints the whole CC-305 form incl. every option -> checked boxes only.
WRONG_SELF_ID = re.compile(r"identify as a veteran|identify as one or more of the classifications of protected veteran|"
                           r"\bi am a protected veteran|i have other military service|"
                           r"asian\s*[-–]\s*(?:chinese|indian|japanese|korean|filipino|vietnamese)", re.I)


def wrong_disability_checked(page):
    """True if a CHECKED box/radio on the page is labelled 'Yes, I have a disability' (or 'have had one')."""
    try:
        return page.evaluate(r'''() => [...document.querySelectorAll('input[type=checkbox]:checked, input[type=radio]:checked, [role=checkbox][aria-checked=true]')]
          .some(e => { const l = (e.id && document.querySelector(`label[for="${CSS.escape(e.id)}"]`)) || e.closest('label');
                       return /yes,? i have a disability/i.test(l ? l.innerText : (e.getAttribute('aria-label') || '')); })''')
    except Exception:
        return False
GLITCH_RX = re.compile(r'something went wrong.{0,120}(refresh|try again)|an error occurred.{0,60}try again', re.I | re.S)


def _recover_glitch(page, job):
    """Workday's 'Something went wrong. Please refresh the page and then try again.' Reload up to 3 times with growing
    waits (the draft is saved server-side); last resort: reopen the job link, which offers 'Continue Application'.
    Returns True when the page is usable again."""
    for i in range(3):
        if not GLITCH_RX.search(body_text(page, 5000)): return True
        print(f'workday: "Something went wrong" -> reload {i + 1}')
        try:
            page.reload(wait_until='domcontentloaded'); page.wait_for_timeout(5000 * (i + 1))
            page.wait_for_selector(NEXT_BTN, state='visible', timeout=20000)
        except Exception:
            pass
    if not GLITCH_RX.search(body_text(page, 5000)): return True
    try:
        page.goto(job.get('url'), wait_until='domcontentloaded', timeout=60000); page.wait_for_timeout(4000)
        btn = page.locator('[data-automation-id="continueButton"], button:has-text("Continue Application")')
        if btn.count(): btn.first.click(); page.wait_for_timeout(5000)
    except Exception:
        pass
    return not GLITCH_RX.search(body_text(page, 5000))


def _rescue(page, job, problems, done):
    """Ask Claude to fix the step (rescue.fix). Returns the problems still left (empty list = step is clean)."""
    try:
        import rescue
        ok, log = rescue.fix(page, job, problems)
        print('workday rescue:', ok, '|', '; '.join(log)[:400])
        if not ok: return problems
        page.wait_for_timeout(800)
        left = [f.get('label') or f.get('aid') or '?' for f in page.evaluate(FIELDS_JS)
                if f.get('required') and not f.get('value') and f.get('kind') not in ('other', 'checkbox')
                and not re.match(r'(workexperience|education|language)-\d+', (f.get('inputId') or f.get('aid') or '').lower())]
        if not left: done.append('rescued by Claude: ' + '; '.join(x for x in log if not x.startswith('refused'))[:200])
        return left
    except Exception as e:
        print('workday rescue failed:', str(e)[:120])
        return problems


def record(page, job, t, step, done, unknown, errs=None, after_next=False):
    """Workday corpus (Oct 13 plan): every page's fields -> data/workday_corpus/<tenant>.jsonl, so the filler can be
    rebuilt from what 68 tenants really ask. 0 tokens; never raises."""
    try:
        import json, datetime as dt, config
        fields = page.evaluate(FIELDS_JS)
        out = config.DATA / 'workday_corpus'; out.mkdir(exist_ok=True)
        with open(out / f'{t}.jsonl', 'a') as fh:
            fh.write(json.dumps({'at': dt.datetime.now().isoformat(timespec='seconds'), 'tenant': t, 'company': job.get('company'),
                                 'title': job.get('title'), 'url': page.url, 'step': step, 'after_next': after_next,
                                 'fields': [dict({k: f.get(k) for k in ('aid', 'inputId', 'label', 'kind', 'required', 'value', 'options')},
                                                 options=f.get('options') or OPTS_SEEN.get(re.sub(r'\s+', ' ', f.get('label') or '').strip().rstrip('*').strip()) or [])
                                            for f in fields],
                                 'done': done, 'unknown': unknown, 'errors': errs or []}, ensure_ascii=False) + '\n')
    except Exception as e:
        print('workday record failed:', str(e)[:100])


def _final_only(page):
    """True when the only forward button is the final Submit (we never click it)."""
    btn = page.locator(NEXT_BTN)
    try:
        return btn.count() > 0 and bool(FINAL_SUBMIT.match(btn.first.inner_text() or ''))
    except Exception:
        return False


def _next(page):
    btn = page.locator(NEXT_BTN)
    try:  # an open dropdown/popup can hide the footer: close it, scroll, wait for the button
        page.keyboard.press('Escape'); page.wait_for_timeout(300)
        if btn.count():
            btn.first.scroll_into_view_if_needed(timeout=3000)
            btn.first.wait_for(state='visible', timeout=6000)
    except Exception:
        pass
    try:
        if btn.count() and btn.first.is_visible():
            txt = (btn.first.inner_text() or '') + ' ' + (btn.first.get_attribute('aria-label') or '')
            # HARD STOP: step navigation never clicks anything that submits; only auth.submit (live, all checks) does
            if FINAL_SUBMIT.match(btn.first.inner_text() or '') or re.search(r'submit|send application|finish', txt, re.I):
                return False
            import auth
            auth._wd_click(btn); return True
    except Exception:
        pass
    return safe_next(page, r'^\s*(next|continue|save (and|&) continue)\s*$')
