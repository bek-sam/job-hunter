"""Lever (jobs.lever.co/<co>/<id>/apply). Single page; hCaptcha appears only on submit."""
import datetime, os, re
import applicant as P
import formfill
from ats.common import captcha, outcome, result

MATCH = r'jobs\.lever\.co'


def _location(page):
    """"Current location" is a typeahead: the value only counts once a suggestion is picked (Oct 22)."""
    loc = page.locator('input[name="location"]')
    if not (loc.count() and loc.first.is_visible()) or loc.first.input_value(): return None
    try:
        loc.first.click(); loc.first.press_sequentially(P.LOCATION, delay=60); page.wait_for_timeout(3000)
        opts = page.locator('.dropdown-results .dropdown-location')
        for i in range(opts.count()):
            if re.search(re.escape(P.LOCATION), opts.nth(i).inner_text(), re.I):
                opts.nth(i).click(); page.wait_for_timeout(800); return True
        loc.first.fill('')
    except Exception:
        pass
    return False


def run(ctx, page, job):
    u = page.url.split('?')[0].rstrip('/')
    if not u.endswith('/apply'):
        page.goto(u + '/apply', wait_until='domcontentloaded'); page.wait_for_timeout(2000)
    if captcha(page): return result('NEEDS YOU', 'CAPTCHA', page=page)
    city = _location(page)
    done, unknown = formfill.fill(page.main_frame, job)
    if city: done.append('Current location')
    if city is False and not any('location' in str(x).lower() for x in unknown): unknown.append(f'Current location (no "{P.LOCATION}" suggestion)')
    if city: unknown = [x for x in unknown if not re.match(r'current location', str(x), re.I)]
    # disability self-ID signature (name + date): AUTO_SELF_ID=1 signs it on every site (owner, Oct 22; was Workday-only)
    sig = page.locator('input[name="eeo[disabilitySignature]"], input[name="eeo[disabilitySignatureDate]"]')
    if sig.count() and os.environ.get('AUTO_SELF_ID') == '1':
        try:
            nm = page.locator('input[name="eeo[disabilitySignature]"]').first
            if not nm.input_value().strip(): nm.fill(P.get('identity', 'workday_self_id_name') or P.NAME)
            dt_ = page.locator('input[name="eeo[disabilitySignatureDate]"]').first
            if not dt_.input_value().strip(): dt_.fill(datetime.date.today().strftime('%m/%d/%Y'))
            done.append('Disability self-ID signature')
            unknown = [x for x in unknown if str(x) not in ('Name', 'Date') and not str(x).startswith(('Name (', 'Date ('))]
        except Exception as e:
            unknown.append(f'Disability self-ID signature (error: {str(e)[:60]})')
    elif sig.count():
        for i in range(sig.count()):
            try: sig.nth(i).fill('')
            except Exception: pass
        done = [x for x in done if x not in ('Name', 'Date')]
        unknown = [x for x in unknown if str(x) not in ('Name', 'Date')]
        if sig.first.evaluate('e => e.required'):
            unknown.append('Disability self-ID signature (name + date) — yours to sign (set AUTO_SELF_ID=1 to sign)')
    if not job.get('pdf'): unknown.append('resume PDF missing on the Notion page')
    return outcome(done, unknown, page)
