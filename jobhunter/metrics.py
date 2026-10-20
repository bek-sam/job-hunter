"""Daily apply metrics (D2), 0 tokens: what the apply runs did today, by ATS and by NEEDS-YOU reason.
Source: data/apply_results.jsonl (one line per attempted row, written by apply_prep.py) + data/submissions.jsonl.

  python metrics.py              # today
  python metrics.py --days 7     # last 7 days
"""
import argparse, collections, datetime as dt, json, re
from zoneinfo import ZoneInfo
import config

CT = ZoneInfo('America/Chicago')
REASONS = [('captcha', r'captcha|spam'), ('e-signature', r'signature|e-sign'), ('login/account', r'sign.?in|log.?in|account|password'),
           ('email/sms code', r'code|verif'), ('question', r'\?'), ('closed', r'closed|no longer'), ('upload', r'upload|transcript')]


def _load(name, since):
    out = []
    try:
        for line in (config.DATA / name).read_text().splitlines():
            try:
                d = json.loads(line)
                if dt.datetime.fromisoformat(d['at']) >= since: out.append(d)
            except Exception:
                continue
    except FileNotFoundError:
        pass
    return out


def reason_of(text):
    return next((k for k, rx in REASONS if re.search(rx, text or '', re.I)), 'other')


def summary(days=1):
    now = dt.datetime.now(CT)
    since = now.replace(hour=0, minute=0, second=0, microsecond=0) - dt.timedelta(days=days - 1)
    res, subs = _load('apply_results.jsonl', since), _load('submissions.jsonl', since)
    res = [r for r in res if r['status'] != 'SKIPPED']   # not on the APPLY_ATS_ONLY list: never attempted
    st = collections.Counter(r['status'] for r in res)
    by_ats = collections.defaultdict(collections.Counter)
    for r in res: by_ats[r.get('ats') or '?'][r['status']] += 1
    need = collections.Counter(reason_of(r['reason']) for r in res if r['status'] == 'NEEDS YOU')
    return dict(since=since, attempted=len(res), status=st, by_ats=by_ats, need=need, submissions=len(subs))


def health_line(days=1):
    s = summary(days)
    ok = s['status'].get('SUBMITTED', 0) + sum(v for k, v in s['need'].items() if k in ('captcha', 'e-signature', 'email/sms code'))
    rate = f"{100 * ok // s['attempted']}%" if s['attempted'] else '-'
    return (f"HEALTH {dt.datetime.now(CT):%b %d %H:%M} | today: attempted {s['attempted']} | submitted {s['status'].get('SUBMITTED', 0)} "
            f"(log {s['submissions']}) | needs you {s['status'].get('NEEDS YOU', 0)} {dict(s['need'])} | errors {s['status'].get('ERROR', 0)} "
            f"| good-outcome rate {rate} (goal >= 60%)")


def stale_alarm(hours=3, every_h=6):
    """Phone/Mac alert when Apply Prep has not run for `hours` (schedule unloaded, Mac asleep, crash loop) or its
    good-outcome rate today is under 30% after 10+ attempts. At most one alert per `every_h` hours."""
    import os, time, notify
    log, stamp = config.DATA / 'logs' / 'apply_prep.log', config.DATA / 'logs' / '.apply_alarm'
    if stamp.exists() and time.time() - stamp.stat().st_mtime < every_h * 3600: return
    msgs = []
    age = (time.time() - log.stat().st_mtime) / 3600 if log.exists() else 999
    if age > hours:
        msgs.append(f'Apply Prep has not run for {age:.0f} h — check `scripts/schedule.sh status` (com.jobhunter.applyprep)')
    s = summary(1)
    ok = s['status'].get('SUBMITTED', 0)
    if s['attempted'] >= 10 and ok * 100 < 30 * s['attempted']:
        msgs.append(f"Apply success today only {ok}/{s['attempted']} — top reasons {dict(s['need'].most_common(3))}")
    if msgs:
        notify.push(' | '.join(msgs), title='Job Hunter — apply alarm'); stamp.touch()


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--days', type=int, default=1); a = ap.parse_args()
    s = summary(a.days)
    print(health_line(a.days))
    print('| ATS | attempted | submitted | needs you | filled | error |\n|---|---|---|---|---|---|')
    for k, c in sorted(s['by_ats'].items(), key=lambda x: -sum(x[1].values())):
        print(f"| {k} | {sum(c.values())} | {c.get('SUBMITTED', 0)} | {c.get('NEEDS YOU', 0)} | {c.get('FILLED', 0)} | {c.get('ERROR', 0)} |")
