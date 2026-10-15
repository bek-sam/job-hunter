"""TikTok / ByteDance careers (lifeattiktok.com, careers.tiktok.com, jobs.bytedance.com, careers.tiktokusds.com).
Account login (email + password, agreement box) -> /resume/<id>/apply: one page with the resume card, basic info and
questions, Submit at the bottom. The login wall is handled by auth.get_through_wall (apply_prep), then this runs again."""
import re
import formfill
from ats.common import captcha, outcome, result

MATCH = r'lifeattiktok\.com|careers\.tiktok\.com|jobs\.bytedance\.com|joinbytedance\.com|careers\.tiktokusds\.com'


def job_id(*urls):
    for u in urls:
        m = re.search(r'/(?:search|position|resume|job)/(\d{12,})', u or '')
        if m: return m.group(1)
    return None


def run(ctx, page, job):
    jid = job_id(page.url, job.get('url'))
    if not jid: return result('NEEDS YOU', 'TikTok/ByteDance: no job id in the link (tab open)', page=page)
    if not re.search(rf'/resume/{jid}/apply', page.url):
        base = re.match(r'https?://[^/]+', page.url).group(0)
        if 'jobs.bytedance.com' in base or 'joinbytedance' in base: base = 'https://jobs.bytedance.com/en'
        elif 'careers.tiktok.com' in base: base = 'https://lifeattiktok.com'
        page.goto(f'{base}/resume/{jid}/apply', wait_until='domcontentloaded', timeout=60000)
        page.wait_for_timeout(5000)
    if re.search(r'/login', page.url):
        return result('NEEDS YOU', 'TikTok/ByteDance: sign in or create account (tab open)', page=page)
    if captcha(page): return result('NEEDS YOU', 'CAPTCHA', page=page)
    if re.search(r'already applied|you have applied|application has been submitted', page.inner_text('body')[:5000], re.I):
        return result('NEEDS YOU', 'already applied (the site says so)', page=page)
    done = []
    # resume card: "Update" opens a file chooser; a fresh form has a plain file input
    if job.get('pdf'):
        try:
            fi = page.locator('input[type="file"]')
            upd = page.get_by_text(re.compile(r'^\s*(update|upload|replace)( resume)?\s*$', re.I))
            if upd.count() and upd.first.is_visible():
                with page.expect_file_chooser(timeout=10000) as fc:
                    upd.first.click()
                fc.value.set_files(job['pdf'])
            elif fi.count():
                fi.first.set_input_files(job['pdf'])
            page.wait_for_timeout(6000)
            done.append('Resume')
        except Exception as e:
            return result('NEEDS YOU', f'TikTok/ByteDance: resume upload failed ({str(e)[:60]}) (tab open)', page=page)
    # Self-introduction is saved on the TikTok profile and was written for another role -> this role's cover letter
    if job.get('cover_text'):
        try:
            ta = page.locator('[data-form-field-id], [class*="form-item"], [class*="formily-item"]').filter(
                has_text=re.compile(r'self-?introduction', re.I)).locator('textarea')
            if ta.count():
                ml = ta.first.evaluate('e => e.maxLength')
                ta.first.fill(job['cover_text'][:ml] if ml and ml > 0 else job['cover_text'])
                done.append('Self-introduction (this role\'s cover letter)')
        except Exception:
            pass
    d, unknown = formfill.fill(page.main_frame, dict(job, pdf=None))
    return outcome(done + d, unknown, page)
