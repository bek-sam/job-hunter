"""SAP SuccessFactors Recruiting (career sites like jobs.sap.com/job/..., career<N>.successfactors.com/career?
...company=..., *.jobs2web.com). Flow: "Apply now" -> sign-in / create-account page (email + password, data
privacy statement) -> multi-section application -> Submit. Sign-in, account creation and the privacy
statement are his -> NEEDS YOU. With a session, sections are filled and we stop before Submit."""
import re
import formfill
from ats import _guard
from ats.common import captcha, needs_login, outcome, result, click_text, safe_next, body_text

MATCH = r'successfactors\.(com|eu)|jobs2web\.com|sapsf\.|(jobs|careers)\.sap\.com'
HTML_MATCH = r'successfactors\.com/career|jobs2web|rmkcdn\.successfactors'


def run(ctx, page, job):
    if not re.search(r'/career\?|applyJob|/apply', page.url, re.I):
        n = len(ctx.pages)
        cands = page.locator('a, button').filter(has_text=re.compile(r'^\s*apply( now)?\s*»?\s*$', re.I))
        vis = [c for c in cands.all() if c.is_visible()]
        if not vis:
            return result('NEEDS YOU', 'SuccessFactors: no Apply button found (tab open)', page=page)
        vis[0].click(timeout=8000)
        page.wait_for_timeout(5000)
        if len(ctx.pages) > n: page = ctx.pages[-1]; page.wait_for_load_state('domcontentloaded'); page.wait_for_timeout(3000)
    if captcha(page): return result('NEEDS YOU', 'SuccessFactors: CAPTCHA (tab open)', page=page)
    t = body_text(page, 8000)
    if needs_login(page) or re.search(r'create an account|sign in|log ?in', t, re.I) and not page.locator('input[id*="firstName" i]').count():
        return result('NEEDS YOU', 'SuccessFactors: sign in or create account (tab open)', page=page)
    if re.search(r'data privacy (statement|consent)', t, re.I) and page.get_by_role('button', name=re.compile(r'^\s*accept\s*$', re.I)).count():
        return result('NEEDS YOU', 'SuccessFactors: accept the data privacy statement (legal/consent — yours), then mark FILL NOW (tab open)', page=page)
    done, unknown = [], []
    for step in range(8):
        d, u = formfill.fill(page.main_frame, job if step == 0 else dict(job, pdf=None))
        d, u = _guard.check(page.main_frame, d, u)
        done += d; unknown += [x for x in u if x not in unknown]
        if unknown: break
        if not safe_next(page): break
        page.wait_for_timeout(3500)
    if re.search(r'data privacy statement', body_text(page), re.I): unknown.append('Data privacy statement (legal/consent — yours)')
    if not job.get('pdf'): unknown.append('resume PDF missing on the Notion page')
    return outcome(done, unknown, page)
