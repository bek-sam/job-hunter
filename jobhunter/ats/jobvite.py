"""Jobvite (jobs.jobvite.com/<co>/job/<id>[/apply]; also app.jobvite.com). Flow: Apply -> "Data Consent" page
(location of residence + Submit / I Decline: that is consent -> his click) -> single-page application form ->
"Send Application"/"Submit". We pick the location, then stop at the consent page; once he has consented (or the
company has no consent page) the form is filled and we stop before the final button."""
import re
import formfill
from ats import _guard
from ats.common import captcha, needs_login, outcome, result, click_text, body_text

MATCH = r'jobvite\.com'
HTML_MATCH = r'jobs\.jobvite\.com|jobvite-apply'


def run(ctx, page, job):
    if not page.url.rstrip('/').endswith('/apply'):
        n = len(ctx.pages)
        if not (click_text(page, r'^\s*apply( now| for this job)?\s*$') or click_text(page, r'^\s*apply( now| for this job)?\s*$', role='link')):
            page.goto(page.url.split('?')[0].rstrip('/') + '/apply', wait_until='domcontentloaded')
        page.wait_for_timeout(3500)
        if len(ctx.pages) > n: page = ctx.pages[-1]; page.wait_for_load_state('domcontentloaded'); page.wait_for_timeout(2500)
    if captcha(page): return result('NEEDS YOU', 'Jobvite: CAPTCHA (tab open)', page=page)
    if needs_login(page): return result('NEEDS YOU', 'Jobvite: sign in or create account (tab open)', page=page)
    sel = page.locator('#jv-country-select')
    if sel.count() and re.search(r'data consent|privacy', body_text(page, 5000), re.I):
        opts = sel.evaluate('e => [...e.options].map(o => o.text)')
        pick = next((o for o in opts if re.search(r'united states|^us\b|usa', o, re.I)), None) or \
               next((o for o in opts if re.search(r'all locations', o, re.I)), None)
        if pick: sel.select_option(label=pick); page.wait_for_timeout(1500)
        return result('NEEDS YOU', f'Jobvite: residence set to "{pick}" — accept the Data Consent (the Submit on the consent page is yours), '
                      'then mark the row FILL NOW to fill the form (tab open)', ['Location of residence'] if pick else [],
                      ['Data Consent (legal/consent — yours)'], page)
    if not page.locator('input[type="email"], input[name*="email" i], input[type="file"]').count():
        return result('NEEDS YOU', 'Jobvite: application form not found (tab open)', page=page)
    fr = formfill.form_frame(page)
    done, unknown = formfill.fill(fr, job)
    done, unknown = _guard.check(fr, done, unknown)
    if not job.get('pdf'): unknown.append('resume PDF missing on the Notion page')
    return outcome(done, unknown, page)
