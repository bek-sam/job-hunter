"""SmartRecruiters (jobs.smartrecruiters.com/<co>/<id> -> "I'm interested" -> oneclick-ui wizard, shadow DOM).
Steps: personal info + resume -> Next -> screening questions -> (final) Submit. Stops before Submit."""
import applicant as P
import re
from ats import _shadow
from ats.common import captcha, needs_login, outcome, result, click_text, safe_next, body_text

MATCH = r'smartrecruiters\.com'
HTML_MATCH = r'jobs\.smartrecruiters\.com/oneclick-ui|smartrecruiters\.com/[^"]+/\d{6,}'


def _to_form(ctx, page):
    if 'oneclick-ui' in page.url: return page
    n = len(ctx.pages)
    for rx in (r"i.?m interested", r'^apply( now)?$', r'apply'):
        if click_text(page, rx) or click_text(page, rx, role='link'):
            page.wait_for_timeout(4000); break
    if len(ctx.pages) > n: page = ctx.pages[-1]; page.wait_for_load_state('domcontentloaded'); page.wait_for_timeout(3000)
    return page


def run(ctx, page, job):
    page = _to_form(ctx, page)
    if 'oneclick-ui' not in page.url:
        return result('NEEDS YOU', 'SmartRecruiters: could not open the application form (tab open)', page=page)
    page.wait_for_timeout(2500)
    done, unknown = [], []
    for step in range(6):
        if captcha(page): return result('NEEDS YOU', 'SmartRecruiters: CAPTCHA (tab open)', done, page=page)
        if needs_login(page): return result('NEEDS YOU', 'SmartRecruiters: sign in or create account (tab open)', done, page=page)
        if step == 0 and job.get('pdf'):   # the "Resume" section dropzone (not the top "apply with resume" one)
            rz = page.locator('[data-test="resume-upload"] input[type="file"]')
            if rz.count():
                rz.first.set_input_files(job['pdf']); done.append('Resume'); page.wait_for_timeout(2500)
        d, u = _shadow.fill(page, dict(job, pdf=None))
        done += d
        city = page.locator('[data-test="location-autocomplete"] input[role="combobox"]')
        if city.count() and city.first.is_visible():   # City is an autocomplete: must pick a suggestion
            try:
                city.first.click(); city.first.fill(''); city.first.press_sequentially(P.CITY, delay=50); page.wait_for_timeout(2500)
                opts = page.locator('[data-test="location-autocomplete"] [role="option"]')
                # option text lives in the host element's light DOM (slots)
                texts = opts.evaluate_all("els => els.map(e => ((e.getRootNode().host || e).textContent || '').replace(/\\s+/g, ' ').trim())")
                idx = next((i for i, t in enumerate(texts) if re.search(re.escape(P.LOCATION), t, re.I)), None)
                if idx is not None: opts.nth(idx).click(); page.wait_for_timeout(800); done.append('City')
                else: unknown.append(f'City (no "{P.LOCATION}" suggestion)')
            except Exception as e:
                unknown.append(f'City (error: {str(e)[:50]})')
        unknown += [x for x in u if x not in unknown]
        if unknown: break
        # last step shows only Submit -> stop there
        nxt = page.get_by_role('button', name=re.compile(r'^\s*(next|continue)\s*$', re.I))
        if not (nxt.count() and nxt.first.is_visible()): break
        before = body_text(page, 3000)
        safe_next(page, r'^\s*(next|continue)\s*$'); page.wait_for_timeout(3500)
        if body_text(page, 3000) == before:   # validation error kept us on the same step
            errs = re.findall(r'([^\n]{0,60}(is required|required field|please (enter|select)[^\n]{0,40}))', body_text(page), re.I)
            unknown += [e[0].strip() for e in errs[:5]] or ['SmartRecruiters: a required field on this step was not accepted']
            break
    if not job.get('pdf'): unknown.append('resume PDF missing on the Notion page')
    return outcome(done, unknown, page)
