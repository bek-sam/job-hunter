"""Greenhouse (job-boards.greenhouse.io, boards.greenhouse.io, embedded gh_jid iframes). Single page."""
import formfill
from ats.common import captcha, outcome, result, click_text

MATCH = r'greenhouse\.io|gh_jid='
HTML_MATCH = r'boards\.greenhouse\.io/embed|job-boards\.greenhouse\.io/embed'


def run(ctx, page, job):
    click_text(page, r'^apply( now| for this job)?$') and page.wait_for_timeout(1500)
    if captcha(page): return result('NEEDS YOU', 'CAPTCHA', page=page)
    frame = formfill.form_frame(page)
    done, unknown = formfill.fill(frame, job)
    if not job.get('pdf'): unknown.append('resume PDF missing on the Notion page')
    return outcome(done, unknown, page)
