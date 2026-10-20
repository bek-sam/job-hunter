"""Greenhouse (job-boards.greenhouse.io, boards.greenhouse.io, embedded gh_jid iframes). Single page."""
import json, re, urllib.request
from urllib.parse import urlparse
import formfill
from ats.common import captcha, outcome, result, click_text

MATCH = r'greenhouse\.io|gh_jid='
HTML_MATCH = r'boards\.greenhouse\.io/embed|job-boards\.greenhouse\.io/embed'


def board_token(url, company=''):
    """Greenhouse board token for a company-site ?gh_jid= link (Avathon Oct 13: the careers page shows no form at all)."""
    m = re.search(r'gh_jid=(\d+)', url or '')
    if not m: return None, None
    host = (urlparse(url).hostname or '').split('.')
    cands = [p for p in host if p not in ('www', 'careers', 'jobs', 'com', 'io', 'ai', 'co', 'org', 'net')]
    cands += [re.sub(r'[^a-z0-9]', '', (company or '').split('(')[0].lower())]
    for t in dict.fromkeys(c for c in cands if c):
        try:
            with urllib.request.urlopen(f'https://boards-api.greenhouse.io/v1/boards/{t}/jobs/{m.group(1)}', timeout=10) as r:
                if json.loads(r.read()).get('id'): return t, m.group(1)
        except Exception:
            continue
    return None, m.group(1)


def has_form(page):
    try: return any(f.locator('input[type="file"], input[id*="first_name"]').count() for f in page.frames)
    except Exception: return False


def run(ctx, page, job):
    click_text(page, r'^apply( now| for this job)?$') and page.wait_for_timeout(1500)
    if captcha(page): return result('NEEDS YOU', 'CAPTCHA', page=page)
    for _ in range(4):   # embedded iframes load late
        if has_form(page): break
        page.wait_for_timeout(1500)
    if not has_form(page) and 'greenhouse.io' not in (urlparse(page.url).hostname or ''):
        tok, jid = board_token(page.url, job.get('company'))
        if tok:   # Greenhouse's own copy of the form for this job
            page.goto(f'https://job-boards.greenhouse.io/embed/job_app?for={tok}&token={jid}', wait_until='domcontentloaded', timeout=60000)
            page.wait_for_timeout(2500)
    frame = formfill.form_frame(page)
    done, unknown = formfill.fill(frame, job)
    if not job.get('pdf'): unknown.append('resume PDF missing on the Notion page')
    return outcome(done, unknown, page)
