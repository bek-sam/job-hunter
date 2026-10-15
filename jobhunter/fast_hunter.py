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
import config, jd as JD, score, notify

CT = ZoneInfo('America/Chicago')
PY = sys.executable
SOFFICE_DIR = '/Applications/LibreOffice.app/Contents/MacOS'
os.environ['PATH'] = SOFFICE_DIR + os.pathsep + os.environ.get('PATH', '')
EXACT_SRC = {'greenhouse', 'ashby', 'lever', 'smartrecruiters', 'simplify', 'simplify-intern', 'amazon', 'google',
             'apple', 'microsoft', 'tiktok', 'eightfold'}


def db(dry=False):
    c = sqlite3.connect(':memory:' if dry else config.DATA / 'seen.db')
    c.execute('create table if not exists seen (url text primary key, company text, title text, verdict text, fit int, at text)')
    return c


def run_hunters(hours):
    out = config.DATA / 'hunt'; out.mkdir(exist_ok=True)
    jobs = {'hunt': ['hunt.py', '--out', str(out / 'new_roles.json')],
            'intern': ['intern_hunt.py', '--out', str(out / 'new_interns.json')],
            'bigtech': ['bigtech.py', '--out', str(out / 'new_bigtech.json')]}
    def go(name):
        args = jobs[name]
        try:
            r = subprocess.run([PY, args[0], '--hours', f'{hours:.2f}'] + args[1:], cwd=config.PKG, capture_output=True, text=True, timeout=600)
            (out / f'{name}.log').write_text(r.stdout + r.stderr)
            return name, json.load(open(args[2])), r.returncode
        except Exception as e:
            return name, [], f'failed: {e}'
    with cf.ThreadPoolExecutor(3) as ex:
        return list(ex.map(go, jobs))


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


def norm(s):
    return re.sub(r'[^a-z0-9]', '', (s or '').lower())


def notion_index():
    import notion
    rows = notion.recent_rows(35)
    links = {norm(r.get('Link')) for r in rows if r.get('Link')}
    pairs = {(norm(r.get('Company'))[:12], norm(r.get('Position'))) for r in rows}
    week = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=7)).isoformat()
    per_co = collections.Counter(norm(r.get('Company'))[:12] for r in rows if (r.get('created') or '') >= week)
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
    if not config.NOTION_TOKEN and not a.dry_run:
        print('NOTION_TOKEN is empty in .env -> running as --dry-run (no Notion writes, no LLM calls)')
        a.dry_run = True
    fresh_min = max(a.minutes, config.FRESH_MIN)   # how old a posting may be when we first see it (search-index lag)
    t0 = dt.datetime.now(dt.timezone.utc); cut = t0 - dt.timedelta(minutes=fresh_min)
    stats = dict(candidates=0, stale=0, unverified=0, blocked=0, low_fit=0, dupes=0, built=0, errors=0)
    why_dropped = []

    if a.no_hunt:
        out = config.DATA / 'hunt'
        results = [(n, json.load(open(out / f)), 0) for n, f in (('hunt', 'new_roles.json'), ('intern', 'new_interns.json'), ('bigtech', 'new_bigtech.json'))]
    else:
        results = run_hunters(fresh_min / 60 + 0.1)
    per_script = {n: (len(l), rc) for n, l, rc in results}
    rows = merged(results); stats['candidates'] = len(rows)
    con = db(a.dry_run)
    retry_cut = (t0 - dt.timedelta(minutes=fresh_min)).isoformat()
    def unseen(r):   # 'retry' rows (page had no posting time yet) get another look until they are fresh_min old
        s = con.execute('select verdict, at from seen where url=?', (r['url'],)).fetchone()
        return s is None or (s[0] == 'retry' and s[1] >= retry_cut)
    rows = [r for r in rows if unseen(r)]

    # 1) freshness: exact timestamp within the window, or proven on the job page
    fresh = []
    for r in rows:
        if r['exact']:
            if utc(r['posted_utc']) >= cut: fresh.append((r, None))
            else: stats['stale'] += 1
            continue
        j = JD.fetch(r['url'])
        ok = (j.get('exact') and j['posted'] and j['posted'] >= cut) or \
             (j.get('posted_label') and (minutes_ago(j['posted_label']) or 9999) <= fresh_min)
        if ok: fresh.append((r, j))
        else:
            known = j.get('exact') or j.get('posted_label')   # we know the age -> too old for good; no age shown -> retry later
            stats['stale' if known else 'unverified'] += 1
            con.execute('insert or ignore into seen values (?,?,?,?,?,?)', (r['url'], r['company'], r['title'], 'stale' if known else 'retry', None, t0.isoformat()))

    # 2) dedupe against Notion (last 35 days) -- one paged query per run, no SQL quota
    links = pairs = set(); per_co = collections.Counter()
    if fresh and not a.dry_run:
        try: links, pairs, per_co = notion_index()
        except Exception as e: print('Notion dedupe failed:', e)

    built, report = [], []
    for r, j in fresh:
        try:
            key = (norm(r['company'])[:12], norm(r['title']))
            if norm(r['url']) in links or key in pairs:
                stats['dupes'] += 1; continue
            pairs = set(pairs) | {key}   # same company + title twice in one run (multi-location reposts) -> build once
            if per_co[key[0]] >= config.PER_COMPANY_WEEK:   # Oct 13: Sephora x3, Clera x2, ElevenLabs x4 ("limiting applications")
                stats['dupes'] += 1; why_dropped.append(f"{r['company']} – {r['title']}: already {per_co[key[0]]} roles this week"); continue
            if score.STAFFING.search(r['company']):
                stats['blocked'] += 1; why_dropped.append(f"{r['company']}: staffing repost"); continue
            j = j or JD.fetch(r['url'])
            text = j.get('text', '')
            why = score.blocked(text, r['title'])
            if why:
                stats['blocked'] += 1; why_dropped.append(f"{r['company']} – {r['title']}: {why}")
                con.execute('insert or replace into seen values (?,?,?,?,?,?)', (r['url'], r['company'], r['title'], 'blocked:' + why, None, t0.isoformat()))
                continue
            if r['kind'] == 'intern' and score.term_too_early(score.intern_term(r['title'], text)):
                stats['blocked'] += 1; why_dropped.append(f"{r['company']} – {r['title']}: term before 2027")
                con.execute('insert or replace into seen values (?,?,?,?,?,?)', (r['url'], r['company'], r['title'], 'blocked:term', None, t0.isoformat()))
                continue
            sc = score.prescore(r, text)
            if sc['fit'] < config.FIT_GATE:
                stats['low_fit'] += 1; why_dropped.append(f"{r['company']} – {r['title']}: fit {sc['fit']}")
                con.execute('insert or replace into seen values (?,?,?,?,?,?)', (r['url'], r['company'], r['title'], 'low', sc['fit'], t0.isoformat()))
                continue
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
    daily_summary(a.dry_run)
    apply_now_daily(a.dry_run)


def apply_now_daily(dry):
    """Once a day after 7 AM CT: close dead postings and push the 10 best live roles waiting on him (apply_now.py)."""
    now = dt.datetime.now(CT)
    flag = config.DATA / 'daily' / f'apply-now-{now:%Y-%m-%d}.done'
    if dry or now.hour < 7 or flag.exists(): return
    flag.parent.mkdir(exist_ok=True); flag.write_text(now.isoformat())
    try:
        r = subprocess.run([sys.executable, str(Path(__file__).parent / 'apply_now.py')], capture_output=True, text=True, timeout=900)
        print(r.stdout[-1500:], r.stderr[-500:])
    except Exception as e:
        print('apply_now failed:', e)


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
    notes = ('FAST | ' + (f'INTERN {term} | ' if kind == 'intern' else '') + f"{src} | fit {sc['fit']} | match {sc['match']}% | evidence {sc['evidence']}%"
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
    import notion
    folder = res['folder']
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
    # where the application happens: LinkedIn posting without an outside apply URL = Easy Apply; else the target site
    via = ('LinkedIn Easy Apply' if 'linkedin.com' in link and j.get('ats') == 'linkedin' and not j.get('apply_url')
           else community.apply_via(link))
    props = notion.props(Company=r['company'], Position=r['title'], Location=r.get('location', ''), Link=link,
                         DatePosted=posted[:10], Status='To apply', Notes=notes, Recruiters=posting_contacts(text, r['url']) or None)
    props['Apply Via'] = {'select': {'name': via}}
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
