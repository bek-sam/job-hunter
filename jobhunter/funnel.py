"""Deterministic job-search funnel from Notion "Applications 2026" (0 LLM tokens).
Shared by manager.py and outreach.py.

  python funnel.py                         # print compact funnel JSON (needs NOTION_TOKEN)
  python funnel.py --fixture data/test/funnel_fixture.json   # print a saved funnel (testing)
"""
import argparse, collections, datetime as dt, json, re, sys
from zoneinfo import ZoneInfo
import config

CT = ZoneInfo('America/Chicago')
STAGES = ['To apply', 'Needs you', 'Applied', 'OA', 'Interview', 'Offer', 'Rejected', 'Ghosted', 'Closed']


class FunnelError(RuntimeError):
    pass


def kind(r):
    return 'Intern' if 'INTERN' in (r.get('Notes') or '') else 'NG'


def _d(s):
    try:
        return dt.date.fromisoformat((s or '')[:10])
    except ValueError:
        return None


def _ct_date(iso):
    try:
        return dt.datetime.fromisoformat(iso.replace('Z', '+00:00')).astimezone(CT).date()
    except Exception:
        return None


def short(r, notes=120):
    return {k: v for k, v in dict(id=r['id'], company=r.get('Company'), position=r.get('Position'), status=r.get('Status'),
                                  posted=r.get('Date Posted'), applied=r.get('Date Applied'), outreach=r.get('Outreach'),
                                  last_contact=r.get('Last Contact'), link=r.get('Link'), linkedin=r.get('LinkedIn'),
                                  recruiters=(r.get('Recruiters') or '')[:300] or None,
                                  notes=(r.get('Notes') or '')[:notes] or None).items() if v}


def load_rows():
    if not config.NOTION_TOKEN:
        raise FunnelError('NOTION_TOKEN is not set in .env — create a Notion integration, connect it to "Jobs 2026", '
                          'and add NOTION_TOKEN=... to ~/Desktop/autoapply/.env')
    import notion
    try:
        return notion.query()
    except notion.NotionError as e:
        raise FunnelError(f'Notion query failed: {e}')


def compute(rows, today=None, max_outreach=10):
    today = today or dt.datetime.now(CT).date()
    by = collections.defaultdict(collections.Counter)
    created = collections.Counter()
    for r in rows:
        by[kind(r)][r.get('Status') or '(none)'] += 1
        c = _ct_date(r.get('created') or '')
        if c and (today - c).days < 7: created[(c.isoformat(), kind(r))] += 1
    week = {d: {'NG': created[(d, 'NG')], 'Intern': created[(d, 'Intern')]}
            for d in sorted({k[0] for k in created})}
    notes = lambda r: r.get('Notes') or ''
    filled_waiting = [r for r in rows if r.get('Status') == 'To apply' and 'FILLED' in notes(r)]
    needs_you = [r for r in rows if r.get('Status') == 'Needs you']
    outreach = collections.Counter(r.get('Outreach') or '(empty)' for r in rows)
    due = [r for r in rows if r.get('Outreach') in ('Sent', 'Follow-up 1') and _d(r.get('Last Contact'))
           and (today - _d(r.get('Last Contact'))).days >= 3 and 'BOUNCED' not in (r.get('Recruiters') or '')]
    cand = [r for r in rows if not r.get('Outreach') and r.get('Status') not in ('Closed', 'Rejected', 'Ghosted', 'Offer')]
    pri = lambda r: (0 if r.get('Status') in ('Applied', 'OA', 'Interview') else 1, -(_d(r.get('Date Posted')) or dt.date(2000, 1, 1)).toordinal())
    cand.sort(key=pri)
    upcoming = [r for r in rows if r.get('Status') in ('OA', 'Interview')]
    return {
        'date': today.isoformat(),
        'totals': {k: {s: v[s] for s in STAGES if v[s]} for k, v in by.items()},
        'created_last_7d': week,
        'today': week.get(today.isoformat(), {'NG': 0, 'Intern': 0}),
        'filled_not_submitted': {'NG': sum(kind(r) == 'NG' for r in filled_waiting), 'Intern': sum(kind(r) == 'Intern' for r in filled_waiting)},
        'needs_you_count': len(needs_you),
        'needs_you_recent': [short(r, 160) for r in sorted(needs_you, key=lambda r: r.get('created') or '', reverse=True)[:15]],
        'oa_interview': [short(r, 200) for r in upcoming],
        'outreach_stages': dict(outreach),
        'followups_due': [short(r, 80) for r in due[:20]],
        'outreach_candidates': [short(r, 80) for r in cand[:max_outreach]],
        'response_patterns': patterns(rows),
    }


def patterns(rows, min_n=5):
    """What gets replies (career-ops analyze-patterns / AkbarDevop job-patterns idea): of applied rows, the share that
    reached OA/Interview/Offer, grouped by kind, apply site and entry-level title. 0 tokens."""
    import re as _re
    applied = [r for r in rows if r.get('Status') in ('Applied', 'OA', 'Interview', 'Offer', 'Rejected', 'Ghosted')]
    hit = lambda r: r.get('Status') in ('OA', 'Interview', 'Offer')
    groups = {'kind': kind, 'apply_via': lambda r: r.get('Apply Via') or '?',
              'entry_title': lambda r: 'entry/new-grad title' if _re.search(r'new grad|entry|junior|early career|university|graduate|intern|\bi\b', r.get('Position') or '', _re.I) else 'other title'}
    out = {}
    for g, fn in groups.items():
        c = collections.defaultdict(lambda: [0, 0])
        for r in applied:
            k = fn(r); c[k][0] += 1; c[k][1] += hit(r)
        out[g] = {k: f'{h}/{n} = {100 * h / n:.1f}%' for k, (n, h) in sorted(c.items(), key=lambda x: -x[1][0]) if n >= min_n}
    out['total'] = f'{sum(map(hit, applied))}/{len(applied)}'
    return out


def ghost_candidates(rows, days=30, today=None):
    """Applied rows with no reply for `days` (ApplyPilot ghosting idea): status change only, never deleted."""
    today = today or dt.datetime.now(CT).date()
    return [r for r in rows if r.get('Status') == 'Applied' and _d(r.get('Date Applied'))
            and (today - _d(r.get('Date Applied'))).days >= days
            and not (_d(r.get('Last Contact')) and (today - _d(r.get('Last Contact'))).days < days)]


def get(fixture=None, max_outreach=10):
    if fixture:
        return json.load(open(fixture))
    return compute(load_rows(), max_outreach=max_outreach)


def team_health(days=2):
    """Summaries of the local runs (0 tokens): Fast Hunter run logs, job logs, LLM usage."""
    out = {}
    runs = sorted((config.DATA / 'runs').glob('*.md'))[-days:]
    lines = []
    for f in runs:
        for l in f.read_text().splitlines():
            if l.startswith(('Fast Hunter local run', 'counts:', 'scripts:', 'No new roles')): lines.append(l[:200])
    out['fast_hunter_runs'] = lines[-40:]
    logs = {}
    for f in sorted((config.DATA / 'logs').glob('*.log')):
        txt = f.read_text(errors='replace').splitlines()
        errs = [l for l in txt if re.search(r'Traceback|Error|failed', l)]
        logs[f.stem] = {'lines': len(txt), 'recent_errors': errs[-5:], 'tail': txt[-3:]}
    out['job_logs'] = logs
    u = config.DATA / 'llm_usage.jsonl'
    if u.exists():
        rec = [json.loads(l) for l in u.read_text().splitlines() if l.strip()]
        out['llm_calls'] = len(rec)
        out['llm_cost_usd'] = round(sum(r.get('cost_usd') or 0 for r in rec), 3)
    return out


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--fixture'); ap.add_argument('--health', action='store_true')
    a = ap.parse_args()
    try:
        print(json.dumps(team_health() if a.health else get(a.fixture), indent=1))
    except FunnelError as e:
        print('ERROR:', e, file=sys.stderr); sys.exit(2)
