"""Community Job Board (Notion, public info only): every job the hunter saves to Applications 2026 is mirrored here
with Company, Job Title, Type, Date Posted, Location, Apply Link. No status/notes/recruiters (owner, Oct 13 2026).
Member columns (one select per community member: Applied/OA/Interview/Offer/Rejected/Not interested)
are filled in by each member — this script never writes them (Oct 14 2026).

  python community.py --backfill      # copy all current (not Closed) Applications 2026 rows once (deduped by link)
fast_hunter calls add() for every new row.
"""
import argparse, re, sys, time
import config, notion

DS = config.COMMUNITY_DS


def job_type(title, notes='', kind=None):
    t = f'{title} {notes}'.lower()
    if kind == 'intern' or 'INTERN' in (notes or '') or re.search(r'\bintern(ship)?\b|co-?op\b', t): return 'Internship'
    if re.search(r'\bcontract(or)?\b|\bc2c\b|\bw-?2\b|\b1099\b|temporary', t): return 'Contract'
    if re.search(r'new grad|new college|entry[- ]level|early career|university|graduate|junior|\bjr\.?\b|associate|'
                 r'engineer i\b|developer i\b|\b(engineer|developer) 1\b|20(26|27) start|class of 202[67]', t): return 'New Grad'
    return 'Full-time'


VIA_OPTS = [('LinkedIn Easy Apply', 'blue'), ('LinkedIn', 'gray'), ('Workday', 'orange'), ('Greenhouse', 'green'), ('Ashby', 'purple'),
            ('Lever', 'pink'), ('iCIMS', 'brown'), ('SmartRecruiters', 'yellow'), ('SuccessFactors', 'red'), ('Oracle', 'red'),
            ('Taleo', 'brown'), ('Eightfold', 'yellow'), ('Jobvite', 'pink'), ('Workable', 'green'), ('Company website', 'default')]
SITES = [('Workday', r'myworkday(jobs|site)\.com|\bworkday\b'), ('Greenhouse', r'greenhouse\.io|gh_jid=|\bgreenhouse\b'), ('Ashby', r'ashbyhq\.com|\bashby\b'),
         ('Lever', r'lever\.co|\blever\b'), ('iCIMS', r'icims'), ('SmartRecruiters', r'smartrecruiters'), ('SuccessFactors', r'successfactors|sapsf'),
         ('Oracle', r'oraclecloud|\boracle\b'), ('Taleo', r'taleo'), ('Eightfold', r'eightfold'), ('Jobvite', r'jobvite'), ('Workable', r'workable')]


def apply_via(link, hint='', easy=None):
    """Where the application happens: LinkedIn Easy Apply / an ATS / Company website. `hint` = apply_prep reasons or the
    external apply URL; easy=True/False when known from the LinkedIn posting."""
    link, hint = link or '', hint or ''
    if 'linkedin.com' in link:
        if easy is True or re.search(r'easy apply', hint, re.I): return 'LinkedIn Easy Apply'
        m = re.search(r'→ (\w+)', hint)
        for name, rx in SITES:
            if re.search(rx, hint, re.I) and (m is None or re.search(rx, m.group(1), re.I) or m.group(1) == 'generic'): return name
        if m and m.group(1) in ('generic',): return 'Company website'
        return 'LinkedIn'
    for name, rx in SITES:
        if re.search(rx, link, re.I): return name
    return 'Company website'


def ensure_column(ds):
    """Add the 'Apply Via' select to a data source (idempotent)."""
    notion._req('PATCH', f'/data_sources/{ds}', {'properties': {'Apply Via': {'select': {'options': [{'name': n, 'color': c} for n, c in VIA_OPTS]}}}})


def _known_links():
    links, cur = set(), None
    while True:
        body = {'page_size': 100}
        if cur: body['start_cursor'] = cur
        r = notion._req('POST', f'/data_sources/{DS}/query', body)
        for p in r['results']:
            u = (p['properties'].get('Apply Link') or {}).get('url')
            if u: links.add(u)
        if not r.get('has_more'): return links
        cur = r['next_cursor']


def add(company, title, link, date_posted=None, location='', kind=None, notes='', known=None, via=None):
    if known is not None and link in known: return False
    props = {'Company': {'title': notion._rt((company or '?')[:200])}, 'Job Title': {'rich_text': notion._rt((title or '')[:300])},
             'Type': {'select': {'name': job_type(title or '', notes, kind)}}, 'Location': {'rich_text': notion._rt((location or '')[:200])},
             'Apply Link': {'url': link or None}, 'Apply Via': {'select': {'name': via or apply_via(link, notes)}}}
    if date_posted: props['Date Posted'] = {'date': {'start': date_posted[:10]}}
    notion.create_row(props, ds=DS)
    if known is not None: known.add(link)
    return True


def backfill():
    rows = [r for r in notion.query() if r.get('Status') != 'Closed' and r.get('Link')]
    known = _known_links(); n = 0
    for r in sorted(rows, key=lambda r: r.get('Date Posted') or r.get('created') or ''):
        try:
            if add(r.get('Company'), r.get('Position'), r.get('Link'), r.get('Date Posted') or (r.get('created') or '')[:10],
                   r.get('Location') or '', notes=r.get('Notes') or '', known=known): n += 1
        except Exception as e:
            print('skip', r.get('Company'), str(e)[:100]); time.sleep(2)
    print(f'community board: +{n} jobs (of {len(rows)} open rows)')


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--backfill', action='store_true')
    a = ap.parse_args()
    if a.backfill: backfill()
