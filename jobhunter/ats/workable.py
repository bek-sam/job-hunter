"""Workable (apply.workable.com/<co>/j/<code>/ -> /apply/). Single page form; cookie banner -> "Decline all"
(privacy-preserving). Stops before "Submit application"."""
import re
import formfill
from ats import _guard
from ats.common import captcha, needs_login, outcome, result, click_text

MATCH = r'workable\.com'
HTML_MATCH = r'apply\.workable\.com'


def _decline_cookies(page, wait=6000):
    """Answer the cookie banner with "Decline all" (it animates in; wait until the button is clickable)."""
    b = page.get_by_role('button', name=re.compile(r'^\s*decline all\s*$', re.I))
    try:
        b.first.wait_for(state='visible', timeout=wait)
        b.first.click(timeout=4000); page.wait_for_timeout(800)
    except Exception:
        pass


def run(ctx, page, job):
    _decline_cookies(page)   # the cookie banner slides in after load and blocks clicks until answered
    if not re.search(r'/apply/?$', page.url.split('?')[0]):   # host is apply.workable.com: check the path
        for rx in (r'apply for this job', r'^\s*application\s*$', r'^\s*apply( now)?\s*$'):
            if click_text(page, rx) or click_text(page, rx, role='tab') or click_text(page, rx, role='link'): break
        page.wait_for_timeout(3000)
        _decline_cookies(page, wait=1500)
    if captcha(page): return result('NEEDS YOU', 'Workable: CAPTCHA (tab open)', page=page)
    if needs_login(page): return result('NEEDS YOU', 'Workable: sign in (tab open)', page=page)
    try:
        page.wait_for_selector('input[name="firstname"], input[type="email"]', timeout=15000)
    except Exception:
        return result('NEEDS YOU', 'Workable: application form not found (tab open)', page=page)
    done, unknown = formfill.fill(page.main_frame, job)
    done, unknown = _guard.check(page.main_frame, done, unknown)
    if not job.get('pdf'): unknown.append('resume PDF missing on the Notion page')
    return outcome(done, unknown, page)
