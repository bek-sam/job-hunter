"""Fast Hunter, local edition. Same rules as the cloud task (reference/schedules/Fast_Hunter*.md):
roles posted in the last 60 min -> hard filters -> dedupe -> BEST-MATCH gate >= 70 -> tailored resume
(gen_v6) + packet -> Notion row with PDF/DOCX. Only the cover letter uses the LLM.

  python fast_hunter.py                 # normal hourly run
  python fast_hunter.py --dry-run       # no Notion writes, no LLM; prints what would be built
  python fast_hunter.py --minutes 180   # wider window (testing)
"""
import applicant as P
import argparse, collections, concurrent.futures as cf, datetime as dt, json, os, re, sqlite3, subprocess, sys, time, traceback
from pathlib import Path
from zoneinfo import ZoneInfo
import config, hunt, jd as JD, score, notify

CT = config.TZ   # local time zone (JOBHUNTER_TZ, default America/Chicago)
PY = sys.executable
SOFFICE_DIR = '/Applications/LibreOffice.app/Contents/MacOS'
os.environ['PATH'] = SOFFICE_DIR + os.pathsep + os.environ.get('PATH', '')
# sources that give only a posting DATE ("Posted Today"), never a time, on the list and on the job page
DATE_ONLY_SRC = {'workday', 'oracle', 'amazon', 'ibm', 'speedyapply', 'jobright',
                 'indeed', 'zip_recruiter', 'google', 'talentbrew', 'atlassian'}   # JobSpy (Nov 7): date only + redirect links with no post time -> were stuck in 'retry'
LAST_OK_JSON = config.DATA / 'hunt' / 'last_ok.json'   # per hunter script: start of its last successful scan
LAST_OK = config.DATA / 'hunt' / 'last_ok'   # (old single value, read once for migration) start time of the last successful scan (catch-up + first-sighting continuity)
EXACT_SRC = {'greenhouse', 'ashby', 'lever', 'smartrecruiters', 'simplify', 'simplify-intern', 'amazon', 'google',
             'apple', 'microsoft', 'tiktok', 'eightfold'}


def db(dry=False):
    c = sqlite3.connect(':memory:' if dry else config.DATA / 'seen.db')
    c.execute('create table if not exists seen (url text primary key, company text, title text, verdict text, fit int, at text)')
    return c


def load_last_ok():
    """{script: datetime} of each hunter script's last successful scan (old single last_ok file = default for all)."""
    try: old = dt.datetime.fromisoformat(LAST_OK.read_text().strip())
    except Exception: old = None
    try: d = {k: dt.datetime.fromisoformat(v) for k, v in json.loads(LAST_OK_JSON.read_text()).items()}
    except Exception: d = {}
    return {s: d.get(s, old) for s in SCRIPTS + ('jobspy',)}


def save_last_ok(per_script, t0):
    """Advance only the scripts that succeeded: one source that stays broken no longer turns EVERY run (all sources)
    into a 24-h catch-up that runs long and makes the next 5-min slots skip on the lock (fix Nov 6)."""
    d = {k: v.isoformat() for k, v in load_last_ok().items() if v}
    for name, (_, rc) in per_script.items():
        if rc == 0: d[name] = t0.isoformat()
    LAST_OK_JSON.write_text(json.dumps(d))


SCRIPTS = ('hunt', 'intern', 'bigtech')
# A few dead/blocked boards (403s, a 404 board) are normal on every scan. Nov 7: treating ANY failure as "incomplete"
# froze last_ok for hunt + bigtech at Nov 4 22:30, so every run was a 24-h catch-up (~50 min, lock skips).
# Coverage counts as complete while failures stay within this share of the sources (min 10).
HEALTH_TOLERANCE = float(os.environ.get('HEALTH_TOLERANCE', '0.01'))


def run_hunters(hours):
    """hours = {script: look-back hours} (or one number for all)."""
    if not isinstance(hours, dict): hours = {s: hours for s in SCRIPTS}
    hours.setdefault('jobspy', max(hours.values()))   # Nov 7: jobspy had no window key -> KeyError, 0 rows every run
    out = config.DATA / 'hunt'; out.mkdir(exist_ok=True)
    jobs = {'hunt': ['hunt.py', '--out', str(out / 'new_roles.json')],
            'intern': ['intern_hunt.py', '--out', str(out / 'new_interns.json')],
            'bigtech': ['bigtech.py', '--out', str(out / 'new_bigtech.json')]}
    if os.environ.get('JOBSPY', '1') == '1':   # Oct 28: Indeed / ZipRecruiter / Google Jobs via python-jobspy
        jobs['jobspy'] = ['jobspy_hunt.py', '--out', str(out / 'new_jobspy.json')]
    def go(name):
        args = jobs[name]
        try:
            r = subprocess.run([PY, args[0], '--hours', f'{hours[name]:.2f}'] + args[1:], cwd=config.PKG, capture_output=True, text=True, timeout=600)
            (out / f'{name}.log').write_text(r.stdout + r.stderr)
            import source_health
            health = source_health.read(args[2])
            code = r.returncode
            if health and not health['complete']:
                nfail = len(health['failures'])
                total = health.get('boards') or health.get('sources') or 0
                ok = nfail <= max(10, HEALTH_TOLERANCE * total) and total > 0
                print(f'{name}: partial coverage; {nfail} of {total or "?"} sources failed (details in {args[2]}.health.json)'
                      + (' -> within tolerance, scan counts as complete' if ok else ''), flush=True)
                if not ok: code = code or 3
            return name, json.load(open(args[2])), code
        except Exception as e:
            return name, [], f'failed: {e}'
    first = go('hunt')   # Oct 14: hunt.py first; intern_hunt.py then reuses its board fetches (no double Workday load)
    rest = [n for n in jobs if n != 'hunt']
    with cf.ThreadPoolExecutor(len(rest)) as ex:
        return [first] + list(ex.map(go, rest))


def merged(results):
    rows, seen = [], set()
    for name, lst, _ in results:
        for r in lst:
            r = dict(r)
            r['kind'] = r.get('kind') or ('intern' if name == 'intern' else 'newgrad')
            if 'exact' not in r: r['exact'] = r.get('source') in EXACT_SRC
            if r['url'] in seen: continue
            seen.add(r['url']); r['script'] = name; rows.append(r)
    return rows


def utc(s):
    return dt.datetime.strptime(s, '%Y-%m-%d %H:%M').replace(tzinfo=dt.timezone.utc)


def minutes_ago(label):
    m = re.search(r'(\d+)\s*(minute|min|hour)', label or '', re.I)
    if not m: return None
    return int(m.group(1)) * (60 if m.group(2).lower().startswith('hour') else 1)


def label_days(label):
    """Age in whole days of a date-only label: today 0, yesterday 1, 'N days ago' N; None when unknown or weeks+."""
    l = (label or '').lower()
    if re.search(r'today|just (now|posted)|moments? ago|seconds? ago|\bnew\b', l): return 0
    if 'yesterday' in l: return 1
    m = re.search(r'(\d+)\+?\s*days?', l)
    return int(m.group(1)) if m and '+' not in l else None


def label_kind(label):
    """'minutes' (exact age in minutes_ago), 'today' (date only: 'Posted Today' / 'Just posted'), 'old' (yesterday, days,
    weeks, 30+), or None when there is no label."""
    l = (label or '').lower()
    if not l: return None
    if minutes_ago(l) is not None: return 'minutes'
    if re.search(r'today|just (now|posted)|moments? ago|seconds? ago|\bnew\b', l): return 'today'
    if re.search(r'yesterday|day|week|month|year|30\+', l): return 'old'
    return None


def norm(s):
    return re.sub(r'[^a-z0-9]', '', (s or '').lower())


def same_co(a, b):
    """Normalized company keys of one employer: equal, or one is a prefix of the other ("principal" / "principalchi" /
    "principalfin" -- jobright posted one Principal role under 5 names, Oct 22)."""
    short, long_ = sorted((a, b), key=len)
    return a == b or (len(short) >= 5 and long_.startswith(short))


def seen_pair(key, pairs):
    return key in pairs or any(t == key[1] and same_co(c, key[0]) for c, t in pairs)


SCAN_HIST = config.DATA / 'scan_history.jsonl'


def repost_count(key, url, t0, days=90):
    """How many OTHER days in the last `days` the same company + title was seen with a different URL (ghost/repost
    signal, idea from career-ops detect-reposts.mjs). Also records this sighting. 0 tokens."""
    import json as _j
    day = t0.strftime('%Y-%m-%d'); cutoff = (t0 - dt.timedelta(days=days)).strftime('%Y-%m-%d'); seen_days = set()
    try:
        for line in SCAN_HIST.open():
            h = _j.loads(line)
            if h['d'] >= cutoff and h['d'] != day and h['t'] == key[1] and same_co(h['c'], key[0]) and h['u'] != norm(url):
                seen_days.add(h['d'])
    except OSError:
        pass
    with SCAN_HIST.open('a') as f:
        f.write(_j.dumps({'d': day, 'c': key[0], 't': key[1], 'u': norm(url)}) + '\n')
    return len(seen_days)


def notion_index():
    import notion
    rows = notion.recent_rows(35)
    links = {norm(r.get('Link')) for r in rows if r.get('Link')}
    pairs = {(norm(r.get('Company'))[:12], norm(r.get('Position'))) for r in rows}
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=config.PER_COMPANY_DAYS)).isoformat()
    # every row still in play counts (To apply / Needs you / Applied / Rejected / OA / Interview); Closed never went out
    per_co = collections.Counter(norm(r.get('Company'))[:12] for r in rows
                                 if (r.get('created') or '') >= since and r.get('Status') != 'Closed')
    return links, pairs, per_co


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--minutes', type=int, default=config.WINDOW_MIN)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--no-hunt', action='store_true', help='reuse last hunter output (testing)')
    a = ap.parse_args()
    if not a.dry_run:   # every 15 min since Oct 13: never two hunter runs at once (a slow run just makes the next one skip)
        import fcntl
        global _LOCK
        _LOCK = open(config.DATA / 'fast_hunter.lock', 'w')
        try: fcntl.flock(_LOCK, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print('another fast_hunter run is active -> skipping this run'); return
    import gen_v5
    if not a.dry_run and not os.access(gen_v5.SOFFICE, os.X_OK):   # Oct 22: LibreOffice deleted -> 17 h of built=0
        print(f'LibreOffice not found ({gen_v5.SOFFICE}): no resumes can be built -> skipping; '
              'last_ok stays put so the next good run catches up'); return
    if not config.NOTION_TOKEN and not a.dry_run:
        print('NOTION_TOKEN is empty in .env -> running as --dry-run (no Notion writes, no LLM calls)')
        a.dry_run = True
    base_min = max(a.minutes, config.FRESH_MIN)   # how old a posting may be when we first see it (search-index lag)
    recovery = config.DATA / 'recovery_since.txt'   # an unfinished catch-up keeps its original start until it is done
    in_recovery = recovery.exists()
    if in_recovery:
        recovery_since = dt.datetime.fromisoformat(recovery.read_text().strip())
        base_min = max(base_min, int((dt.datetime.now(dt.timezone.utc) - recovery_since).total_seconds() / 60) + 10)
    t0 = dt.datetime.now(dt.timezone.utc)
    # Auto catch-up (Oct 15), per hunter script since Nov 6: if a script's last SUCCESSFUL scan is older than the normal
    # window (Mac asleep, crash, no network, that source failing), widen THAT script's window back to it (+10 min
    # overlap, max MAX_CATCHUP_MIN). hunt + intern share board fetches, so they share the wider of their two windows.
    last = load_last_ok()
    win, cont = {}, {}
    for s in SCRIPTS:
        lo = last.get(s)
        gap = (t0 - lo).total_seconds() / 60 if lo else None
        cu = bool(gap and gap + 10 > base_min)
        win[s] = max(base_min, min(int(gap) + 10, config.MAX_CATCHUP_MIN)) if cu else base_min
        # first sighting = freshness for date-only sources, but only while that script's scans are continuous (no
        # sleep gap): catch-up runs count too -- every scan since last_ok is accounted for
        cont[s] = bool(lo and (t0 - lo <= dt.timedelta(minutes=40) or cu))
        if cu: print(f'CATCH-UP {s}: last successful scan {gap:.0f} min ago -> window {win[s]} min')
    win['hunt'] = win['intern'] = max(win['hunt'], win['intern'])
    lo = last.get('jobspy')   # JobSpy (Indeed): same continuity rule; it has no window of its own (uses the widest)
    cont['jobspy'] = bool(lo and t0 - lo <= dt.timedelta(minutes=40))
    fresh_min = max(win.values())   # widest window (summary + legacy uses)
    fmin = lambda r: win.get(r.get('script'), fresh_min)
    cutf = lambda r: t0 - dt.timedelta(minutes=fmin(r))
    stats = dict(candidates=0, stale=0, unverified=0, blocked=0, low_fit=0, dupes=0, built=0, errors=0)
    why_dropped = []

    if a.no_hunt:
        out = config.DATA / 'hunt'
        results = [(n, json.load(open(out / f)), 0) for n, f in (('hunt', 'new_roles.json'), ('intern', 'new_interns.json'), ('bigtech', 'new_bigtech.json'))]
    else:
        results = run_hunters({s: win[s] / 60 + 0.1 for s in SCRIPTS})
    per_script = {n: (len(l), rc) for n, l, rc in results}
    rows = merged(results); stats['candidates'] = len(rows)
    # Process and verify timestamps in recency order. If a catch-up has more
    # job pages than one run can safely inspect, leave the rest unseen for the
    # next scheduled pass instead of spending the whole cycle on older rows.
    rows.sort(key=lambda r: utc(r['posted_utc']), reverse=True)
    con = db(a.dry_run)
    def unseen(r):   # 'retry' rows (page had no posting time yet) get another look until they are window-old
        s = con.execute('select verdict, at from seen where url=?', (r['url'],)).fetchone()
        return s is None or (s[0] == 'retry' and s[1] >= cutf(r).isoformat())
    rows = [r for r in rows if unseen(r)]

    # 1) freshness: exact timestamp within the window, or proven on the job page
    fresh = []
    # job pages are fetched in parallel (Oct 14: LinkedIn now yields ~400 cards per run; one-by-one took minutes)
    need = [r for r in rows if not r['exact']]
    workers = max(1, int(os.environ.get('JD_WORKERS', '8')))
    pages = {}
    freshness_deadline = time.monotonic() + float(os.environ.get('HUNTER_FRESHNESS_SLICE_MIN', '4')) * 60
    remaining = False
    for start in range(0, len(need), workers):
        if time.monotonic() >= freshness_deadline:
            remaining = True
            print('REFRESH DUE: more recent job-page checks are queued for the next scheduled run', flush=True)
            break
        batch = need[start:start + workers]
        with cf.ThreadPoolExecutor(workers) as ex:
            pages.update((r['url'], pg) for r, pg in zip(batch, ex.map(lambda r: JD.fetch(r['url'], render=False), batch)))
    for r in need:   # pages that need a real browser: one at a time (one headless Chromium per fetch)
        if r['url'] not in pages or pages[r['url']].get('text'):
            continue
        if time.monotonic() >= freshness_deadline:
            remaining = True
            print('REFRESH DUE: browser freshness checks are queued for the next scheduled run', flush=True)
            break
        pages[r['url']] = JD.fetch(r['url'])
    for r in rows:
        if r['exact']:
            if utc(r['posted_utc']) >= cutf(r): fresh.append((r, None))
            else: stats['stale'] += 1
            continue
        if r['url'] not in pages:
            continue  # deferred, not marked stale; it will be reconsidered on the next pass
        j = pages[r['url']]
        ok = (j.get('exact') and j['posted'] and j['posted'] >= cutf(r)) or \
             (j.get('posted_label') and (minutes_ago(j['posted_label']) or 9999) <= fmin(r))
        lk = label_kind(j.get('posted_label'))
        if not ok and cont.get(r.get('script')) and r.get('source') in DATE_ONLY_SRC and not j.get('exact') and lk in (None, 'today') \
                and r.get('posted_utc') and utc(r['posted_utc']) >= t0 - dt.timedelta(hours=26):
            ok = True; r['first_seen'] = True   # date-only source, new since the last continuous scan
        if not ok and in_recovery and r.get('source') in DATE_ONLY_SRC and not j.get('exact'):
            # recovery back to recovery_since.txt (Nov 7): a date-only label ("Posted Yesterday" / "Posted 3 Days Ago")
            # counts when that date is inside the recovery window
            days = label_days(j.get('posted_label'))
            if days is not None and t0 - dt.timedelta(days=days) >= cutf(r):
                ok = True; r['first_seen'] = True
        if ok: fresh.append((r, j))
        else:
            known = j.get('exact') or lk in ('minutes', 'old')   # age known -> too old for good; unknown / 'today' -> retry
            stats['stale' if known else 'unverified'] += 1
            con.execute('insert or ignore into seen values (?,?,?,?,?,?)', (r['url'], r['company'], r['title'], 'stale' if known else 'retry', None, t0.isoformat()))

    # 2) dedupe against Notion (last 35 days) -- one paged query per run, no SQL quota
    links = pairs = set(); per_co = collections.Counter()
    if fresh and not a.dry_run:
        try: links, pairs, per_co = notion_index()
        except Exception as e: print('Notion dedupe failed:', e)

    # New postings must not wait behind source/tier order during a large recovery.
    fresh.sort(key=lambda item: (item[1].get('posted') if item[1] and item[1].get('posted')
                               else utc(item[0]['posted_utc'])), reverse=True)
    built, report = [], []
    refresh_at = time.monotonic() + float(os.environ.get('HUNTER_BUILD_SLICE_MIN', '10')) * 60
    for r, j in fresh:
        if time.monotonic() >= refresh_at:
            remaining = True
            # Keep the original recovery boundary. A later scan retries unfinished
            # candidates, with the newest postings now at the front. This is not a daily cap.
            if not a.dry_run and not recovery.exists():
                recovery.write_text((min([v for v in last.values() if v], default=None) or t0 - dt.timedelta(minutes=fresh_min)).isoformat())
            print('REFRESH DUE: unfinished catch-up retained; next scheduled run fetches new postings', flush=True)
            break
        try:
            if re.fullmatch(r'[0-9a-fA-F-]{36}', r.get('company') or ''):
                public_name = JD.employer(r['url'])
                if public_name: r['company'] = public_name
            key = (norm(r['company'])[:12], norm(r['title']))
            if norm(r['url']) in links or seen_pair(key, pairs):
                stats['dupes'] += 1; continue
            pairs = set(pairs) | {key}   # same company + title twice in one run (multi-location reposts) -> build once
            n_co = sum(n for c, n in per_co.items() if same_co(c, key[0]))
            if config.PER_COMPANY_MAX and n_co >= config.PER_COMPANY_MAX:   # Oct 13: Sephora x3, Clera x2, ElevenLabs x4 ("limiting applications")
                stats['dupes'] += 1; why_dropped.append(f"{r['company']} – {r['title']}: already {n_co} role(s) in {config.PER_COMPANY_DAYS} days"); continue
            if hunt.foreign(r.get('location')) or hunt.non_us_title(r['title']):   # every source, not only hunt.py's own
                stats['blocked'] += 1; why_dropped.append(f"{r['company']} – {r['title']}: non-US ({r.get('location')})"); continue
            if score.STAFFING.search(r['company']):
                stats['blocked'] += 1; why_dropped.append(f"{r['company']}: staffing repost"); continue
            j = j or JD.fetch(r['url'])
            text = j.get('text', '')
            why = score.blocked(text, r['title']) or (lambda w: w and f'company blocklist: {w}')(score.bad_company(r['company'], text))
            if why:
                stats['blocked'] += 1; why_dropped.append(f"{r['company']} – {r['title']}: {why}")
                con.execute('insert or replace into seen values (?,?,?,?,?,?)', (r['url'], r['company'], r['title'], 'blocked:' + why, None, t0.isoformat()))
                continue
            if r['kind'] == 'intern' and score.term_too_early(score.intern_term(r['title'], text)):
                stats['blocked'] += 1; why_dropped.append(f"{r['company']} – {r['title']}: term before 2027")
                con.execute('insert or replace into seen values (?,?,?,?,?,?)', (r['url'], r['company'], r['title'], 'blocked:term', None, t0.isoformat()))
                continue
            sc = score.prescore(r, text)
            reps = repost_count(key, r['url'], t0)   # career-ops detect-reposts: same co+title, other URLs, other days
            if reps >= 2:
                sc['repost'] = reps; sc['fit'] -= 5 * min(reps - 1, 3)
            if sc['fit'] < config.FIT_GATE:
                stats['low_fit'] += 1; why_dropped.append(f"{r['company']} – {r['title']}: fit {sc['fit']}")
                con.execute('insert or replace into seen values (?,?,?,?,?,?)', (r['url'], r['company'], r['title'], 'low', sc['fit'], t0.isoformat()))
                continue
            if config.CAREER_EVAL and not a.dry_run and __import__('community').job_type(r['title'], kind=r['kind']) == 'Full-time':   # Career-Ops eval, Full-time only (Oct 31); fails open
                try:
                    import career_eval
                    ev = career_eval.evaluate(r, text, job='fast_hunter_eval')
                    sc['eval'] = ev
                    if ev['score'] < config.EVAL_GATE:
                        stats['low_fit'] += 1; why_dropped.append(f"{r['company']} – {r['title']}: eval {ev['score']}/5 ({ev['why'][:80]})")
                        con.execute('insert or replace into seen values (?,?,?,?,?,?)', (r['url'], r['company'], r['title'], 'low_eval', sc['fit'], t0.isoformat()))
                        continue
                except Exception:
                    traceback.print_exc()
            built.append(build(r, j, sc, dry=a.dry_run))
            stats['built'] += 1; per_co[key[0]] += 1
            con.execute('insert or replace into seen values (?,?,?,?,?,?)', (r['url'], r['company'], r['title'], 'built', sc['fit'], t0.isoformat()))
        except Exception:
            stats['errors'] += 1; traceback.print_exc()
        finally:
            con.commit()

    today = today_counts(a.dry_run)
    finish(built, stats, per_script, why_dropped, today, a)
    print(f"RUN TIME {(dt.datetime.now(dt.timezone.utc) - t0).total_seconds():.0f}s (built {stats['built']})")
    if not a.dry_run and not a.no_hunt and not remaining and stats['errors'] == 0:
        save_last_ok(per_script, t0)   # per script: start of this scan = everything it saw posted before is covered
        recovery.unlink(missing_ok=True)
    daily_summary(a.dry_run)
    apply_now_daily(a.dry_run)
    prune_daily(a.dry_run)
    try:
        import reports; reports.scheduled(a.dry_run)   # daily apply report (10 PM) + 14-day response review
    except Exception as e:
        print('reports failed:', str(e)[:120])


def prune_daily(dry):
    """Once a day: trim old logs / seen.db rows / temp downloads (prune.py; submissions.jsonl is never touched)."""
    flag = config.DATA / 'daily' / f'prune-{dt.datetime.now(CT):%Y-%m-%d}.done'
    if dry or flag.exists(): return
    try:
        import prune; prune.main()
        flag.parent.mkdir(exist_ok=True); flag.write_text('ok')
    except Exception as e:
        print('prune failed:', str(e)[:120])


def apply_now_daily(dry):
    """Once a day after 7 AM: close dead postings and push the 10 best live roles waiting on him (apply_now.py).
    Started in the BACKGROUND (it took up to 15 min while holding the hunter lock -> 3 skipped 5-min slots daily).
    apply_now.py writes the .done flag itself when it finishes, so a crashed run is started again (at most once an hour)."""
    now = dt.datetime.now(CT)
    flag = config.DATA / 'daily' / f'apply-now-{now:%Y-%m-%d}.done'
    started = config.DATA / 'daily' / 'apply-now.started'
    if dry or now.hour < 7 or flag.exists(): return
    if started.exists() and time.time() - started.stat().st_mtime < 3600: return
    flag.parent.mkdir(exist_ok=True); started.write_text(now.isoformat())
    try:
        (config.DATA / 'logs').mkdir(exist_ok=True)
        log = open(config.DATA / 'logs' / 'apply_now.log', 'a')
        awake = ['/usr/bin/caffeinate', '-i'] if os.path.exists('/usr/bin/caffeinate') else []   # don't sleep mid-run
        subprocess.Popen(awake + [sys.executable, str(Path(__file__).parent / 'apply_now.py'), '--daily-flag', str(flag)],
                         stdout=log, stderr=subprocess.STDOUT, cwd=str(Path(__file__).parent), start_new_session=True)
        print('apply_now started in the background (log: data/logs/apply_now.log)')
    except Exception as e:
        print('apply_now failed to start:', e)


def refresh_resume_facts(max_age_h=24):
    """Re-pull the Notion Experience/Projects bullet bank at most once a day (~25 API calls)."""
    f = config.DATA / 'resume_facts.json'
    if f.exists() and time.time() - f.stat().st_mtime < max_age_h * 3600:
        return
    old = f.read_bytes() if f.exists() else None
    try:
        import resume_sync, json; resume_sync.main()
        import gen_v6, test_resumes  # a Notion edit that loses pinned bullets must not reach live resumes
        errs = test_resumes.check_pins(json.loads(f.read_text()))
        if errs and old is not None:
            f.write_bytes(old); print('resume_sync: new Notion data rejected, kept previous facts:', '; '.join(errs))
    except Exception as e:  # keep the old file; never block a build on Notion
        if old is not None: f.write_bytes(old)
        print('resume_sync failed (using cached facts):', e)


def build(r, j, sc, dry=False):
    import gen_v6, packet
    gen_v6.OUT = str(config.RESUMES)
    refresh_resume_facts()
    kind = r['kind']; text = j.get('text', '')
    term = score.intern_term(r['title'], text) if kind == 'intern' else None
    enroll = score.enrollment_rule(text)
    fid = 'F' + time.strftime('%m%d%H%M') + '-' + norm(r['company'])[:6] + str(abs(hash(r['url'])) % 1000)  # neutral folder id
    req = sc['required'] if config.SKILLS_MODE == 'all' else [t for t in sc['required'] if t in score.EVIDENCE]
    pref = sc['preferred'] if config.SKILLS_MODE == 'all' else [t for t in sc['preferred'] if t in score.EVIDENCE]
    spec = {'id': fid, 'file': fid, 'position': r['title'], 'required': req, 'preferred': pref,
            'kind': 'intern' if kind == 'intern' else 'newgrad', 'location': r.get('location', '')}
    posted = (j.get('posted').strftime('%Y-%m-%d %H:%M') if j.get('posted') else r['posted_utc'])
    src = r.get('source') + (f" ({minutes_ago(j.get('posted_label'))} min old)" if j.get('posted_label') and minutes_ago(j.get('posted_label')) else '')
    if r.get('first_seen'): src += ' (new since last scan; source shows date only)'
    notes = ('FAST | ' + (f'INTERN {term} | ' if kind == 'intern' else '') + f"{src} | fit {sc['fit']} | match {sc['match']}% | evidence {sc['evidence']}% | resume {gen_v6.VERSION}"
             + (' | stretch' if sc['stretch'] else '') + (' | ELIGIBILITY? enrollment rule' if enroll else ''))
    item = dict(company=r['company'], title=r['title'], kind=kind, posted=posted, fit=sc['fit'], source=r.get('source'), link=r['url'], notes=notes)
    if dry:
        print('[dry-run] would build:', json.dumps(item)); return item
    w = packet.write(r, text, sc, kind, term)
    # Oct 14 (the owner): EVERY required + preferred JD skill goes on the resume -> merge the LLM's exact list
    def merge(a, b): return list(dict.fromkeys([x.strip() for x in (a or []) + (b or []) if x and x.strip()]))
    spec['required'] = merge(spec['required'], w.get('skills_required'))
    spec['preferred'] = [x for x in merge(spec['preferred'], w.get('skills_preferred')) if x not in spec['required']]
    spec['jd_text'] = text[:8000]
    res = gen_v6.make(spec)
    if res['pages'] != 1: notes += ' | resume 2 pages?'
    md = packet.page_markdown(r, j, sc, kind, term, enroll, w)
    if res.get('unbacked'):   # JD skills listed on the resume without a bullet that shows them -> interview prep
        md += '\n\n### Skills listed from the job post without a bullet showing them (be ready to talk about these)\n' + ', '.join(res['unbacked'])
    if res.get('numbers'):  # recruiter guide: every number on the resume is an interview question
        md += '\n\n### Be ready to explain (numbers on this resume)\n' + '\n'.join(f'- {b}' for b in res['numbers'])
    folder = res['folder']
    # final-PDF checks (resume_check.py): ATS parse problems block auto-submit; coverage + gaps go on the page
    try:
        import resume_check as RC
        pdf_path = os.path.join(folder, P.file('Resume', 'pdf'))
        txt = RC.pdf_text(pdf_path)
        errs = RC.parse_check(pdf_path, txt)
        rep = RC.match_report(txt, spec['required'], spec['preferred'])
        notes += f" | cov {rep['required_pct']}%"
        if errs: notes += ' | RESUME CHECK FAILED: ' + '; '.join(errs)[:200]
        md += '\n\n' + RC.markdown(rep, errs, res.get('reworded') or ())
        item['coverage'] = rep['required_pct']
    except Exception as e:
        print('resume check skipped:', str(e)[:100])
    if w.get('knockouts'):   # hard screens from the JD: worth a look before applying
        md += '\n\n### Knockout requirements in the job post\n' + '\n'.join(f'- {k}' for k in w['knockouts'][:8])
        notes += ' | knockouts: ' + '; '.join(w['knockouts'][:3])[:160]
    import notion
    pdf_id = notion.upload_file(os.path.join(folder, P.file('Resume', 'pdf')))
    docx_id = notion.upload_file(os.path.join(folder, P.file('Resume', 'docx')))
    extra = []
    try:  # cover letter as files too (same text as the page's "### Cover letter")
        import coverletter
        cl_pdf = notion.upload_file(coverletter.to_pdf(w['cover_letter'], folder))
        cl_docx = notion.upload_file(coverletter.to_docx(w['cover_letter'], folder))
        extra = [{'type': 'heading_3', 'heading_3': {'rich_text': notion._rt('Cover letter files')}},
                 {'type': 'file', 'file': {'type': 'file_upload', 'file_upload': {'id': cl_pdf}}},
                 {'type': 'file', 'file': {'type': 'file_upload', 'file_upload': {'id': cl_docx}}}]
    except Exception as e:
        print('cover letter files skipped:', e)
    link = j.get('apply_url') or r['url']
    import community
    # where the application happens (Oct 20, owner): Easy Apply only when LinkedIn's own page shows its Easy Apply button
    # (a missing outside URL just means LinkedIn hides it from logged-out visitors); else the site Apply leads to
    via, apply_link, via_tag = community.via_and_link(link, r['company'], r['title'])
    if via_tag: notes = f"{via_tag} | {notes}"
    props = notion.props(Company=r['company'], Position=r['title'], Location=r.get('location', ''), Link=link,
                         DatePosted=posted[:10], Status='To apply', Notes=notes, Recruiters=posting_contacts(text, r['url']) or None)
    props['Apply Via'] = {'select': {'name': via}}
    if apply_link: props['Apply Link'] = {'url': apply_link[:1900]}
    props['Type'] = {'select': {'name': community.job_type(r['title'], kind=kind)}}   # same value as the Community board
    page = notion.create_row(props,
                             notion.file_blocks(pdf_id, docx_id) + extra + notion.blocks_from_md(md))
    item['notion'] = page.get('url'); item['folder'] = folder
    try:   # mirror to the public Community Job Board (company, title, type, date, location, link only)
        import community
        community.add(r['company'], r['title'], link, posted[:10], r.get('location', ''), kind=kind, via=via)
    except Exception as e:
        print('community board add failed:', str(e)[:100])
    return item


NOREPLY = re.compile(r'no-?reply|donotreply|do-not-reply|privacy|accommodat|disabilit|eeo|ada@|legal|support@|security@|abuse|unsubscribe|example\.', re.I)


def posting_contacts(text, url):
    """Recruiting emails printed in the job post itself (public by definition, 0 tokens): 'send your resume to jobs@…'.
    Outreach then drafts to them without extra research. Accessibility/EEO/no-reply inboxes are skipped."""
    found = []
    for e in re.findall(r'[\w.+-]+@[\w-]+(?:\.[\w-]+)+', text or ''):
        e = e.strip('.').lower()
        if NOREPLY.search(e) or e in found or e.endswith(('.png', '.jpg')): continue
        found.append(e)
    return '\n'.join(f'{e} – published in the job post – {url}' for e in found[:3])


def today_counts(dry):
    if dry: return None
    try:
        import notion
        start = dt.datetime.now(CT).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(dt.timezone.utc)
        rows = notion.query({'and': [{'timestamp': 'created_time', 'created_time': {'on_or_after': start.isoformat()}},
                                     {'property': 'Notes', 'rich_text': {'contains': 'FAST'}}]})
        intern = sum('INTERN' in (x.get('Notes') or '') for x in rows)
        return dict(ng=len(rows) - intern, intern=intern)
    except Exception:
        return None


def finish(built, stats, per_script, why, today, a):
    if why and not a.dry_run:   # Oct 28: keep every drop reason for pattern analysis (career-ops discard-analytics idea)
        with open(config.DATA / 'discards.jsonl', 'a') as fh:
            for w in why: fh.write(json.dumps({'at': dt.datetime.now(CT).isoformat(timespec='seconds'), 'why': w}) + '\n')
    tc = f"today: NG {today['ng']}, Intern {today['intern']}" if today else 'today: count n/a'
    lines = [f"Fast Hunter local run {dt.datetime.now(CT):%Y-%m-%d %H:%M} CT (window {a.minutes} min){' DRY-RUN' if a.dry_run else ''}"]
    if built:
        lines.append('| Company | Role | Kind | Posted (UTC) | Fit | Source | Notion |')
        lines.append('|---|---|---|---|---|---|---|')
        for b in built:
            lines.append(f"| {b['company']} | {b['title']} | {'Intern' if b['kind'] == 'intern' else 'NG'} | {b['posted']} | {b['fit']} | {b['source']} | {b.get('notion', '-')} |")
    else:
        lines.append('No new roles.')
    lines.append('scripts: ' + ', '.join(f'{k}={n}' + ('' if rc == 0 else f' ({rc})') for k, (n, rc) in per_script.items()))
    lines.append('counts: ' + ', '.join(f'{k}={v}' for k, v in stats.items()) + ' | ' + tc)
    lines += [f'  dropped: {w}' for w in why[:15]]
    txt = '\n'.join(lines)
    print(txt)
    log = config.DATA / 'runs'; log.mkdir(exist_ok=True)
    with open(log / f'{dt.datetime.now(CT):%Y-%m-%d}.md', 'a') as f: f.write(txt + '\n\n')
    if built and not a.dry_run:
        notify.push(f"{len(built)} new best-match roles — apply now: " + ', '.join(b['company'] for b in built[:6]) + f' ({tc})')
    # Oct 20: off by default -- li_resolve calls LinkedIn's internal API with his session, which LinkedIn's terms /
    # robots.txt prohibit (account risk). Opt in with LI_RESOLVE=1 in .env.
    if built and not a.dry_run and os.environ.get('LI_RESOLVE') == '1':   # where each new LinkedIn row really applies
        try:
            import subprocess, sys as _s
            subprocess.run([_s.executable, str(config.ROOT / 'jobhunter' / 'li_resolve.py'), '--pending'], timeout=300,
                           cwd=str(config.ROOT / 'jobhunter'))
        except Exception as e:
            print('LinkedIn resolve skipped:', str(e)[:100])
    if not a.dry_run:   # Oct 19: Apply Prep sat unloaded Oct 13-19 and nobody noticed -> the 15-min hunter checks on it
        try:
            import metrics; metrics.stale_alarm()
        except Exception as e:
            print('apply alarm failed:', str(e)[:100])


def daily_summary(dry):
    """First run at/after 10 PM CT writes data/daily/Daily Jobs — <date>.md from today's runs (once per day)."""
    now = dt.datetime.now(CT)
    f = config.DATA / 'daily' / f'Daily Jobs — {now:%Y-%m-%d}.md'
    if now.hour < 22 or f.exists() or dry: return
    f.parent.mkdir(exist_ok=True)
    runs = config.DATA / 'runs' / f'{now:%Y-%m-%d}.md'
    rows = [l for l in (runs.read_text().splitlines() if runs.exists() else []) if l.startswith('| ') and not l.startswith('| Company')]
    ng = [l for l in rows if '| NG |' in l]; it = [l for l in rows if '| Intern |' in l]
    hdr = '| Company | Role | Kind | Posted (UTC) | Fit | Source | Notion |\n|---|---|---|---|---|---|---|'
    f.write_text(f"# Daily Jobs — {now:%Y-%m-%d}\n\nNew grad: {len(ng)} · Internships: {len(it)}\n\n## New grad\n{hdr}\n" + '\n'.join(ng)
                 + f"\n\n## Internships\n{hdr}\n" + '\n'.join(it) + '\n')


if __name__ == '__main__':
    main()
