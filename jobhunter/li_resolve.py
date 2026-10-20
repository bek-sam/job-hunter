"""Where a LinkedIn posting really applies (owner, Oct 20 2026: "Easy Apply" in Notion was wrong for most rows).

LinkedIn's public page only says Easy Apply vs. external; the company URL is shown to signed-in members only. This asks
LinkedIn's own job API (voyager jobPostings/<id>.applyMethod) through the signed-in apply Chrome, one paced request per
job, read-only:
  OffsiteApply.companyApplyUrl  -> the site the Apply button opens  -> Apply Via = that ATS / Company website
  *OnsiteApply                  -> LinkedIn Easy Apply
Results go to Notion: 'Apply Via' and a note "APPLY URL: <url>" (apply_prep then opens that URL directly). The Link
column keeps the LinkedIn URL (dedupe uses it).

  python li_resolve.py --pending          # rows written by the hunter in the last 2 days, not resolved yet (after each hunt)
  python li_resolve.py --backfill 60      # up to 60 open rows (To apply / Needs you), newest unresolved first
Holds data/apply_prep.lock (never shares the apply Chrome with an apply run); --pending skips if the lock is busy.
"""
import argparse, datetime as dt, fcntl, json, re, sys, time
import config, notion

TAG = 'APPLY URL:'
FETCH_JS = r"""async (id) => {
  const c = (document.cookie.match(/JSESSIONID="?([^";]+)/) || [])[1];
  if (!c) return {err: 'not signed in'};
  const r = await fetch('/voyager/api/jobs/jobPostings/' + id, {headers: {'csrf-token': c, 'x-restli-protocol-version': '2.0.0', accept: 'application/json'}});
  if (r.status !== 200) return {err: 'http ' + r.status};
  const j = await r.json(); const am = j.applyMethod || {};
  const k = Object.keys(am)[0] || ''; const v = am[k] || {};
  return {kind: /Offsite/.test(k) ? 'offsite' : (/Onsite/.test(k) ? 'easy' : ''), url: v.companyApplyUrl || '', closed: j.jobState && j.jobState !== 'LISTED' ? j.jobState : ''};
}"""


def job_id(link):
    m = re.search(r'(\d{8,})', re.sub(r'\?.*', '', link or '')) or re.search(r'currentJobId=(\d+)', link or '')
    return m.group(1) if m and 'linkedin.com' in (link or '') else None


def resolve(ids, pause=3.0):
    """{id: {'kind': 'easy'|'offsite'|'', 'url': str, 'closed': str} or {'err': ...}} via the signed-in apply Chrome."""
    from playwright.sync_api import sync_playwright
    import browser
    out = {}
    with sync_playwright() as p:
        b, ctx = browser.connect(p)
        page = ctx.new_page()
        try:
            page.goto('https://www.linkedin.com/robots.txt', wait_until='domcontentloaded', timeout=60000)
            for i, jid in enumerate(ids):
                for attempt in range(2):
                    try: out[jid] = page.evaluate(FETCH_JS, jid); break
                    except Exception as e:
                        out[jid] = {'err': str(e)[:80]}
                        if attempt or not re.search(r'closed|navigat|destroyed', str(e), re.I): break
                        print('resolver tab lost:', str(e)[:60], '| open tabs:', [x.url[:50] for x in ctx.pages])
                        try:   # tab closed / navigated away under us (Oct 20 09:20: 135 lookups lost) -> fresh tab, retry once
                            page = ctx.new_page(); page.goto('https://www.linkedin.com/robots.txt', wait_until='domcontentloaded', timeout=60000)
                        except Exception:
                            break
                if (out[jid].get('err') or '').startswith('http 429'):   # rate-limited: stop, the rest waits for the next run
                    print('LinkedIn 429 -> stopping this resolve run'); break
                time.sleep(pause)
        finally:
            page.close()
    return out


def final_url(url):
    """Follow redirects of short / tracking links (grnh.se, bit.ly, job-board redirectors) to the real application page."""
    import urllib.request
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 Chrome/126 Safari/537.36'})
        with urllib.request.urlopen(req, timeout=15) as f: return f.geturl()
    except Exception:
        return url


def via_of(info):
    import community
    if info.get('kind') == 'easy': return 'LinkedIn Easy Apply'
    if info.get('kind') == 'offsite' and info.get('url'):
        v = community.apply_via(info['url'])
        if v == 'Company website':   # a redirect may reveal the ATS (Phoenix Contact: grnh.se -> Greenhouse)
            info['url'] = final_url(info['url']); v = community.apply_via(info['url'])
        return v
    return None


def update_rows(rows, pause=3.0):
    ids = {}
    for r in rows:
        jid = job_id(r.get('Link'))
        if jid: ids.setdefault(jid, []).append(r)
    if not ids: return 0
    got = resolve(list(ids), pause); n = 0
    for jid, info in got.items():
        if info.get('closed') in ('CLOSED', 'SUSPENDED', 'DELETED', 'EXPIRED'):   # LinkedIn: no longer accepting applications
            for r in ids[jid]:
                try:
                    notion.update_row(r['id'], notion.props(Status='Closed', Notes=(f"CLOSED – LinkedIn posting no longer listed "
                                      f"({info['closed'].lower()}, {dt.datetime.now():%b %d}) | " + (r.get('Notes') or ''))[:1990])); n += 1
                except Exception as e: print('notion update failed', r.get('Company'), str(e)[:80])
            continue
        via = via_of(info)
        if not via: continue
        for r in ids[jid]:
            notes = r.get('Notes') or ''
            add = f"{TAG} {info['url']} | " if info.get('kind') == 'offsite' and info.get('url') else 'APPLY: LinkedIn Easy Apply | '
            upd = {'Apply Via': {'select': {'name': via}},
                   'Apply Link': {'url': info['url'] if info.get('kind') == 'offsite' and info.get('url') else r.get('Link')}}
            if TAG not in notes and 'APPLY: LinkedIn Easy Apply' not in notes:
                upd.update(notion.props(Notes=(add + notes)[:1990]))
            try: notion.update_row(r['id'], upd); n += 1
            except Exception as e: print('notion update failed', r.get('Company'), str(e)[:80])
    import collections
    why = collections.Counter(i.get('err') or i.get('closed') or i.get('kind') or 'no apply method' for i in got.values())
    print(f'resolved {len(got)} LinkedIn postings, updated {n} rows | {dict(why)}')
    return n


EXT_TAG = 'APPLY: external site (exact link unknown)'


def unresolved(r):
    n = r.get('Notes') or ''
    return job_id(r.get('Link')) and TAG not in n and 'APPLY: LinkedIn Easy Apply' not in n and EXT_TAG not in n


def guest_rows(rows, pause=4.0):
    """No LinkedIn login (owner, Oct 20): Easy Apply vs external from LinkedIn's PUBLIC job page; an external job's exact
    page from the company's own Greenhouse/Ashby/Lever/Workday board (public APIs). Writes Apply Via / Apply Link / a note."""
    import collections, community
    from ats import linkedin as LI
    why = collections.Counter(); n = 0
    skip_f = config.DATA / 'li_guest_unknown.json'
    try: skip = json.loads(skip_f.read_text())
    except Exception: skip = {}
    for r in rows:
        if time.time() - skip.get(r['id'], 0) < 6 * 3600: continue   # no answer recently: retry after 6 h
        kind = LI.apply_kind(r.get('Link'))
        if kind is None:   # rate-limited / posting gone: try a public check once more later
            skip[r['id']] = time.time(); skip_f.write_text(json.dumps(skip))
            why['unknown'] += 1
            if why['unknown'] >= 8 and why['unknown'] > (why['easy'] + why['external']):
                print('LinkedIn public page not answering (rate limit?) -> stopping this run'); break
            time.sleep(pause * 3); continue
        notes = r.get('Notes') or ''
        if kind == 'closed':   # LinkedIn: "No longer accepting applications"
            try:
                notion.update_row(r['id'], notion.props(Status='Closed', Notes=(f"CLOSED – LinkedIn: no longer accepting applications "
                                                                              f"({dt.datetime.now():%b %d}) | " + notes)[:1990])); n += 1
            except Exception as e: print('notion update failed', r.get('Company'), str(e)[:80])
            why['closed'] += 1; time.sleep(pause); continue
        if kind == 'easy':
            via, link, tag = 'LinkedIn Easy Apply', r.get('Link'), 'APPLY: LinkedIn Easy Apply'
        else:
            try: u = LI.resolve_offsite(r.get('Company') or '', r.get('Position') or '')
            except Exception: u = None
            if u: via, link, tag = community.apply_via(u), u, f'{TAG} {u}'
            else: via, link, tag = 'Company website', None, EXT_TAG
        why['easy' if kind == 'easy' else 'external']+= 1
        upd = {'Apply Via': {'select': {'name': via}}}
        if link: upd['Apply Link'] = {'url': link[:1900]}
        upd.update(notion.props(Notes=(tag + ' | ' + notes)[:1990]))
        try: notion.update_row(r['id'], upd); n += 1
        except Exception as e: print('notion update failed', r.get('Company'), str(e)[:80])
        time.sleep(pause)
    print(f'public check: updated {n} rows | {dict(why)}', flush=True)
    return n


def apply_url(r):
    """The company URL recorded by this resolver, or None."""
    m = re.search(re.escape(TAG) + r' (\S+)', r.get('Notes') or '')
    return m.group(1) if m else None


def fill_links():
    """Backfill the 'Apply Link' column from what is already known (no LinkedIn calls)."""
    n = 0
    for r in notion.query():
        if r.get('Apply Link'): continue
        link, u = r.get('Link') or '', apply_url(r)
        val = u or (link if 'linkedin.com' not in link else (link if 'APPLY: LinkedIn Easy Apply' in (r.get('Notes') or '') else None))
        if not val: continue
        try: notion.update_row(r['id'], {'Apply Link': {'url': val[:1900]}}); n += 1
        except Exception as e: print('skip', r.get('Company'), str(e)[:80]); time.sleep(2)
        if n % 200 == 0 and n: print(n, 'filled', flush=True)
    print(f'Apply Link filled on {n} rows')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pending', action='store_true'); ap.add_argument('--backfill', type=int, metavar='N')
    ap.add_argument('--pause', type=float, default=3.0); ap.add_argument('--fill-links', action='store_true')
    ap.add_argument('--guest', type=int, metavar='N', help='no-login check of up to N unresolved open rows (no browser)')
    a = ap.parse_args()
    if a.fill_links: return fill_links()
    if a.guest:
        rows = [r for r in notion.query({'or': [{'property': 'Status', 'select': {'equals': 'To apply'}},
                                                {'property': 'Status', 'select': {'equals': 'Needs you'}}]}) if unresolved(r)]
        rows.sort(key=lambda r: r.get('created') or '', reverse=True)
        try: skip = json.loads((config.DATA / 'li_guest_unknown.json').read_text())
        except Exception: skip = {}
        rows = [r for r in rows if time.time() - skip.get(r['id'], 0) >= 6 * 3600]   # retry-later rows must not fill the batch
        return guest_rows(rows[:a.guest], max(a.pause, 4.0))
    lock = open(config.DATA / 'apply_prep.lock', 'w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | (fcntl.LOCK_NB if a.pending else 0))
    except OSError:
        print('apply run active -> LinkedIn rows resolved later'); return
    if a.pending:
        rows = [r for r in notion.recent_rows(2) if unresolved(r) and r.get('Status') in ('To apply', 'Needs you')][:40]
    else:
        rows = [r for r in notion.query({'or': [{'property': 'Status', 'select': {'equals': 'To apply'}},
                                                {'property': 'Status', 'select': {'equals': 'Needs you'}}]}) if unresolved(r)]
        rows.sort(key=lambda r: r.get('created') or '', reverse=True)
        rows = rows[:a.backfill or 60]
    if not rows: print('nothing to resolve'); return
    update_rows(rows, a.pause)


if __name__ == '__main__':
    main()
