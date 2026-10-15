"""iCIMS (careers-<co>.icims.com/jobs/<id>/<slug>/job, often inside an iframe on the company site).
Flow: "Apply for this job online" -> login page: email (+ privacy/terms checkbox) -> password / create account ->
profile + questions wizard -> Submit. We may type the email; the consent box, password/account step and any
code are his -> NEEDS YOU. If already signed in, the wizard steps are filled with safe_next (never the final Submit)."""
import applicant as P
import re
import formfill, config
from ats import _guard
from ats.common import captcha, needs_login, outcome, result, click_text, safe_next, body_text, dismiss_cookies, first_visible

MATCH = r'icims\.com'
HTML_MATCH = r'icims\.com/jobs/\d+'

EMAIL = getattr(config, 'LOGIN_EMAIL', P.EMAIL)


def _icims_frame(page):
    """The content lives in the in_iframe=1 frame (the outer icims.com page is only a wrapper)."""
    frs = [fr for fr in page.frames if 'icims.com' in (fr.url or '')]
    for fr in frs:
        if 'in_iframe=1' in fr.url: return fr
    return frs[-1] if frs else page.main_frame


def run(ctx, page, job):
    fr = _icims_frame(page)
    if '/login' not in fr.url and not re.search(r'mode=apply|/candidate', fr.url):
        n = len(ctx.pages)
        btn = fr.get_by_role('link', name=re.compile(r'apply for this job|apply now|^\s*apply\s*$', re.I))
        if not btn.count(): btn = fr.get_by_role('button', name=re.compile(r'apply for this job|apply now|^\s*apply\s*$', re.I))
        dismiss_cookies(page)
        b1 = first_visible(btn)
        if not b1: return result('NEEDS YOU', 'iCIMS: no Apply button found (tab open)', page=page)
        try: b1.click(timeout=8000)
        except Exception: b1.click(timeout=8000, force=True)
        page.wait_for_timeout(4000)
        if len(ctx.pages) > n: page = ctx.pages[-1]; page.wait_for_load_state('domcontentloaded'); page.wait_for_timeout(2500)
        fr = _icims_frame(page)
    if captcha(page): return result('NEEDS YOU', 'iCIMS: CAPTCHA (tab open)', page=page)
    pw = fr.locator('input[type="password"]')
    if pw.count() and pw.first.is_visible():
        return result('NEEDS YOU', 'iCIMS: sign in or create account (tab open)', page=page)
    em = fr.locator('input[type="email"], input#email, input[name*="email" i]')
    try: t = fr.inner_text('body')[:6000]
    except Exception: t = body_text(page, 6000)
    if em.count() and em.first.is_visible() and ('/login' in fr.url or re.search(r'log ?in|sign ?in|email address|get started', t, re.I)) \
            and not fr.locator('input[name*="firstname" i], input[id*="FirstName" i]').count():
        if not em.first.input_value(): em.first.fill(EMAIL)
        import os, auth
        box = fr.locator('input[type="checkbox"]:visible')
        if (box.count() and os.environ.get('AUTO_CONSENT') != '1') or os.environ.get('AUTO_LOGIN') != '1' or job.get('dry'):
            need = (['tick the privacy/terms box (legal/consent — yours)'] if box.count() else []) + \
                   ['click Next / Log In, then sign in or create your iCIMS account']
            return result('NEEDS YOU', 'iCIMS: email entered — ' + '; '.join(need) + ' (tab open)', ['Email'], need, page)
        # phone block on the iCIMS login page (country code select + number)
        try:
            cc = fr.locator('select[name*="country" i], select[id*="CountryCode" i], select[id*="phoneCountry" i]')
            if cc.count() and cc.first.is_visible():
                opts = [o.strip() for o in cc.first.locator('option').all_inner_texts()]
                pick = next((o for o in opts if re.search(r'united states|\(\+1\)|^\+?1\b|^us\b', o, re.I)
                             and not re.search(r'minor|samoa|virgin', o, re.I)), None)
                if pick: cc.first.select_option(label=pick)
            dd = fr.locator('a#dropdown[role="combobox"], [role="combobox"][aria-label*="Country Code" i]')
            if dd.count() and dd.first.is_visible() and re.search(r'make a selection', dd.first.inner_text(), re.I):
                dd.first.click(timeout=5000); fr.wait_for_timeout(600)
                srch = fr.locator('input#countryCode, input[name="countryCodeSearch"]')
                if srch.count():
                    try: srch.first.fill('United States', timeout=3000)
                    except Exception: fr.page.keyboard.type('United States', delay=30)
                    fr.wait_for_timeout(1200)
                items = fr.locator('#dropdownResults li, [role="option"]')
                texts = [t.strip() for t in items.all_inner_texts()]
                i = next((k for k, t in enumerate(texts) if re.search(r'united states', t, re.I)
                          and not re.search(r'minor|outlying|virgin|samoa', t, re.I)), None)
                if i is not None: items.nth(i).click(timeout=5000); fr.wait_for_timeout(500)
            ph = fr.locator('input#css_phoneNumber, input[name*="phone" i]:not([type="hidden"])')
            if ph.count() and ph.first.is_visible() and not ph.first.input_value(): ph.first.fill(re.sub(r'\D', '', P.PHONE))
        except Exception:
            pass
        for i in range(box.count()):                       # AUTO_CONSENT=1: privacy/terms
            try: box.nth(i).check(force=True)
            except Exception: pass
        sb = fr.locator('#enterEmailSubmitButton, input[type="submit"]:visible, button[type="submit"]:visible')
        if sb.count():
            sb.first.click(timeout=8000)
        elif not click_text(fr, r'^\s*(next|log ?in|continue|submit)\s*$'):
            em.first.press('Enter')
        page.wait_for_timeout(4000); fr = _icims_frame(page)
        if captcha(page):   # hCaptcha image challenge after Next: never solved automatically
            return result('NEEDS YOU', 'iCIMS: CAPTCHA after the first page — solve it, the form is filled (tab open)',
                          ['Email', 'Phone', 'Privacy (consent)'], ['CAPTCHA'], page)
        if fr.locator('input[type="password"]:visible').count():
            from urllib.parse import urlparse
            ok, why = auth.get_through_wall(ctx, fr, urlparse(page.url).hostname)
            if not ok: return result('NEEDS YOU', f'iCIMS: {why} (tab open)', ['Email'], [why], page)
            page.wait_for_timeout(3000); fr = _icims_frame(page)
    done, unknown = [], []
    for step in range(8):          # signed in: profile / questions / EEO steps
        d, u = formfill.fill(fr, job if step == 0 else dict(job, pdf=None))
        d, u = _guard.check(fr, d, u)
        done += d; unknown += [x for x in u if x not in unknown]
        if unknown: break
        before = body_text(page, 2000)
        if not safe_next(fr if fr is not page.main_frame else page): break
        page.wait_for_timeout(3500); fr = _icims_frame(page)
        if body_text(page, 2000) == before: unknown.append('iCIMS: a required field on this step was not accepted'); break
    if re.search(r'e-?signature|electronic signature', body_text(page), re.I): unknown.append('e-Signature (yours)')
    if not job.get('pdf'): unknown.append('resume PDF missing on the Notion page')
    return outcome(done, unknown, page)
