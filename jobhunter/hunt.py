"""Job Hunter — ATS watcher v1.
Polls company job boards directly (Greenhouse, Ashby, Lever, Workday, SmartRecruiters)
+ the GitHub tracker lists, and prints NEW new-grad/entry SWE roles posted in the last N hours.

Usage:  python3 hunt.py [--hours 3] [--out new_roles.json]
Watchlist: built fresh each run from the Simplify listings.json URLs (every company that has
ever posted a new-grad role there, ~2,700 boards) + EXTRA below. No state file needed:
freshness comes from each board's own publish time; dedupe against Notion afterwards.
"""
import json, re, sys, time, argparse, datetime as dt, concurrent.futures as cf
import os, random, threading, urllib.error, urllib.request

UA = {'User-Agent': 'Mozilla/5.0 (job-hunter)', 'Accept': 'application/json'}
SIMPLIFY = 'https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json'
TRACKERS_MD = [
 'https://raw.githubusercontent.com/speedyapply/2027-SWE-College-Jobs/main/NEW_GRAD_USA.md',
 'https://raw.githubusercontent.com/speedyapply/2026-SWE-College-Jobs/main/NEW_GRAD_USA.md',
 'https://raw.githubusercontent.com/jobright-ai/2026-Software-Engineer-New-Grad/master/README.md']
# extra boards to always watch: (ats, slug...) — add companies here as you find them
EXTRA = [('ashby', 'teleskope'), ('ashby', 'confido'), ('workday', 'johngore', 'wd1', 'Careers')]

PATS = {'greenhouse': r'(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_app\?for=)?([A-Za-z0-9_-]+)',
        'ashby': r'jobs\.ashbyhq\.com/([^/?#]+)',
        'lever': r'jobs\.lever\.co/([^/?#]+)',
        'workday': r'https?://([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([^/?#]+)',
        'smartrecruiters': r'jobs\.smartrecruiters\.com/([^/?#]+)'}

ROLE = re.compile(r'(software|\bswe\b|\bsde\b|member of technical staff|forward deployed|programmer|'
                  r'((full[- ]?stack|front[- ]?end|back[- ]?end|machine learning|\bml\b|\bai\b|genai|generative|data|mobile|\bios\b|android|'
                  r'cloud|platform|devops|site reliability|web|infrastructure|application|applications|systems|product|llm|python|java|\.net|'
                  r'react|node)\b.{0,25}\b(engineer|developer)))', re.I)
SKIP = re.compile(r'(senior|\bsr\.?\b|staff|principal|\blead\b|manager|director|\bhead\b|architect|intern\b|internship|co-?op|'
                  r'\bphd\b|\bii\b|\biii\b|\biv\b|\b[2-5]\+? ?years|sales|recruit|account|clearance|ts/sci|\bsecret\b|polygraph|'
                  r'mid[- ]level|mid[- ]career|experienced|expert|specialist|technician|support engineer|qa analyst|intermediate|\bavp\b|\bvp\b|'
                  r'distinguished|\bsme\b|scientist|counsel|consultant|\blevel ?[2-9]\b|\bleve [2-9]\b|\b(engineer|developer) [2-9]\b|'
                  r'1[0-9]\+? years|inspector|plumber|checker)', re.I)
ENTRY = re.compile(r'(new ?grad|graduate|entry|early career|early-career|junior|\bjr\b|associate|university|campus|college|'
                   r'\bi\b\s*$|\bi\b[,(\-–]|engineer i\b|level 1|\bl1\b|2026|2027|apprentice|rotational|emerging talent|residency)', re.I)
NON_US = re.compile(r'(canada|toronto|vancouver|montreal|ottawa|\buk\b|united kingdom|london|england|ireland|dublin|belfast|leeds|manchester|edinburgh|scotland|\bindia\b|bangalore|'
                    r'bengaluru|hyderabad|pune|chennai|germany|berlin|munich|france|paris|netherlands|amsterdam|poland|spain|'
                    r'singapore|japan|tokyo|china|shanghai|beijing|hong kong|taiwan|korea|australia|sydney|mexico|brazil|'
                    r'\bind\b|israel|tel aviv|switzerland|zurich|sweden|romania|serbia|philippines|vietnam|argentina|colombia|emea|apac|latam|'
                    r'lausanne|geneva|malaysia|kuala lumpur|south america|latin america|chile|peru|uruguay|costa rica|portugal|lisbon|italy|'
                    r'gurgaon|gurugram|noida|mumbai|delhi|ukraine|czech|prague|hungary|budapest|denmark|copenhagen|norway|finland|'
                    r'belgium|austria|vienna|warsaw|krakow|bucharest|egypt|nigeria|kenya|pakistan|indonesia|thailand|new zealand|'
                    r'dubai|\buae\b|turkey|istanbul|estonia|lithuania|bulgaria|greece|cyprus|mauritius|ebene|south africa|morocco)', re.I)
US = re.compile(r'(united states|\busa\b|\bu\.s\.|\bus\b|remote|anywhere|, ?(al|ak|az|ar|ca|co|ct|de|dc|fl|ga|hi|id|il|in|ia|ks|ky|la|me|md|'
                r'ma|mi|mn|ms|mo|mt|ne|nv|nh|nj|nm|ny|nc|nd|oh|ok|or|pa|ri|sc|sd|tn|tx|ut|vt|va|wa|wv|wi|wy)\b|new york|nyc|'
                r'san francisco|seattle|austin|boston|chicago|los angeles|san jose|palo alto|mountain view|sunnyvale|menlo park|'
                r'redmond|bellevue|atlanta|denver|dallas|houston|miami|washington|philadelphia|pittsburgh|san diego|portland|'
                r'raleigh|salt lake|phoenix|detroit|minneapolis|nashville|charlotte|columbus|bay area|california|texas|virginia)', re.I)

# Oct 14: 48 parallel requests made Workday answer HTTP 429 for ~330 boards every run (and intern_hunt.py doubled it).
# Now: at most WD_PER_HOST requests at a time per Workday server (wd1, wd5, ...) and retries with backoff on 429 / 5xx.
WD_PER_HOST = int(os.environ.get('WD_PER_HOST', '6'))
_host_locks, _hl = {}, threading.Lock()

def _gate(url):
    m = re.search(r'\.(wd\d+)\.myworkdayjobs\.com', url)
    if not m: return None
    with _hl: return _host_locks.setdefault(m.group(1), threading.BoundedSemaphore(WD_PER_HOST))

def get(url, data=None, timeout=20, tries=4):
    h = dict(UA)
    if data is not None: h['Content-Type'] = 'application/json'
    gate = _gate(url)
    for k in range(tries):
        req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None, headers=h)
        try:
            if gate: gate.acquire()
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    return r.read().decode('utf-8', 'replace')
            finally:
                if gate: gate.release()
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504, 520) or k == tries - 1: raise
            ra = e.headers.get('Retry-After') if e.headers else None
            time.sleep(min(float(ra), 20) if ra and ra.isdigit() else 2 ** k + random.random() * 2)
        except (TimeoutError, urllib.error.URLError) as e:
            if k >= 1: raise
            time.sleep(1 + random.random())

def iso(s):
    try: return dt.datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp()
    except Exception: return 0

DEFENSE = re.compile(r'(lockheed|l3harris|northrop|\bngc\b|booz allen|caci|national security agency|raytheon|\brtx\b|general dynamics|'
                     r'leidos|\bsaic\b|\bbae\b|frontier technology|mach industries|anduril|huntington ingalls|peraton|mitre|parsons|amentum|\bpae\b|accenture federal)', re.I)

def foreign(loc):
    """Location names a non-US place and no US one. "Remote" alone is not a US place: "Remote Canada", "India remote
    India", "Lausanne remote Switzerland" were all applied to (Oct 28 review) because US matched the word remote."""
    if not loc or not NON_US.search(loc): return False
    rest = re.sub(r'remote|anywhere', ' ', loc, flags=re.I)
    if re.search(r'\bindia\b|bangalore|bengaluru|hyderabad|pune|chennai|gurgaon|gurugram|noida|mumbai|delhi', loc, re.I):
        rest = re.sub(r',\s*IN\b', ' ', rest)   # "Bangalore, IN" is India, not Indiana
    return not US.search(rest)


def non_us_title(title):
    """A non-US place named in the title's location part ("... - Leeds, United Kingdom", "(London)"), not "India Payments"."""
    parts = re.split(r'\s[-–—|@]\s|[,(/]', title or '', maxsplit=1)
    tail = parts[1] if len(parts) > 1 else ''
    return bool(tail and NON_US.search(tail) and not US.search(tail))


def other_school_program(title):
    """Programs only for one university's students ("Northeastern University Software Engineer Co-op", Oct 19)."""
    import applicant as P
    m = re.search(r'\b((?:[A-Z][\w&.-]+ ){1,3}(?:University|College|Institute of Technology)(?: of [A-Z][\w-]+)?)\b|\b(UC [A-Z]\w+|Georgia Tech|Virginia Tech|Texas A&M|MIT|CMU)\b', title or '')
    return bool(m and not re.search((P.get('school', 'rx') or r'(?!x)x'), title, re.I) and re.search(r'co-?op|intern|program|fellow', title, re.I))


def want(title, loc, co=''):
    if not ROLE.search(title) or SKIP.search(title): return None
    if foreign(loc): return None
    if non_us_title(title) or other_school_program(title): return None   # Oct 19: "Software Engineer - Leeds, United Kingdom" had no loc
    if loc and not US.search(loc) and not re.search(r'remote', loc, re.I): return None
    if DEFENSE.search(co): return 'D'            # defense: usually US-citizen / clearance -> check JD, low priority
    return 'A' if ENTRY.search(title) else 'B'   # A = explicit entry-level title, B = plain title (check JD years)

def gh(slug, cut):
    d = json.loads(get(f'https://boards-api.greenhouse.io/v1/boards/{slug}/jobs'))
    for j in d.get('jobs', []):
        t = iso(j.get('first_published') or j.get('updated_at') or '')
        if t >= cut: yield j.get('company_name') or slug, j['title'], j.get('location', {}).get('name', ''), j['absolute_url'], t

def ashby(slug, cut):
    d = json.loads(get(f'https://api.ashbyhq.com/posting-api/job-board/{slug}'))
    for j in d.get('jobs', []):
        t = iso(j.get('publishedAt', ''))
        if t >= cut and j.get('isListed', True):
            loc = j.get('location', '') + (' remote' if j.get('isRemote') else '') + ' ' + str((j.get('address') or {}).get('postalAddress', {}).get('addressCountry', ''))
            yield slug, j['title'], loc, j['jobUrl'], t

def lever(slug, cut):
    d = json.loads(get(f'https://api.lever.co/v0/postings/{slug}?mode=json'))
    for j in d if isinstance(d, list) else []:
        t = (j.get('createdAt') or 0) / 1000
        if t >= cut:
            c = j.get('categories', {})
            yield slug, j['text'], (c.get('location') or '') + ' ' + (j.get('country') or ''), j['hostedUrl'], t

def sr(slug, cut):
    d = json.loads(get(f'https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100'))
    for j in d.get('content', []):
        t = iso(j.get('releasedDate', ''))
        if t >= cut:
            l = j.get('location', {})
            yield j.get('company', {}).get('name', slug), j['name'], f"{l.get('city','')}, {l.get('region','')} {l.get('country','')}", \
                  f"https://jobs.smartrecruiters.com/{slug}/{j['id']}", t

def wd(tenant, wdn, site, cut, hours):
    ok = ('Posted Today',) + (('Posted Yesterday',) if hours > 20 else ())
    base = f'https://{tenant}.{wdn}.myworkdayjobs.com'
    for off in range(0, 400, 20):     # default sort = newest first; stop at first older posting (big boards post 100+ a day)
        d = json.loads(get(f'{base}/wday/cxs/{tenant}/{site}/jobs', {'appliedFacets': {}, 'limit': 20, 'offset': off, 'searchText': ''}))
        ps = d.get('jobPostings', [])
        for j in ps:
            if j.get('postedOn') in ok:
                yield tenant, j['title'], j.get('locationsText', ''), f"{base}/{site}{j['externalPath']}", time.time()
        if not ps or any(j.get('postedOn') not in ok for j in ps): break

WATCHLIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'watchlist.json')

def watchlist():
    """Extra boards found by research (Oct 15: YC hiring companies + well-known tech employers + other GitHub job lists),
    validated to exist. Edit watchlist.json to add companies: ["greenhouse", "slug"] / ["workday", "tenant", "wd5", "Site"]."""
    try: return {tuple(b) for b in json.load(open(WATCHLIST))}
    except Exception: return set()

def boards(simplify):
    seen = set(EXTRA) | watchlist()
    for x in simplify:
        for k, p in PATS.items():
            m = re.search(p, x.get('url', ''))
            if m: seen.add((k,) + m.groups())
    return sorted(seen)

# Shared per-run cache: hunt.py saves every board's postings; intern_hunt.py (run right after) reuses them instead of
# hitting the same ~2,800 boards again. Boards that answer 404 (deleted / renamed) are skipped for a day.
CACHE_DIR = None
_cache, _dead, _new_dead = {}, {}, {}

def load_cache(out_path, max_age=900):
    global CACHE_DIR
    CACHE_DIR = os.path.dirname(os.path.abspath(out_path))
    try:
        c = json.load(open(os.path.join(CACHE_DIR, 'board_cache.json')))
        if time.time() - c.get('t', 0) <= max_age: _cache.update({k: (c['cut'], v) for k, v in c['boards'].items()})
    except Exception: pass
    try:
        _dead.update({k: t for k, t in json.load(open(os.path.join(CACHE_DIR, 'dead_boards.json'))).items() if time.time() - t < 86400})
    except Exception: pass

def save_cache(results, cut):
    if not CACHE_DIR: return
    tmp = os.path.join(CACHE_DIR, 'board_cache.json.tmp')
    json.dump({'t': time.time(), 'cut': cut, 'boards': results}, open(tmp, 'w'))
    os.replace(tmp, os.path.join(CACHE_DIR, 'board_cache.json'))
    json.dump({**_dead, **_new_dead}, open(os.path.join(CACHE_DIR, 'dead_boards.json'), 'w'))

def run_board(b, cut, hours):
    key = json.dumps(list(b))
    if key in _dead: return [], None                      # 404 earlier today: skip
    if key in _cache and _cache[key][0] <= cut:            # fetched minutes ago by hunt.py with a wider window
        return [tuple(r) for r in _cache[key][1] if r[-1] >= cut], None
    k = b[0]
    try:
        it = {'greenhouse': lambda: gh(b[1], cut), 'ashby': lambda: ashby(b[1], cut), 'lever': lambda: lever(b[1], cut),
              'smartrecruiters': lambda: sr(b[1], cut), 'workday': lambda: wd(b[1], b[2], b[3], cut, hours)}[k]()
        rows = [(k,) + r for r in it]
        _fresh[key] = rows
        return rows, None
    except urllib.error.HTTPError as e:
        if e.code in (404, 410, 422): _new_dead[key] = time.time()   # board deleted, renamed or moved off this ATS
        return [], f'{b}: HTTP {e.code}'
    except Exception as e:
        return [], f'{b}: {type(e).__name__}'

_fresh = {}   # boards fetched successfully this run -> saved for intern_hunt.py

def trackers(cut, simplify):
    out = []
    for x in simplify:   # Simplify: exact epoch time
        if x.get('active') and max(x.get('date_posted', 0), x.get('date_updated', 0) if False else 0) >= cut:
            if 'citizenship' in str(x.get('sponsorship', '')).lower(): continue   # Oct 28: same skips as intern_hunt.py
            if x.get('degrees') and not any('bachelor' in d.lower() for d in x['degrees']): continue
            out.append(('simplify', x['company_name'], x['title'], '; '.join(x.get('locations', [])), x['url'], x['date_posted']))
    today = dt.datetime.now(dt.timezone.utc).strftime('%b %d')
    for u in TRACKERS_MD:   # markdown lists: rows dated today / 0d
        try: md = get(u)
        except Exception: continue
        last = None
        for l in md.splitlines():
            if not l.startswith('|'): continue
            c = [x.strip() for x in l.split('|')[1:-1]]
            if len(c) < 4: continue
            if 'jobright' in u:
                m0 = re.search(r'\[(.*?)\]', c[0]); last = m0.group(1) if m0 else last
                if not c[-1].endswith(today): continue
                m = re.search(r'\*\*\[(.*?)\]\((.*?)\)', c[1])
                if m: out.append(('jobright', last, m.group(1), c[2], m.group(2), time.time()))
            elif c[-1] == '0d':
                hm = re.search(r'href="([^"]+)"', c[-2]); co = re.sub('<.*?>', '', c[0])
                if hm: out.append(('speedyapply', co, c[1], c[2], hm.group(1), time.time()))
    return out

if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--hours', type=float, default=3); ap.add_argument('--out', default='new_roles.json')
    ap.add_argument('--workers', type=int, default=48); a = ap.parse_args()
    t0 = time.time(); cut = t0 - a.hours * 3600
    load_cache(a.out, max_age=0)   # hunt.py always fetches fresh; it only loads the dead-board list
    simplify = json.loads(get(SIMPLIFY, timeout=60))
    bl = boards(simplify)
    rows, errs = [], []
    with cf.ThreadPoolExecutor(a.workers) as ex:
        for r, e in ex.map(lambda b: run_board(b, cut, a.hours), bl):
            rows += r
            if e: errs.append(e)
    save_cache(_fresh, cut)
    rows += trackers(cut, simplify)
    res, keys = [], set()
    for src, co, title, loc, url, t in rows:
        tier = want(title, loc or url.replace('-', ' ').replace('/', ' '), co)
        if not tier: continue
        k = (re.sub(r'\W', '', co.lower())[:12], title.lower(), loc.lower()[:20])
        if k in keys: continue
        keys.add(k)
        res.append(dict(source=src, tier=tier, company=co, title=title, location=loc.strip(), url=url,
                        posted_utc=dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime('%Y-%m-%d %H:%M')))
    res.sort(key=lambda r: (r['tier'], r['source'] in ('jobright', 'speedyapply', 'simplify'), r['posted_utc']), reverse=False)
    res.sort(key=lambda r: ('ABD'.index(r['tier']), r['source'] in ('jobright', 'speedyapply'), -iso(r['posted_utc'].replace(' ', 'T') + ':00+00:00')))
    json.dump(res, open(a.out, 'w'), indent=1)
    import collections
    kinds = collections.Counter(e.split(': ', 1)[1] for e in errs)
    print(f'boards={len(bl)} errors={len(errs)} {dict(kinds)} skipped_dead={len(_dead)} candidates={len(res)} in {time.time()-t0:.0f}s')
    for r in res: print(f"[{r['tier']}] {r['posted_utc']} | {r['source']} | {r['company']} | {r['title']} | {r['location'][:40]} | {r['url']}")
    if len(errs) > 0.3 * len(bl): print('HUNT FAILED: most boards errored', file=sys.stderr); sys.exit(2)   # not a successful scan
