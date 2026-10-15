"""Job Apply Prep, local edition (Playwright, 0 LLM tokens). Same rules as the cloud task
(reference/schedules/Job_Apply_Prep*.md): fill forms in the apply Chrome window so he only reviews and clicks Submit.

Picks Notion rows: Status "To apply", Notes contain FAST and not FILLED, created in the last 48h (newest, highest fit
first) + any row whose Notes contain "FILL NOW". Each link is routed to an ATS adapter in jobhunter/ats/
(Workday, LinkedIn, Greenhouse, Lever, Ashby, SmartRecruiters, iCIMS, Oracle, Eightfold, Taleo, Jobvite, Workable,
SuccessFactors, generic). Adapters stop on the review page BEFORE the final Submit and leave the tab open.

  python apply_prep.py                                   # normal run (Notion rows)
  python apply_prep.py --url URL [--page NOTION_PAGE_ID] [--pdf resume.pdf] --dry   # one form (testing)
"""
import applicant as P
import argparse, datetime as dt, json, os, re, tempfile, time, traceback
from zoneinfo import ZoneInfo
import config, formfill, notion, notion_pages, notify
import ats
from ats.common import result

CT = ZoneInfo('America/Chicago')
FILES = config.DATA / 'apply_files'; FILES.mkdir(exist_ok=True)


def rows_to_fill():
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=48)).isoformat()
    fast = notion.query({'and': [{'property': 'Status', 'select': {'equals': 'To apply'}},
                                 {'property': 'Notes', 'rich_text': {'contains': 'FAST'}},
                                 {'property': 'Notes', 'rich_text': {'does_not_contain': 'FILLED'}},
                                 {'timestamp': 'created_time', 'created_time': {'on_or_after': since}}]})
    now = notion.query({'property': 'Notes', 'rich_text': {'contains': 'FILL NOW'}})
    fit = lambda r: int((re.search(r'fit (\d+)', r.get('Notes') or '') or [0, 0])[1])
    fast.sort(key=lambda r: (r['created'][:13], fit(r)), reverse=True)
    done_states = {'Applied', 'Closed', 'Rejected', 'OA', 'Interview', 'Offer', 'Ghosted'}
    tried = attempts()
    cutoff = time.time() - RETRY_HOURS * 3600
    fails = recent_fails()
    seen, out = set(), []
    # owner, Oct 12: automate only Greenhouse / Ashby / Workday (+ LinkedIn/jobright rows, which often lead there);
    # every other site is left for him / Claude in the browser instead of burning runs on it
    focus = re.compile(os.environ.get('APPLY_FOCUS', r'greenhouse\.io|gh_jid=|ashbyhq\.com|myworkdayjobs\.com|myworkdaysite\.com|linkedin\.com/jobs|jobright\.ai'), re.I)
    # fresh FAST rows first: Oct 12 the 8 slots went to the same failing FILL NOW rows every hour and new rows waited
    for r in fast + now:
        if r['id'] in seen or r.get('Status') in done_states: continue
        if not focus.search(r.get('Link') or ''): continue
        if tried.get(r['id'], 0) > cutoff: continue          # back off: tried within the last RETRY_HOURS
        if fails.get(r['id'], 0) >= 2 and tried.get(r['id'], 0) > time.time() - 12 * 3600: continue   # keeps failing: 2x/day
        seen.add(r['id']); out.append(r)
    return out


RETRY_HOURS = float(os.environ.get('RETRY_HOURS', '0.9'))   # while sites are being fixed: retry every hourly run
ATTEMPTS = config.DATA / 'apply_attempts.json'


def attempts():
    try:
        return json.loads(ATTEMPTS.read_text())
    except Exception:
        return {}


def posting_key(url):
    """Stable id of an application posting: ATS + job id (Greenhouse/Ashby/Lever/Workday/SmartRecruiters), else the path."""
    u = (url or '').lower()
    for rx in (r'greenhouse\.io/(?:embed/job_app\?for=)?([\w-]+)/jobs/(\d+)', r'gh_jid=(\d+)()', r'ashbyhq\.com/([^/]+)/([0-9a-f-]{36})',
               r'lever\.co/([^/]+)/([0-9a-f-]{36})', r'([\w-]+)\.wd\d+\.myworkdayjobs\.com/.*?/job/(?:[^/]+/)?([^/?#]+)',
               r'smartrecruiters\.com/([^/]+)/(\d+)'):
        m = re.search(rx, u)
        if m: return ':'.join(x for x in m.groups() if x)
    return re.sub(r'(/apply.*|/application|/confirmation|\?.*|#.*)$', '', u)


def already_submitted(url):
    """'<date> as <company>' if this posting is in data/submissions.jsonl, else None."""
    k = posting_key(url)
    if not k: return None
    try:
        for line in (config.DATA / 'submissions.jsonl').read_text().splitlines():
            try: d = json.loads(line)
            except Exception: continue
            if d.get('url') and posting_key(d['url']) == k: return f"{d.get('at', '')[:16]} as {d.get('company')}"
    except FileNotFoundError:
        pass
    global _DONE_KEYS
    if _DONE_KEYS is None:          # applied before submissions.jsonl existed: Notion rows already past "To apply"
        _DONE_KEYS = {}
        try:
            for st in ('Applied', 'OA', 'Interview', 'Offer', 'Rejected', 'Closed'):
                for r in notion.query({'property': 'Status', 'select': {'equals': st}}):
                    if r.get('Link'): _DONE_KEYS.setdefault(posting_key(r['Link']), f"(Notion: {r.get('Company')}, {st})")
        except Exception:
            pass
    return _DONE_KEYS.get(k)


_DONE_KEYS = None

_CONFIRM_SCHEMA = {'type': 'object', 'required': ['found'], 'properties': {
    'found': {'type': 'boolean'}, 'subject': {'type': ['string', 'null']}, 'date': {'type': ['string', 'null']}}}


def emailed_confirmation(r):
    """Rows older than 48 h may have been submitted by hand (cloud Apply Prep left them 'FILLED – click Submit').
    One cheap read-only Gmail lookup: is there already an application confirmation for this company + role?"""
    try:
        created = dt.datetime.fromisoformat((r.get('created') or '').replace('Z', '+00:00'))
        if created > dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=48): return None
    except Exception:
        pass
    company, role = (r.get('Company') or '').split('(')[0].strip(), r.get('Position') or ''
    if not company: return None
    import auth, manager
    try:
        out = manager.agent(
            f"Use search_threads with query: newer_than:60d \"{company}\" (\"thank you for applying\" OR \"thanks for applying\" OR "
            f"\"application received\" OR \"received your application\" OR \"application has been received\"). "
            f"found=true if ANY of the results is an application confirmation from {company} (any role; if it names a role, put "
            f"it in subject). We are about to apply for \"{role}\".",
            auth._GMAIL_TOOLS + ['ToolSearch'], dict(_CONFIRM_SCHEMA, title='confirm'), model='haiku', budget_usd=0.06, timeout=180,
            system='Read-only Gmail lookup. Load Gmail tools with ToolSearch (select:<names>). Never send, draft or delete. '
                   'Finish with only the JSON object.')
        if out.get('found'): return f"possibly by hand — {company} confirmation email {out.get('date') or ''}: {(out.get('subject') or '')[:70]}; check before re-applying"
    except Exception as e:
        print('confirmation lookup failed:', str(e)[:120])
    return None


def confirmed_by_email(r, wait_s=90):
    """After a Submit click with no on-page confirmation: confirmation emails arrive 2 s - 2 min later. One cheap
    read-only Gmail lookup decides SUBMITTED vs. still unknown (Oct 12-13: 15 rows sat in "Needs you" this way)."""
    company = (r.get('Company') or '').split('(')[0].strip()
    if not company: return None
    time.sleep(wait_s)
    import auth, manager
    try:
        out = manager.agent(
            f"Run TWO searches with search_threads: (1) newer_than:2h \"{company}\" and (2) newer_than:2h from:myworkday.com. "
            f"found=true if ANY result from the last 2 hours is an application confirmation from {company} — e.g. subjects/snippets like "
            f"'Thank you for applying', 'Thank You for Applying', 'Thank you for your online submission', 'We have received your "
            f"application', 'Application received', 'Your application has been submitted', 'Thank you for your interest in <company>' "
            f"(also when the sender is <tenant>@myworkday.com and the body names {company}). Put its subject in subject.",
            auth._GMAIL_TOOLS + ['ToolSearch'], dict(_CONFIRM_SCHEMA, title='confirm'), model='haiku', budget_usd=0.06, timeout=180,
            system='Read-only Gmail lookup. Load Gmail tools with ToolSearch (select:<names>). Never send, draft or delete. '
                   'Finish with only the JSON object.')
        if out.get('found'): return f"confirmed by email: {(out.get('subject') or '')[:80]}"
    except Exception as e:
        print('confirmation lookup failed:', str(e)[:120])
    return None


def recent_fails(hours=48):
    """Row id -> number of non-submitted outcomes in the last `hours` (data/apply_results.jsonl)."""
    out, since = {}, dt.datetime.now(CT) - dt.timedelta(hours=hours)
    try:
        for line in (config.DATA / 'apply_results.jsonl').read_text().splitlines():
            try:
                d = json.loads(line)
                if d.get('id') and d['status'] != 'SUBMITTED' and dt.datetime.fromisoformat(d['at']) >= since:
                    out[d['id']] = out.get(d['id'], 0) + 1
            except Exception:
                continue
    except FileNotFoundError:
        pass
    return out


def mark_attempt(page_id):
    a = attempts(); a[page_id] = time.time()
    ATTEMPTS.write_text(json.dumps(a))


def job_files(r, pdf_override=None):
    """Download the row's tailored resume + write the cover letter to a .txt for upload fields."""
    info = notion_pages.read(r['id']) if r.get('id') else {}
    d = tempfile.mkdtemp(dir=FILES)
    pdf = pdf_override or (notion_pages.download(info['pdf'], os.path.join(d, P.file('Resume', 'pdf'))) if info.get('pdf') else None)
    docx = notion_pages.download(info['docx'], os.path.join(d, P.file('Resume', 'docx'))) if info.get('docx') else None
    cover = info.get('cover_letter') or ''
    cfile = None
    if cover:  # PDF is accepted everywhere (Workday/iCIMS reject .txt); fall back to .txt if rendering fails
        try:
            import coverletter
            cfile = coverletter.to_pdf(cover, d)
        except Exception:
            cfile = os.path.join(d, P.file('Cover_Letter', 'txt')); open(cfile, 'w').write(cover)
    return dict(pdf=pdf, docx=docx, cover_txt=cfile, cover_text=cover)


def looks_like_login(page):
    """Sign-in / sign-up wall: login wording + account wording, and no resume upload on the page."""
    try:
        t = page.inner_text('body')[:12000]
        has_file = page.locator('input[type="file"]').count() > 0
    except Exception:
        return False
    return (not has_file and bool(re.search(r'\bsign ?in\b|\blog ?in\b', t, re.I))
            and bool(re.search(r'create (an )?account|first time here|forgot (your )?password|new user|register', t, re.I)))


SUBMIT_REQ = re.compile(r'submit|/applications?(/|\?|$)|apply(Job|ToJob|Now)?\b|/jobs/\d+/?(\?|$)', re.I)


GUARD_HOSTS = re.compile(r'greenhouse\.io|ashbyhq\.com|lever\.co', re.I)


def dry_guard(page, blocked, armed=None):
    """Abort any submit-like POST/PUT at the network layer. --dry: always (Pogo/Ashby Oct 12: a dry run DID submit).
    Live (armed = {'on': False}, single-page ATSs only): blocked until auth.submit's own click arms it -- Oct 12 the
    Neuralink form was posted while it was still being filled (Greenhouse emailed a security code).
    Uploads/autosaves pass; only requests whose URL or JSON body says submit."""
    def h(route, req):
        try:
            if req.method in ('POST', 'PUT') and not (armed and armed.get('on')):
                ct = (req.headers.get('content-type') or '').lower()
                body = (req.post_data or '')[:3000] if ('json' in ct or 'x-www-form' in ct) else ''
                op = re.search(r'[?&]op=(\w+)|"operationName"\s*:\s*"(\w+)"', req.url + ' ' + body)
                what = (op.group(1) or op.group(2)) if op else req.url + ' ' + body   # GraphQL: judge by operation name only
                if re.search(r'/(event|events|track|tracking|log|logs|analytics|telemetry|collect|beacon|metrics?)(/|\?|$)', req.url, re.I):
                    hit = False                 # analytics pings ("jobright.ai/swan/event/submit") are not applications
                elif 'linkedin.com' in req.url:   # LinkedIn SDUI: opening/stepping Easy Apply is a POST to .../EasyApply -- only 'submit' counts
                    hit = bool(re.search(r'submit', req.url + ' ' + body, re.I))
                else:
                    hit = bool((re.search(r'submit', what, re.I) and not (op and re.search(r'consent|texting|upload|file|resume|survey|parse|draft', what, re.I)))
                               or (not op and SUBMIT_REQ.search(req.url) and 'upload' not in req.url.lower()))
                if hit:
                    blocked.append(f"{req.method} {req.url[:150]} {body[:150]}")
                    return route.abort()
            route.continue_()
        except Exception:
            try: route.continue_()
            except Exception: pass
    page.route(lambda u: bool(re.search(r'submit|graphql|/api/|/applications?\b|apply|/jobs/\d+/?(\?|$)', u, re.I))
               and (armed is None or bool(GUARD_HOSTS.search(u)))
               and not re.search(r'upload|recaptcha|\.(js|css|png|svg|jpe?g|woff2?)(\?|$)', u, re.I), h)
    page.add_init_script("""(()=>{window.__jhClicks=[];addEventListener('click',e=>{const t=e.target.closest('button,a,[role=button]')||e.target;
      window.__jhClicks.push((t.tagName||'')+' '+((t.innerText||'').trim().slice(0,40))+' jh='+t.getAttribute('data-jh')+' trusted='+e.isTrusted)},true);
      addEventListener('keydown',e=>{if(e.key==='Enter')window.__jhClicks.push('ENTER in '+e.target.tagName+' '+(e.target.name||e.target.id||''))},true)})()""")


KEEP_TABS = os.environ.get('KEEP_TABS', '1') == '1'   # Oct 13 (owner): leave a stopped form open for him to finish
MAX_KEPT = int(os.environ.get('MAX_KEPT_TABS', '20'))
PROTECT = []   # pages that must survive cleanup: tabs kept for him (this run + earlier runs)


def close_forms(ctx):
    """Close form tabs except one blank tab, the LinkedIn feed and the tabs kept open for him (PROTECT)."""
    try:
        pages = list(ctx.pages)
        if not any(x.url == 'about:blank' for x in pages): ctx.new_page()
        for x in pages:
            if x.url != 'about:blank' and 'linkedin.com/feed' not in x.url and x not in PROTECT: x.close()
    except Exception:
        pass


def keep_tab(ctx, before, r, res):
    """Keep this row's tab(s) open for manual fixing (NEEDS YOU only, max MAX_KEPT). Returns True if kept."""
    if not KEEP_TABS or res.get('status') != 'NEEDS YOU': return False
    if re.search(r'posting closed|no longer|not a .*posting|bad link|already applied', res.get('reason') or '', re.I): return False
    if len([p for p in PROTECT if not p.is_closed()]) >= MAX_KEPT: return False
    new = [p for p in ctx.pages if p not in before and p.url not in ('about:blank', '')]
    if not new: return False
    PROTECT.extend(new)
    with open(config.DATA / 'kept_tabs.jsonl', 'a') as fh:
        fh.write(json.dumps({'at': dt.datetime.now(CT).isoformat(timespec='seconds'), 'id': r.get('id'), 'company': r.get('Company'),
                             'role': r.get('Position'), 'url': new[-1].url, 'why': (res.get('reason') or '')[:300]}) + '\n')
    return True


def thin_ok(res, page):
    """Is a FILLED form really complete? Real applications upload our resume or fill many fields.
    LinkedIn Easy Apply arrives with name/email/phone pre-filled, so it fills only 1-3 fields itself: it counts as
    complete once the adapter reached "Submit application" with nothing missing AND our tailored resume was uploaded.
    (Oct 13: 31 complete Easy Apply forms were sent back to "Needs you" because the check wanted exactly 'Resume'.)"""
    done = res.get('done', [])
    if any(str(x).startswith('Resume') for x in done): return True
    return len(done) >= 6


def run_one(ctx, r, files, dry):
    page = ctx.new_page()
    blocked, armed = [], {'on': False}
    if dry: dry_guard(page, blocked)
    elif GUARD_HOSTS.search(r.get('Link') or ''): dry_guard(page, blocked, armed)
    try:
        from apply_now import workday_open
        if workday_open(r.get('Link') or '') is False:   # removed Workday job: don't walk into its sign-in wall
            res = result('NEEDS YOU', 'posting closed (Workday says the job no longer exists)', page=page); res['ats'] = 'workday'
            return res
    except Exception:
        pass
    page.goto(r['Link'], wait_until='domcontentloaded', timeout=60000)
    page.wait_for_timeout(2500)
    from ats.common import dismiss_cookies
    dismiss_cookies(page)
    if re.search(r'myworkday(jobs|site)\.com', page.url): page.wait_for_timeout(3500)   # SPA: error text renders late
    try: head = page.inner_text('body', timeout=5000)[:1500]
    except Exception: head = ''
    if re.search(r'page (you requested )?(was )?not found|job (is )?no longer (available|open)|position has been filled|'
                 r'no longer accepting applications|job not found|posting (has been )?closed|page you are looking for (doesn.t|does not) exist', head, re.I) \
            or re.search(r'greenhouse\.io/[\w-]+/?\?error=true', page.url):   # Greenhouse: removed job -> board ?error=true
        res = result('NEEDS YOU', 'posting closed (page says the job is gone)', page=page); res['ats'] = '?'
        return res
    try:
        html = page.content()[:200000]
    except Exception:
        html = ''
    adapter = ats.detect(page.url, html)
    job = dict(url=r['Link'], company=r.get('Company'), title=r.get('Position'), dry=dry, **files)
    res = adapter.run(ctx, page, job)
    res['ats'] = adapter.__name__.split('.')[-1]
    if dry:
        try: clicks = (res.get('page') or page).evaluate('window.__jhClicks || []')
        except Exception: clicks = []
        print('DRY clicks:', clicks)
    if blocked:
        print('GUARD blocked submit request(s) during fill:', blocked)
        if dry:   # live: the blocked request never left -> the normal armed submit below still applies
            res.update(status='ERROR', reason=f'a submit request was sent during fill and BLOCKED: {blocked[0][:120]}')
    import auth
    from urllib.parse import urlparse
    pg0 = res.get('page') or page
    # Oct 13 (owner): if the form asks for ANY government ID (SSN, passport/driver's license/state ID number, ID upload,
    # identity verification) the application is cancelled: no rescue, no submit, Notion -> Closed with the reason
    gov = [u for u in (res.get('unknown') or []) if str(u).startswith('GOV-ID asked')] or \
          ([res.get('reason')] if formfill.GOV_ID.search(res.get('reason') or '') else [])
    if gov:
        res.update(status='CANCELLED', reason=f"CANCELLED – the application asks for a government ID: {str(gov[0])[:200]}")
        return res
    # Oct 13: single-page forms (Greenhouse, Ashby, generic...) get one Claude rescue before giving up (rescue.py).
    # Workday rescues inside its own step loop. Walls/CAPTCHA/closed/already-applied are never "fixed".
    if res['status'] == 'NEEDS YOU' and not dry and res.get('ats') != 'workday' and not re.search(
            r'captcha|sign in|create (an )?account|log ?in|closed|no longer|already applied|possibly by hand|verification|'
            r'no application form|no apply button|not a .* posting|site limit|limiting applications', res.get('reason') or '', re.I):
        try:
            import rescue
            ok, log = rescue.fix(pg0, job, [res.get('reason') or ''] + list(res.get('unknown') or []))
            print('rescue:', ok, '|', '; '.join(log)[:400])
            if ok:
                left = formfill.still_empty(formfill.form_frame(pg0))
                if not left:
                    res.update(status='FILLED', reason='rescued by Claude: ' + '; '.join(log)[:200])
                    res['done'] = list(res.get('done') or []) + ['Claude rescue']
                else:
                    res['reason'] = f"after Claude rescue still empty: {'; '.join(left)[:300]}"
        except Exception as e:
            print('rescue failed:', str(e)[:150])
    # guard 1: an adapter that "filled" a sign-in / sign-up page is really at a wall
    if res['status'] == 'FILLED' and looks_like_login(pg0):
        res.update(status='NEEDS YOU', reason='sign in or create account (login page, tab open)')
    # guard 2: never treat a thin form as complete (real applications upload a resume or have many fields)
    if res['status'] == 'FILLED' and not thin_ok(res, pg0):
        res.update(status='NEEDS YOU', reason=f"only {len(res.get('done', []))} field(s) filled — check this form (tab open)")
    # sign-in / account wall -> auth (switch-gated), then run the adapter again on the same tab
    # (never on LinkedIn: that's your personal account -- sign in to it once by hand in the apply window)
    # Decide by where the row ENDED UP (LinkedIn rows usually land on Workday/Eightfold/...), not where it started.
    pg = res.get('page') or page
    if res['status'] == 'NEEDS YOU' and 'linkedin.com' not in pg.url and 'LinkedIn: sign in' not in res.get('reason', '') \
            and re.search(r'sign in|create (an )?account|log ?in', res.get('reason', ''), re.I) and not dry:
        ok, why = auth.get_through_wall(ctx, pg, urlparse(pg.url).hostname)
        if ok:
            pg.wait_for_timeout(2000)
            final = ats.detect(pg.url, pg.content()[:200000])   # the adapter for the site we're actually on
            res = final.run(ctx, pg, job); res['ats'] = final.__name__.split('.')[-1]
            res['reason'] = f"{why}; " + res.get('reason', '')
            if res['status'] == 'FILLED' and not thin_ok(res, pg):
                res.update(status='NEEDS YOU', reason=f"{why}; only {len(res.get('done', []))} field(s) filled — check (tab open)")
        else:
            res['reason'] = f"{res['reason']} [{why}]"
    # final submit (switch-gated; only when every required field is filled)
    if res['status'] == 'FILLED' and not dry:
        # B2: a live submit must carry the row's real tailored resume, never the test file
        if not files.get('pdf') or '/data/test/' in str(files.get('pdf')) or os.path.getsize(files['pdf']) < 45000:
            res.update(status='NEEDS YOU', reason='resume missing or not the tailored one — not submitting (tab open)')
            return res
        res['dry'] = dry
        prior = already_submitted((res.get('page') or page).url)
        if prior:   # same posting reached through another row (EngRadar repost of RF-SMART, Oct 12: 3rd application)
            res.update(status='NEEDS YOU', reason=f"already applied — same posting was submitted {prior} (not submitting again)")
            return res
        maybe = emailed_confirmation(r)
        if maybe:   # old row: he may have applied by hand -> his call (stays Needs you, not Applied)
            res.update(status='NEEDS YOU', reason=f"form filled, NOT submitted: {maybe}")
            return res
        armed['on'] = True                      # only auth.submit's own click may send the application
        res = auth.submit(res.get('page') or page, res)
        if res['status'] != 'SUBMITTED' and 'no confirmation' in (res.get('reason') or ''):
            ok = confirmed_by_email(r)
            if ok: res.update(status='SUBMITTED', reason=ok)
        if res['status'] == 'SUBMITTED':   # B3: audit log of every submission
            import hashlib
            with open(config.DATA / 'submissions.jsonl', 'a') as fh:
                fh.write(json.dumps({'at': dt.datetime.now(CT).isoformat(), 'company': r.get('Company'), 'role': r.get('Position'),
                                     'ats': res.get('ats'), 'url': (res.get('page') or page).url, 'resume_bytes': os.path.getsize(files['pdf']),
                                     'resume_sha1': hashlib.sha1(open(files['pdf'], 'rb').read()).hexdigest()[:12],
                                     'cover_letter': bool(files.get('cover_text'))}) + '\n')
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--url'); ap.add_argument('--page'); ap.add_argument('--pdf'); ap.add_argument('--dry', action='store_true')
    ap.add_argument('--max', type=int, default=15)
    ap.add_argument('--only', help='only rows whose Company contains this text (re-run specific rows)')
    ap.add_argument('--ids', help='comma-separated Notion page ids (exact rows)')
    ap.add_argument('--ats', help='open rows of any age whose Link is on these systems, e.g. greenhouse,ashby,lever')
    a = ap.parse_args()
    from playwright.sync_api import sync_playwright
    import browser
    if not a.url and not config.NOTION_TOKEN:
        print('NOTION_TOKEN is empty in .env -> nothing to read; skipping.'); return
    if a.ids:
        rows = [notion.flat(notion._req('GET', f'/pages/{i.strip()}')) for i in a.ids.split(',') if i.strip()]
    elif a.ats:
        dom = {'greenhouse': 'greenhouse.io', 'ashby': 'ashbyhq.com', 'lever': 'lever.co', 'workday': 'myworkdayjobs.com',
               'smartrecruiters': 'smartrecruiters.com', 'icims': 'icims.com', 'linkedin': 'linkedin.com/jobs'}
        want = [dom.get(x.strip(), x.strip()) for x in a.ats.split(',')]
        tried = attempts(); cutoff = time.time() - RETRY_HOURS * 3600
        rows = [r for r in notion.query({'or': [{'property': 'Status', 'select': {'equals': 'To apply'}},
                                                {'property': 'Status', 'select': {'equals': 'Needs you'}}]})
                if any(w in (r.get('Link') or '') for w in want) and tried.get(r['id'], 0) < cutoff]
        rows.sort(key=lambda r: r.get('created') or '', reverse=True)
        rows = rows[:a.max]
    elif a.only:
        rows = [r for r in notion.rows_for_company(a.only) if r.get('Status') in ('To apply', 'Needs you')][:a.max]
    else:
        rows = [dict(id=a.page, Link=a.url, Company='(test)', Position='', Notes='')] if a.url else rows_to_fill()[:a.max]
    if not rows: print('Nothing to fill.'); return
    # one apply run at a time: two CDP clients on the apply Chrome hang/reset each other (Oct 12 19:40)
    import fcntl
    lock = open(config.DATA / 'apply_prep.lock', 'w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print('another apply_prep run is active -> skipping this run'); return
    results = []
    with sync_playwright() as p:
        b, ctx = browser.connect(p)
        ctx.set_default_timeout(30000)              # no single browser action may hang forever
        # start clean: close tabs left by earlier runs -- except, with KEEP_TABS, the ones kept open for him to finish
        if KEEP_TABS:
            PROTECT.extend(p for p in ctx.pages if p.url not in ('about:blank', ''))
        else:
            for old in list(ctx.pages)[:-1] if len(ctx.pages) > 1 else []:
                try:
                    if 'linkedin.com/feed' not in old.url: old.close()
                except Exception:
                    pass
        li_limited = False
        for r in rows:
            stamp = dt.datetime.now(CT).strftime('%b %d %I:%M %p CT')
            try: before = list(ctx.pages)
            except Exception: before = []
            if li_limited and 'linkedin.com' in (r.get('Link') or ''):
                print(f"skip {r.get('Company')}: LinkedIn is rate-limiting this run (retried next hour)"); continue
            try:                                    # reconnect if the previous row broke the browser connection
                _ = ctx.pages
            except Exception:
                b, ctx = browser.connect(p); ctx.set_default_timeout(30000)
            try:
                files = job_files(r, a.pdf) if (r.get('id') or a.pdf) else dict(pdf=None, docx=None, cover_txt=None, cover_text='')
                res = run_one(ctx, r, files, a.dry)
                if res['status'] == 'ERROR' and re.search(r'timeout|timed out|net::err', res.get('reason') or '', re.I):
                    close_forms(ctx); res = run_one(ctx, r, files, a.dry)   # one retry: slow page loads (21 rows Oct 12-13)
                if a.dry and res.get('page'):
                    # never leave a filled test form open after we detach: on Oct 12 filled tabs were submitted
                    # seconds after the script disconnected (cause not found yet)
                    try: res['page'].close()
                    except Exception: pass
            except Exception as e:
                traceback.print_exc(); res = result('ERROR', str(e)[:200]); res['ats'] = '?'
                if re.search(r'Timeout \d+ms exceeded|timed out|net::ERR', str(e)) and not a.dry:
                    try:   # one retry on a fresh tab for slow loads / slow buttons
                        close_forms(ctx); res = run_one(ctx, r, files, a.dry)
                    except Exception as e2:
                        res = result('ERROR', f'retry failed: {str(e2)[:180]}'); res['ats'] = '?'
                if re.search(r'has been closed|Target closed|Connection closed', str(e)):
                    try:
                        b, ctx = browser.connect(p); ctx.set_default_timeout(30000)
                    except Exception:
                        pass
            if res['status'] == 'ERROR' and 'ERR_HTTP_RESPONSE_CODE_FAILURE' in (res.get('reason') or '') \
                    and 'linkedin.com' in (res.get('reason') or ''):
                li_limited = True   # LinkedIn answered 429: stop hitting it this run; this row is retried next hour
                print('LinkedIn rate limit hit -> skipping the remaining LinkedIn rows this run')
                close_forms(ctx); continue
            results.append((r, res))
            if not a.dry:                           # D2: per-row outcome log for daily metrics (metrics.py)
                try:
                    with open(config.DATA / 'apply_results.jsonl', 'a') as fh:
                        fh.write(json.dumps({'at': dt.datetime.now(CT).isoformat(timespec='seconds'), 'id': r.get('id'),
                                             'company': r.get('Company'), 'ats': res.get('ats'), 'status': res['status'],
                                             'reason': (res.get('reason') or '')[:300]}) + '\n')
                except Exception:
                    pass
            if r.get("id") and not a.dry: mark_attempt(r["id"])
            # remember the real company application URL so next time we skip LinkedIn's flaky Apply hop
            try:
                fin = (res.get('page').url if res.get('page') else '') or ''
                # only a URL that still identifies the job: Oct 13 a login redirect overwrote 11 TikTok rows with the
                # careers home page (careers.tiktokusds.com/usds) and the job link was lost
                job_like = re.search(r'\d{5,}|/jobs?/|/job-|jobid|gh_jid|/position|/requisition|/apply/', fin, re.I) \
                    and not re.search(r'login|sign-?in|signup|/auth|/account|/usds/?$', fin, re.I)
                if r.get('id') and not a.dry and 'linkedin.com' in (r.get('Link') or '') and fin.startswith('http') \
                        and 'linkedin.com' not in fin and res['status'] != 'ERROR' and job_like:
                    notion.update_row(r['id'], notion.props(Link=re.sub(r'/(login|apply/[^?]*)(\?.*)?$', '', fin)))
            except Exception:
                pass
            if r.get('id') and not a.dry and res['status'] != 'ERROR':
                applied = None
                if res['status'] == 'SUBMITTED':
                    note, status = f"APPLIED {stamp} (auto-submitted, {res['ats']}, confirmation seen)", 'Applied'
                    applied = dt.datetime.now(CT).strftime('%Y-%m-%d')
                elif re.search(r'already applied', res.get('reason', ''), re.I):
                    note, status = f"APPLIED – the site says an application already exists ({stamp})", 'Applied'
                elif re.search(r'posting closed|CLOSED\)|no longer accepting|not currently accepting', res.get('reason', ''), re.I):
                    note, status = f"CLOSED – posting no longer accepts applications ({stamp})", 'Closed'
                elif res['status'] == 'CANCELLED':
                    note, status = f"{res['reason'][:600]} ({stamp})", 'Closed'
                elif res['status'] == 'FILLED':
                    note, status = f"FILLED – click Submit ({res['ats']}, tab open, {stamp})", None
                else:
                    note, status = f"NEEDS YOU – {res['reason'][:600]} ({res['ats']}, tab open, {stamp})", 'Needs you'
                note = note.replace('(tab open', '(tab closed — open the Link to finish').replace('tab open', 'tab closed — open the Link to finish')
                upd = notion.props(Notes=(note + ' | ' + (r.get('Notes') or ''))[:1990], Status=status, DateApplied=applied)
                try:   # learned where this job really applies (Easy Apply / external ATS) -> Apply Via column
                    import community
                    hint = res.get('reason') or ''
                    if res.get('ats') == 'linkedin' and 'external apply' not in hint: hint = 'Easy Apply ' + hint
                    upd['Apply Via'] = {'select': {'name': community.apply_via(r.get('Link'), hint)}}
                except Exception:
                    pass
                notion.update_row(r['id'], upd)
            # Close the form tabs after each row (Oct 12: filled forms left open submitted themselves 30 s - 3 min later).
            # Oct 13 (owner): a stopped NEEDS-YOU form stays OPEN for him to fix by hand (KEEP_TABS, max 20) and is
            # listed in data/kept_tabs.jsonl + its Notion note; everything else is closed.
            kept = keep_tab(ctx, before, r, res)
            if kept and r.get('id') and not a.dry:
                try: notion.update_row(r['id'], notion.props(Notes=(f"TAB LEFT OPEN in the apply Chrome for you to finish | NEEDS YOU – "
                                                                    f"{(res.get('reason') or '')[:500]} ({stamp}) | " + (r.get('Notes') or ''))[:1990]))
                except Exception: pass
            close_forms(ctx)
    print('| Company | Role | ATS | Result |\n|---|---|---|---|')
    for r, res in results:
        print(f"| {r.get('Company')} | {r.get('Position')} | {res.get('ats')} | {res['status']}: {res['reason'][:300]} |")
    sent = [r['Company'] for r, res in results if res['status'] == 'SUBMITTED']
    ready = [r['Company'] for r, res in results if res['status'] == 'FILLED']
    need = [f"{r['Company']} ({res['reason'][:40]})" for r, res in results if res['status'] == 'NEEDS YOU']
    if (sent or ready or need) and not a.dry:
        notify.push((f"{len(sent)} submitted: {', '.join(sent)} | " if sent else '')
                    + f"{len(ready)} ready to submit: {', '.join(ready)} | {len(need)} need you: {', '.join(need)}")
    if not a.dry:
        try:   # keep the Q&A database (data/applications.db) current after every run (0 tokens, ~1 s)
            import qa_db; qa_db.build(quiet=True)
        except Exception as e:
            print('qa_db rebuild failed:', str(e)[:120])
        try:
            import metrics; print(metrics.health_line())
        except Exception as e:
            print('health line failed:', e)


if __name__ == '__main__':
    main()
