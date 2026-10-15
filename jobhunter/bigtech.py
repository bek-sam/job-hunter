"""Big-tech + LinkedIn hunter v2. Add-on to hunt.py + intern_hunt.py (save all 3 in one folder).
v2 (Oct 9 2026): from 5 companies to ~210 large employers, each checked directly on its own careers system. All tested from the cloud Oct 9 2026.
  Custom APIs : Amazon, Google, Apple, Microsoft, IBM, Goldman Sachs, TikTok
  Eightfold   : Netflix, Qualcomm, Micron, Starbucks, John Deere, Morgan Stanley, Bayer, PayPal, Ericsson, NetApp, Lam Research
  Oracle Cloud: JPMorgan Chase, Oracle, Texas Instruments, Ford
  Workday     : ~75 big companies (NVIDIA, Salesforce, Adobe, Intel, Walmart, Capital One, Cisco, Visa, ...) - BIG_WD below
  Greenhouse/Ashby/Lever/SmartRecruiters: 111 well-known tech/AI/fintech/quant boards (Stripe, Airbnb, OpenAI, Databricks, ...)
    - 63 of them were NOT in hunt.py's Simplify-built watchlist before v2.
  LinkedIn public guest search (no login), new-grad + intern keyword searches.
NOT reachable from the cloud (Oct 9 2026): Meta, Tesla, Uber, Citadel, AMD, Dell, Intuit, Bloomberg (no dates) - those need Chrome.
Usage:  python3 bigtech.py [--hours 1] [--out new_bigtech.json] [--no-linkedin]
Output rows: kind = newgrad (tier A/B/D, hunt.py rules) or intern (tier I/D, intern_hunt.py rules); exact = True when the
source gives an exact post time, False when it gives only a date ("Posted Today" etc.) -> confirm the time on the job page.
"""
import json, re, time, argparse, html, datetime as dt, urllib.parse, urllib.request, concurrent.futures as cf
import hunt, intern_hunt as ih

def get(url, data=None, timeout=30, tries=3, headers=None):
    for k in range(tries):
        try:
            if headers is None: return hunt.get(url, data, timeout)
            h = dict(hunt.UA, **headers)
            if data is not None: h['Content-Type'] = 'application/json'
            r = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None, headers=h)
            with urllib.request.urlopen(r, timeout=timeout) as x: return x.read().decode('utf-8', 'replace')
        except Exception as e:
            if k < tries - 1 and ('429' in str(e) or 'URLError' in type(e).__name__ or 'reset' in str(e) or 'timed out' in str(e)):
                time.sleep(4 * (k + 1)); continue
            raise

FOREIGN_ONLY = re.compile(r'(united states|\busa?\b|\bu\.s\.)', re.I)

def classify(co, title, loc):
    # v2: drop rows whose location names only a foreign country (e.g. "Bangalore, IN, India", "United Kingdom remote")
    if loc and hunt.NON_US.search(loc) and not FOREIGN_ONLY.search(loc): return None, None
    t = ih.iwant(title, loc, co)
    if t: return 'intern', t
    t = hunt.want(title, loc, co)
    return ('newgrad', t) if t else (None, None)

DAY = 86400
QUERIES = ('software engineer', 'software developer', 'new grad', 'intern', 'machine learning')

# ---------------- custom APIs ----------------
def amazon(cut):
    for q in ('software engineer', 'software development engineer', 'software intern', 'machine learning', 'data engineer'):
        u = 'https://www.amazon.jobs/en/search.json?' + urllib.parse.urlencode(
            {'base_query': q, 'sort': 'recent', 'result_limit': 100, 'country': 'USA'})
        for j in json.loads(get(u)).get('jobs', []):
            try: t = dt.datetime.strptime(j['posted_date'], '%B %d, %Y').replace(tzinfo=dt.timezone.utc).timestamp()
            except Exception: continue
            if t >= cut - DAY:  # date only: keep today + yesterday, dedupe vs Notion later
                yield 'Amazon', j['title'], j.get('normalized_location') or j.get('location', ''), 'https://www.amazon.jobs' + j['job_path'], t, False

def google(cut):
    base = 'https://www.google.com/about/careers/applications/jobs/results/'
    for lvl in ('INTERN_AND_APPRENTICE', 'EARLY'):
        for page in (1, 2, 3):
            s = get(f'{base}?q=software&target_level={lvl}&sort_by=date&page={page}')
            i = s.find("key: 'ds:1'"); j = s.find('data:', i) + 5
            d, _ = json.JSONDecoder().raw_decode(s[j:])
            jobs = (d or [None])[0] or []
            old = False
            for x in jobs:
                t = (x[12] or [0])[0] if len(x) > 12 else 0
                if t < cut: old = True; continue
                loc = '; '.join(l[0] for l in (x[9] or []))
                slug = re.sub(r'[^a-z0-9]+', '-', x[1].lower()).strip('-')
                yield 'Google', x[1], loc, f'{base}{x[0]}-{slug}', t, True
            if old or not jobs: break

def apple(cut):
    for q in ('software', 'machine learning', 'intern'):
        s = get('https://jobs.apple.com/en-us/search?' + urllib.parse.urlencode({'search': q, 'sort': 'newest', 'location': 'united-states-USA'}))
        k = '__staticRouterHydrationData = JSON.parse('
        raw, _ = json.JSONDecoder().raw_decode(s[s.find(k) + len(k):])
        for r in json.loads(raw)['loaderData']['search']['searchResults']:
            t = hunt.iso(r.get('postDateInGMT', ''))
            if t >= cut:
                loc = '; '.join(filter(None, [', '.join(filter(None, [l.get('city'), l.get('stateProvince'), l.get('countryName')])) for l in r.get('locations', [])])) or 'United States'
                yield 'Apple', r['postingTitle'], loc, f"https://jobs.apple.com/en-us/details/{r['positionId']}/{r.get('transformedPostingTitle', '')}", t, True

def ibm(cut):
    for q in ('software engineer', 'software developer', 'intern'):
        body = {'appId': 'careers', 'scopes': ['careers2'], 'size': 50, 'sort': [{'dcdate': 'desc'}],
                'query': {'bool': {'must': [{'simple_query_string': {'query': q, 'fields': ['title^3', 'description^2']}}]}},
                '_source': ['title', 'url', 'dcdate', 'field_keyword_05', 'field_keyword_19']}
        for h in json.loads(get('https://www-api.ibm.com/search/api/v2', body))['hits']['hits']:
            s = h['_source']; t = hunt.iso(s.get('dcdate', '') + 'T12:00:00+00:00')
            if t >= cut - DAY:
                yield 'IBM', s['title'], f"{s.get('field_keyword_19', '')}, {s.get('field_keyword_05', '')}", s['url'], t, False

def goldman(cut):
    q = ('query GetRoles($searchQueryInput: RoleSearchQueryInput!) { roleSearch(searchQueryInput: $searchQueryInput) '
         '{ totalCount items { roleId jobTitle createdDate locations { city state country } externalSource { sourceId } } } }')
    for exp in (['EARLY_CAREER'], ['PROFESSIONAL'], ['INTERNSHIP']):
        body = {'operationName': 'GetRoles', 'query': q, 'variables': {'searchQueryInput': {
            'page': {'pageSize': 40, 'pageNumber': 0}, 'sort': {'sortStrategy': 'POSTED_DATE', 'sortOrder': 'DESC'},
            'filters': [], 'experiences': exp, 'searchTerm': 'software engineer'}}}
        try: d = json.loads(get('https://api-higher.gs.com/gateway/api/v1/graphql', body, headers={'Origin': 'https://higher.gs.com'}))
        except Exception: continue
        for r in ((d.get('data') or {}).get('roleSearch') or {}).get('items') or []:
            c = str(r.get('createdDate') or '')
            t = float(c) / (1000 if float(c or 0) > 1e11 else 1) if c.replace('.', '').isdigit() else hunt.iso(c if 'T' in c else c + 'T00:00:00+00:00')
            exact = 'T' in c or c.isdigit()
            if t >= cut - (0 if exact else DAY):
                loc = '; '.join(', '.join(filter(None, [l.get('city'), l.get('state'), l.get('country')])) for l in r.get('locations') or [])
                yield 'Goldman Sachs', r['jobTitle'], loc, f"https://higher.gs.com/roles/{(r.get('externalSource') or {}).get('sourceId') or r['roleId']}", t, exact

def tiktok(cut):
    # list API has no post time; the job id (ByteDance snowflake) carries its creation time in the top 32 bits
    H = {'website-path': 'tiktok', 'accept-language': 'en-US', 'Origin': 'https://lifeattiktok.com', 'Referer': 'https://lifeattiktok.com/'}
    for q in ('software engineer', 'new grad', 'graduate', 'intern', 'machine learning', 'backend', 'frontend'):
        d = json.loads(get('https://api.lifeattiktok.com/api/v1/public/supplier/search/job/posts',
                           {'recruitment_id_list': [], 'job_category_id_list': [], 'subject_id_list': [], 'location_code_list': [],
                            'keyword': q, 'limit': 100, 'offset': 0}, headers=H))
        for j in (d.get('data') or {}).get('job_post_list') or []:
            t = int(j['id']) >> 32
            if t >= cut:
                c = j.get('city_info') or {}; p = c.get('parent') or {}; pp = p.get('parent') or {}
                loc = ', '.join(filter(None, [c.get('en_name'), p.get('en_name'), pp.get('en_name')]))
                yield 'TikTok', j['title'], loc, f"https://lifeattiktok.com/search/{j['id']}", t, True

# ---------------- Eightfold (pcsx / apply v2) ----------------
EIGHTFOLD = [  # (company, host, domain, api)   api: pcsx = /api/pcsx/search, v2 = /api/apply/v2/jobs
    ('Microsoft', 'apply.careers.microsoft.com', 'microsoft.com', 'pcsx'),
    ('Netflix', 'explore.jobs.netflix.net', 'netflix.com', 'v2'), ('Qualcomm', 'careers.qualcomm.com', 'qualcomm.com', 'pcsx'),
    ('Micron', 'careers.micron.com', 'micron.com', 'pcsx'), ('Starbucks', 'starbucks.eightfold.ai', 'starbucks.com', 'pcsx'),
    ('John Deere', 'careers.deere.com', 'johndeere.com', 'pcsx'), ('Morgan Stanley', 'morganstanley.eightfold.ai', 'morganstanley.com', 'pcsx'),
    ('Bayer', 'talent.bayer.com', 'bayer.com', 'v2'), ('PayPal', 'paypal.eightfold.ai', 'paypal.com', 'pcsx'),
    ('Ericsson', 'jobs.ericsson.com', 'ericsson.com', 'pcsx'), ('NetApp', 'netapp.eightfold.ai', 'netapp.com', 'v2'),
    ('Lam Research', 'careers.lamresearch.com', 'lamresearch.com', 'pcsx')]

def eightfold(co, host, dom, api, cut):
    for q in ('software engineer', 'software intern', 'machine learning engineer', 'data engineer'):
        for start in (0, 10, 20):
            path = '/api/pcsx/search' if api == 'pcsx' else '/api/apply/v2/jobs'
            u = f'https://{host}{path}?' + urllib.parse.urlencode({'domain': dom, 'query': q, 'location': 'United States', 'start': start, 'sort_by': 'timestamp'})
            d = json.loads(get(u, tries=2))
            ps = (d.get('data') or {}).get('positions') if api == 'pcsx' else d.get('positions')
            old = False
            for p in ps or []:
                t = float(p.get('postedTs') or p.get('t_create') or 0)
                if t < cut - DAY: old = True; continue
                exact = t % 3600 != 0          # midnight-rounded = date only
                if exact and t < cut: continue
                url = p.get('positionUrl') or p.get('canonicalPositionUrl') or f"/careers/job/{p.get('id')}"
                loc = '; '.join(p.get('standardizedLocations') or p.get('locations') or [p.get('location') or ''])
                yield co, p['name'], loc, url if url.startswith('http') else f'https://{host}{url}', t, exact
            if old or not ps: break
            time.sleep(1)

# ---------------- Oracle Recruiting Cloud ----------------
ORACLE = [('JPMorgan Chase', 'jpmc.fa.oraclecloud.com', 'CX_1001'), ('Oracle', 'eeho.fa.us2.oraclecloud.com', 'CX_45001'),
          ('Texas Instruments', 'edbz.fa.us2.oraclecloud.com', 'CX'), ('Ford', 'efds.fa.em5.oraclecloud.com', 'CX_1')]

def oracle(co, host, site, cut):
    for q in ('software', 'intern', 'machine learning'):
        u = (f'https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true&expand=requisitionList'
             f'&finder=findReqs;siteNumber={site},keyword={urllib.parse.quote(q)},sortBy=POSTING_DATES_DESC,limit=50')
        for r in json.loads(get(u))['items'][0].get('requisitionList', []):
            t = hunt.iso((r.get('PostedDate') or '') + 'T12:00:00+00:00')
            if t >= cut - DAY:
                yield co, r['Title'], r.get('PrimaryLocation', '') + ' ' + r.get('PrimaryLocationCountry', ''), \
                      f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{r['Id']}", t, False

# ---------------- Workday (big companies; hunt.wd = newest first, "Posted Today") ----------------
BIG_WD = [  # (company, tenant, wdN, site) - all returned jobs on Oct 9 2026
    ('NVIDIA', 'nvidia', 'wd5', 'NVIDIAExternalCareerSite'), ('Salesforce', 'salesforce', 'wd12', 'External_Career_Site'),
    ('Adobe', 'adobe', 'wd5', 'external_experienced'), ('Intel', 'intel', 'wd1', 'External'), ('HP', 'hp', 'wd5', 'ExternalCareerSite'),
    ('HPE', 'hpe', 'wd5', 'Jobsathpe'), ('Capital One', 'capitalone', 'wd12', 'Capital_One'),
    ('Walmart', 'walmart', 'wd504', 'WalmartExternal'), ('Target', 'target', 'wd5', 'targetcareers'), ('Disney', 'disney', 'wd5', 'disneycareer'),
    ('Mastercard', 'mastercard', 'wd1', 'CorporateCareers'), ('Boeing', 'boeing', 'wd1', 'EXTERNAL_CAREERS'), ('Workday', 'workday', 'wd5', 'Workday'),
    ('CrowdStrike', 'crowdstrike', 'wd5', 'crowdstrikecareers'), ('Broadcom', 'broadcom', 'wd1', 'External_Career'), ('Autodesk', 'autodesk', 'wd1', 'Ext'),
    ('Zoom', 'zoom', 'wd5', 'Zoom'), ('Motorola Solutions', 'motorolasolutions', 'wd5', 'Careers'), ('Analog Devices', 'analogdevices', 'wd1', 'External'),
    ('Micron', 'micron', 'wd1', 'External'), ('Fidelity', 'fmr', 'wd1', 'targeted'), ('Bank of America', 'ghr', 'wd1', 'Lateral-US'),
    ('Wells Fargo', 'wf', 'wd1', 'WellsFargoJobs'), ('Citi', 'citi', 'wd5', '2'), ('T-Mobile', 'tmobile', 'wd1', 'External'),
    ('Verizon', 'verizon', 'wd12', 'verizon-careers'), ('Accenture', 'accenture', 'wd103', 'AccentureCareers'), ('Comcast', 'comcast', 'wd115', 'Comcast_Careers'),
    ('CVS Health', 'cvshealth', 'wd1', 'CVS_Health_Careers'), ('Expedia', 'expedia', 'wd108', 'search'), ('Nike', 'nike', 'wd1', 'nke'),
    ('Samsung', 'sec', 'wd3', 'Samsung_Careers'), ('Sony', 'sonyglobal', 'wd1', 'SonyGlobalCareers'), ('Red Hat', 'redhat', 'wd5', 'jobs'),
    ('Yahoo', 'ouryahoo', 'wd5', 'careers'), ('General Motors', 'generalmotors', 'wd5', 'Careers_GM'), ('Toyota', 'toyota', 'wd503', 'tmna'),
    ('Home Depot', 'homedepot', 'wd5', 'CareerDepot'), ('USAA', 'usaa', 'wd1', 'USAAJOBSWD'), ('Synchrony', 'synchronyfinancial', 'wd5', 'careers'),
    ('PNC', 'pnc', 'wd5', 'External'), ('U.S. Bank', 'usbank', 'wd1', 'US_Bank_Careers'), ('State Street', 'statestreet', 'wd1', 'Global'),
    ('Northern Trust', 'ntrs', 'wd1', 'northerntrust'), ('Vanguard', 'vanguard', 'wd5', 'vanguard_external'), ('BlackRock', 'blackrock', 'wd1', 'BlackRock_Professional'),
    ('Caterpillar', 'cat', 'wd5', 'CaterpillarCareers'), ('GE Vernova', 'gevernova', 'wd5', 'Vernova_ExternalSite'), ('GE Aerospace', 'geaerospace', 'wd5', 'GE_ExternalSite'),
    ('Cisco', 'cisco', 'wd5', 'Cisco_Careers'), ('eBay', 'ebay', 'wd5', 'apply'), ('Snap', 'snapchat', 'wd1', 'snap'), ('Chevron', 'chevron', 'wd5', 'jobs'),
    ('Humana', 'humana', 'wd5', 'Humana_External_Career_Site'), ('Medtronic', 'medtronic', 'wd1', 'MedtronicCareers'), ('Pfizer', 'pfizer', 'wd1', 'PfizerCareers'),
    ('Johnson Controls', 'jci', 'wd5', 'JCI'), ('Philips', 'philips', 'wd3', 'jobs-and-careers'), ("Lowe's", 'lowes', 'wd5', 'LWS_External_CS'),
    ('T. Rowe Price', 'troweprice', 'wd5', 'TRowePrice'), ('Southwest Airlines', 'swa', 'wd1', 'external'), ('FedEx', 'fedex', 'wd1', 'FXE-LAC_External_Career_Site'),
    ('Visa', 'visa', 'wd5', 'Visa'), ('Equifax', 'equifax', 'wd5', 'External'), ('TransUnion', 'transunion', 'wd5', 'TransUnion'), ('Gartner', 'gartner', 'wd5', 'EXT'),
    ('Ciena', 'ciena', 'wd5', 'Careers'), ('KLA', 'kla', 'wd1', 'Search'), ('Applied Materials', 'amat', 'wd1', 'External'), ('Cadence', 'cadence', 'wd1', 'External_Careers'),
    ('Marvell', 'marvell', 'wd1', 'MarvellCareers'), ('Palo Alto Networks', 'paloaltonetworks', 'wd5', 'panwexternalcareers')]

# ---------------- Greenhouse / Ashby / Lever / SmartRecruiters (big names; hunt.py functions) ----------------
BIG_ATS = [('greenhouse', s) for s in (
    'stripe airbnb pinterest coinbase reddit robinhood datadog cloudflare figma discord roblox instacart lyft dropbox twilio okta '
    'mongodb elastic gitlab affirm brex chime sofi samsara anthropic databricks scaleai asana airtable duolingo squarespace toast '
    'block waymo spacex verkada faire gusto flexport nuro andurilindustries jumptrading imc point72 virtu akunacapital '
    'oldmissioncapital janestreet vercel mercury carta webflow intercom zscaler sentinellabs tanium qualtrics nextdoor peloton '
    'calendly lattice gemini doximity oscar sambanovasystems togetherai xai watershed applovin epicgames riotgames twitch mozilla '
    'wikimedia khanacademy coursera').split()] + \
    [('ashby', s) for s in ('openai notion ramp plaid snowflake linear vanta cursor perplexity elevenlabs cohere sierra decagon harvey '
                            'replit supabase posthog clickhouse modal baseten runway pika suno character mercor').split()] + \
    [('lever', s) for s in 'palantir spotify zoox wealthfront shieldai matchgroup outreach'.split()] + \
    [('smartrecruiters', s) for s in ('ServiceNow', 'Experian', 'Canva')]

def board_jobs(b, cut, hours):
    rows, err = hunt.run_board(b, cut, hours)
    if err: raise RuntimeError(err)
    for k, co, title, loc, url, t in rows: yield co, title, loc, url, t, k != 'workday'

# ---------------- LinkedIn guest search ----------------
LI_Q = [('software engineer new grad', 2), ('entry level software engineer', 2), ('junior software engineer', 2),
        ('associate software engineer', 2), ('software engineer I', 2), ('machine learning engineer', 2),
        ('software engineer intern', 1), ('software engineering internship 2027', 1), ('machine learning intern', 1),
        ('software developer co-op', 1), ('data engineer intern', 1)]

def linkedin(cut, hours):
    secs = max(3600, int(hours * 3600))
    for q, lvl in LI_Q:
        for start in (0, 25):
            u = 'https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?' + urllib.parse.urlencode(
                {'keywords': q, 'location': 'United States', 'f_TPR': f'r{secs}', 'f_E': lvl, 'start': start})
            try: s = get(u, tries=2)
            except Exception: break
            cards = s.split('<li>')[1:]
            for c in cards:
                f = lambda p: html.unescape(re.sub(r'<.*?>', '', (re.search(p, c, re.S) or [None, ''])[1])).strip()
                title = f(r'base-search-card__title[^>]*>(.*?)</h3>'); co = f(r'base-search-card__subtitle[^>]*>(.*?)</h4>')
                loc = f(r'job-search-card__location[^>]*>(.*?)</span>')
                link = (re.search(r'href="(https://[^"]*linkedin\.com/jobs/view/[^"?]+)', c) or [None, ''])[1]
                ts = (re.search(r'datetime="([\d-]+)"', c) or [None, ''])[1]
                if title and link: yield co, title, loc, link, hunt.iso(ts + 'T00:00:00+00:00') if ts else time.time(), False
            if len(cards) < 25: break
            time.sleep(2)

def sources(hours, no_li):
    s = {'amazon': amazon, 'google': google, 'apple': apple, 'ibm': ibm, 'goldman': goldman, 'tiktok': tiktok}
    for co, h, d, api in EIGHTFOLD: s['eightfold:' + co] = (lambda c, co=co, h=h, d=d, api=api: eightfold(co, h, d, api, c))
    for co, h, site in ORACLE: s['oracle:' + co] = (lambda c, co=co, h=h, site=site: oracle(co, h, site, c))
    names = {(b[1], b[2], b[3]): b[0] for b in BIG_WD}
    for co, ten, wdn, site in BIG_WD:
        s['workday:' + co] = (lambda c, b=('workday', ten, wdn, site), co=co: ((co,) + r[1:] for r in board_jobs(b, c, hours)))
    for b in BIG_ATS: s[f'{b[0]}:{b[1]}'] = (lambda c, b=b: board_jobs(b, c, hours))
    if not no_li: s['linkedin'] = lambda c: linkedin(c, hours)
    return s

if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--hours', type=float, default=1)
    ap.add_argument('--out', default='new_bigtech.json'); ap.add_argument('--no-linkedin', action='store_true')
    ap.add_argument('--workers', type=int, default=24); a = ap.parse_args()
    t0 = time.time(); cut = t0 - a.hours * 3600
    srcs = sources(a.hours, a.no_linkedin)
    def run(item):
        name, fn = item; out = []
        try:
            for co, title, loc, url, t, exact in fn(cut): out.append((co, title, loc, url, t, exact))
            return name, out, None
        except Exception as e: return name, out, f'{type(e).__name__}: {str(e)[:60]}'
    rows, stat, errs = [], {}, {}
    with cf.ThreadPoolExecutor(a.workers) as ex:
        for name, out, err in ex.map(run, srcs.items()):
            stat[name] = len(out)
            if err: errs[name] = err
            for co, title, loc, url, t, exact in out:
                kind, tier = classify(co, title, loc)
                if kind: rows.append(dict(source=name.split(':')[0], kind=kind, tier=tier, company=co, title=title, location=loc, url=url, exact=exact,
                                          posted_utc=dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime('%Y-%m-%d %H:%M')))
    seen, res = set(), []
    for r in rows:
        k = (r['company'].lower()[:12], r['title'].lower(), r['location'].lower()[:20])
        if k not in seen: seen.add(k); res.append(r)
    res.sort(key=lambda r: (r['kind'] != 'newgrad', 'ABIWD'.index(r['tier']), not r['exact'], -hunt.iso(r['posted_utc'].replace(' ', 'T') + ':00+00:00')))
    json.dump(res, open(a.out, 'w'), indent=1)
    print('per source:', {k: v for k, v in stat.items() if not k.startswith(('greenhouse', 'ashby', 'lever', 'smartrecruiters', 'workday'))})
    print(f'sources={len(srcs)} ok={len(srcs) - len(errs)} errors={len(errs)}', {k: v for k, v in errs.items()})
    print(f'candidates={len(res)} (newgrad {sum(r["kind"]=="newgrad" for r in res)}, intern {sum(r["kind"]=="intern" for r in res)}) in {time.time()-t0:.0f}s')
    for r in res: print(f"[{r['kind']}/{r['tier']}{'' if r['exact'] else '~date'}] {r['posted_utc']} | {r['source']} | {r['company']} | {r['title']} | {r['location'][:40]} | {r['url']}")
