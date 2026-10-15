"""Question bank: every application question ever seen (Workday corpus + all-site form log), merged by meaning-ish key,
with the answers given, how often, and where. Training data for the rules + a review sheet for the owner.

  .venv/bin/python jobhunter/question_bank.py          # writes data/answer_log/question_bank.{json,md}
0 tokens. Inputs: data/workday_corpus/*.jsonl, data/answer_log/forms.jsonl.
"""
import glob, json, re
from collections import Counter, defaultdict
import config

SKIP = re.compile(r'^(first|last|middle|preferred)? ?name$|^e-?mail|phone|address|city|zip|postal|state$|country$|'
                  r'linkedin|website|url$|password|resume|cover letter|^company$|^from$|^to$|^job title$', re.I)


def key(label):
    k = re.sub(r'\*|\(optional\)|\(required\)', '', (label or '').lower())
    k = re.sub(r'[^a-z0-9 ]', ' ', k)
    return re.sub(r'\s+', ' ', k).strip()[:140]


def add(bank, label, value, kind, options, company, source, required):
    k = key(label)
    if not k or SKIP.search(k) or len(k) < 4: return
    b = bank.setdefault(k, {'label': (label or '').strip()[:300], 'kinds': Counter(), 'answers': Counter(),
                            'companies': set(), 'sources': Counter(), 'options': [], 'required': 0, 'seen': 0})
    b['seen'] += 1; b['kinds'][kind or '?'] += 1; b['sources'][source] += 1; b['companies'].add(company or '?')
    b['required'] += 1 if required else 0
    v = ', '.join(value) if isinstance(value, list) else str(value if value not in (None, True, False) else
                                                              ('checked' if value is True else '' if value is None else 'unchecked'))
    b['answers'][v.strip()[:120] or '(empty)'] += 1
    if options and not b['options']: b['options'] = [o for o in options if o][:20]


def main():
    bank = {}
    for fn in glob.glob(str(config.DATA / 'workday_corpus' / '*.jsonl')):
        for line in open(fn):
            d = json.loads(line)
            if d.get('after_next'): continue
            for f in d['fields']:
                add(bank, f.get('label'), f.get('value'), f.get('kind'), f.get('options'), d.get('company') or d.get('tenant'),
                    'workday', f.get('required'))
    fl = config.DATA / 'answer_log' / 'forms.jsonl'
    if fl.exists():
        for line in fl.read_text().splitlines():
            d = json.loads(line)
            host = re.sub(r'^https?://([^/]+).*', r'\1', d.get('url') or '')
            src = 'greenhouse' if 'greenhouse' in host else 'ashby' if 'ashby' in host else 'linkedin' if 'linkedin' in host else host
            for f in d['fields']:
                add(bank, f.get('label'), f.get('value'), f.get('kind'), f.get('options'), d.get('company'), src, f.get('required'))
    items = sorted(bank.values(), key=lambda b: (-len(b['companies']), -b['seen']))
    out = config.DATA / 'answer_log'; out.mkdir(exist_ok=True)
    (out / 'question_bank.json').write_text(json.dumps(
        [dict(b, companies=sorted(b['companies']), kinds=dict(b['kinds']), answers=dict(b['answers'].most_common(8)),
              sources=dict(b['sources'])) for b in items], indent=1, ensure_ascii=False))
    lines = [f'# Question bank — {len(items)} distinct questions', '',
             '| # | Question | Companies | Answers given (count) | Type |', '|---|---|---|---|---|']
    for i, b in enumerate(items, 1):
        ans = '; '.join(f'{a} ({n})' for a, n in b['answers'].most_common(3))
        lines.append(f"| {i} | {b['label'][:110].replace('|', '/')} | {len(b['companies'])} | {ans[:120].replace('|', '/')} | "
                     f"{', '.join(b['kinds'])} |")
    (out / 'question_bank.md').write_text('\n'.join(lines) + '\n')
    print(f"{len(items)} distinct questions -> {out / 'question_bank.md'}")
    for b in items[:15]:
        print(f"  {len(b['companies']):2} cos | {b['label'][:70]:70} | {b['answers'].most_common(1)[0][0][:40]}")


if __name__ == '__main__':
    main()
