"""Oracle Recruiting Cloud / HCM Candidate Experience (*.oraclecloud.com/hcmUI/CandidateExperience; JPMorgan,
Oracle, Texas Instruments, Ford, Goldman higher.gs.com). Flow: Apply -> email page (+ legal disclaimer checkbox,
hCaptcha) -> one-time code -> multi-section form -> e-signature -> Submit.
We type the email only. The disclaimer box (consent), the one-time code and any CAPTCHA are his -> NEEDS YOU.
If a session already exists (he verified earlier), the sections are filled and we stop before Submit."""
import applicant as P
import re
import formfill, config
from ats import _guard
from ats.common import captcha, outcome, result, click_text, safe_next, body_text

MATCH = r'oraclecloud\.com/hcmUI|/hcmUI/CandidateExperience|higher\.gs\.com'
HTML_MATCH = r'hcmUI/CandidateExperience'


def run(ctx, page, job):
    if '/apply' not in page.url:
        if not (click_text(page, r'^\s*apply( now)?\s*$') or click_text(page, r'^\s*apply( now)?\s*$', role='link')):
            return result('NEEDS YOU', 'Oracle: no Apply button found (tab open)', page=page)
        page.wait_for_timeout(4000)
    em = page.locator('input#primary-email, input[type="email"]')
    if em.count() and em.first.is_visible():
        if not em.first.input_value(): em.first.fill(getattr(config, 'LOGIN_EMAIL', P.EMAIL))
        box = page.locator('#legal-disclaimer-checkbox, input[type="checkbox"][id*="disclaimer" i]')
        need = []
        if box.count() and not box.first.is_checked():
            need.append('tick the legal disclaimer (legal/consent — yours)')
        need.append('click NEXT, then enter the one-time code Oracle emails you')
        if captcha(page): need.append('CAPTCHA')
        return result('NEEDS YOU', 'Oracle: email entered — ' + '; '.join(need) + ' (tab open)', ['Email'], need, page)
    if re.search(r'verification code|one-time|pin code|enter the code', body_text(page, 6000), re.I):
        return result('NEEDS YOU', 'Oracle: enter the one-time code from your email (tab open)', page=page)
    if captcha(page): return result('NEEDS YOU', 'Oracle: CAPTCHA (tab open)', page=page)
    # signed-in application form: sections on one long page, sometimes split into steps
    done, unknown = [], []
    for step in range(6):
        d, u = formfill.fill(page.main_frame, job if step == 0 else dict(job, pdf=None))
        done += d; unknown += [x for x in u if x not in unknown]
        if unknown: break
        done, unknown = _guard.check(page.main_frame, done, unknown)
        if unknown: break
        if not safe_next(page): break
        page.wait_for_timeout(3000)
    if not job.get('pdf'): unknown.append('resume PDF missing on the Notion page')
    if re.search(r'e-?signature|full name.*signature', body_text(page), re.I):
        unknown.append('e-Signature (yours)')
    return outcome(done, unknown, page)
