"""Job Apply Prep, local edition (Playwright, 0 LLM tokens). Same rules as the cloud task
(reference/schedules/Job_Apply_Prep*.md): fill forms in the apply Chrome window so he only reviews and clicks Submit.

Picks Notion rows: Status "To apply", Notes contain FAST and not FILLED, created in the last 48h (newest, highest fit
first) + any row whose Notes contain "FILL NOW". Each link is routed to an ATS adapter in jobhunter/ats/
(Workday, LinkedIn, Greenhouse, Lever, Ashby, SmartRecruiters, iCIMS, Oracle, Eightfold, Taleo, Jobvite, Workable,
SuccessFactors, generic). Adapters stop on the review page BEFORE the final Submit and leave the tab open.

  python apply_prep.py                                   # normal run (Notion rows)
  python apply_prep.py --url URL [--page NOTION_PAGE_ID] [--pdf resume.pdf] --dry   # one form (testing)
"""
from urllib.parse import urlsplit
import applicant as P
import argparse, datetime as dt, json, os, re, tempfile, time, traceback
from zoneinfo import ZoneInfo
import config, formfill, notion, notion_pages, notify
import ats
from ats.common import result

CT = ZoneInfo('America/Chicago')
FILES = config.DATA / 'apply_files'; FILES.mkdir(exist_ok=True)


ENTRY_TITLE = re.compile(r'new grad|graduate|intern|co-?op|entry|junior|\bjr\b|associate|early career|university|campus|202[6-8]|'
                         r'\b(i|1)\b(?!\w)|apprentice|fellow|rotational|development program|leadership program', re.I)


def rows_to_fill():
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=48)).isoformat()
    fast = notion.query({'and': [{'property': 'Status', 'select': {'equals': 'To apply'}},
                                 {'property': 'Notes', 'rich_text': {'contains': 'FAST'}},
                                 {'property': 'Notes', 'rich_text': {'does_not_contain': 'FILLED'}},
                                 {'timestamp': 'created_time', 'created_time': {'on_or_after': since}}]})
    now = notion.query({'property': 'Notes', 'rich_text': {'contains': 'FILL NOW'}})
    fit = lambda r: int((re.search(r'fit (\d+)', r.get('Notes') or '') or [0, 0])[1])
    # Oct 23: entry-level titles get ~4x the OA/interview rate (2.9% vs 0.7% of Sep-Oct 18 applications) -> they go first
    entry = lambda r: bool(ENTRY_TITLE.search(r.get('Position') or '')) or r.get('Type') in ('New Grad', 'Internship')
    fast.sort(key=lambda r: (entry(r), r['created'][:13], fit(r)), reverse=True)
    # Oct 29: rows older than 48 h were never picked again (1,364 of 1,427 open rows) -> after the fresh rows, newest first
    days = float(os.environ.get('BACKLOG_DAYS', '14'))
    if days > 2:
        old_since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).isoformat()
        backlog = notion.query({'and': [{'property': 'Status', 'select': {'equals': 'To apply'}},
                                        {'property': 'Notes', 'rich_text': {'does_not_contain': 'FILLED'}},
                                        {'timestamp': 'created_time', 'created_time': {'on_or_after': old_since}},
                                        {'timestamp': 'created_time', 'created_time': {'before': since}}]})
        backlog.sort(key=lambda r: (entry(r), r['created'][:10], fit(r)), reverse=True)
        fast += backlog
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
        if ats.ONLY and still_skipped(r.get('Notes'), r.get('Link')): continue   # already found to apply elsewhere
        if ats.ONLY and not via_allowed(r): continue   # li_resolve found it applies on another system
        if tried.get(r['id'], 0) > cutoff: continue          # back off: tried within the last RETRY_HOURS
        if fails.get(r['id'], 0) >= 2 and tried.get(r['id'], 0) > time.time() - 12 * 3600: continue   # keeps failing: 2x/day
        seen.add(r['id']); out.append(r)
    return out


def still_skipped(notes, link=''):
    """An AUTO-SKIP note still holds unless the system it names was added to APPLY_ATS_ONLY later (Oct 22: Lever,
    SmartRecruiters, Workable, Rippling, JazzHR, Breezy, BambooHR -- the last four were skipped as 'generic')."""
    m = re.search(r'AUTO-SKIP[^|]*?this job applies on (\w+)', notes or '')
    if m and m.group(1) == 'generic':
        from ats import generic
        return not generic.small_ats(link)
    if m: return m.group(1).lower() not in ats.ONLY
    return 'AUTO-SKIP' in (notes or '')


def via_allowed(r):
    """APPLY_ATS_ONLY: False when the row's resolved Apply Via is a system outside the list (LinkedIn rows only count once
    li_resolve recorded where they really apply)."""
    v = (r.get('Apply Via') or '').lower()
    if 'linkedin.com' in (r.get('Link') or '') and 'APPLY URL:' not in (r.get('Notes') or '') and 'APPLY: LinkedIn Easy Apply' not in (r.get('Notes') or ''):
        return True   # not resolved yet: open it to find out
    if not v or v == 'company website': return 'linkedin.com' not in (r.get('Link') or '') or 'APPLY URL:' in (r.get('Notes') or '')
    return any(x in v for x in ats.ONLY)


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
    # Greenhouse embed (?for=<board>&token=<id>, any order): Oct 25 these all collapsed to ".../embed/job_app", so one
    # Epic Games / Elastic / Stripe submission blocked every later embed posting as "already applied"
    if 'greenhouse.io/embed/job_app' in u:
        b, t = re.search(r'[?&]for=([\w-]+)', u), re.search(r'[?&]token=(\d+)', u)
        if b and t: return f'{b[1]}:{t[1]}'
    # else path + the query params that identify the job (tracking params dropped, never the whole query)
    s = urlsplit(u)
    path = re.sub(r'(/apply.*|/application|/confirmation)$', '', s.path)
    q = sorted(kv for kv in s.query.split('&') if kv and not re.match(r'(utm_\w+|jr_id|gh_src|source|src|ref|lever-\w+)=', kv))
    return f'{s.scheme}://{s.netloc}{path}' + ('?' + '&'.join(q) if q else '')


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
    'found': {'type': 'boolean'}, 'subject': {'type': ['string', 'null']}, 'date': {'type': ['string', 'null']},
    'same_role': {'type': ['boolean', 'null']}}}


def own_roles(company):
    """Normalized role titles we submitted to this company (data/submissions.jsonl)."""
    c = _co(company); out = []
    try:
        for l in (config.DATA / 'submissions.jsonl').read_text().splitlines():
            d = json.loads(l) if l.strip() else {}
            if _co(d.get('company')) == c: out.append(re.sub(r'\W+', ' ', (d.get('role') or '').lower()).strip())
    except Exception:
        pass
    return out


def company_submitted(company):
    """True if our own submissions log has an application to this company (any role)."""
    c = _co(company)
    try:
        return any(_co(json.loads(l).get('company')) == c for l in (config.DATA / 'submissions.jsonl').read_text().splitlines() if l.strip())
    except Exception:
        return False


def company_recent(company, role=''):
    """Date we submitted another role to this company within PER_COMPANY_DAYS (data/submissions.jsonl), else None."""
    c, me = _co(company), re.sub(r'\W+', ' ', (role or '').lower()).strip()
    since = (dt.datetime.now(CT) - dt.timedelta(days=config.PER_COMPANY_DAYS)).isoformat()
    hits = []
    try:
        for l in (config.DATA / 'submissions.jsonl').read_text().splitlines():
            d = json.loads(l) if l.strip() else {}
            if c and _co(d.get('company')) == c and (d.get('at') or '') >= since \
                    and re.sub(r'\W+', ' ', (d.get('role') or '').lower()).strip() != me:
                hits.append(d['at'][:10])
    except Exception:
        pass
    return max(hits) if len(hits) >= config.PER_COMPANY_MAX else None


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
            f"it in subject). We are about to apply for \"{role}\". same_role=true only if a confirmation is for this exact job title "
            f"(ignore only a location suffix; a different team, specialty or level in the title is a DIFFERENT job), false if every "
            f"confirmation names a different job, null if none names a job.",
            auth._GMAIL_TOOLS + ['ToolSearch'], dict(_CONFIRM_SCHEMA, title='confirm'), model='haiku', budget_usd=0.06, timeout=180,
            system='Read-only Gmail lookup. Load Gmail tools with ToolSearch (select:<names>). Never send, draft or delete. '
                   'Finish with only the JSON object.')
        # Oct 20: one confirmation per company blocked every other role there (tonight's own Anthropic submissions
        # blocked 2 more Anthropic roles) -> only the same role, or a role-less email we can't explain by our own log
        own = company_submitted(company)
        subj = re.sub(r'\W+', ' ', (out.get('subject') or '').lower())
        mine = [x for x in own_roles(company) if x and x in subj and x != re.sub(r'\W+', ' ', role.lower()).strip()]
        if mine and not re.sub(r'\W+', ' ', role.lower()).strip() in subj: out['same_role'] = False   # our own submission tonight
        if out.get('found') and (out.get('same_role') or (out.get('same_role') is None and not own)):
            return f"possibly by hand — {company} confirmation email {out.get('date') or ''}: {(out.get('subject') or '')[:70]}; check before re-applying"
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


RECHECKS = config.DATA / 'confirm_rechecks.json'


def recheck_unconfirmed(max_checks=4):
    """Oct 26: ~100 rows (Oct 20-26) ended "clicked Submit, no confirmation" after the single 90 s Gmail look, while
    many confirmation emails arrive minutes later. Look again ~10 min and ~60 min after the Submit click; a confirmation
    marks the row Applied (apply_results + submissions.jsonl + Notion). Read-only Gmail, haiku, <= max_checks per run."""
    try: state = json.loads(RECHECKS.read_text())
    except Exception: state = {}
    now = dt.datetime.now(CT)
    rows, subs = {}, set()
    for line in (config.DATA / 'apply_results.jsonl').read_text().splitlines()[-3000:]:
        try: d = json.loads(line)
        except Exception: continue
        if not d.get('id'): continue
        if d['status'] == 'SUBMITTED': subs.add(d['id']); rows.pop(d['id'], None)
        elif 'no confirmation' in (d.get('reason') or '') and d['id'] not in subs:
            rows[d['id']] = d
    n = 0
    for pid, d in rows.items():
        age = (now - dt.datetime.fromisoformat(d['at'])).total_seconds() / 60
        done = state.get(pid, [])
        due = (len(done) == 0 and age >= 10) or (len(done) == 1 and age >= 60)
        if age > 24 * 60 or len(done) >= 2 or not due: continue
        if n >= max_checks: break
        n += 1
        state[pid] = done + [now.isoformat(timespec='seconds')]
        RECHECKS.write_text(json.dumps(state))
        ok = confirmed_by_email({'Company': d.get('company')}, wait_s=0)
        print(f"recheck {d.get('company')}: {ok or 'no confirmation yet'}")
        if not ok: continue
        try:
            with open(config.DATA / 'apply_results.jsonl', 'a') as fh:
                fh.write(json.dumps({'at': now.isoformat(timespec='seconds'), 'id': pid, 'company': d.get('company'),
                                     'ats': d.get('ats'), 'status': 'SUBMITTED', 'reason': ok + ' (late recheck)'}) + '\n')
            r = notion.flat(notion._req('GET', f'/pages/{pid}'))
            with open(config.DATA / 'submissions.jsonl', 'a') as fh:
                fh.write(json.dumps({'at': now.isoformat(), 'company': d.get('company'), 'role': r.get('Position'),
                                     'ats': d.get('ats'), 'note': 'confirmed by a late Gmail recheck'}) + '\n')
            if r.get('Status') in ('Needs you', 'To apply'):
                note = f"APPLIED {now.strftime('%b %d %I:%M %p CT')} (auto-submitted, {d.get('ats')}; {ok})"
                notion.update_row(pid, notion.props(Notes=(note + ' | ' + (r.get('Notes') or ''))[:1990], Status='Applied',
                                                    DateApplied=now.strftime('%Y-%m-%d')))
        except Exception as e:
            print('recheck update failed:', str(e)[:120])


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


def prune_traces(days=None):
    """Oct 29: .trace.zip recordings grew 2-4 GB a day and filled the disk (9.4 GB). Keep them TRACE_KEEP_DAYS (1);
    the small .json/.html/.png notes stay (needs_answers.py reads the .json). Never raises."""
    days = float(os.environ.get('TRACE_KEEP_DAYS', '1')) if days is None else days
    cutoff = time.time() - days * 86400
    for f in (config.DATA / 'failures').glob('*/*.zip'):
        try:
            if f.stat().st_mtime < cutoff: f.unlink()
        except Exception:
            pass


def save_failure(r, res):
    """Oct 26: every row that stops short keeps a screenshot, the page HTML and its still-empty required labels in
    data/failures/<date>/ -- "only 2 fields filled" alone never said WHICH field broke. Never raises."""
    try:
        pg = res.get('page')
        if not pg or pg.is_closed(): return None
        d = config.DATA / 'failures' / dt.datetime.now(CT).strftime('%Y-%m-%d'); d.mkdir(parents=True, exist_ok=True)
        base = d / (re.sub(r'\W+', '_', (r.get('Company') or 'x'))[:40] + '_' + (r.get('id') or 'test')[-8:] + dt.datetime.now(CT).strftime('_%H%M'))
        pg.screenshot(path=str(base) + '.png', full_page=True, timeout=15000)
        fr = formfill.form_frame(pg)
        (base.parent / (base.name + '.html')).write_text(fr.content()[:3000000])
        meta = {'at': dt.datetime.now(CT).isoformat(timespec='seconds'), 'id': r.get('id'), 'company': r.get('Company'),
                'role': r.get('Position'), 'ats': res.get('ats'), 'status': res['status'], 'reason': res.get('reason'),
                'url': pg.url, 'frame_url': fr.url, 'done': res.get('done'), 'unknown': res.get('unknown'),
                'still_empty': formfill.still_empty(fr)}
        (base.parent / (base.name + '.json')).write_text(json.dumps(meta, indent=1, ensure_ascii=False))
        return base
    except Exception as e:
        print('failure capture failed:', str(e)[:120])
        return None


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


# bot checks and monitoring beacons: never an application (Workable's Cloudflare check, JazzHR's New Relic -- Oct 22)
NOT_APP = re.compile(r'/cdn-cgi/challenge-platform/|nr-data\.net|sentry\.io|datadoghq|google-analytics|googletagmanager', re.I)
GUARD_HOSTS = re.compile(r'greenhouse\.io|ashbyhq\.com|lever\.co|smartrecruiters\.com|workable\.com|rippling\.com|applytojob\.com|breezy\.hr|bamboohr\.com', re.I)


def _site(url):
    """Registrable-ish site of a URL (last two host labels): careers.vituity.com -> vituity.com."""
    h = (urlsplit(url).hostname or '').split('.')
    return '.'.join(h[-2:])


def dry_guard(page, blocked, armed=None):
    """Abort any submit-like POST/PUT at the network layer. --dry: always (Pogo/Ashby Oct 12: a dry run DID submit).
    Live (armed = {'on': False}, single-page ATSs only): blocked until auth.submit's own click arms it -- Oct 12 the
    Neuralink form was posted while it was still being filled (Greenhouse emailed a security code).
    Uploads/autosaves pass; only requests whose URL or JSON body says submit."""
    def h(route, req):
        try:
            first_party = _site(req.url) == _site(page.url) or bool(GUARD_HOSTS.search(req.url))
            if armed is None and first_party and req.method in ('POST', 'PUT', 'PATCH') and not re.search(
                    r'search|widgets?|/get|lookup|/list|query|config|settings?|/session|/token|/csrf', urlsplit(req.url).path, re.I) and not re.search(
                    r'upload|presign|/files?\b|attachment|resume.?pars|parse|s3\.|amazonaws|/(event|events|track|tracking|log|logs|'
                    r'analytics|telemetry|collect|beacon|metrics?)(/|\?|$)|sentry|datadog|segment|google|recaptcha|hcaptcha', req.url, re.I):
                # --dry (Oct 28, Garage/Dover: a pilot test DID submit -- its POST didn't look like a submit): block EVERY
                # write except uploads / parsing / analytics, whatever its name
                blocked.append(f"{req.method} {req.url[:150]} (dry: all writes blocked)")
                return route.abort()
            if req.method in ('POST', 'PUT') and not (armed and armed.get('on')):
                ct = (req.headers.get('content-type') or '').lower()
                body = (req.post_data or '')[:3000] if ('json' in ct or 'x-www-form' in ct) else ''
                op = re.search(r'[?&]op=(\w+)|"operationName"\s*:\s*"(\w+)"', req.url + ' ' + body)
                # judge the path, never the host: "apply.workable.com", "x.applytojob.com" are hosts (Oct 22)
                path = urlsplit(req.url).path
                what = (op.group(1) or op.group(2)) if op else path + ' ' + body   # GraphQL: judge by operation name only
                ddo = re.search(r'"ddoKey"\s*:\s*"(\w+)"', body)   # Phenom (Chewy Oct 28): every call posts to /applySubmit,
                if ddo: what = ddo.group(1); op = ddo                  # the ddoKey says what it is (applyGetReferences = a read)
                if re.search(r'/(event|events|track|tracking|log|logs|analytics|telemetry|collect|beacon|metrics?)(/|\?|$)', req.url, re.I) \
                        or NOT_APP.search(req.url):
                    hit = False                 # analytics pings ("jobright.ai/swan/event/submit") are not applications
                elif 'linkedin.com' in req.url:   # LinkedIn SDUI: opening/stepping Easy Apply is a POST to .../EasyApply -- only 'submit' counts
                    hit = bool(re.search(r'submit', req.url + ' ' + body, re.I))
                else:
                    hit = bool((re.search(r'submit', what, re.I) and not (op and re.search(r'consent|texting|upload|file|resume|survey|parse|draft', what, re.I)))
                               or (not op and SUBMIT_REQ.search(path) and 'upload' not in req.url.lower()))
                if hit:
                    blocked.append(f"{req.method} {req.url[:150]} {body[:150]}")
                    return route.abort()
            route.continue_()
        except Exception:
            try: route.continue_()
            except Exception: pass
    page.route(lambda u: (armed is None or bool(re.search(r'submit|graphql|/api/|/applications?\b|apply|/jobs/\d+/?(\?|$)', u, re.I))
                          and bool(GUARD_HOSTS.search(u)))   # --dry: every request is inspected (Oct 28)
               and not re.search(r'upload|recaptcha|\.(js|css|png|svg|jpe?g|woff2?)(\?|$)', u, re.I), h)
    page.add_init_script("""(()=>{window.__jhClicks=[];addEventListener('click',e=>{const t=e.target.closest('button,a,[role=button]')||e.target;
      window.__jhClicks.push((t.tagName||'')+' '+((t.innerText||'').trim().slice(0,40))+' jh='+t.getAttribute('data-jh')+' trusted='+e.isTrusted)},true);
      addEventListener('keydown',e=>{if(e.key==='Enter')window.__jhClicks.push('ENTER in '+e.target.tagName+' '+(e.target.name||e.target.id||''))},true)})()""")


KEEP_TABS = os.environ.get('KEEP_TABS', '1') == '1'   # Oct 13 (owner): leave a stopped form open for him to finish
MAX_KEPT = int(os.environ.get('MAX_KEPT_TABS', '20'))
PILOT_LIMIT = int(os.environ.get('PILOT_LIMIT', '15'))   # Claude pilot runs per apply_prep run (Oct 28)
PILOT_USED = 0
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
    from ats.common import needs_you_banner   # Oct 28: show what's missing right on the page
    needs_you_banner(new[-1], res.get('unknown') or [res.get('reason') or ''])
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


LIMITS = config.DATA / 'site_limits.json'


def _co(name):
    return re.sub(r'[^a-z0-9]', '', (name or '').split('(')[0].lower())


def company_limited(company, days=90):
    """Date the company's site said "one application per N days" (ElevenLabs Oct 12-13: 5 rows hit it), if recent."""
    try: seen = json.loads(LIMITS.read_text()).get(_co(company))
    except Exception: return None
    if seen and dt.datetime.fromisoformat(seen) > dt.datetime.now(CT) - dt.timedelta(days=days): return seen[:10]
    return None


def note_limit(company):
    try: d = json.loads(LIMITS.read_text())
    except Exception: d = {}
    d[_co(company)] = dt.datetime.now(CT).isoformat(timespec='seconds')
    LIMITS.write_text(json.dumps(d, indent=1))


def posting_location(page):
    """The location line a Greenhouse/Ashby posting shows ('London, UK'), or ''."""
    try:
        return page.evaluate(r"""() => {
          const t = s => (s ? (s.innerText || '').replace(/\s+/g, ' ').trim() : '');
          const gh = document.querySelector('.job__location, .location, [class*="job__location"]');
          if (gh && t(gh)) return t(gh);
          for (const h of document.querySelectorAll('h2, h3, dt, [class*="sectionHeading"], [class*="heading"]')) {   // Ashby: "Location" heading + value
            if (/^location$/i.test(t(h))) { const v = h.nextElementSibling || (h.parentElement && h.parentElement.querySelector('p, dd, span:not(:first-child)')); if (v && t(v)) return t(v); }
          }
          return '';
        }""")[:200]
    except Exception:
        return ''


def run_one(ctx, r, files, dry):
    import hunt
    pos = r.get('Position') or ''
    if r.get('id') and hunt.non_us_title(pos):   # owner rule: non-US roles are blocked (Speechify UK, Oct 19)
        res = result('CANCELLED', f'CLOSED – non-US role ({pos[:80]})'); res['ats'] = '?'
        return res
    if r.get('id') and hunt.other_school_program(pos):   # program for another university's students only
        res = result('CANCELLED', f'CLOSED – program for another university\'s students ({pos[:80]})'); res['ats'] = '?'
        return res
    if r.get('id'):   # Oct 28: rows queued before the stricter rules still go through them before anything is filled
        import score
        why = score.blocked('', pos) or ('non-US role' if hunt.foreign(r.get('Location') or '') else None)
        if why:
            res = result('CANCELLED', f'CLOSED – {why} ({pos[:80]}, {r.get("Location") or "?"})'); res['ats'] = '?'
            return res
        prev = company_recent(r.get('Company'), pos)
        if prev:
            res = result('CANCELLED', f'CLOSED – already applied to {r.get("Company")} on {prev} (one application per company per '
                                      f'{config.PER_COMPANY_DAYS} days)'); res['ats'] = '?'
            return res
    seen = company_limited(r.get('Company')) if r.get('id') else None
    if seen:
        res = result('NEEDS YOU', f'posting closed for him — site limit: {r.get("Company")} allows one application per 90 days '
                                  f'(limit shown {seen})'); res['ats'] = '?'
        return res
    import li_resolve
    target = li_resolve.apply_url(r) if 'linkedin.com' in (r.get('Link') or '') else None
    al, link = r.get('Apply Link') or '', r.get('Link') or ''
    if not target and al and not urlsplit(link).path.strip('/') and urlsplit(al).netloc == urlsplit(link).netloc:
        target = al   # a skip run left only the careers-site root in Link (JazzHR, Oct 22); Apply Link has the job
    page = ctx.new_page()
    blocked, armed = [], {'on': False}
    if dry: dry_guard(page, blocked)
    elif GUARD_HOSTS.search(target or r.get('Link') or ''): dry_guard(page, blocked, armed)
    try:
        from apply_now import workday_open
        if workday_open(r.get('Link') or '') is False:   # removed Workday job: don't walk into its sign-in wall
            res = result('NEEDS YOU', 'posting closed (Workday says the job no longer exists)', page=page); res['ats'] = 'workday'
            return res
    except Exception:
        pass
    if ats.ONLY and 'linkedin' not in ats.ONLY and 'APPLY: LinkedIn Easy Apply' in (r.get('Notes') or ''):
        res = result('SKIPPED', 'LinkedIn Easy Apply (auto-apply is limited to ' + '/'.join(ats.ONLY) + ')', page=page)
        res['ats'] = 'linkedin'; res['via'] = 'LinkedIn Easy Apply'
        return res
    page.goto(target or r['Link'], wait_until='domcontentloaded', timeout=60000)   # resolved company URL: no LinkedIn hop
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
    loc_txt = posting_location(page)
    if r.get('id') and loc_txt:
        import hunt
        if hunt.foreign(loc_txt):
            res = result('CANCELLED', f'CLOSED – non-US role (posting location: {loc_txt[:80]})', page=page)
            res['ats'] = adapter.__name__.split('.')[-1]
            return res
    job = dict(url=r['Link'], company=r.get('Company'), title=r.get('Position'), dry=dry, **files)
    if not ats.allowed(adapter) and adapter.__name__.split('.')[-1] not in ('linkedin', 'jobright', 'generic'):
        res = ats.skipped(adapter, page)   # APPLY_ATS_ONLY (owner, Oct 19): Greenhouse + Ashby only for now
    else:
        res = adapter.run(ctx, page, job)
    res['ats'] = adapter.__name__.split('.')[-1]
    if res['status'] == 'SKIPPED': return res
    if dry:
        try: clicks = (res.get('page') or page).evaluate('window.__jhClicks || []')
        except Exception: clicks = []
        print('DRY clicks:', clicks)
    if blocked:
        print('GUARD blocked submit request(s) during fill:', blocked)
        # writes stopped only by the broad --dry rule are not submit attempts; a submit-looking one still fails the dry run
        if dry and [b for b in blocked if '(dry: all writes blocked)' not in b]:   # live: the blocked request never left -> the normal armed submit below still applies
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
    inel = [str(u) for u in (res.get('unknown') or []) if str(u).startswith('INELIGIBLE')]
    if inel:   # owner rule: clearance / ITAR roles are blocked (CHAOS, Bespoke Oct 13 asked only inside the form)
        kind = 'non-US role' if 'non-US' in inel[0] else 'clearance role'
        res.update(status='CANCELLED', reason=f"CLOSED – {kind}, the form asks: {inel[0].split(': ', 1)[-1][:200]}")
        return res
    # Oct 13: single-page forms (Greenhouse, Ashby, generic...) get one Claude rescue before giving up (rescue.py).
    # Workday rescues inside its own step loop. Walls/CAPTCHA/closed/already-applied are never "fixed".
    # never on a jobright.ai page: it has no form, the rescue only clicked jobright's own APPLY WITH AUTOFILL (Oct 24)
    if res['status'] == 'NEEDS YOU' and not dry and res.get('ats') != 'workday' and 'jobright.ai' not in pg0.url and not re.search(
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
    # Oct 28: the adapter stopped short (no Apply / Next / Submit found, form not found, fields left) -> Claude pilot
    # drives the page forward until the final Submit is visible (pilot.py; it never clicks Submit or sign-in itself)
    global PILOT_USED
    if res['status'] == 'NEEDS YOU' and PILOT_USED < PILOT_LIMIT and 'jobright.ai' not in pg0.url and re.search(
            r'no apply button|no application form|no job id|no submit button|filled, but no submit|only \d+ field|could not (find|open)|'
            r'still empty|did not leave|not complete|timeout', res.get('reason') or '', re.I) and not re.search(
            r'captcha|closed|no longer|already applied|site limit|limiting applications|sign in|create (an )?account|log ?in',
            res.get('reason') or '', re.I):
        PILOT_USED += 1
        try:
            import pilot
            pg_p = res.get('page') or pg0
            out = pilot.run(pg_p, job, [res.get('reason') or ''] + [str(u) for u in (res.get('unknown') or [])][:8])
            print('pilot:', out['state'], '|', '; '.join(out['log'])[:500])
            res['page'] = out['page']; pg0 = out['page']
            tag = 'pilot: ' + '; '.join(out['log'][-4:])[:200]
            if out['state'] == 'submitted':   # a pilot click ended on a confirmation page: it WAS sent, record it truthfully
                res.update(status='SUBMITTED', reason=f"submitted by a pilot click (confirmation page seen) — {tag}")
                with open(config.DATA / 'submissions.jsonl', 'a') as fh:
                    fh.write(json.dumps({'at': dt.datetime.now(CT).isoformat(), 'company': r.get('Company'), 'role': r.get('Position'),
                                         'ats': res.get('ats'), 'url': out['page'].url, 'note': 'pilot click'}) + '\n')
                return res
            if out['state'] == 'ready':
                res.update(status='FILLED', reason=tag); res['done'] = list(res.get('done') or []) + ['Claude pilot']
            elif out['state'] == 'gov_id':
                res.update(status='CANCELLED', reason=f"CANCELLED – the application asks for a government ID: {out['question']}")
                return res
            elif out['state'] == 'closed': res['reason'] = f"posting closed ({tag})"
            elif out['state'] == 'login': res['reason'] = f"sign in or create account ({tag})"
            elif out['state'] == 'captcha': res['reason'] = f"CAPTCHA ({tag})"
            elif out['state'] == 'need_human': res['reason'] = f"question only you can answer: {out['question'][:200]} ({tag})"
            else: res['reason'] = f"{res.get('reason')} | {tag}"
        except Exception as e:
            print('pilot failed:', str(e)[:150])
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
        pg_s = res.get('page') or page
        fr_s = formfill.form_frame(pg_s)   # Greenhouse embedded on a company site (Asana, PathAI Oct 20): Submit is in the iframe
        res = auth.submit(pg_s, res, frame=fr_s if fr_s is not pg_s.main_frame else None)
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
    prune_traces()
    from playwright.sync_api import sync_playwright
    import browser
    if not a.url and not config.NOTION_TOKEN:
        print('NOTION_TOKEN is empty in .env -> nothing to read; skipping.'); return
    if a.ids:
        rows = [notion.flat(notion._req('GET', f'/pages/{i.strip()}')) for i in a.ids.split(',') if i.strip()]
    elif a.ats:
        dom = {'greenhouse': r'greenhouse\.io|gh_jid=', 'ashby': r'ashbyhq\.com', 'lever': 'lever.co', 'workday': 'myworkdayjobs.com',
               'smartrecruiters': 'smartrecruiters.com', 'icims': 'icims.com', 'linkedin': 'linkedin.com/jobs',
               'workable': 'workable.com', 'rippling': 'rippling.com', 'jazzhr': 'applytojob.com', 'breezy': 'breezy.hr', 'bamboohr': 'bamboohr.com'}
        want = [dom.get(x.strip(), re.escape(x.strip())) for x in a.ats.split(',')]
        tried = attempts(); cutoff = time.time() - RETRY_HOURS * 3600; fails = recent_fails()
        # rows that wait on HIS decision are never auto-applied (Akuna "your ONE pick", expired assessments)
        human = re.compile(r'must confirm|your ONE\b|EXPIRED', re.I)
        rows = [r for r in notion.query({'or': [{'property': 'Status', 'select': {'equals': 'To apply'}},
                                                {'property': 'Status', 'select': {'equals': 'Needs you'}}]})
                if (any(re.search(w, r.get('Link') or '', re.I) for w in want)
                    or any(re.search(x.strip(), str(r.get('Apply Via') or ''), re.I) for x in a.ats.split(',')))
                and tried.get(r['id'], 0) < cutoff and not human.search(r.get('Notes') or '') and not still_skipped(r.get('Notes'), r.get('Link'))
                and not (fails.get(r['id'], 0) >= 2 and tried.get(r['id'], 0) > time.time() - 12 * 3600)]
        rows.sort(key=lambda r: r.get('created') or '', reverse=True)
        rows = rows[:a.max]
    elif a.only:
        rows = [r for r in notion.rows_for_company(a.only) if r.get('Status') in ('To apply', 'Needs you')][:a.max]
    else:
        rows = [dict(id=a.page, Link=a.url, Company='(test)', Position='', Notes='')] if a.url else rows_to_fill()[:a.max]
    if not (a.url or a.dry or a.ids or a.only or a.ats):
        try: recheck_unconfirmed()
        except Exception as e: print('confirmation recheck failed:', str(e)[:120])
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
        from ats.linkedin import limited_today
        li_limited = limited_today()   # Oct 28: LinkedIn daily Easy Apply limit hit earlier today -> skip LinkedIn rows
        import faulthandler
        for r in rows:
            # Oct 20: one row (Akuna) hung a run for 40 min and blocked every later run (lock). Watchdog: after 25 min on
            # one row, print where it hangs and exit; the attempt is stamped first so the next run skips that row a while
            faulthandler.dump_traceback_later(1500, exit=True)
            if r.get('id') and not a.dry: mark_attempt(r['id'])
            stamp = dt.datetime.now(CT).strftime('%b %d %I:%M %p CT')
            try: before = list(ctx.pages)
            except Exception: before = []
            if li_limited and 'linkedin.com' in (r.get('Link') or ''):
                print(f"skip {r.get('Company')}: LinkedIn is rate-limiting this run (retried next hour)"); continue
            try:                                    # reconnect if the previous row broke the browser connection
                _ = ctx.pages
            except Exception:
                b, ctx = browser.connect(p); ctx.set_default_timeout(30000)
            tracing = False
            if not a.dry:   # Oct 26: Playwright trace per row, kept only for rows that stop short (save_failure)
                try: ctx.tracing.stop()   # a row that left early (rate-limit `continue`) may still be tracing
                except Exception: pass
                try: ctx.tracing.start(screenshots=True, snapshots=True, sources=False); tracing = True
                except Exception as e: print('trace start failed:', str(e)[:100])
            try:
                if r.get('id') and not a.pdf and not a.dry:   # Oct 28: rows built by an older generator get a current resume first
                    import regen
                    if regen.ON and not regen.current(r):
                        try: print('  resume rebuilt' if regen.refresh(r) else '  resume kept (job post unreadable)')
                        except Exception as e: print('  resume rebuild failed, using the old one:', str(e)[:120])
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
            if 'daily Easy Apply limit' in (res.get('reason') or ''): li_limited = True
            results.append((r, res))
            if not a.dry and re.search(r'site limit: (?!.*allows one application)|limiting applications', res.get('reason') or '', re.I):
                note_limit(r.get('Company'))
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
            if r.get('id') and not a.dry and res['status'] == 'SKIPPED':
                try:
                    import community
                    upd = notion.props(Notes=(f"AUTO-SKIP {stamp} – {res['reason']} (apply by hand) | " + (r.get('Notes') or ''))[:1990])
                    fin = (res.get('page').url if res.get('page') else '') or ''
                    via = res.get('via') or (community.apply_via(fin) if fin.startswith('http') and 'linkedin.com' not in fin else None)
                    if via: upd['Apply Via'] = {'select': {'name': via}}
                    if via and via != 'LinkedIn Easy Apply' and fin.startswith('http') and 'linkedin.com' not in fin and not re.search(r'login|sign-?in|/auth|/account', fin, re.I):
                        upd['Apply Link'] = {'url': fin[:1900]}
                    elif via == 'LinkedIn Easy Apply': upd['Apply Link'] = {'url': r.get('Link')}
                    notion.update_row(r['id'], upd)
                except Exception: pass
            elif r.get('id') and not a.dry and res['status'] != 'ERROR':
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
                try:   # learned where this job really applies -> Apply Via column. Only what was SEEN: the Easy Apply
                    # button (linkedin adapter sets res['via']) or the site the run ended on; a LinkedIn error/sign-in
                    # wall says nothing (Oct 20: those were all labelled Easy Apply)
                    import community
                    fin = (res.get('page').url if res.get('page') else '') or ''
                    via = res.get('via') or (community.apply_via(fin) if fin.startswith('http') and 'linkedin.com' not in fin else None)
                    if via: upd['Apply Via'] = {'select': {'name': via}}
                    if via and via != 'LinkedIn Easy Apply' and fin.startswith('http') and 'linkedin.com' not in fin and not re.search(r'login|sign-?in|/auth|/account', fin, re.I):
                        upd['Apply Link'] = {'url': fin[:1900]}
                    elif via == 'LinkedIn Easy Apply': upd['Apply Link'] = {'url': r.get('Link')}
                except Exception:
                    pass
                notion.update_row(r['id'], upd)
            base = save_failure(r, res) if not a.dry and res['status'] in ('NEEDS YOU', 'ERROR', 'FILLED') else None
            if tracing:
                try: ctx.tracing.stop(path=str(base) + '.trace.zip') if base else ctx.tracing.stop()
                except Exception as e: print('trace stop failed:', str(e)[:100])
            # Close the form tabs after each row (Oct 12: filled forms left open submitted themselves 30 s - 3 min later).
            # Oct 13 (owner): a stopped NEEDS-YOU form stays OPEN for him to fix by hand (KEEP_TABS, max 20) and is
            # listed in data/kept_tabs.jsonl + its Notion note; everything else is closed.
            kept = keep_tab(ctx, before, r, res)
            if kept and r.get('id') and not a.dry:
                try: notion.update_row(r['id'], notion.props(Notes=(f"TAB LEFT OPEN in the apply Chrome for you to finish | NEEDS YOU – "
                                                                    f"{(res.get('reason') or '')[:500]} ({stamp}) | " + (r.get('Notes') or ''))[:1990]))
                except Exception: pass
            close_forms(ctx)
    faulthandler.cancel_dump_traceback_later()
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
        try:   # Oct 26: refresh the answer-once list (data/answer_log/needs_answers.md, 0 tokens)
            import needs_answers; needs_answers.main()
        except Exception as e:
            print('needs_answers failed:', str(e)[:120])
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
