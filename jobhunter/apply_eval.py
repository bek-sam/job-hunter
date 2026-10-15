"""Evaluation loop for one ATS (owner, Oct 13 2026): fill ONE real application at a time with the normal apply flow,
wait, confirm by Gmail, classify, and stop on the first real failure so it can be fixed. Goal: N consecutive PASS.

  python apply_eval.py --ats workday --target 20          # runs until 20 PASS in a row, or the first FAIL
  python apply_eval.py --ats greenhouse --status          # show the streak / history only

PASS     = submitted AND a confirmation email from the company arrived (checked 60 s after the tab closed).
EXCLUDED = not a filling error by his rules: posting closed, CAPTCHA, eligibility (citizen / US person / clearance /
           export), site application limit, already applied, or a question only he can answer (listed for him).
FAIL     = anything else (field missed, step stuck, crash, submitted without any email). The streak resets on FAIL.
State: data/eval/<ats>.json (tried ids, history, streak). Uses apply_prep.py --ids for the real run (same code path).
"""
import argparse, datetime as dt, json, re, subprocess, sys, time
from pathlib import Path
import config, notion, score

HERE = Path(__file__).parent
EVAL = config.DATA / 'eval'; EVAL.mkdir(exist_ok=True)
ATS_RX = {'workday': r'myworkday(jobs|site)\.com', 'greenhouse': r'greenhouse\.io|gh_jid=', 'ashby': r'ashbyhq\.com'}
EXCLUDE = [
    ('closed', r'posting closed|no longer accepting|no longer exists|job is gone|not currently accepting'),
    ('captcha', r'captcha'),
    ('gov id', r'CANCELLED|GOV-ID|government id'),
    ('eligibility', r'security clearance|clearance|itar|poly(graph)? type|u\.?s\.? citizen(ship)? (is )?required'),
    ('site limit', r'limiting applications|site limit'),
    ('already applied', r'already applied|possibly by hand|already submitted'),
    ('his answer', r'\(yours\)'),
]


def state(ats):
    f = EVAL / f'{ats}.json'
    try: return json.loads(f.read_text())
    except Exception: return {'tried': [], 'history': [], 'streak': 0, 'best': 0}


def save(ats, st):
    (EVAL / f'{ats}.json').write_text(json.dumps(st, indent=1, ensure_ascii=False))


def fit(r):
    m = re.search(r'fit (\d+)', r.get('Notes') or '')
    return int(m.group(1)) if m else 0


def candidates(ats, st):
    rows = notion.query({'or': [{'property': 'Status', 'select': {'equals': 'To apply'}},
                                {'property': 'Status', 'select': {'equals': 'Needs you'}}]})
    applied = notion.query({'property': 'Status', 'select': {'equals': 'Applied'}})
    week = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=7)).isoformat()
    co = lambda r: re.sub(r'[^a-z0-9]', '', (r.get('Company') or '').lower())[:12]
    per_co = {}
    for r in applied:
        if (r.get('Date Applied') or '') >= week[:10]: per_co[co(r)] = per_co.get(co(r), 0) + 1
    out = [r for r in rows if re.search(ATS_RX[ats], r.get('Link') or '', re.I) and r['id'] not in st['tried']
           and not score.STAFFING.search(r.get('Company') or '') and not score.blocked('', r.get('Position') or '')
           and 'LINK LOST' not in (r.get('Notes') or '') and per_co.get(co(r), 0) < config.PER_COMPANY_WEEK]
    out.sort(key=lambda r: (fit(r), r.get('created') or ''), reverse=True)
    return out


def last_result(row_id, since):
    res = [json.loads(l) for l in (config.DATA / 'apply_results.jsonl').read_text().splitlines() if l.strip()]
    mine = [r for r in res if r['id'] == row_id and r['at'] >= since]
    return mine[-1] if mine else None


def email_confirmed(r):
    import apply_prep
    return apply_prep.confirmed_by_email(r, wait_s=60)


def classify(res, emailed):
    if res is None: return 'FAIL', 'no result logged (crash before the row finished)'
    reason = res.get('reason') or ''
    if res['status'] == 'SUBMITTED':
        return ('PASS', f'submitted + {emailed}') if emailed else ('FAIL', 'submitted but NO confirmation email after 60 s')
    if emailed: return 'PASS', f'confirmation email found ({emailed}) although the run said: {reason[:120]}'
    for name, rx in EXCLUDE:
        if re.search(rx, reason, re.I): return 'EXCLUDED', f'{name}: {reason[:200]}'
    return 'FAIL', reason[:400]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ats', required=True, choices=list(ATS_RX)); ap.add_argument('--target', type=int, default=20)
    ap.add_argument('--max', type=int, default=60, help='max applications this invocation')
    ap.add_argument('--status', action='store_true'); ap.add_argument('--continue-on-fail', action='store_true')
    ap.add_argument('--retry-fails', action='store_true', help='try again every job whose latest verdict was FAIL')
    a = ap.parse_args()
    st = state(a.ats)
    if a.status:
        print(json.dumps({k: st[k] for k in ('streak', 'best')}, indent=1))
        for h in st['history'][-30:]: print(h['at'][5:16], h['verdict'], '|', h['company'][:24], '|', h['why'][:150])
        return
    if a.retry_fails:
        last = {}
        for h in st['history']: last[h['id']] = h['verdict']
        st['tried'] = [i for i in st['tried'] if last.get(i) != 'FAIL']; save(a.ats, st)
    from apply_now import liveness
    n = 0
    while st['streak'] < a.target and n < a.max:
        disk = state(a.ats)   # keep edits made while running (verdicts corrected by hand)
        st['history'] = disk['history'] if len(disk['history']) >= len(st['history']) else st['history']
        cands = candidates(a.ats, st)
        if not cands: print('NO MORE CANDIDATES'); break
        r = cands[0]; st['tried'].append(r['id']); save(a.ats, st)
        live = liveness(r.get('Link') or '')
        if a.ats == 'workday':
            from apply_now import workday_open
            wo = workday_open(r.get('Link') or '')
            if wo is None and not re.search(r'/job/', r.get('Link') or ''):
                live = 'badlink'
        if live == 'badlink':
            notion.update_row(r['id'], notion.props(Notes=('LINK IS NOT A JOB POSTING (careers home) – find the role on the '
                                                           'company site | ' + (r.get('Notes') or ''))[:1990]))
            verdict, why = 'EXCLUDED', 'bad link: not a job posting URL'
        elif live == 'closed':
            notion.update_row(r['id'], notion.props(Status='Closed', Notes=('CLOSED – eval check | ' + (r.get('Notes') or ''))[:1990]))
            verdict, why = 'EXCLUDED', 'closed: posting no longer open (checked before applying)'
        else:
            n += 1
            since = dt.datetime.now(dt.timezone(dt.timedelta(hours=-5))).isoformat(timespec='seconds')[:19]
            print(f"\n=== [{a.ats}] #{n} {r.get('Company')} — {r.get('Position')} (fit {fit(r)})", flush=True)
            p = subprocess.run([sys.executable, str(HERE / 'apply_prep.py'), '--ids', r['id']], capture_output=True, text=True, timeout=1500)
            tail = '\n'.join(l for l in p.stdout.splitlines() if (l.startswith('| ') and not l.startswith('| Company') and not l.startswith('|---'))
                             or re.match(r'(workday )?rescue', l) or 'did not stick' in l)
            print(tail[:1500])
            res = last_result(r['id'], since)
            emailed = email_confirmed(r) if res is not None else None
            verdict, why = classify(res, emailed)
            if res is None: why += ' | stderr: ' + p.stderr[-600:].replace('\n', ' ')
        st['streak'] = st['streak'] + 1 if verdict == 'PASS' else (st['streak'] if verdict == 'EXCLUDED' else 0)
        st['best'] = max(st['best'], st['streak'])
        st['history'].append({'at': dt.datetime.now().isoformat(timespec='seconds'), 'id': r['id'], 'company': r.get('Company'),
                              'role': r.get('Position'), 'link': r.get('Link'), 'verdict': verdict, 'why': why})
        save(a.ats, st)
        print(f"--> {verdict}: {why[:300]}  | streak {st['streak']}/{a.target}", flush=True)
        if verdict == 'FAIL' and not a.continue_on_fail:
            print('STOPPED ON FAIL — fix, then run again (the streak restarts from 0)'); return 2
    print(f"done: streak {st['streak']} (best {st['best']})")


if __name__ == '__main__':
    sys.exit(main())
