"""SmartRecruiters wizard: validate each page, advance Next, stop at final Submit."""
import re
import applicant as P
from ats import _shadow
from ats.common import captcha, needs_login, outcome, result, click_text, safe_next, upload_ok
from application_state import inspect, missing

MATCH = r'smartrecruiters\.com'
HTML_MATCH = r'jobs\.smartrecruiters\.com/oneclick-ui|smartrecruiters\.com/[^\"]+/\d{6,}'
NEXT = re.compile(r'^\s*(next|continue)\s*$', re.I)
FINAL = re.compile(r'^\s*submit( (my )?application)?\s*$', re.I)


def _to_form(ctx, page):
    if 'oneclick-ui' in page.url: return page
    before = list(ctx.pages)
    for rx in (r"i.?m interested", r'^apply( now)?$', r'apply'):
        if click_text(page, rx) or click_text(page, rx, role='link'):
            page.wait_for_timeout(1500); break
    added = [p for p in ctx.pages if p not in before]
    if added:
        page = added[-1]; page.wait_for_load_state('domcontentloaded')
    return page


def _city(page):
    city = page.locator('[data-test="location-autocomplete"] input[role="combobox"]')
    if not city.count() or not city.first.is_visible(): return []
    loc = city.first
    try:
        wanted = P.LOCATION
        loc.fill(wanted.split(',')[0]); page.wait_for_timeout(1200)
        opts = page.locator('[data-test="location-autocomplete"] [role="option"]')
        texts = opts.evaluate_all(_shadow.OPT_TEXT_JS)
        norm = lambda x: re.sub(r'[^a-z0-9]+', ' ', x.lower()).strip()
        expected = norm(wanted)
        index = next((i for i, t in enumerate(texts) if norm(t) == expected or norm(t).startswith(expected + ' ')), None)
        if index is not None:
            opts.nth(index).click(); page.wait_for_timeout(300)
            if norm(loc.input_value()).startswith(norm(wanted.split(',')[0])): return []
        # Optional location must not be left holding an unaccepted search query.
        loc.fill(''); loc.press('Escape')
        if loc.get_attribute('aria-required') == 'true' or loc.get_attribute('required') is not None:
            return ['City: matching location suggestion not found']
    except Exception as error:
        return ['City could not be verified: ' + type(error).__name__]
    return []


def _signature(page):
    state = inspect(page)
    return (page.url, [(f['label'], f['type']) for f in state['fields']], state['headings'],
            [(b['text'], b['disabled']) for b in state['buttons']])


def run(ctx, page, job):
    page = _to_form(ctx, page)
    if 'oneclick-ui' not in page.url:
        return result('NEEDS YOU', 'SmartRecruiters: application wizard did not open', page=page)
    done = []
    for step in range(10):
        if captcha(page): return result('NEEDS YOU', 'SmartRecruiters: CAPTCHA', done, page=page)
        if needs_login(page): return result('NEEDS YOU', 'SmartRecruiters: sign in or create account', done, page=page)
        unknown = []
        upload = page.locator('[data-test="resume-upload"] input[type="file"]')
        if upload.count() and job.get('pdf'):
            try:
                upload.first.set_input_files(job['pdf'])
                if upload_ok(page, upload.first, job['pdf']): done.append('Resume')
                else: unknown.append('Resume upload not confirmed')
            except Exception as error: unknown.append('Resume upload failed: ' + type(error).__name__)
        d, u = _shadow.fill(page, dict(job, pdf=job.get('pdf') if not upload.count() else None))
        done += d
        city_issues = _city(page)
        # Recheck actual persisted controls, not stale unknowns from a prior pass.
        remaining = missing(page)
        unknown += city_issues + [x for x in u if 'City (' not in x and (x in remaining or re.search(r'legal|consent|signature|upload|GOV-ID|INELIGIBLE', x, re.I))]
        unknown += remaining
        if unknown: return outcome(list(dict.fromkeys(done)), list(dict.fromkeys(unknown)), page)
        state = inspect(page)
        if not state['ok']: return outcome(done, state['missing'], page)
        next_buttons = [b for b in state['buttons'] if NEXT.fullmatch(b['text'])]
        if not next_buttons:
            if any(FINAL.fullmatch(b['text']) and not b['disabled'] for b in state['buttons']):
                return outcome(list(dict.fromkeys(done)), [], page)
            return outcome(done, ['SmartRecruiters: neither a Next step nor an enabled final Submit was found'], page)
        before = _signature(page)
        if not safe_next(page, NEXT.pattern):
            return outcome(done, ['SmartRecruiters: Next could not be clicked'], page)
        changed = False
        for _ in range(20):
            page.wait_for_timeout(500)
            if _signature(page) != before:
                changed = True; break
        if not changed:
            return outcome(done, missing(page) or ['SmartRecruiters: Next did not advance; check validation errors'], page)
    return outcome(done, ['SmartRecruiters: wizard step limit reached'], page)
