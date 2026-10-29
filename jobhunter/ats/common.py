"""Helpers shared by adapters."""
import os, re, time

FINAL_SUBMIT = re.compile(r'^\s*(submit( application| my application)?|send application|apply( now)?|finish|complete application)\s*$', re.I)


def result(status, reason='', done=None, unknown=None, page=None):
    return dict(status=status, reason=reason, done=done or [], unknown=unknown or [], page=page)


def body_text(page, n=20000):
    try:
        return page.inner_text('body')[:n]
    except Exception:
        return ''


CAPTCHA_WAIT = int(os.environ.get('CAPTCHA_WAIT', '0'))   # seconds to wait for the owner to solve a CAPTCHA in the open tab


def captcha(page):
    """True if a CAPTCHA challenge is still open. Never solved by code. With CAPTCHA_WAIT > 0 (Oct 28, the owner watches
    the runs): the tab comes to the front and the run waits for him to solve it, then goes on; unsolved -> NEEDS YOU."""
    return _captcha_now(page) and not wait_solved(page, _captcha_now)


def wait_solved(page, still_open):
    """Owner-solved CAPTCHA: bring the tab forward, poll until still_open(page) is False (True) or CAPTCHA_WAIT runs out."""
    if CAPTCHA_WAIT <= 0: return False
    print(f'  CAPTCHA: waiting up to {CAPTCHA_WAIT} s for you to solve it in the open tab', flush=True)
    try: page.bring_to_front()
    except Exception: pass
    end = time.time() + CAPTCHA_WAIT
    while time.time() < end:
        page.wait_for_timeout(2000)
        if not still_open(page):
            print('  CAPTCHA solved, continuing', flush=True); page.wait_for_timeout(1500); return True
    return False


def _captcha_now(page):
    """A visible CAPTCHA challenge (invisible reCAPTCHA badges don't count)."""
    try:
        vis = page.locator('iframe[src*="recaptcha/api2/bframe"], iframe[title*="challenge" i], iframe[src*="hcaptcha"][src*="challenge"], '
                           'iframe[src*="hcaptcha"][src*="frame=challenge"]')
        for i in range(vis.count()):
            el = vis.nth(i)
            if el.is_visible() and (el.bounding_box() or {}).get('height', 0) > 100: return True
    except Exception:
        pass
    try:   # challenge frames nested inside other iframes (iCIMS embeds the form in an iframe); a big frame = image challenge
        for fr in page.frames:
            ifr = fr.locator('iframe[src*="hcaptcha.com/captcha"], iframe[src*="recaptcha/api2/bframe"], iframe[src*="recaptcha/enterprise/bframe"]')
            for i in range(ifr.count()):
                bb = ifr.nth(i).bounding_box()   # Lever pre-renders a hidden full-page hCaptcha enclave: must be visible
                if bb and bb.get('height', 0) >= 300 and bb.get('width', 0) >= 250 and ifr.nth(i).is_visible(): return True
    except Exception:
        pass
    return bool(re.search(r'verify you are human|are you a robot|press and hold', body_text(page, 5000), re.I))


def needs_login(page):
    """Sign-in / account wall: a visible password field."""
    try:
        pw = page.locator('input[type="password"]')
        return bool(pw.count() and pw.first.is_visible())
    except Exception:
        return False


def click_text(page, rx, role='button', timeout=4000):
    loc = page.get_by_role(role, name=re.compile(rx, re.I))
    try:
        if loc.count() and loc.first.is_visible():
            loc.first.click(timeout=timeout); return True
    except Exception:
        pass
    return False


def safe_next(page, rx=r'^\s*(next|continue|save (and|&) continue|review)\s*$'):
    """Click a step button that is NOT a final submit. Returns True if clicked."""
    loc = page.get_by_role('button', name=re.compile(rx, re.I))
    for i in range(loc.count()):
        b = loc.nth(i)
        try:
            t = (b.inner_text() or '') + ' ' + (b.get_attribute('aria-label') or '')
            if b.is_visible() and not FINAL_SUBMIT.match(b.inner_text() or '') \
                    and not re.search(r'submit|send application|finish', t, re.I):   # never submit from step navigation
                b.click(timeout=5000); return True
        except Exception:
            continue
    return False


def outcome(done, unknown, page, extra_reason=''):
    if unknown:
        return result('NEEDS YOU', '; '.join(unknown)[:600] + (f' {extra_reason}' if extra_reason else ''), done, unknown, page)
    return result('FILLED', extra_reason or f'{len(done)} fields', done, unknown, page)


def dismiss_cookies(page):
    """Close cookie/consent banners, choosing the privacy-preserving option (Decline / Reject / Necessary only)."""
    rx = re.compile(r'^\s*(decline( all)?|reject( all)?|deny|refuse|necessary (cookies )?only|only (necessary|essential)|'
                    r'use necessary cookies only|continue without accepting)\s*$', re.I)
    for frame in [page.main_frame] + [f for f in page.frames if f is not page.main_frame][:3]:
        try:
            b = frame.get_by_role('button', name=rx)
            if b.count() and b.first.is_visible():
                b.first.click(timeout=3000); page.wait_for_timeout(600); return True
        except Exception:
            continue
    # OneTrust (Oct 26, SuccessFactors): its reject buttons have no "Reject" role name in some skins, and an open
    # preference center leaves a dark filter that swallows every click -> its own reject ids, else remove the overlay
    # (no choice made = nothing accepted)
    try:
        for sel in ('#onetrust-reject-all-handler', '.ot-pc-refuse-all-handler', 'button.save-preference-btn-handler'):
            b = page.locator(sel)
            if b.count() and b.first.is_visible():
                b.first.click(timeout=3000); page.wait_for_timeout(600); break
        if page.locator('.onetrust-pc-dark-filter:visible, #onetrust-pc-sdk:visible').count():
            page.evaluate("() => document.getElementById('onetrust-consent-sdk')?.remove()")
            return True
    except Exception:
        pass
    return False


def first_visible(loc):
    for i in range(min(loc.count(), 10)):
        try:
            if loc.nth(i).is_visible(): return loc.nth(i)
        except Exception:
            continue
    return None


UPLOAD_ERR = re.compile(r'upload (failed|error)|(file|attachment) (is )?(too large|type not (allowed|supported))|unsupported file|could not (upload|parse)', re.I)


def upload_ok(frame, loc, path, wait_ms=8000):
    """Did the file really attach? (neonwatty job-apply idea): the input holds a file or the page shows its name, and no
    upload error is shown. Polls up to wait_ms."""
    import os
    name = os.path.basename(path or '')
    for _ in range(max(1, wait_ms // 500)):
        try:
            if UPLOAD_ERR.search(frame.locator('body').inner_text(timeout=2000)[:20000]): return False
        except Exception:
            pass
        try:
            if loc.evaluate('e => !!(e.files && e.files.length)'): return True
        except Exception:
            pass
        try:
            if name and frame.get_by_text(name.rsplit('.', 1)[0], exact=False).count(): return True
        except Exception:
            pass
        frame.wait_for_timeout(500)
    return False


def needs_you_banner(page, missing):
    """Fixed banner on a kept tab listing what is missing + red outline on those fields (ApplyPilot human_review idea).
    Display only: never clicks or submits anything."""
    try:
        page.evaluate("""m => {
          const old = document.getElementById('jh-banner'); if (old) old.remove();
          const b = document.createElement('div'); b.id = 'jh-banner';
          b.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:2147483647;background:#b00020;color:#fff;font:14px/1.4 system-ui;padding:8px 12px;max-height:30vh;overflow:auto';
          b.textContent = 'Job Hunter — needs you: ' + m.join(' • ');
          b.onclick = () => b.remove(); document.body.appendChild(b);
          const norm = s => (s || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
          for (const l of document.querySelectorAll('label, legend')) {
            const t = norm(l.innerText);
            if (t && m.some(x => { const n = norm(x); return n.length > 3 && (t.includes(n.slice(0, 40)) || n.includes(t.slice(0, 40))); }))
              (l.closest('fieldset, .field, [class*="question"], div') || l).style.outline = '2px solid #b00020';
          }
        }""", [str(x)[:120] for x in missing][:20])
    except Exception:
        pass
