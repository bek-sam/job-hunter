"""Intern hunter v1 — add-on to hunt.py (ATS Watcher v1). Save hunt.py first, same folder.
Finds NEW SWE / AI / data internships and co-ops for Winter / Spring / Summer / Fall 2027 posted in the last N hours.
Sources: (1) Simplify Summer2027-Internships listings.json (has terms, category, degrees, exact epoch time);
         (2) the same company boards hunt.py polls (Greenhouse/Ashby/Lever/SmartRecruiters/Workday), plus every
             board seen in the intern listings, with intern titles allowed.
Usage:  python3 intern_hunt.py [--hours 3] [--out new_interns.json] [--no-boards]
Tiers: I = intern title with a 2027 term (or no term in title), W = Simplify says "Winter 2026" (check the start
       date in the JD: keep only if it starts Jan 2027 or later), D = defense company (usually citizenship/clearance).
"""
import json, re, time, argparse, datetime as dt, concurrent.futures as cf
import hunt

INTERN_JSON = 'https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json'
KEEP_TERMS = {'Winter 2027', 'Spring 2027', 'Summer 2027', 'Fall 2027'}
CHECK_TERMS = {'Winter 2026', 'N/A'}          # ambiguous: open JD, keep only if start >= Jan 2027
CATS = re.compile(r'(software|ai/ml|data science)', re.I)
INTERN = re.compile(r'(\bintern\b|internship|co-?op\b)', re.I)
OLD_TERM = re.compile(r'(summer|fall|spring) ?2026|2025', re.I)
SWE = re.compile(r'(software|\bswe\b|\bsde\b|developer|programmer|machine learning|\bml\b|\bai\b|genai|llm|data engineer|'
                 r'back[- ]?end|front[- ]?end|full[- ]?stack|mobile|\bios\b|android|cloud|devops|platform|web|infrastructure|'
                 r'site reliability|computer science|technology intern|^engineering intern$|^software intern$)', re.I)
ISKIP = re.compile(hunt.SKIP.pattern.replace(r'intern\b|internship|co-?op|', ''), re.I)
NOT_SW = re.compile(r'(hardware|electrical|mechanical|civil|chemical|manufacturing|process engineer|test technician|industrial|'
                    r'materials|quality|aircraft|operations engineering|multiphysics|chemistry|biology|pathobiology|customer technical|'
                    r'application engineering|systems engineering|systems security|structural|marketing|finance|accounting|\bhr\b|'
                    r'human resources|legal|design verification|asic|fpga|\brf\b|capital markets)', re.I)
CORE = re.compile(r'(software|computer science|developer|programmer|\bswe\b|\bsde\b|machine learning|\bml\b|\bai\b|data engineer|cloud|web|'
                  r'full[- ]?stack|back[- ]?end|front[- ]?end|mobile|\bios\b|android|devops|\.net)', re.I)

def iwant(title, loc, co=''):
    if not INTERN.search(title) or not SWE.search(title): return None
    if ISKIP.search(title) or OLD_TERM.search(title): return None
    if NOT_SW.search(title) and not CORE.search(title): return None
    if re.search(r'research|chemistry|biology', title, re.I) and not re.search(r'software|engineer', title, re.I): return None
    if loc and hunt.NON_US.search(loc) and not hunt.US.search(loc): return None
    if loc and not hunt.US.search(loc) and not re.search(r'remote', loc, re.I): return None
    return 'D' if hunt.DEFENSE.search(co) else 'I'

if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--hours', type=float, default=3)
    ap.add_argument('--out', default='new_interns.json'); ap.add_argument('--workers', type=int, default=48)
    ap.add_argument('--no-boards', action='store_true'); a = ap.parse_args()
    t0 = time.time(); cut = t0 - a.hours * 3600
    lst = json.loads(hunt.get(INTERN_JSON, timeout=90))
    rows = []
    for x in lst:  # (1) Simplify intern listings
        if not x.get('active') or x.get('date_posted', 0) < cut: continue
        if not CATS.search(x.get('category') or ''): continue
        if x.get('degrees') and not any('bachelor' in d.lower() for d in x['degrees']): continue  # PhD/MS-only
        if 'citizenship' in str(x.get('sponsorship', '')).lower(): continue
        terms = set(x.get('terms') or [])
        if terms & KEEP_TERMS: tier = None
        elif terms & CHECK_TERMS or not terms: tier = 'W'
        else: continue
        loc = '; '.join(x.get('locations', []))
        t = iwant(x['title'] if INTERN.search(x['title']) else x['title'] + ' Intern', loc, x['company_name'])
        if not t: continue
        rows.append(('simplify-intern', x['company_name'], x['title'], loc, x['url'], x['date_posted'],
                     tier or t, ', '.join(sorted(terms))))
    if not a.no_boards:  # (2) company boards, intern titles allowed
        bl = sorted(set(hunt.boards(lst)) | set(hunt.boards(json.loads(hunt.get(hunt.SIMPLIFY, timeout=60)))))
        errs = 0
        with cf.ThreadPoolExecutor(a.workers) as ex:
            for r, e in ex.map(lambda b: hunt.run_board(b, cut, a.hours), bl):
                errs += bool(e)
                for src, co, title, loc, url, t in r:
                    tier = iwant(title, loc or url.replace('-', ' ').replace('/', ' '), co)
                    if tier: rows.append((src, co, title, loc, url, t, tier, ''))
        print(f'boards={len(bl)} errors={errs}')
    res, keys = [], set()
    for src, co, title, loc, url, t, tier, terms in rows:
        k = (re.sub(r'\W', '', co.lower())[:12], title.lower(), loc.lower()[:20])
        if k in keys: continue
        keys.add(k)
        res.append(dict(source=src, tier=tier, company=co, title=title, location=loc.strip(), url=url, terms=terms,
                        posted_utc=dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime('%Y-%m-%d %H:%M')))
    res.sort(key=lambda r: ('IWD'.index(r['tier']), -hunt.iso(r['posted_utc'].replace(' ', 'T') + ':00+00:00')))  # tier, newest first
    json.dump(res, open(a.out, 'w'), indent=1)
    print(f'intern candidates={len(res)} in {time.time()-t0:.0f}s')
    for r in res: print(f"[{r['tier']}] {r['posted_utc']} | {r['source']} | {r['company']} | {r['title']} | {r['terms']} | {r['location'][:40]} | {r['url']}")
