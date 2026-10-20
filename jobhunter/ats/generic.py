"""Single-page application forms (and the fallback for unknown sites)."""
import re
import formfill
from ats.common import captcha, needs_login, outcome, result, click_text

MATCH = r'^$'


def run(ctx, page, job):
    if captcha(page): return result('NEEDS YOU', 'CAPTCHA', page=page)
    # job page with an Apply button that reveals/opens the form
    # (no resume upload yet = not the form; a lone email box is usually a job-alert signup -- Enlyte Oct 12)
    if not page.locator('input[type="file"]').count():
        start = page.url
        # notice popups (Enlyte scam warning, Oct 12) sit over the Apply button: Escape, then a close button inside a modal
        try:
            page.keyboard.press('Escape'); page.wait_for_timeout(500)
            x = page.locator('[role="dialog"] [aria-label*="close" i]:visible, .modal [aria-label*="close" i]:visible, '
                             '[role="dialog"] button:has-text("×"):visible, .modal button.close:visible, [class*="modal" i] [class*="close" i]:visible')
            if x.count(): x.first.click(timeout=3000); page.wait_for_timeout(800)
        except Exception:
            pass
        # an "Apply" link with a real URL (often straight to the ATS, e.g. Enlyte -> iCIMS): just open it
        try:
            href = page.evaluate("""() => { const a = [...document.querySelectorAll('a[href^="http"]')].find(e => e.offsetWidth
                && /^\\s*apply\\b/i.test(e.innerText || '') && !/linkedin|indeed|glassdoor|facebook|twitter/i.test(new URL(e.href).hostname));
                return a ? a.href : null; }""")
        except Exception:
            href = None
        if href and href.split('#')[0] != page.url.split('#')[0]:
            page.goto(href, wait_until='domcontentloaded', timeout=60000); page.wait_for_timeout(3000)
        for rx in (() if page.url != start else (r'^apply( now| for this (job|position|role))?$', r'^apply\b', r'start (your )?application', r'i.?m interested')):
            if click_text(page, rx) or click_text(page, rx, role='link'):
                page.wait_for_timeout(4000)
                if len(ctx.pages) and ctx.pages[-1] is not page and ctx.pages[-1].url not in ('about:blank', ''):
                    page = ctx.pages[-1]; page.wait_for_load_state('domcontentloaded'); page.wait_for_timeout(2000)
                break
        if page.url != start:     # Apply went to a real ATS (iCIMS, Workday, ...) -> its own adapter
            import ats
            try: html = page.content()[:200000]
            except Exception: html = ''
            m = ats.detect(page.url, html)
            if not m.__name__.endswith('.generic'):
                if not ats.allowed(m): return ats.skipped(m, page)
                res = m.run(ctx, page, job); res['reason'] = f"via {m.__name__.split('.')[-1]}: {res.get('reason', '')}"
                return res
    import ats, sys
    if not ats.allowed(sys.modules[__name__]) and not re.search(r'greenhouse\.io|ashbyhq\.com', formfill.form_frame(page).url or ''):
        return ats.skipped(sys.modules[__name__], page)   # APPLY_ATS_ONLY: the company's own form is not on the list
    if needs_login(page): return result('NEEDS YOU', 'sign in / create account (tab open)', page=page)
    if captcha(page): return result('NEEDS YOU', 'CAPTCHA', page=page)
    frame = formfill.form_frame(page)
    if not frame.locator('input, textarea, select').count():
        return result('NEEDS YOU', 'no application form found on this page (tab open)', page=page)
    done, unknown = formfill.fill(frame, job)
    if not job.get('pdf'): unknown.append('resume PDF missing on the Notion page')
    return outcome(done, unknown, page)
