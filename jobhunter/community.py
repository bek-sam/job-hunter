"""Community Job Board (Notion, public info only): every job the hunter saves to Applications 2026 is mirrored here
with Company, Job Title, Type, Date Posted, Location, Apply Link. No status/notes/recruiters (owner, Oct 13 2026).
Member columns (one select per community member: Applied/OA/Interview/Offer/Rejected/Not interested)
are filled in by each member — this script never writes them (Oct 14 2026).

  python community.py --backfill      # copy all current (not Closed) Applications 2026 rows once (deduped by link)
  python community.py --sync-types 5  # fill empty Applications 2026 'Type' from this board (rows created in the last N days)
fast_hunter calls add() for every new row.
"""
import argparse, collections, re, sys, time
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
SITES = [('Workday', r'myworkday(jobs|site)\.com|\bworkday\b'), ('Greenhouse', r'greenhouse\.io|grnh\.se|gh_jid=|\bgreenhouse\b'), ('Ashby', r'ashbyhq\.com|\bashby\b'),
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


def via_and_link(link, company='', title=''):
    """(Apply Via, Apply Link or None, note tag or '') with no LinkedIn login -- see li_resolve.guest_rows."""
    link = link or ''
    if 'linkedin.com' not in link: return apply_via(link), link, ''
    from ats import linkedin as LI
    k = LI.apply_kind(link)
    if k == 'easy': return 'LinkedIn Easy Apply', link, 'APPLY: LinkedIn Easy Apply'
    if k == 'offsite':
        try: u = LI.resolve_offsite(company, title)
        except Exception: u = None
        if u: return apply_via(u), u, f'APPLY URL: {u}'
        return 'Company website', None, 'APPLY: external site (exact link unknown)'
    if k == 'closed': return 'LinkedIn', None, 'CLOSED on LinkedIn (no longer accepting applications)'
    return 'LinkedIn', None, ''   # could not tell (rate-limited / gone): li_resolve --guest retries later


def via_for(link, company='', title=''):
    """Apply Via for a new row (Oct 20, owner): 'LinkedIn Easy Apply' only when the LinkedIn posting really has LinkedIn's
    Easy Apply button; an external-apply posting gets the site its Apply button leads to (found on the company's ATS
    without LinkedIn login), else 'Company website'. 'LinkedIn' = could not tell (posting gone / rate-limited)."""
    link = link or ''
    if 'linkedin.com' not in link: return apply_via(link)
    from ats import linkedin as LI
    k = LI.apply_kind(link)
    if k == 'easy': return 'LinkedIn Easy Apply'
    if k == 'offsite':
        try: u = LI.resolve_offsite(company, title)
        except Exception: u = None
        return apply_via(u) if u else 'Company website'
    return 'LinkedIn'


def ensure_column(ds):
    """Add the 'Apply Via' select to a data source (idempotent)."""
    notion._req('PATCH', f'/data_sources/{ds}', {'properties': {'Apply Via': {'select': {'options': [{'name': n, 'color': c} for n, c in VIA_OPTS]}}}})


def _board_types():
    """Apply Link -> Type for every row on the board."""
    types, cur = {}, None
    while True:
        body = {'page_size': 100}
        if cur: body['start_cursor'] = cur
        r = notion._req('POST', f'/data_sources/{DS}/query', body)
        for p in r['results']:
            u = (p['properties'].get('Apply Link') or {}).get('url')
            if u: types[u] = ((p['properties'].get('Type') or {}).get('select') or {}).get('name')
        if not r.get('has_more'): return types
        cur = r['next_cursor']


def _known_links():
    return set(_board_types())


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


def sync_types(days=5):
    """Applications 2026 rows with an empty Type get the board's Type (matched by link; job_type() if not on the board).
    Rows that already have a Type are left alone."""
    board = _board_types(); n = 0
    for r in notion.recent_rows(days):
        if r.get('Type'): continue
        t = board.get(r.get('Link')) or job_type(r.get('Position') or '', r.get('Notes') or '')
        try:
            notion.update_row(r['id'], {'Type': {'select': {'name': t}}}); n += 1
        except Exception as e:
            print('skip', r.get('Company'), str(e)[:100]); time.sleep(2)
    print(f'Applications 2026: Type set on {n} rows (last {days} days)')


def fix_via(dry=False, pause=1.5):
    """Re-derive Applications 2026 'Apply Via' (Oct 20, owner: most rows said 'LinkedIn Easy Apply' wrongly). Evidence, best
    first: a non-LinkedIn Link; where an apply run really ended ("→ workday" in Notes); LinkedIn's public Easy Apply /
    external marker (via_for). Rows the check can't decide keep their value."""
    names = {n.lower(): n for n, _ in VIA_OPTS}
    rows = [r for r in notion.query() if r.get('Link') and r.get('Apply Via') in (None, '', 'LinkedIn Easy Apply', 'LinkedIn')]
    print(f'{len(rows)} rows to check'); changed = collections.Counter()
    for i, r in enumerate(rows):
        link, notes, via = r['Link'], r.get('Notes') or '', None
        if 'linkedin.com' not in link:
            via = apply_via(link)
        else:
            m = re.findall(r'→ (\w+):', notes)
            seen = [x for x in m if x not in ('generic', 'linkedin')]
            if seen: via = next((n for n, rx in SITES if re.search(rx, seen[0], re.I)), None)
            elif m and m[0] == 'generic': via = 'Company website'
            if not via:
                via = via_for(link, r.get('Company') or '', r.get('Position') or ''); time.sleep(pause)
                if via == 'LinkedIn': via = None   # couldn't tell -> leave as is
        if via and via != r.get('Apply Via') and via.lower() in names:
            changed[f"{r.get('Apply Via')} -> {via}"] += 1
            if not dry:
                try: notion.update_row(r['id'], {'Apply Via': {'select': {'name': via}}})
                except Exception as e: print('skip', r.get('Company'), str(e)[:80]); time.sleep(2)
        if i % 100 == 99: print(i + 1, dict(changed.most_common(6)), flush=True)
    print('done', dict(changed))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--backfill', action='store_true')
    ap.add_argument('--sync-types', type=int, metavar='DAYS')
    ap.add_argument('--fix-via', action='store_true', help="re-derive 'Apply Via' on Applications 2026 rows")
    ap.add_argument('--dry', action='store_true')
    ap.add_argument('--pause', type=float, default=1.5, help='seconds between LinkedIn guest lookups')
    a = ap.parse_args()
    if a.backfill: backfill()
    if a.sync_types: sync_types(a.sync_types)
    if a.fix_via: fix_via(a.dry, a.pause)
