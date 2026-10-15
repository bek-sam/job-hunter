"""Ashby (jobs.ashbyhq.com/<org>/<id>/application). Single page; Yes/No button questions."""
import formfill
from ats.common import captcha, outcome, result

MATCH = r'ashbyhq\.com'


def run(ctx, page, job):
    u = page.url.split('?')[0].rstrip('/')
    if '/application' not in u:
        page.goto(u + '/application', wait_until='domcontentloaded'); page.wait_for_timeout(2500)
    if captcha(page): return result('NEEDS YOU', 'CAPTCHA', page=page)
    done, unknown = formfill.fill(page.main_frame, job)
    # the resume upload starts Ashby's resume autofill, which lands seconds later and can blank fields we already
    # filled (Foundation Health Oct 12: server said Name + "based in the US?" missing) -> wait, then refill what is empty
    page.wait_for_timeout(6000)
    d2, unknown = formfill.fill(page.main_frame, dict(job, pdf=None, cover_txt=None))
    done += [x for x in d2 if x not in done]
    if not job.get('pdf'): unknown.append('resume PDF missing on the Notion page')
    res = outcome(done, unknown, page)
    # auth.submit calls this when Ashby's server answers "not submitted: missing entry for ..." (state lost, see above)
    res['refill'] = lambda: formfill.fill(page.main_frame, dict(job, pdf=None, cover_txt=None))
    return res
