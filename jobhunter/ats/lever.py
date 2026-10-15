"""Lever (jobs.lever.co/<co>/<id>/apply). Single page; hCaptcha appears only on submit."""
import formfill
from ats.common import captcha, outcome, result

MATCH = r'jobs\.lever\.co'


def run(ctx, page, job):
    u = page.url.split('?')[0].rstrip('/')
    if not u.endswith('/apply'):
        page.goto(u + '/apply', wait_until='domcontentloaded'); page.wait_for_timeout(2000)
    if captcha(page): return result('NEEDS YOU', 'CAPTCHA', page=page)
    done, unknown = formfill.fill(page.main_frame, job)
    if not job.get('pdf'): unknown.append('resume PDF missing on the Notion page')
    return outcome(done, unknown, page)
