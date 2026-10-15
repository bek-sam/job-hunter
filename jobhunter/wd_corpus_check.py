"""Check the filler against EVERY recorded Workday page (data/workday_corpus/*.jsonl), not one company at a time.

For each recorded choice/text field it computes what the rules would answer now and flags:
  AMBIGUOUS     the rule's option pattern matches 2+ options (how "I identify as a veteran, just not a protected
                veteran" slipped in, Oct 13) -> the pattern must be tightened
  CONTRADICTS   the picked option contradicts the Answer Bank (veteran / disability / ethnicity sub-group ...)
  NO RULE       required field with no rule (goes to Claude at run time; listed so the Answer Bank can cover it)
  NO OPTION     a rule exists but none of the real options matches it
0 tokens, no browser. Run after any rule change:  .venv/bin/python jobhunter/wd_corpus_check.py [--all]
"""
import glob, json, re, sys
from collections import defaultdict
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import config, formfill
from ats.workday_map import id_rule
from ats.workday import WRONG_SELF_ID

SKIP_AID = re.compile(r'(workexperience|education|language|certification)-\d+|legalname|address|city|postal|phone|email|'
                      r'^country$|countryregion|^source$|password|^url$|linkedinaccount', re.I)
# filled by dedicated code (fill_work / fill_education / self_identify / consent ticking), not by rules
CODE_FILLED = re.compile(r'^(company|from|to|job title|date|language|please check one of the boxes below:?|yes, i have read and consent.*)$', re.I)


def answer(f):
    aid = (f.get('aid') or f.get('inputId') or '')
    val, opt = id_rule(aid)
    if val is None and opt is None: val, opt = formfill.rule_for(f.get('label') or '')
    return val, opt


def main(show_all=False):
    rows = defaultdict(list)
    for fn in glob.glob(str(config.DATA / 'workday_corpus' / '*.jsonl')):
        for line in open(fn):
            d = json.loads(line)
            if d.get('after_next'): continue
            for f in d['fields']:
                if SKIP_AID.search(f.get('aid') or f.get('inputId') or ''): continue
                lab = (f.get('label') or '').strip()
                if not lab or f.get('kind') in ('other',) or CODE_FILLED.match(lab): continue
                rows[(d['step'], lab[:160])].append((d['tenant'], f))
    issues = defaultdict(list)
    for (step, lab), items in sorted(rows.items()):
        tenants = sorted({t for t, _ in items})
        f = items[0][1]
        val, opt = answer(f)
        opts = [o for o in (f.get('options') or []) if not re.match(r'^\s*select( one)?\s*$', o, re.I)]
        if opts and opt:
            hits = [o for o in opts if re.search(opt, o.lower())]
            pick = formfill.pick(opts, opt, val)
            if len(hits) > 1: issues['AMBIGUOUS'].append(f'{step} | {lab[:90]} | matches {hits[:4]} -> picks {pick!r} | {tenants}')
            if pick and WRONG_SELF_ID.search(pick): issues['CONTRADICTS'].append(f'{step} | {lab[:90]} | picks {pick!r} | {tenants}')
            if not pick: issues['NO OPTION'].append(f'{step} | {lab[:90]} | rule {val!r} vs options {opts[:6]} | {tenants}')
        elif val is None and opt is None and f.get('required'):
            issues['NO RULE'].append(f'{step} | {lab[:110]} | {tenants}')
        elif show_all:
            issues['OK'].append(f'{step} | {lab[:90]} -> {val!r}')
    n = sum(len(v) for k, v in issues.items() if k != 'OK')
    for k in ('CONTRADICTS', 'AMBIGUOUS', 'NO OPTION', 'NO RULE', 'OK'):
        if issues.get(k):
            print(f'\n## {k} ({len(issues[k])})')
            for x in issues[k]: print('  -', x)
    print(f'\n{len(rows)} distinct questions from {len({t for v in rows.values() for t, _ in v})} companies; {n} issue(s)')
    return 1 if issues.get('CONTRADICTS') else 0


if __name__ == '__main__':
    sys.exit(main('--all' in sys.argv))
