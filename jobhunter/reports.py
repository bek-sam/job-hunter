"""Two Scale.jobs-style reports, 0 LLM tokens, written to the Notion Reports database (+ a push):

daily   once a day after 10 PM: what the apply runs did today -- submitted (with links to the rows, whose pages carry
        the proof screenshots), filled and waiting for your Submit, needs you (by reason), skipped (by reason), errors.
review  every 14 days: response rate (OA / interview / offer, and any reply incl. rejection) of the last 60 days of
        applications by Apply Via (ATS), job type, resume version, fit band and coverage band -- so filters and
        resume rules get tuned on what actually gets answers.

  python reports.py daily [--dry]
  python reports.py review [--days 60] [--dry]
"""
import argparse, collections, datetime as dt, json, re
import config, metrics, notify

CT = config.TZ
RESPONDED = {'OA', 'Interview', 'Offer'}
ANSWERED = RESPONDED | {'Rejected'}
DONE = ANSWERED | {'Applied', 'Ghosted'}


def _today_lines():
    since = dt.datetime.now(CT).replace(hour=0, minute=0, second=0, microsecond=0)
    res = metrics._load('apply_results.jsonl', since)
    subs = metrics._load('submissions.jsonl', since)
    return res, subs


def daily_text():
    res, subs = _today_lines()
    by = collections.defaultdict(list)
    for r in res: by[r['status']].append(r)
    sent = [s for s in subs if not s.get('pending')]
    pending = [s for s in subs if s.get('pending')]
    seen_rows, lines = set(), []
    import notion
    def row(s):
        return f"{s.get('company')} — {s.get('role')} ({s.get('ats')})" + (f" · {notion.page_url(s['row'])}" if s.get('row') else '')
    lines.append(f"Submitted: {len({s.get('row') or s.get('url') for s in sent})}")
    for s in sent:
        k = s.get('row') or s.get('url')
        if k in seen_rows: continue
        seen_rows.add(k); lines.append('  - ' + row(s) + (' · proof screenshot on the row' if s.get('proof') else ''))
    if pending:
        lines.append(f"Submit clicked, no confirmation (check your inbox): {len({s.get('row') for s in pending})}")
        lines += ['  - ' + row(s) for s in {s.get('row'): s for s in pending}.values()]
    lines.append(f"Filled, waiting for your Submit: {len(by['FILLED'])}")
    lines += [f"  - {r.get('company')} ({r.get('ats')})" for r in by['FILLED'][:15]]
    need = collections.Counter(metrics.reason_of(r.get('reason')) for r in by['NEEDS YOU'])
    lines.append(f"Needs you: {len(by['NEEDS YOU'])} {dict(need.most_common())}")
    lines += [f"  - {r.get('company')}: {(r.get('reason') or '')[:110]}" for r in by['NEEDS YOU'][:15]]
    skip = collections.Counter(re.sub(r'\(.*', '', r.get('reason') or '')[:60].strip() for r in by['SKIPPED'])
    lines.append(f"Skipped: {len(by['SKIPPED'])}" + (''.join(f'\n  - {k}: {v}' for k, v in skip.most_common(8)) if skip else ''))
    lines.append(f"Closed / cancelled: {len(by['CANCELLED'])}")
    lines.append(f"Errors: {len(by['ERROR'])}")
    lines += [f"  - {r.get('company')}: {(r.get('reason') or '')[:110]}" for r in by['ERROR'][:10]]
    return '\n'.join(lines), len(seen_rows), len(by['NEEDS YOU'])


def _band(n, edges, labels):
    for e, l in zip(edges, labels):
        if n is not None and n < e: return l
    return labels[-1] if n is not None else 'n/a'


def review_rows(days=60):
    import notion
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).isoformat()
    return [r for r in notion.query({'timestamp': 'created_time', 'created_time': {'on_or_after': since}})
            if r.get('Status') in DONE]


def review_text(rows):
    def num(rx, s):
        m = re.search(rx, s or ''); return int(m.group(1)) if m else None
    dims = {
        'Apply Via': lambda r: r.get('Apply Via') or 'unknown',
        'Type': lambda r: r.get('Type') or ('Intern' if 'INTERN' in (r.get('Notes') or '') else 'New Grad'),
        'Resume version': lambda r: (re.search(r'resume (v[\d.]+)', r.get('Notes') or '') or [None, 'unknown'])[1],
        'Fit': lambda r: _band(num(r'fit (\d+)', r.get('Notes')), [75, 85, 95], ['70-74', '75-84', '85-94', '95+']),
        'Required-term coverage': lambda r: _band(num(r'cov (\d+)%', r.get('Notes')), [50, 75, 90], ['<50%', '50-74%', '75-89%', '90%+']),
    }
    tot = len(rows); resp = sum(r.get('Status') in RESPONDED for r in rows); ans = sum(r.get('Status') in ANSWERED for r in rows)
    out = [f"{tot} applications · OA/interview/offer {resp} ({100 * resp // tot if tot else 0}%) · any answer {ans} "
           f"({100 * ans // tot if tot else 0}%)"]
    for name, f in dims.items():
        g = collections.defaultdict(lambda: [0, 0, 0])
        for r in rows:
            k = f(r); g[k][0] += 1; g[k][1] += r.get('Status') in RESPONDED; g[k][2] += r.get('Status') in ANSWERED
        out.append(f"\n{name}:")
        for k, (n, p, a) in sorted(g.items(), key=lambda x: -x[1][0])[:10]:
            out.append(f"  - {k}: {n} applied · {p} OA+ ({100 * p // n}%) · {a} answered ({100 * a // n}%)"
                       + ('  (small sample)' if n < 10 else ''))
    co = collections.Counter((r.get('Company') or '?') for r in rows if r.get('Status') in RESPONDED)
    if co: out.append('\nCompanies that moved you forward: ' + ', '.join(f'{c} ({n})' for c, n in co.most_common(10)))
    return '\n'.join(out)


def _post(title, kind, text, slot):
    import notion
    today = dt.datetime.now(CT).strftime('%Y-%m-%d')
    blocks = [{'type': 'paragraph', 'paragraph': {'rich_text': notion._rt(chunk)}} for chunk in
              [text[i:i + 1900] for i in range(0, len(text), 1900)]]
    notion.create_row({'Name': {'title': notion._rt(f'{title} — {today}')}, 'Date': {'date': {'start': today}},
                       'Kind': {'select': {'name': kind}}, 'Slot': {'select': {'name': slot}},
                       'Source': {'select': {'name': 'local'}}}, blocks, ds=config.REPORTS_DS)


def daily(dry=False):
    text, sent, need = daily_text()
    print(text)
    if dry or not config.REPORTS_DS: return text
    _post('Apply report', 'Daily Apply Report', text, 'daily')
    notify.push(f'Apply report: {sent} submitted, {need} need you (details in Notion Reports)', title='Job Hunter — daily')
    return text


def review(days=60, dry=False):
    text = review_text(review_rows(days))
    print(text)
    if dry or not config.REPORTS_DS: return text
    _post(f'Response review ({days} days)', 'Response Review', text, 'weekly')
    notify.push('Response-rate review is in Notion Reports: ' + text.splitlines()[0], title='Job Hunter — review')
    return text


def scheduled(dry=False):
    """Called by fast_hunter after every run: daily after 10 PM, review every 14 days. Flag files make each once only."""
    if dry: return
    now = dt.datetime.now(CT); d = config.DATA / 'daily'; d.mkdir(exist_ok=True)
    f = d / f'apply-report-{now:%Y-%m-%d}.done'
    if now.hour >= 22 and not f.exists():
        try: daily(); f.write_text('ok')
        except Exception as e: print('daily apply report failed:', str(e)[:120])
    rv = d / 'response-review.last'
    try: last = dt.datetime.fromisoformat(rv.read_text().strip())
    except Exception: last = None
    if last is None or now - last >= dt.timedelta(days=14):
        try: review(); rv.write_text(now.isoformat())
        except Exception as e: print('response review failed:', str(e)[:120])


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('what', choices=['daily', 'review'])
    ap.add_argument('--days', type=int, default=60); ap.add_argument('--dry', action='store_true')
    a = ap.parse_args()
    daily(a.dry) if a.what == 'daily' else review(a.days, a.dry)
