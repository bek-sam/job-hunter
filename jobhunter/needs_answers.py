"""Answer-once list (Oct 26): every form question the filler left blank in the last 14 days, most frequent first, so
the owner answers each ONCE in profile.json "owner_answers" ("question text": "answer") and answers.py uses it for the
same question on every site from then on. Legal/consent/signature, CAPTCHA and government-ID items are never listed.

  .venv/bin/python jobhunter/needs_answers.py          # writes data/answer_log/needs_answers.md
0 tokens. Inputs: data/failures/*/*.json, data/apply_results.jsonl.
"""
import datetime as dt, glob, json, re
from collections import Counter, defaultdict
import applicant as P
import config

# reason fragments that are status text, not questions
NOISE = re.compile(r'tab (open|closed)|only \d+ field|no submit|submit button|confirmation|sign in|create (an )?account|log ?in|'
                   r'captcha|posting|closed|no longer|already applied|possibly by hand|site limit|modal|not a .* posting|'
                   r'no application form|no apply|did not (open|leave|advance)|could not open|timeout|error|resume (pdf )?missing|'
                   r'one-time code|^\W*$|legal/consent|signature|e-?sign|gov-?id|ineligible|rescue|was not accepted|'
                   r'linkedin|oracle:|workday:|smartrecruiters:|step \d+|refused the form|check before re-applying|^signed in$|^account created|auto-signing|sign-in after|\[my experience\] add work entry|'
                   r'^(\w+ )*(intern|engineer|developer)$', re.I)


def norm(q):
    q = re.sub(r'\s*\((still empty|required|optional)\)\s*', ' ', q or '')
    q = re.sub(r'^(after claude rescue still empty|missing entry for required fields?)\s*:\s*', '', q.strip(), flags=re.I)
    return re.sub(r'\s+', ' ', q.replace('✱', '').replace('*', '')).strip(' :;-')


def collect(days=14):
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    seen, where = Counter(), defaultdict(set)
    for f in glob.glob(str(config.DATA / 'failures' / '*' / '*.json')):
        try: d = json.loads(open(f).read())
        except Exception: continue
        for q in (d.get('unknown') or []) + (d.get('still_empty') or []):
            q = norm(str(q))
            if q and not NOISE.search(q): seen[q] += 1; where[q].add(d.get('company') or '?')
    for line in (config.DATA / 'apply_results.jsonl').read_text().splitlines():
        try: d = json.loads(line)
        except Exception: continue
        if d.get('status') != 'NEEDS YOU' or dt.datetime.fromisoformat(d['at']) < since: continue
        for q in re.split(r';\s+', d.get('reason') or ''):
            q = norm(q)
            if 4 <= len(q) <= 300 and not NOISE.search(q) and not q.endswith('…'):
                seen[q] += 1; where[q].add(d.get('company') or '?')
    known = {norm(k).lower() for k in (P.get('owner_answers', default={}) or {})}
    return [(q, n, sorted(where[q])) for q, n in seen.most_common() if q.lower() not in known]


def main():
    rows = collect()
    out = config.DATA / 'answer_log' / 'needs_answers.md'
    lines = [f"# Questions to answer once — {len(rows)} open ({dt.date.today()})", '',
             'Add answers to profile.json → "owner_answers" as "question text": "your answer" (copy the question as shown).',
             'The next runs use them for the same question on any site. Only answer what is true; leave the rest.', '',
             '| # | Question | Times | Companies |', '|---|---|---|---|']
    import formfill
    has = lambda q: bool((formfill.rule_for(q) or (None,))[0])   # the Answer Bank already answers it -> filler bug, not yours
    mine = [x for x in rows if not has(x[0])]; bugs = [x for x in rows if has(x[0])]
    lines[0] = f"# Questions to answer once — {len(mine)} need you, {len(bugs)} are filler bugs ({dt.date.today()})"
    lines += [f"| {i} | {q.replace('|', '/')} | {n} | {', '.join(c)[:120]} |" for i, (q, n, c) in enumerate(mine, 1)]
    lines += ['', '## Already in the Answer Bank — the filler failed to enter it (code fix, nothing for you)', '',
              '| Question | Times | Companies |', '|---|---|---|']
    lines += [f"| {q.replace('|', '/')} | {n} | {', '.join(c)[:120]} |" for q, n, c in bugs]
    out.write_text('\n'.join(lines) + '\n')
    print(f'{len(mine)} questions need him, {len(bugs)} filler bugs -> {out}')


if __name__ == '__main__':
    main()
