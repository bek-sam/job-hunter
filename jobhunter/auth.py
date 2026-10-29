"""Sign-in, account creation, email verification and final submit for the apply browser.
Authorized by the owner (Oct 11 2026). Every action is behind its own switch in .env, all OFF by default:

  AUTO_LOGIN=1           sign in on ATS sign-in walls
  AUTO_CREATE_ACCOUNT=1  create an account when the site requires one
  AUTO_VERIFY=1          read the site's verification code / link from Gmail (read-only) and use it
  AUTO_CONSENT=1         tick terms/consent boxes on account forms and application forms
  AUTO_SUBMIT=1          click the final Submit when every required field is filled, then confirm

The password is read from the macOS Keychain at run time (service "jobhunter", account = LOGIN_EMAIL) and is never
printed, logged or written to disk. CAPTCHAs are never solved: they always end as NEEDS YOU.
"""
import applicant as P
import json, os, re, subprocess, time
import config

ON = lambda k: os.environ.get(k) == '1'
EMAIL = os.environ.get('LOGIN_EMAIL', P.EMAIL)
SERVICE = 'jobhunter'


# ------------------------------------------------------------------ credentials
def password(host=None):
    for acct in ([host] if host else []) + [EMAIL]:
        r = subprocess.run(['security', 'find-generic-password', '-s', SERVICE, '-a', acct, '-w'], capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.rstrip('\n')
    return None


def _vis(loc):
    try:
        return loc.count() > 0 and loc.first.is_visible()
    except Exception:
        return False


def _body(page, n=8000):
    try:
        return page.inner_text('body')[:n]
    except Exception:
        return ''


def _captcha(page):
    """Open challenge (with CAPTCHA_WAIT > 0 the run first waits for the owner to solve it -- ats.common.captcha)."""
    from ats.common import wait_solved
    return _captcha_now(page) and not wait_solved(page, _captcha_now)


def _captcha_now(page):
    try:
        ch = page.locator('iframe[src*="recaptcha/api2/bframe"], iframe[title*="challenge" i], iframe[src*="hcaptcha"]')
        if ch.count() and ch.first.is_visible(): return True
    except Exception:
        pass
    return bool(re.search(r'verify you are human|are you a robot|press and hold', _body(page, 4000), re.I))


_CONSENT_BOX = re.compile(r'agree|acknowledg|consent|certif|attest|terms|privacy|accurate|true and (correct|complete)|'
                          r'i (have read|understand|confirm)|authori[sz]e', re.I)
_BOX_TEXT_JS = """e => { const l = (e.id && document.querySelector(`label[for="${CSS.escape(e.id)}"]`)) || e.closest('label');
    let t = ((l && l.innerText) || e.getAttribute('aria-label') || '').trim();
    // text can sit next to the box, outside its label (TikTok "I have read and agree ..."): climb a few levels
    for (let p = e.parentElement, i = 0; p && i < 4 && t.length < 12; i++, p = p.parentElement) t = (p.innerText || '').trim();
    return t.slice(0, 400); }"""


def _tick_boxes(scope):
    """Tick only consent/certification boxes (AUTO_CONSENT). Oct 12: ticking EVERY box on LinkedIn hit page controls
    outside the Easy Apply dialog and the final Submit never happened; 'follow company'/marketing boxes stay as they are."""
    boxes = scope.locator('input[type="checkbox"]')
    for i in range(boxes.count()):
        b = boxes.nth(i)
        try:
            if b.is_visible() and not b.is_checked() and _CONSENT_BOX.search(b.evaluate(_BOX_TEXT_JS) or ''): b.check(force=True)
        except Exception:
            try: b.click(force=True)
            except Exception: pass
    for b in scope.locator('[role="checkbox"][aria-checked="false"]').all():
        try:
            if b.is_visible() and _CONSENT_BOX.search(b.evaluate(_BOX_TEXT_JS) or ''): b.click()
        except Exception:
            pass


def _click(page, rx, fallback_css=None):
    btn = page.get_by_role('button', name=re.compile(rx, re.I))
    if not _vis(btn) and fallback_css: btn = page.locator(fallback_css)
    if _vis(btn):
        btn.first.click(timeout=8000); return True
    return False


# ------------------------------------------------------------------ sign in / sign up
def sign_in(page, host=None):
    """Returns 'ok' | 'disabled' | 'no-password' | 'no-form' | 'captcha' | 'needs-verify' | 'failed: …'."""
    if not ON('AUTO_LOGIN'): return 'disabled'
    if _captcha(page): return 'captcha'
    pw = password(host)
    if not pw: return 'no-password'
    # some sites show a "Sign In" link/tab first (Workday: "Sign In" button on the Create Account panel)
    pf = page.locator('input[type="password"]')
    if not _vis(pf):
        _click(page, r'^\s*sign in( with email)?\s*$', '[data-automation-id="signInLink"], button[data-automation-id="signInLink"]')
        page.wait_for_timeout(1500); pf = page.locator('input[type="password"]')
    em = page.locator('input[type="email"], input[autocomplete="username"], input[name*="email" i], input[id*="email" i], '
                      'input[data-automation-id="email"], input[name*="user" i]')
    if _vis(em) and not _vis(pf):  # two-step login: email first
        em.first.fill(EMAIL)
        if not _click(page, r'^\s*(next|continue|sign in|log ?in|submit|send (code|link))\s*$', 'button[type="submit"]'):
            em.first.press('Enter')
        page.wait_for_timeout(4000)
        pf = page.locator('input[type="password"]')
        code = page.locator('input[autocomplete="one-time-code"], input[name*="code" i], input[id*="code" i], input[inputmode="numeric"]')
        if not _vis(pf) and (_vis(code) or re.search(r'verification code|enter the code|we (sent|emailed)|check your (email|inbox)|'
                                                      r'one[- ]time|magic link|sign-?in link', _body(page, 5000), re.I)):
            return 'needs-verify'          # passwordless login: code / link by email -> complete_verification
    if not _vis(pf): return 'no-form'
    if _vis(em): em.first.fill(EMAIL)
    pf.first.fill(pw)
    # "I have read and agree to the User Agreement" boxes under the form (TikTok Careers, Oct 12)
    if ON('AUTO_CONSENT'): _tick_boxes(page)
    if not _click(page, r'^\s*(sign in|log ?in|continue|next|submit)\s*$',
                  '[data-automation-id="signInSubmitButton"], button[type="submit"], input[type="submit"]'):
        pf.first.press('Enter')
    page.wait_for_timeout(5000)
    if _captcha(page): return 'captcha'
    t = _body(page)
    if re.search(r'verification code|enter the code|one[- ]time|check your email|verify your email', t, re.I): return 'needs-verify'
    if _vis(page.locator('input[type="password"]')) and re.search(r'incorrect|invalid|wrong|does not match|try again|not recognized', t, re.I):
        return 'failed: email or password not accepted (account may not exist)'
    # still on the same sign-in form = not signed in (a silent refusal must not count as success)
    if _vis(page.locator('input[type="password"]')) and re.search(r'/(login|sign-?in)\b', page.url, re.I):
        return 'failed: still on the sign-in page (no error shown)'
    return 'ok'


def create_account(page, host=None):
    """Returns 'ok' | 'disabled' | 'no-password' | 'no-form' | 'captcha' | 'needs-consent' | 'needs-verify' | 'exists' | 'failed: …'."""
    if not ON('AUTO_CREATE_ACCOUNT'): return 'disabled'
    if _captcha(page): return 'captcha'
    pw = password(host)
    if not pw: return 'no-password'
    pfs = page.locator('input[type="password"]')
    if pfs.count() < 2 or not pfs.first.is_visible():
        _click(page, r'create (an )?account|sign up|register', '[data-automation-id="createAccountLink"]')
        page.wait_for_timeout(1500); pfs = page.locator('input[type="password"]')
    if not _vis(pfs): return 'no-form'
    em = page.locator('input[type="email"], input[name*="email" i], input[id*="email" i], input[data-automation-id="email"]')
    if _vis(em): em.first.fill(EMAIL)
    for i in range(pfs.count()):
        if pfs.nth(i).is_visible(): pfs.nth(i).fill(pw)
    for nm, val in (('first', P.FIRST), ('last', P.LAST)):
        f = page.locator(f'input[name*="{nm}" i]:visible, input[id*="{nm}" i]:visible')
        if f.count():
            try:
                if not f.first.input_value(): f.first.fill(val)
            except Exception: pass
    if page.locator('input[type="checkbox"]:visible, [role="checkbox"]:visible').count():
        if not ON('AUTO_CONSENT'): return 'needs-consent'
        _tick_boxes(page)
    if not _click(page, r'^\s*(create account|sign up|register|create|continue)\s*$',
                  '[data-automation-id="createAccountSubmitButton"], button[type="submit"]'):
        return 'failed: no create-account button'
    page.wait_for_timeout(6000)
    if _captcha(page): return 'captcha'
    t = _body(page)
    if re.search(r'already (exists|in use|registered)|account with this email', t, re.I): return 'exists'
    if re.search(r'verify your (email|account)|verification (email|code|link)|check your (email|inbox)|activate your account|enter the code', t, re.I):
        return 'needs-verify'
    return 'ok'


# ------------------------------------------------------------------ email verification via Gmail (read-only)
_VERIFY_SCHEMA = {'type': 'object', 'required': ['found'], 'properties': {
    'found': {'type': 'boolean'}, 'code': {'type': ['string', 'null']}, 'link': {'type': ['string', 'null']},
    'from': {'type': ['string', 'null']}, 'received': {'type': ['string', 'null']},
    'text': {'type': ['string', 'null'], 'description': 'the newest matching message snippet/body text, verbatim (first 400 chars)'}}}
_GMAIL_TOOLS = ['mcp__claude_ai_Gmail__search_threads', 'mcp__claude_ai_Gmail__get_thread', 'mcp__claude_ai_Gmail__get_message']
_GMAIL_BLOCK = ['mcp__claude_ai_Gmail__send_message', 'mcp__claude_ai_Gmail__reply', 'mcp__claude_ai_Gmail__forward',
                'mcp__claude_ai_Gmail__create_draft', 'mcp__claude_ai_Gmail__update_draft', 'mcp__claude_ai_Gmail__trash_message',
                'mcp__claude_ai_Gmail__trash_thread', 'mcp__claude_ai_Gmail__delete_draft', 'Bash', 'Write', 'Edit']


def fetch_verification(site_hint, since_epoch, timeout_s=150):
    """Poll Gmail (via headless Claude, read-only tools) for the newest verification code/link from `site_hint`
    received after `since_epoch`. Returns dict(code=…, link=…) or None."""
    if not ON('AUTO_VERIFY'): return None
    after = time.strftime('%Y/%m/%d', time.localtime(since_epoch - 86400))
    prompt = (f"Search Gmail (query: newer_than:1d after:{after}) for the newest account verification / sign-in code / "
              f"email-confirmation message related to: {site_hint}. Only messages received after Unix time {int(since_epoch)} "
              f"count. Return found=false if none. If found, return the numeric/alphanumeric verification code if the email "
              f"has one, and/or the verification or activation link (full https URL), and put the message snippet/body text verbatim in `text`. Use the NEWEST message (threads hold several codes). Do not take any other action.")
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        time.sleep(25)
        try:
            out = _gmail_ask(prompt)
            if out.get('found') and not out.get('code') and out.get('text'):
                # the model sometimes finds the email but leaves `code` empty (Eightfold Oct 12) -> read it ourselves
                m = re.search(r'(?:code|passcode)[^0-9A-Za-z]{0,40}([A-Za-z0-9]{6,8})\b', out['text'], re.I) or \
                    re.search(r'\b(\d{6})\b', out['text'])
                if m: out['code'] = m.group(1)
            if out.get('found') and (out.get('code') or out.get('link')):
                return out
        except Exception as e:
            print('verification lookup failed:', str(e)[:200])
    return None


def _gmail_ask(prompt):
    """Read-only Gmail lookup through the Manager's tool-restricted claude -p wrapper (connectors load via ToolSearch;
    send/reply/forward/trash/drafts are denied)."""
    import manager
    return manager.agent(prompt, _GMAIL_TOOLS + ['ToolSearch'], dict(_VERIFY_SCHEMA, title='verify'), model='haiku',
                         budget_usd=0.08, timeout=240,
                         system='You look up one verification email, read-only. Load Gmail tools with ToolSearch '
                                '(select:<names>). Never send, reply, draft or delete. Finish with only the JSON object.')


def complete_verification(ctx, page, site_hint, since_epoch):
    """Enter the emailed code on the page, or open the emailed link. Returns 'ok' | 'disabled' | 'not-found' | 'failed: …'."""
    if not ON('AUTO_VERIFY'): return 'disabled'
    v = fetch_verification(site_hint, since_epoch)
    if not v: return 'not-found'
    if v.get('code'):
        box = page.locator('input[autocomplete="one-time-code"], input[name*="code" i], input[id*="code" i], '
                           'input[inputmode="numeric"], input[type="tel"]')
        if _vis(box):
            cells = box.all()
            if len(cells) >= len(v['code']) > 1 and all(c.get_attribute('maxlength') == '1' for c in cells[:len(v['code'])]):
                for c, ch in zip(cells, v['code']): c.fill(ch)
            else:
                box.first.fill(v['code'])
            _click(page, r'^\s*(verify|submit|continue|next|confirm)\s*$', 'button[type="submit"]')
            page.wait_for_timeout(5000)
            return 'ok'
    if v.get('link') and v['link'].startswith('https://'):
        vp = ctx.new_page(); vp.goto(v['link'], wait_until='domcontentloaded', timeout=60000); vp.wait_for_timeout(4000)
        vp.close()
        page.reload(wait_until='domcontentloaded'); page.wait_for_timeout(3000)
        return 'ok'
    return 'failed: email found but no usable code/link'


# ------------------------------------------------------------------ Workday (both forms on one page)
def _aid(page, a):
    return page.locator(f'[data-automation-id="{a}"]')


def _wd_wall_gone(page):
    page.wait_for_timeout(4000)
    return not (_vis(_aid(page, 'signInSubmitButton')) or _vis(_aid(page, 'createAccountSubmitButton'))
                or _vis(_aid(page, 'SignInWithEmailButton')))


def _wd_error(page):
    sel = ('[data-automation-id="errorMessage"], [data-automation-id*="rror"], [data-automation-id="inputAlert"], '
           '[data-automation-id*="Alert"], [data-automation-id="errorBanner"], [role="alert"]')
    errs = []
    for e in page.locator(sel).all():
        try:
            t = re.sub(r'\s+', ' ', e.inner_text()).strip()
            if e.is_visible() and t and t not in errs: errs.append(t[:160])
        except Exception:
            continue
    return '; '.join(errs)[:300]


def _wd_click(loc):
    """Workday puts a transparent click_filter div over its buttons: try a normal click, the overlay, then a DOM click."""
    el = loc.first
    try:
        el.click(timeout=4000); return
    except Exception:
        pass
    try:
        label = (el.get_attribute('aria-label') or el.inner_text() or '').strip()
        if label:
            ov = el.page.locator(f'[data-automation-id="click_filter"][aria-label="{label}"]')
            if ov.count(): ov.first.click(timeout=4000); return
    except Exception:
        pass
    try:
        el.dispatch_event('click'); return
    except Exception:
        pass
    el.evaluate('e => e.click()')


WRONG_PW = re.compile(r'wrong email address or password|account might be locked|incorrect (email|password)', re.I)
LOCKOUT_FILE = config.DATA / 'auth_failures.json'
OTHER_PW_MSG = ('Workday already has an account for this email with a different password (or it is locked). '
                'Use "Forgot your password?" on this site and set it to the jobhunter Keychain password, or sign in yourself')


def _failed_hosts():
    try: return json.loads(LOCKOUT_FILE.read_text())
    except Exception: return {}


def _mark_failed(host):
    """Remember a tenant where our password is refused, so later runs don't keep trying (and lock the account)."""
    d = _failed_hosts(); d[host] = time.time(); LOCKOUT_FILE.write_text(json.dumps(d))


def workday_wall(ctx, page, host, _depth=0):
    """Workday 'Create Account/Sign In' step. Sign in first; if that fails, create the account (+ email verify)."""
    t0 = time.time()
    if time.time() - _failed_hosts().get(host, 0) < 7 * 86400: return False, OTHER_PW_MSG + ' (not retried for 7 days)'
    if not ON('AUTO_LOGIN'): return False, 'sign in (AUTO_LOGIN off)'
    if _captcha(page): return False, 'CAPTCHA'
    pw = password(host)
    if not pw: return False, 'no Keychain password for jobhunter'
    # 1) sign in  (no form on screen yet -> open it from the header "Sign In" button)
    if not (_vis(_aid(page, 'signInSubmitButton')) or _vis(_aid(page, 'createAccountSubmitButton'))
            or _vis(_aid(page, 'SignInWithEmailButton'))) and _vis(_aid(page, 'utilityButtonSignIn')):
        _wd_click(_aid(page, 'utilityButtonSignIn')); page.wait_for_timeout(3000)
    if _vis(_aid(page, 'SignInWithEmailButton')): _wd_click(_aid(page, 'SignInWithEmailButton')); page.wait_for_timeout(2500)
    if not _vis(_aid(page, 'signInSubmitButton')) and _vis(_aid(page, 'signInLink')):
        _wd_click(_aid(page, 'signInLink')); page.wait_for_timeout(2500)
    form = page.locator('[data-automation-id="signInContent"]:has([data-automation-id="signInSubmitButton"]), '
                        'form:has([data-automation-id="signInSubmitButton"])')
    scope = form.first if form.count() else page
    if _vis(_aid(page, 'signInSubmitButton')):
        scope.locator('[data-automation-id="email"], input[type="email"]').first.fill(EMAIL)
        scope.locator('[data-automation-id="password"], input[type="password"]').first.fill(pw)
        _wd_click(_aid(page, 'signInSubmitButton'))
        if _wd_wall_gone(page): return True, 'signed in'
        if _captcha(page): return False, 'CAPTCHA'
        if re.search(r'verify your|verification|confirm your (email|account)', _wd_error(page) + _body(page, 4000), re.I):
            v = complete_verification(ctx, page, f'Workday {host}', t0)
            if v == 'ok' and _wd_wall_gone(page): return True, 'signed in after email verification'
    # 2) create the account
    if not ON('AUTO_CREATE_ACCOUNT'): return False, 'sign-in failed and AUTO_CREATE_ACCOUNT is off'
    if not _vis(_aid(page, 'createAccountSubmitButton')):
        link = _aid(page, 'createAccountLink')
        if _vis(link): _wd_click(link); page.wait_for_timeout(2500)
    if not _vis(_aid(page, 'createAccountSubmitButton')):
        return False, 'sign-in failed and no Create Account form (' + (_wd_error(page) or 'no error shown') + ')'
    form = page.locator('[data-automation-id="signInContent"]:has([data-automation-id="createAccountSubmitButton"]), '
                        'form:has([data-automation-id="createAccountSubmitButton"])')
    scope = form.first if form.count() else page
    scope.locator('[data-automation-id="email"], input[type="email"]').first.fill(EMAIL)
    scope.locator('[data-automation-id="password"]').first.fill(pw)
    vp = scope.locator('[data-automation-id="verifyPassword"]')
    if vp.count(): vp.first.fill(pw)
    box = scope.locator('[data-automation-id="createAccountCheckbox"], input[type="checkbox"]')
    if box.count() and box.first.is_visible():
        if not ON('AUTO_CONSENT'): return False, 'create account needs the terms box (AUTO_CONSENT off)'
        try: box.first.check(force=True)
        except Exception: box.first.click(force=True)
    _wd_click(_aid(page, 'createAccountSubmitButton'))
    for _ in range(15):    # Workday takes a few seconds to create the account and redirect
        page.wait_for_timeout(1000)
        if not _vis(_aid(page, 'createAccountSubmitButton')) or _wd_error(page): break
    if _captcha(page): return False, 'CAPTCHA on create account'
    t = _body(page, 6000); err = _wd_error(page)
    if re.search(r'already (exists|in use|registered)|account .* exists', err + t, re.I):
        return False, 'Workday account exists but sign-in failed — the saved password may differ for this tenant'
    if re.search(r'verify|verification|activate|confirm your email|check your email', t, re.I):
        v = complete_verification(ctx, page, f'Workday {host}', t0)
        if v != 'ok': return False, f'account created, email verification: {v}'
        page.reload(wait_until='domcontentloaded'); page.wait_for_timeout(3000)
        if _wd_wall_gone(page): return True, 'account created + verified'
        return workday_wall(ctx, page, host, _depth + 1) if _depth < 1 else (False, 'verified but sign-in still failing')
    if _wd_wall_gone(page): return True, 'account created'
    # Oct 13 (Mastercard): the register POST returns 303 and Workday shows its Sign In form with no error = the account
    # exists now and this tenant wants a sign-in (sometimes after the verification email). Old code called that a failure.
    if _vis(_aid(page, 'signInSubmitButton')) and not err:
        if _wd_sign_in(page, pw): return True, 'account created + signed in'
        v = complete_verification(ctx, page, f'Workday {host}', t0)
        if v == 'ok':
            page.wait_for_timeout(2000)
            if _wd_wall_gone(page) or _wd_sign_in(page, pw): return True, 'account created + verified + signed in'
        if WRONG_PW.search(_wd_error(page)):
            _mark_failed(host); return False, OTHER_PW_MSG
        return False, f'account created; sign-in after create failed (verification: {v})'
    return False, 'create account did not complete' + (f' ({err})' if err else '')


def _wd_sign_in(page, pw):
    """Fill and send the Workday Sign In form that is on screen; True when the wall is gone."""
    if not _vis(_aid(page, 'signInSubmitButton')) and _vis(_aid(page, 'signInLink')):
        _wd_click(_aid(page, 'signInLink')); page.wait_for_timeout(2000)
    if not _vis(_aid(page, 'signInSubmitButton')): return False
    form = page.locator('[data-automation-id="signInContent"]:has([data-automation-id="signInSubmitButton"]), '
                        'form:has([data-automation-id="signInSubmitButton"])')
    scope = form.first if form.count() else page
    scope.locator('[data-automation-id="email"], input[type="email"]').first.fill(EMAIL)
    scope.locator('[data-automation-id="password"], input[type="password"]').first.fill(pw)
    _wd_click(_aid(page, 'signInSubmitButton'))
    return _wd_wall_gone(page)


def get_through_wall(ctx, page, host):
    """Called by an adapter that hit a sign-in / create-account wall. Tries sign-in, then account creation, then email
    verification. Returns (ok: bool, reason: str)."""
    if 'myworkdayjobs.com' in (page.url or '') or 'myworkdaysite.com' in (page.url or ''):
        return workday_wall(ctx, page, host)
    t0 = time.time()
    s = sign_in(page, host)
    if s == 'ok': return True, 'signed in'
    if s == 'needs-verify':
        v = complete_verification(ctx, page, host, t0)
        return (v == 'ok'), f'sign-in verification: {v}'
    if s in ('disabled', 'no-password', 'captcha'):
        return False, {'disabled': 'sign in (AUTO_LOGIN off)', 'no-password': 'no Keychain password for jobhunter',
                       'captcha': 'CAPTCHA'}[s]
    c = create_account(page, host)
    if c == 'ok': return True, 'account created'
    if c == 'needs-verify':
        v = complete_verification(ctx, page, host, t0)
        if v != 'ok': return False, f'account created, email verification: {v}'
        s2 = sign_in(page, host)
        return (s2 == 'ok'), f'account created + verified, sign-in: {s2}'
    if c == 'exists':
        return False, f'account exists but sign-in failed ({s})'
    return False, f'sign-in: {s}; create account: {c}'


# ------------------------------------------------------------------ final submit
CONFIRM = re.compile(r'thank you for (applying|your (application|interest|submission))|application (has been |was )?'
                     r'(submitted|received|complete)|we.ve received your application|successfully (submitted|applied)|'
                     r"you.ve applied|application (was )?sent|your application is (on its way|complete)", re.I)
SUBMIT_RX = r'^\s*(submit( application| my application)?|send application|apply|submit & apply|finish|complete( application)?)\s*$'


def _wait_confirm(page, scope, secs=25):
    """Poll up to `secs` for a confirmation (page or frame), a CAPTCHA, or an error; returns the page text."""
    t = ''
    for i in range(secs // 2):
        page.wait_for_timeout(2000)
        t = _body(page, 10000)
        if scope is not page: t += ' ' + (_body(scope, 5000) or '')
        if CONFIRM.search(t) or '/confirmation' in page.url or _captcha(page) or _security_code(scope):
            break
        # form errors only count once the redirect had time: A-TEK Oct 12 reached /confirmation ~10 s after an early
        # "is required" match had already ended the wait
        if i >= 6 and _form_errors(scope, t):
            break
    return t


def _security_code(scope):
    """Greenhouse: 'Security code' boxes appear after Submit; the code is emailed ('enter the code, then resubmit')."""
    try:
        return scope.locator('input[id^="security-input-"]').count() > 0 and scope.locator('input[id^="security-input-"]').first.is_visible()
    except Exception:
        return False


def _enter_security_code(page, scope, button_rx, since):
    """AUTO_VERIFY: read the emailed Greenhouse security code, type it, resubmit (the first Submit was held back by
    Greenhouse, so this is the same application, not a second one). Returns the new page text or None."""
    v = fetch_verification('Greenhouse "Security code for your application"', since)
    code = (v or {}).get('code') or ''
    cells = scope.locator('input[id^="security-input-"]').all()
    if not code or len(code) != len(cells): return None
    for c, ch in zip(cells, code): c.fill(ch)
    btn = scope.get_by_role('button', name=re.compile(r'^\s*submit', re.I))
    if not _vis(btn): btn = scope.get_by_role('button', name=re.compile(button_rx, re.I))
    if not _vis(btn): btn = scope.locator('button[type="submit"]:visible, input[type="submit"]:visible')
    if not _vis(btn): return None
    btn.first.click(timeout=8000)
    return _wait_confirm(page, scope, secs=90)


def _form_errors(scope, t):
    """Validation errors after Submit: invalid fields in the DOM or explicit error wording (not bare 'required',
    which job descriptions use)."""
    try:
        if scope.locator('[aria-invalid="true"]:visible').count(): return True
    except Exception:
        pass
    return bool(re.search(r'this field is required|(field|answer|question) is required|please (enter|select|complete)', t, re.I))


def submit(page, res, frame=None, button_rx=SUBMIT_RX):
    """Click the final Submit when AUTO_SUBMIT=1 and the adapter result is FILLED (every required field answered,
    resume attached). Ticks remaining consent boxes first only if AUTO_CONSENT=1. Updates res: status -> SUBMITTED or
    NEEDS YOU."""
    if res.get('status') != 'FILLED' or not ON('AUTO_SUBMIT') or res.get('dry'):
        return res
    scope = frame or page
    try:   # an open application dialog (LinkedIn Easy Apply) is the only place to tick boxes and find Submit
        dlg = page.locator('dialog[open], [data-testid="dialog"]')
        if not frame and dlg.count() and dlg.first.is_visible(): scope = dlg.first
    except Exception:
        pass
    if ON('AUTO_CONSENT'): _tick_boxes(scope)
    btn = scope.locator('[data-automation-id="pageFooterNextButton"], [data-automation-id="bottom-navigation-next-button"]')
    if not (_vis(btn) and re.match(button_rx, btn.first.inner_text() or '', re.I)):   # Workday's footer Submit
        # prefer a real "Submit…" button: Greenhouse pages also have a top "Apply" button that only scrolls to the
        # form (Neuralink/Glean Oct 12: clicking it = nothing sent)
        btn = scope.get_by_role('button', name=re.compile(r'^\s*submit', re.I))
        if not _vis(btn): btn = scope.get_by_role('button', name=re.compile(button_rx, re.I))
    if not _vis(btn): btn = scope.locator('button[type="submit"]:visible, input[type="submit"]:visible')
    # JazzHR (Oct 26): "SUBMIT APPLICATION" is a link (a#resumator-submit-resume) -> ~12 complete forms never sent.
    # Only links that say submit; never an "Apply" link (those just open/scroll to the form)
    if not _vis(btn): btn = scope.locator('a#resumator-submit-resume:visible')
    if not _vis(btn): btn = scope.get_by_role('link', name=re.compile(r'^\s*submit( (my |your )?application)?\s*$', re.I))
    if not _vis(btn):
        res.update(status='NEEDS YOU', reason='filled, but no Submit button found (tab open)'); return res
    try: btn.first.scroll_into_view_if_needed(timeout=3000)
    except Exception: pass
    since = time.time() - 30
    rejected = []   # Ashby: the submit response itself says "not submitted" + which fields are missing
    def _on_resp(r):
        try:
            if re.search(r'op=ApiSubmit\w*Form\w*Action', r.url):   # Single...FormAction / MultipleFormsAction (Zip)
                m = (list(r.json()['data'].values())[0] or {}).get('applicationFormResult') or {}
                if m.get('errorMessages'): rejected.append(m['errorMessages'])
        except Exception:
            pass
    page.on('response', _on_resp)
    _wd_click(btn)   # normal click -> overlay (Workday click_filter) -> DOM click
    t = _wait_confirm(page, scope, secs=90)   # Greenhouse reCAPTCHA can take ~60 s before /confirmation (A-TEK Oct 12)
    for _ in range(2):   # retry ONLY when the server explicitly refused the form (no application was created)
        if not (rejected and res.get('refill') and not CONFIRM.search(t)): break
        print('Ashby refused the form:', rejected[-1][:4], '-> refill and submit again')
        missing = list(rejected[-1]); rejected.clear()
        try: res['refill'](missing)
        except Exception as e: print('refill failed:', str(e)[:100]); break
        page.wait_for_timeout(1500)
        _wd_click(btn)
        t = _wait_confirm(page, scope, secs=60)
    if rejected and not CONFIRM.search(t):
        res.update(status='NEEDS YOU', reason='Ashby refused the form (NOT submitted): ' + '; '.join(rejected[-1])[:300])
        return res
    if _security_code(scope) and not CONFIRM.search(t):
        if not ON('AUTO_VERIFY'):
            res.update(status='NEEDS YOU', reason='Greenhouse emailed a security code: enter it and click Submit (tab open)'); return res
        t2 = _enter_security_code(page, scope, button_rx, since)
        if t2 is None:
            res.update(status='NEEDS YOU', reason='Greenhouse security code: could not read/enter it — enter the emailed code and click Submit (tab open)'); return res
        t = t2
    # NEVER click Submit a second time: on Greenhouse the 1st click can submit while the page still shows the form
    # (RF-SMART Oct 12: a 2nd click made a duplicate application). No confirmation -> NEEDS YOU (check the email).
    if re.search(r'/confirmation\b', page.url) and not CONFIRM.search(t): t += ' application received'
    lim = re.search(r'limiting applications[^.]*\.[^.]*(within the last \d+ days)?|you cannot submit an application for this position|'
                    r'(reached|exceeded) the (maximum|limit)[^.]{0,80}|maximum (number )?of (two|\d+) (positions|applications)[^.]{0,60}|'
                    r'you have already applied|already submitted an application', t, re.I)
    if lim and not CONFIRM.search(t):
        res.update(status='NEEDS YOU', reason=f'posting closed for him — site limit: {lim.group(0)[:140]}')
    elif _captcha(page):
        res.update(status='NEEDS YOU', reason='CAPTCHA appeared on Submit (tab open)')
    elif CONFIRM.search(t) or any(CONFIRM.search(_body(f.page if hasattr(f, 'page') else page, 5000) or '') for f in [scope]):
        res.update(status='SUBMITTED', reason='submitted — confirmation shown')
    else:
        err = re.search(r'(this field is required|is required|please (enter|select|complete)[^.\n]{0,60}|error[^.\n]{0,60})', t, re.I)
        res.update(status='NEEDS YOU', reason='clicked Submit ONCE but saw no confirmation — it may have gone through: check email for a confirmation before re-submitting' + (f': {err.group(0)[:80]}' if err else '') + ' (tab open)')
    return res
