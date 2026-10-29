"""LinkedIn job links (linkedin.com/jobs/view/<id>, or ?currentJobId=<id> search/collection URLs).

- External apply ("Apply" -> company site): follow it and hand the company page to the matching adapter.
  Guests (apply Chrome not signed in to LinkedIn) can't see the external URL, so we try to find the same posting
  on the company's own ATS through public APIs (Workday tenants from bigtech.BIG_WD, Greenhouse/Lever/Ashby slugs).
- Easy Apply (signed in only): walk the modal with Next/Review, fill from the Answer Bank via formfill, upload the
  tailored PDF if no resume is selected, and STOP when the button turns into "Submit application" (never clicked).
- Never signs in, never types passwords, never solves challenges, never submits. Unknown questions (e.g. "years of
  experience with X") are left blank -> NEEDS YOU.
"""
import json, re, urllib.parse, urllib.request
import config  # noqa: F401  (sets SSL_CERT_FILE for urllib)
import formfill
from ats.common import captcha, outcome, result, body_text

MATCH = r'linkedin\.com/jobs'
UA = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 Chrome/126 Safari/537.36',
      'Accept': 'application/json', 'Content-Type': 'application/json', 'website-path': 'tiktok'}
# Oct 12 2026 LinkedIn moved Easy Apply into a native <dialog data-testid="dialog"> with hashed class names
DIALOG = 'dialog[open], [data-testid="dialog"], div[role="dialog"].jobs-easy-apply-modal, .jobs-easy-apply-modal, div[role="dialog"]'
SUBMIT_RX = re.compile(r'submit application|^\s*submit\s*$', re.I)
NEXT_RX = re.compile(r'^\s*(next|continue|review|review your application|continue to next step)\s*$', re.I)


# ---------------- helpers ----------------
LIMIT_RX = re.compile(r"exceeded the daily application limit|reached (the|your) daily (application )?limit|limit daily submissions", re.I)


def _jitter(page, lo=1500, hi=3500):
    """Random human-like pause instead of a fixed wait (anti-ban idea from wodsuz / beatwad bots)."""
    import random
    page.wait_for_timeout(random.randint(lo, hi))


def mark_daily_limit():
    """LinkedIn said the Easy Apply daily limit is reached: apply_prep skips LinkedIn until tomorrow (GodsScion idea)."""
    import datetime as dt, config
    (config.DATA / 'linkedin_limit_day.txt').write_text(dt.date.today().isoformat())


def limited_today():
    import datetime as dt, config
    try: return (config.DATA / 'linkedin_limit_day.txt').read_text().strip() == dt.date.today().isoformat()
    except Exception: return False


def job_id(url):
    m = re.search(r'/jobs/view/(?:[\w%-]*-)?(\d{6,})', url) or re.search(r'currentJobId=(\d{6,})', url)
    return m and m.group(1)


def signed_in(page):
    """Session cookie is the reliable signal (LinkedIn renames its nav elements often)."""
    try:
        if any(c['name'] == 'li_at' and c.get('value') for c in page.context.cookies('https://www.linkedin.com')):
            return True
        if page.locator('a[data-tracking-control-name="public_jobs_nav-header-signin"]').count(): return False
        return bool(page.locator('#global-nav, .global-nav, header.global-nav').count())
    except Exception:
        return False


def _norm(s):
    return re.sub(r'[^a-z0-9]', '', (s or '').lower())


def _title_match(a, b):
    a, b = _norm(a), _norm(b)
    return bool(a and b and (a == b or (len(a) > 12 and (a in b or b in a))))


def _get_json(url, data=None, timeout=20):
    r = urllib.request.Request(url, data=json.dumps(data).encode() if data else None, headers=UA, method='POST' if data else 'GET')
    with urllib.request.urlopen(r, timeout=timeout) as f:
        return json.loads(f.read())


def page_company_title(page):
    """Company + title from the LinkedIn top card (guest or signed-in)."""
    try:
        return page.evaluate("""() => {
          const t = s => (document.querySelector(s)?.innerText || '').trim();
          return [t('.topcard__org-name-link, .job-details-jobs-unified-top-card__company-name, .topcard__flavor a'),
                  t('h1.top-card-layout__title, h1.topcard__title, .job-details-jobs-unified-top-card__job-title h1, h1')]; }""")
    except Exception:
        return ['', '']


def apply_kind(link):
    """'easy' | 'offsite' | None from LinkedIn's public job page (no login): the Apply button is tracked as
    public_jobs_apply-link-onsite (Easy Apply) or ...-offsite (company site)."""
    m = re.search(r'(\d{8,})', re.sub(r'\?.*', '', link or '')) or re.search(r'currentJobId=(\d+)', link or '')
    if not m or 'linkedin.com' not in (link or ''): return None
    try:
        r = urllib.request.Request(f'https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{m.group(1)}', headers=UA)
        with urllib.request.urlopen(r, timeout=20) as f: h = f.read().decode('utf-8', 'ignore')
    except Exception:
        return None
    if 'apply-link-offsite' in h: return 'offsite'
    if 'apply-link-onsite' in h: return 'easy'
    if re.search(r'No longer accepting applications|closed-job', h, re.I): return 'closed'
    return None


def resolve_offsite(company, title):
    """Find the same posting on the company's own ATS without LinkedIn login. Returns a URL or None."""
    if not company or not title: return None
    try:  # Workday tenants of ~75 big employers (bigtech.py)
        import bigtech
        for co, ten, wdn, site in bigtech.BIG_WD:
            if _norm(co) == _norm(company) or (len(_norm(co)) > 3 and _norm(co) in _norm(company)):
                host = f'{ten}.{wdn}.myworkdayjobs.com'
                d = _get_json(f'https://{host}/wday/cxs/{ten}/{site}/jobs',
                              {'appliedFacets': {}, 'limit': 20, 'offset': 0, 'searchText': title})
                for jp in d.get('jobPostings', []):
                    if _title_match(jp.get('title'), title):
                        return f"https://{host}/{site}{jp['externalPath']}"
    except Exception:
        pass
    if re.search(r'tiktok', company, re.I):   # TikTok careers public search (jobright/LinkedIn reposts, Oct 12)
        try:
            d = _get_json('https://api.lifeattiktok.com/api/v1/public/supplier/search/job/posts',
                          {'keyword': title, 'limit': 10, 'offset': 0})
            for j in (d.get('data') or {}).get('job_post_list', []):
                if _title_match(j.get('title'), title): return f"https://lifeattiktok.com/search/{j['id']}"
        except Exception:
            pass
    words = re.findall(r'[a-z0-9]+', company.lower())
    slugs = list(dict.fromkeys([_norm(company), '-'.join(words), words[0] if words else '']))
    for slug in [s for s in slugs if s]:
        for kind in ('greenhouse', 'lever', 'ashby'):
            try:
                if kind == 'greenhouse':
                    for j in _get_json(f'https://boards-api.greenhouse.io/v1/boards/{slug}/jobs').get('jobs', []):
                        if _title_match(j.get('title'), title): return j['absolute_url']
                elif kind == 'lever':
                    for j in _get_json(f'https://api.lever.co/v0/postings/{slug}?mode=json'):
                        if _title_match(j.get('text'), title): return j['hostedUrl']
                else:
                    for j in _get_json(f'https://api.ashbyhq.com/posting-api/job-board/{slug}').get('jobs', []):
                        if _title_match(j.get('title'), title): return j['jobUrl']
            except Exception:
                continue
    return None


def gone(page):
    """The company page is a removed posting: Greenhouse ?error=true, Lever board root / 404, or 'job not found' text."""
    u = page.url
    if re.search(r'greenhouse\.io/.*[?&]error=true', u) or re.search(r'jobs\.lever\.co/[^/?#]+/?(\?|#|$)', u):
        return True
    try: title, head = page.title(), page.inner_text('body', timeout=5000)[:1500]
    except Exception: return False
    return bool(re.search(r'\b404\b|not found', title, re.I) or re.search(
        r'page (you requested )?(was )?not found|job (is )?no longer (available|open)|position has been filled|'
        r'no longer accepting applications|job not found|posting (has been )?closed', head, re.I))


def route(ctx, li_page, company_page, job, how):
    """Hand the company page to its adapter and prefix the reason."""
    import ats
    company_page.wait_for_load_state('domcontentloaded')
    company_page.wait_for_timeout(3000)
    from ats.common import dismiss_cookies
    dismiss_cookies(company_page)
    if re.search(r'linkedin\.com', company_page.url):
        return result('NEEDS YOU', 'LinkedIn: apply link did not leave LinkedIn (tab open)', page=li_page)
    if gone(company_page):   # Oct 25: removed Lever (404 / board root) and Greenhouse (?error=true) jobs came back "0 fields filled"
        return result('NEEDS YOU', f'posting closed (the {how} link leads to a removed job)', page=company_page)
    try:
        html = company_page.content()[:200000]
    except Exception:
        html = ''
    m = ats.detect(company_page.url, html)
    if m.__name__.endswith('.linkedin'):
        m = __import__('ats.generic', fromlist=['run'])
    if not ats.allowed(m) and not m.__name__.endswith('.generic'):   # generic decides itself (it may lead to Greenhouse/Ashby)
        res = ats.skipped(m, company_page)
    else:
        res = m.run(ctx, company_page, job)
    res['reason'] = f"via LinkedIn ({how}) → {m.__name__.split('.')[-1]}: {res.get('reason', '')}"
    import community
    res['via'] = community.apply_via(company_page.url)   # the site LinkedIn's Apply button really led to
    if company_page is not li_page:
        try: li_page.close()
        except Exception: pass
    return res


# ---------------- external apply ----------------
def external_signed_in(ctx, page, job, btn):
    """Click the signed-in 'Apply' button; catch the new tab (also when it opens late), a same-tab redirect, or the
    'Continue to company site' interstitial."""
    before = set(id(x) for x in ctx.pages)
    try:
        btn.click(timeout=8000)
    except Exception:
        pass
    for _ in range(20):                      # up to ~20 s for the company tab
        page.wait_for_timeout(1000)
        dlg = page.locator('div[role="dialog"]')
        try:
            if dlg.count() and dlg.first.is_visible():
                cont = dlg.first.get_by_role('button', name=re.compile(r'^\s*(continue|apply)\b', re.I))
                if cont.count(): cont.first.click(timeout=5000)
        except Exception:
            pass
        new = [x for x in ctx.pages if id(x) not in before and x.url not in ('about:blank', '')]
        if new:
            return route(ctx, page, new[-1], job, 'external apply')
        if 'linkedin.com' not in page.url:
            return route(ctx, page, page, job, 'external apply')
    return result('NEEDS YOU', 'LinkedIn: external Apply did not open the company site (tab open)', page=page)


# ---------------- Easy Apply ----------------
EMPTY_REQUIRED_JS = r"""
(root) => {
  const dlg = root; const out = [];
  const txt = e => (e ? (e.innerText || e.textContent || '') : '').replace(/\s+/g, ' ').trim();
  const lab = el => { if (el.id) { const l = dlg.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) return txt(l); }
    const f = el.closest('fieldset'); if (f && f.querySelector('legend')) return txt(f.querySelector('legend'));
    const c = el.closest('[data-test-form-element], .fb-dash-form-element, .jobs-easy-apply-form-section__grouping');
    return c ? txt(c.querySelector('label, legend, span')) : (el.getAttribute('aria-label') || el.name || ''); };
  const groups = {};
  for (const el of dlg.querySelectorAll('input, textarea, select')) {
    if (!(el.offsetWidth || el.offsetHeight) || ['hidden', 'file', 'search'].includes(el.type)) continue;
    const req = el.required || el.getAttribute('aria-required') === 'true' || (el.type === 'radio' && /\*\s*$/.test(lab(el)));
    if (!req) continue;
    if (el.type === 'radio') { const g = el.name; groups[g] = groups[g] || {label: lab(el), ok: false}; if (el.checked) groups[g].ok = true; continue; }
    if (el.type === 'checkbox') { if (!el.checked) out.push(lab(el)); continue; }
    const v = (el.value || '').trim();
    if (!v || /^select an option$/i.test(v) || (el.tagName === 'SELECT' && /^select/i.test(el.options[el.selectedIndex]?.text || ''))) out.push(lab(el));
  }
  for (const g of Object.values(groups)) if (!g.ok) out.push(g.label);
  for (const e of dlg.querySelectorAll('.artdeco-inline-feedback--error, [data-test-form-element-error-messages]'))
    if (txt(e)) out.push('error: ' + txt(e));
  return [...new Set(out.filter(Boolean))];
}
"""


def _primary(dlg):
    """Footer primary button of the Easy Apply modal + its text."""
    btns = dlg.locator('footer button, .jobs-easy-apply-modal footer button, button.artdeco-button--primary')
    if not btns.count():   # new dialog: no footer/artdeco classes -> any button that says next/review/submit
        btns = dlg.locator('button').filter(has_text=re.compile(r'^\s*(next|continue|review|submit)', re.I))
    for i in range(btns.count() - 1, -1, -1):
        b = btns.nth(i)
        try:
            if b.is_visible():
                return b, ' '.join([(b.inner_text() or ''), b.get_attribute('aria-label') or '']).strip()
        except Exception:
            continue
    return None, ''


def easy_apply(ctx, page, job, btn):
    btn.click(timeout=8000)
    page.wait_for_timeout(2500)
    dlg = page.locator(DIALOG).first
    if not dlg.count():
        return result('NEEDS YOU', 'LinkedIn Easy Apply modal did not open (tab open)', page=page)
    done, unknown = [], []
    for step in range(15):
        if captcha(page): return result('NEEDS YOU', 'LinkedIn: security check / CAPTCHA (tab open)', done, unknown, page)
        # resume step: upload the tailored PDF when no resume is selected
        # new dialog (Oct 2026): "Upload resume" button opens a file chooser; the upload becomes the first, selected card
        try:
            up = dlg.get_by_role('button', name=re.compile(r'^\s*upload resume', re.I))
            if job.get('pdf') and 'Resume (uploaded)' not in done and up.count() and up.first.is_visible():
                with page.expect_file_chooser(timeout=10000) as fc:
                    up.first.click()
                fc.value.set_files(job['pdf']); page.wait_for_timeout(5000)
                card = dlg.locator('[role="radio"][aria-label$=".pdf" i]').first
                if card.count() and card.get_attribute('aria-checked') != 'true': card.click(); page.wait_for_timeout(800)
                done.append('Resume (uploaded)')
        except Exception as e:
            unknown.append(f'LinkedIn resume upload failed: {str(e)[:60]}')
        try:
            selected = dlg.locator('.jobs-document-upload-redesign-card__container--selected, input[type="radio"][id*="resume" i]:checked, [role="radio"][aria-label$=".pdf" i][aria-checked="true"]')
            fileinp = dlg.locator('input[type="file"]')
            if fileinp.count() and not selected.count() and job.get('pdf'):
                fileinp.first.set_input_files(job['pdf']); page.wait_for_timeout(2500); done.append('Resume (uploaded)')
        except Exception:
            pass
        try: dlg.evaluate("e => e.setAttribute('data-jh-root', '')")   # fill/log only the dialog (Oct 26: the log held the page's follow/alert boxes)
        except Exception: pass
        d, u = formfill.fill(page.main_frame, dict(job, pdf=None))   # never re-upload via generic file inputs
        try: dlg.evaluate("e => e.removeAttribute('data-jh-root')")
        except Exception: pass
        done += [x for x in d if x not in done]
        legal = [x for x in u if 'legal/consent' in x]
        try:
            empty = dlg.evaluate(EMPTY_REQUIRED_JS)
        except Exception:
            empty = u
        step_unknown = list(dict.fromkeys(legal + empty))
        b, label = _primary(dlg)
        if b is None:
            return outcome(done, unknown + step_unknown + ['LinkedIn modal: no Next/Submit button found'], page)
        if SUBMIT_RX.search(label):
            try:   # GodsScion idea: don't auto-follow every company (profile.json linkedin_follow_companies, default false)
                import applicant as P
                if not P.get('linkedin_follow_companies', default=False):
                    fc_box = dlg.locator('#follow-company-checkbox')
                    if fc_box.count() and fc_box.first.is_checked():
                        dlg.locator('label[for="follow-company-checkbox"]').first.click(); done.append('Follow company: unchecked')
            except Exception:
                pass
            # final step: stop BEFORE "Submit application" -- never clicked. Without our upload LinkedIn would send the
            # old resume saved on the profile, so that must stay with him (says so plainly instead of "only N fields")
            if job.get('pdf') and not any(str(x).startswith('Resume') for x in done):
                step_unknown = step_unknown + ['Easy Apply: tailored resume not uploaded (LinkedIn would send the saved one)']
            return outcome(done, unknown + step_unknown, page, 'Easy Apply filled, stopped before "Submit application" (modal open)')
        if step_unknown:
            return outcome(done, unknown + step_unknown, page, f'(Easy Apply step {step + 1}, modal open)')
        if not NEXT_RX.search(label) and not re.search(r'next|continue|review', label, re.I):
            return outcome(done, unknown + [f'LinkedIn modal: unexpected button "{label[:40]}"'], page)
        try:
            before = dlg.inner_text()[:3000]
        except Exception:
            before = ''
        _jitter(page, 600, 1800)   # human pacing before clicking Next
        b.click(timeout=8000)
        _jitter(page, 1800, 3500)
        try:
            if LIMIT_RX.search(page.locator('body').inner_text()[:20000]):
                mark_daily_limit()
                return outcome(done, unknown + ['LinkedIn: daily Easy Apply limit reached (LinkedIn skipped until tomorrow)'], page)
        except Exception:
            pass
        try:
            after = dlg.inner_text()[:3000]
        except Exception:
            after = ''
        if after == before:
            errs = dlg.evaluate(EMPTY_REQUIRED_JS) if dlg.count() else []
            return outcome(done, unknown + (errs or ['LinkedIn modal did not advance']), page, '(modal open)')
    return outcome(done, unknown + ['LinkedIn modal: too many steps'], page)


# ---------------- entry point ----------------
def run(ctx, page, job):
    jid = job_id(page.url) or job_id(job.get('url', ''))
    if jid and '/jobs/view/' not in page.url:
        page.goto(f'https://www.linkedin.com/jobs/view/{jid}/', wait_until='domcontentloaded', timeout=60000)
        page.wait_for_timeout(2500)
    if captcha(page): return result('NEEDS YOU', 'LinkedIn: security check / CAPTCHA (tab open)', page=page)
    html = ''
    try: html = page.content()
    except Exception: pass
    if re.search(r'no longer accepting applications|not currently accepting applications|this job is closed', html, re.I):
        return result('NEEDS YOU', 'LinkedIn: no longer accepting applications (posting CLOSED)', page=page)

    if signed_in(page):
        _jitter(page, 1200, 2600)
        m = re.search(r'applied (\d+) (minute|hour|day|week|month)s? ago', body_text(page, 8000), re.I)   # beatwad idea
        if m:
            return result('NEEDS YOU', f'LinkedIn shows you already applied {m.group(1)} {m.group(2)}(s) ago — set Status = Applied', page=page)
        if limited_today():
            return result('NEEDS YOU', 'LinkedIn daily Easy Apply limit reached today — retried tomorrow', page=page)
        easy = page.locator('button.jobs-apply-button[aria-label*="Easy Apply" i], button[aria-label*="Easy Apply" i]')
        if not (easy.count() and easy.first.is_visible()):
            easy = page.get_by_role('button', name=re.compile(r'^\s*easy apply', re.I))
        if easy.count() and easy.first.is_visible():
            import ats
            if not ats.allowed(__import__('ats.linkedin', fromlist=['run'])):
                res = ats.skipped(__import__('ats.linkedin', fromlist=['run']), page)   # Easy Apply is LinkedIn's own form
            else:
                res = easy_apply(ctx, page, job, easy.first)
            res['via'] = 'LinkedIn Easy Apply'   # seen: the posting has LinkedIn's own Easy Apply button
            return res
        ext = page.locator('button.jobs-apply-button, a.jobs-apply-button, button[aria-label^="Apply" i]')
        if not (ext.count() and ext.first.is_visible()):
            ext = page.get_by_role('button', name=re.compile(r'^\s*apply(\s+on company (web)?site)?\s*$', re.I))
        if not (ext.count() and ext.first.is_visible()):
            ext = page.get_by_role('link', name=re.compile(r'^\s*apply(\s+on company (web)?site)?\s*$', re.I))
        if ext.count() and ext.first.is_visible():
            return external_signed_in(ctx, page, job, ext.first)
        if re.search(r'applied \d|application submitted|you applied', body_text(page, 8000), re.I):
            return result('NEEDS YOU', 'LinkedIn shows you already applied — set Status = Applied', page=page)
        return result('NEEDS YOU', 'LinkedIn: no Apply button found (tab open)', page=page)

    # guest: external-apply jobs are marked "apply-link-offsite"; the URL itself needs login
    offsite = 'apply-link-offsite' in html
    if offsite:
        company, title = page_company_title(page)
        url = resolve_offsite(company or job.get('company', ''), title or job.get('title', ''))
        if url:
            newp = ctx.new_page()
            newp.goto(url, wait_until='domcontentloaded', timeout=60000)
            return route(ctx, page, newp, job, 'matched on company site')
        return result('NEEDS YOU', 'LinkedIn: sign in to LinkedIn in the apply Chrome window (external apply link is '
                                   'hidden for guests; company posting not found automatically) (tab open)', page=page)
    return result('NEEDS YOU', 'LinkedIn: sign in to LinkedIn in the apply Chrome window to use Easy Apply (tab open)', page=page)
