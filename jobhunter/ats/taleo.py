"""Oracle Taleo (<co>.taleo.net/careersection/<x>/jobdetail.ftl?job=<id>). Flow: "Apply Online" -> login
(user name + password, or "New User" registration with legal agreement) -> multi-page wizard -> Submit.
Login, registration and the privacy agreement are his -> NEEDS YOU. If a session exists, the wizard pages are
filled with safe_next ("Save and Continue"), never the final Submit."""
import re
import formfill
from ats import _guard
from ats.common import captcha, needs_login, outcome, result, click_text, safe_next, body_text

MATCH = r'taleo\.net'
HTML_MATCH = r'taleo\.net/careersection'


def run(ctx, page, job):
    if 'jobdetail' in page.url or 'jobsearch' in page.url:
        n = len(ctx.pages)
        if not (click_text(page, r'apply online|apply now|^\s*apply\s*$', role='link') or click_text(page, r'apply online|apply now|^\s*apply\s*$')):
            return result('NEEDS YOU', 'Taleo: no Apply button found (tab open)', page=page)
        page.wait_for_timeout(5000)
        if len(ctx.pages) > n: page = ctx.pages[-1]; page.wait_for_load_state('domcontentloaded'); page.wait_for_timeout(3000)
    if captcha(page): return result('NEEDS YOU', 'Taleo: CAPTCHA (tab open)', page=page)
    t = body_text(page, 8000)
    if needs_login(page) or re.search(r'new user|user name|login', t, re.I) and 'login' in page.url.lower():
        return result('NEEDS YOU', 'Taleo: sign in or create account (tab open)', page=page)
    if re.search(r'privacy agreement|accept the (terms|agreement)|i have read', t, re.I) and page.locator('input[type="checkbox"], input[type="radio"]').count() <= 3:
        return result('NEEDS YOU', 'Taleo: accept the privacy agreement (legal/consent — yours), then mark FILL NOW (tab open)', page=page)
    done, unknown = [], []
    for step in range(10):
        d, u = formfill.fill(page.main_frame, job if step == 0 else dict(job, pdf=None))
        d, u = _guard.check(page.main_frame, d, u)
        done += d; unknown += [x for x in u if x not in unknown]
        if unknown: break
        before = body_text(page, 2000)
        if not safe_next(page, r'^\s*(save and continue|next|continue)\s*$'): break
        page.wait_for_timeout(4000)
        if body_text(page, 2000) == before: unknown.append('Taleo: a required field on this page was not accepted'); break
    if re.search(r'e-?signature|electronic signature', body_text(page), re.I): unknown.append('e-Signature (yours)')
    if not job.get('pdf'): unknown.append('resume PDF missing on the Notion page')
    return outcome(done, unknown, page)
