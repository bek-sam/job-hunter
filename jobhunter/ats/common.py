"""Helpers shared by adapters."""
import re

FINAL_SUBMIT = re.compile(r'^\s*(submit( application| my application)?|send application|apply( now)?|finish|complete application)\s*$', re.I)


def result(status, reason='', done=None, unknown=None, page=None):
    return dict(status=status, reason=reason, done=done or [], unknown=unknown or [], page=page)


def body_text(page, n=20000):
    try:
        return page.inner_text('body')[:n]
    except Exception:
        return ''


def captcha(page):
    """A visible CAPTCHA challenge (invisible reCAPTCHA badges don't count). Never solved -> NEEDS YOU."""
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
    return False


def first_visible(loc):
    for i in range(min(loc.count(), 10)):
        try:
            if loc.nth(i).is_visible(): return loc.nth(i)
        except Exception:
            continue
    return None
