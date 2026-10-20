"""Ashby (jobs.ashbyhq.com/<org>/<id>/application). Single page; Yes/No button questions."""
import re
import formfill
from ats.common import captcha, outcome, result

MATCH = r'ashbyhq\.com'


def upload_resume(page, pdf):
    """Set the resume file input (the one labelled Resume/CV, else the first file input). True if set."""
    inputs = page.locator('input[type="file"]')
    for i in range(inputs.count()):
        el = inputs.nth(i)
        try:
            ctx_txt = el.evaluate("e => { const b = e.closest('[class*=fieldEntry], fieldset, div'); return (e.id + ' ' + e.name + ' ' + (b ? b.innerText : '')).slice(0, 300); }")
        except Exception:
            ctx_txt = ''
        if re.search(r'resume|\bcv\b', ctx_txt, re.I) and not re.search(r'cover', ctx_txt, re.I):
            el.set_input_files(pdf); return True
    if inputs.count():
        inputs.first.set_input_files(pdf); return True
    return False


def run(ctx, page, job):
    u = page.url.split('?')[0].rstrip('/')
    if '/application' not in u:
        page.goto(u + '/application', wait_until='domcontentloaded'); page.wait_for_timeout(2500)
    if captcha(page): return result('NEEDS YOU', 'CAPTCHA', page=page)
    # The resume upload starts Ashby's "autofill from resume", which resets the form state seconds later while the
    # DOM still shows our answers (Oct 19-20: 8 forms refused with "Missing entry" for fields that looked filled).
    # -> upload the resume FIRST, let the autofill finish, then fill everything once.
    done = []
    if job.get('pdf') and upload_resume(page, job['pdf']):
        done.append('Resume')
        try: page.wait_for_load_state('networkidle', timeout=15000)
        except Exception: pass
        page.wait_for_timeout(5000)
    d1, unknown = formfill.fill(page.main_frame, dict(job, pdf=None) if done else job)
    done += [x for x in d1 if x not in done]
    page.wait_for_timeout(2000)
    d2, unknown = formfill.fill(page.main_frame, dict(job, pdf=None, cover_txt=None))
    done += [x for x in d2 if x not in done]
    if not job.get('pdf'): unknown.append('resume PDF missing on the Notion page')
    res = outcome(done, unknown, page)
    # auth.submit calls this when Ashby's server answers "not submitted: missing entry for ..." (state lost, see above)
    # Oct 13 ElevenLabs: "Missing entry for required field: Resume" -> the refill must upload the resume again
    def refill(missing=()):
        labels = [str(m).split(':', 1)[-1] for m in missing]
        formfill.FORCE.clear(); formfill.FORCE.update(re.sub(r'\W+', '', l.lower())[:60] for l in labels)
        try:
            return formfill.fill(page.main_frame, dict(
                job, pdf=job.get('pdf') if any(re.search(r'resume|\bcv\b', l, re.I) for l in labels) else None, cover_txt=None))
        finally:
            formfill.FORCE.clear()
    res['refill'] = refill
    return res
