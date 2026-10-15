"""Apply-now list: the 10 best live roles that are waiting on the owner, once a day (0 tokens).

Why (Oct 13 2026 analysis): 398 rows sat in "Needs you" with no order, many already closed, so his 30 minutes a day
went to dead or weak postings. This script:
  1. takes "Needs you" / "To apply" rows from the last N days,
  2. checks each posting is still open with one plain HTTP request (LinkedIn guest page, Greenhouse/Lever/Ashby APIs,
     or the page itself) and sets Status = Closed for the dead ones,
  3. writes the top 10 live rows (fit, then newest) with the one thing blocking each to a Reports row + a push.

  python apply_now.py              # daily list (fast_hunter calls it once a day after 7 AM CT)
  python apply_now.py --sweep      # also clean the whole backlog (all ages) of closed postings
  python apply_now.py --dry        # print only, no Notion writes
"""
import argparse, concurrent.futures as cf, datetime as dt, json, re, sys, urllib.error, urllib.request
from zoneinfo import ZoneInfo
import config, notion, notify

CT = ZoneInfo('America/Chicago')
UA = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 Chrome/126 Safari/537.36'}
CLOSED_RX = re.compile(r'no longer accepting applications|not currently accepting|this job (is|has been) closed|job (is )?no longer '
                       r'(available|open)|position has been filled|posting (has been )?(closed|removed)|job not found|'
                       r'page (you requested )?(was )?not found|this job has expired|job has been removed', re.I)


def _get(url, timeout=20, accept='*/*'):
    r = urllib.request.Request(url, headers=dict(UA, Accept=accept))
    with urllib.request.urlopen(r, timeout=timeout) as f:
        return f.status, f.geturl(), f.read(400000).decode('utf-8', 'replace')


import threading, time
_LI = {'lock': threading.Lock(), 'n': 0, 'blocked': False}
LI_MAX = 40   # LinkedIn guest pages per run. Oct 13: 8 parallel checks of ~440 pages got the IP rate-limited (429) and the
              # apply run at the same minute lost 10 LinkedIn rows -> one at a time, 3 s apart, stop at the first 429


def _linkedin_closed(url):
    with _LI['lock']:
        if _LI['blocked'] or _LI['n'] >= LI_MAX: return 'unknown'
        _LI['n'] += 1; time.sleep(3)
        try:
            _, _, h = _get(url.split('?')[0])
        except urllib.error.HTTPError as e:
            if e.code in (429, 999): _LI['blocked'] = True; return 'unknown'
            return 'closed' if e.code in (404, 410) else 'unknown'
        return 'closed' if re.search(r'no longer accepting applications|closed-job', h, re.I) else 'open'


def workday_open(url):
    """Workday posting still up? Its public CXS API answers 404 for removed jobs (the page itself only renders an error
    icon, which the apply flow mistook for a sign-in wall: GE Vernova, Oct 13). None = not a Workday job URL / unknown."""
    m = re.search(r'https?://([\w-]+)\.(wd\d+)\.myworkday(?:jobs|site)\.com/(?:[a-z]{2}-[A-Z]{2}/)?([^/]+)/job/(.+?)(?:/apply.*)?(?:[?#].*)?$', url)
    if not m: return None
    tenant, wd, site, path = m.groups()
    try:
        _get(f'https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/job/{path}', accept='application/json')
        return True
    except urllib.error.HTTPError as e:
        return False if e.code == 404 else None
    except Exception:
        return None


def liveness(url):
    """'open' | 'closed' | 'unknown' for a job link, from one cheap request."""
    if not url: return 'unknown'
    try:
        m = re.search(r'boards\.greenhouse\.io/(?:embed/job_app\?for=)?([\w-]+)/jobs/(\d+)|job-boards\.greenhouse\.io/([\w-]+)/jobs/(\d+)', url)
        if m:
            slug, jid = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
            try: _get(f'https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{jid}'); return 'open'
            except urllib.error.HTTPError as e: return 'closed' if e.code == 404 else 'unknown'
        m = re.search(r'jobs\.lever\.co/([\w-]+)/([0-9a-f-]{36})', url)
        if m:
            try: _get(f'https://api.lever.co/v0/postings/{m.group(1)}/{m.group(2)}'); return 'open'
            except urllib.error.HTTPError as e: return 'closed' if e.code == 404 else 'unknown'
        m = re.search(r'jobs\.ashbyhq\.com/([^/?#]+)/([0-9a-f-]{36})', url)
        if m:
            _, _, h = _get(f'https://api.ashbyhq.com/posting-api/job-board/{m.group(1)}', accept='application/json')
            return 'open' if m.group(2) in h else 'closed'
        wd = workday_open(url)
        if wd is not None: return 'open' if wd else 'closed'
        if 'linkedin.com/' in url:   # guest page (throttled; the closed banner is in the HTML)
            return _linkedin_closed(url) if '/jobs/view/' in url else 'unknown'
        code, final, h = _get(url)
        if re.search(r'greenhouse\.io/[\w-]+/?\?error=true', final): return 'closed'
        text = re.sub(r'<[^>]+>', ' ', h)[:20000]
        return 'closed' if CLOSED_RX.search(text) else 'open'
    except urllib.error.HTTPError as e:
        return 'closed' if e.code in (404, 410) else 'unknown'
    except Exception:
        return 'unknown'


def fit(r):
    m = re.search(r'fit (\d+)', r.get('Notes') or '')
    return int(m.group(1)) if m else 0


def blocker(r):
    """The one thing he has to do, from the newest apply_prep note."""
    n = r.get('Notes') or ''
    m = re.search(r'NEEDS YOU – (.+?)(?: \(\w+, tab closed| \| |$)', n)
    if m: return m.group(1)[:140]
    if re.search(r'FILLED', n): return 'form was filled — open the link and click Submit'
    return 'not tried yet — open the link and apply'


def candidates(days):
    rows = notion.query({'or': [{'property': 'Status', 'select': {'equals': 'Needs you'}},
                                {'property': 'Status', 'select': {'equals': 'To apply'}}]})
    if days:
        since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).isoformat()
        rows = [r for r in rows if (r.get('created') or '') >= since]
    import score   # rows written before the Oct 13 filters: skip what the hunter would now reject
    return [r for r in rows if r.get('Link') and 'LINK LOST' not in (r.get('Notes') or '')
            and not score.STAFFING.search(r.get('Company') or '') and not score.blocked('', r.get('Position') or '')]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=10); ap.add_argument('--top', type=int, default=10)
    ap.add_argument('--sweep', action='store_true', help='check every open row (any age) and close dead ones')
    ap.add_argument('--dry', action='store_true')
    a = ap.parse_args()
    rows = candidates(0 if a.sweep else a.days)
    rows.sort(key=lambda r: (fit(r), r.get('created') or ''), reverse=True)
    check = rows if a.sweep else rows[:60]          # daily: only the best 60 need a check
    with cf.ThreadPoolExecutor(8) as ex:
        live = dict(zip([r['id'] for r in check], ex.map(lambda r: liveness(r['Link']), check)))
    stamp = dt.datetime.now(CT).strftime('%b %d %I:%M %p CT')
    closed = [r for r in check if live[r['id']] == 'closed']
    for r in closed:
        if not a.dry:
            notion.update_row(r['id'], notion.props(Status='Closed', Notes=(f"CLOSED – posting no longer accepts applications "
                                                                            f"(apply_now check {stamp}) | " + (r.get('Notes') or ''))[:1990]))
    top, per_co = [], {}
    for r in check:
        co = re.sub(r'[^a-z0-9]', '', (r.get('Company') or '').lower())[:12]
        if live[r['id']] == 'closed' or per_co.get(co): continue   # one role per company on the list
        per_co[co] = 1; top.append(r)
        if len(top) == a.top: break
    print(f"checked {len(check)} rows: {len(closed)} closed, {sum(v == 'open' for v in live.values())} open, "
          f"{sum(v == 'unknown' for v in live.values())} unknown")
    lines = [f"{i}. {r.get('Company')} — {r.get('Position')} (fit {fit(r)}) — {blocker(r)} — {r.get('Link')}" for i, r in enumerate(top, 1)]
    print('\n'.join(lines))
    if a.dry or not top: return
    today = dt.datetime.now(CT).strftime('%Y-%m-%d')
    blocks = [{'type': 'paragraph', 'paragraph': {'rich_text': notion._rt(
        f"Best {len(top)} live roles waiting on you (checked {stamp}; {len(closed)} closed postings were marked Closed). "
        "Open each link, fix the one blocker, submit, then set Status = Applied.")}}]
    for r in top:
        blocks.append({'type': 'numbered_list_item', 'numbered_list_item': {'rich_text': [
            {'type': 'text', 'text': {'content': f"{r.get('Company')} — {r.get('Position')}", 'link': {'url': r['Link']}},
             'annotations': {'bold': True}},
            {'type': 'text', 'text': {'content': f"  (fit {fit(r)}) · {blocker(r)} · "}},
            {'type': 'text', 'text': {'content': 'Notion row', 'link': {'url': r['url']}}}]}})
    notion.create_row({'Name': {'title': notion._rt(f'Apply now — {today}')}, 'Date': {'date': {'start': today}},
                       'Kind': {'select': {'name': 'Apply Now'}}, 'Slot': {'select': {'name': 'daily'}},
                       'Source': {'select': {'name': 'local'}}}, blocks, ds=config.REPORTS_DS)
    notify.push(f"Apply now: {len(top)} live roles waiting — " + '; '.join(f"{r.get('Company')}" for r in top[:5]), title='Apply now')


if __name__ == '__main__':
    sys.exit(main())
