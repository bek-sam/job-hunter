"""Notion Experience + Projects -> data/resume_facts.json (the resume generator's bullet bank).

Run when you edit those Notion tables (or once a day): ~25 API calls, 0 LLM tokens.
gen_v6 reads only the JSON, so building a resume makes no Notion calls.

Only bullets under "## Resume bullet bank" / "## Resume bullets" are taken, grouped by their
"### <role group>" heading. "## Resume bullet bank — pinned" holds the hand-approved bullets (single source of truth):
each sits under "### Tags: a, b[, always][, boost]", keeps its order, and is marked pin=True
(always = always shown first; boost = ranked above other bullets for matching roles). Dropped: "Ideas to strengthen" groups, anything marked (stated) / confirm /
not trained, and claims the owner removed on Oct 5 2026 (DENY below).
Usage: .venv/bin/python jobhunter/resume_sync.py
"""
import applicant as P
import datetime as dt, json, re, sys
import config, notion

EXPERIENCE_DS = config.EXPERIENCE_DS
PROJECTS_DS = config.PROJECTS_DS
OUT = config.DATA / 'resume_facts.json'

SKIP_GROUP = re.compile(r'ideas to strengthen', re.I)
UNSAFE = re.compile(r'\(stated|confirm|not trained|\bsay ["“]|fill in', re.I)
NOT_DEPLOYED = re.compile(r'deploy|in production|live users|serving \d', re.I)
NOT_DEPLOYED_ROWS = tuple(P.get('resume_rules', 'not_deployed_rows', default=[]))   # projects that must never claim deployment
DENY = re.compile(P.get('resume_rules', 'deny_rx', default='') or r'(?!x)x', re.I)   # claims you removed
TAGS = [  # (tag, regex on the "### group" heading)
    ('agentdev', r'agentic|agent engineering|ai-assisted'), ('ios', r'ios|mobile|pwa'),
    ('ai', r'\bai\b(?!-agent|-assisted)|ml|llm|vision|safety'), ('frontend', r'front|design system|full-stack'),
    ('fullstack', r'full-stack|full stack'), ('backend', r'back|api|platform|integration'), ('data', r'data|analytics'),
    ('devops', r'devops|cloud'), ('testing', r'test'), ('security', r'security'),
    ('automation', r'automation|document|image processing'), ('general', r'general|founder|summary|team'),
]


def _kids(bid):
    out, cur = [], None
    while True:
        r = notion._req('GET', f'/blocks/{bid}/children?page_size=100' + (f'&start_cursor={cur}' if cur else ''))
        out += r['results']
        if not r.get('has_more'):
            return out
        cur = r['next_cursor']


def _text(b):
    return ''.join(x['plain_text'] for x in b.get(b['type'], {}).get('rich_text', [])).strip()


def _val(v):
    t = v['type']; x = v[t]
    if t in ('title', 'rich_text'): return ''.join(i['plain_text'] for i in x).strip()
    if t == 'select': return (x or {}).get('name', '')
    if t == 'multi_select': return [i['name'] for i in x]
    return x


TAG_NAMES = {t for t, _ in TAGS}


def tags_for(group):
    g = group.lower()
    return [t for t, rx in TAGS if re.search(rx, g)] or ['general']


def bullets(page_id, name=''):
    out, inbank, group, confirmed, pinned = [], False, '', False, False
    for b in _kids(page_id):
        t = b['type']
        if t == 'heading_2':
            h = _text(b); inbank = bool(re.search(r'resume bullet', h, re.I)); group = 'General'
            confirmed = bool(re.search(r'confirmed', h, re.I)); pinned = bool(re.search(r'pinned', h, re.I)); continue
        if not inbank:
            continue
        if t == 'heading_3':
            group = _text(b); continue
        if t != 'bulleted_list_item' or SKIP_GROUP.search(group):
            continue
        s = re.sub(r'\s*\(see [^)]*\)', '', _text(b)).strip()
        if name.startswith(NOT_DEPLOYED_ROWS) and NOT_DEPLOYED.search(s):
            continue
        ok = s and not UNSAFE.search(s) and not DENY.search(s)
        if pinned:
            toks = [x.strip().lower() for x in re.sub(r'^tags:\s*', '', group, flags=re.I).split(',')]
            if not ok:
                print(f'WARNING: pinned bullet dropped by safety filters ({name[:30]}): {s[:80]}', file=sys.stderr); continue
            # confirmed=False: pins are chosen by position and 'boost', not by the bank's confirmed bonus
            out.append({'tags': [x for x in toks if x in TAG_NAMES] or ['general'], 'group': group, 'text': s,
                        'confirmed': False, 'pin': True, 'always': 'always' in toks, 'boost': 'boost' in toks})
        elif ok:
            out.append({'tags': tags_for(group), 'group': group, 'text': s, 'confirmed': confirmed})
    return out


def rows(ds, kind):
    res = notion._req('POST', f'/data_sources/{ds}/query', {'page_size': 100, 'sorts': [{'property': 'Order', 'direction': 'ascending'}]})
    out = []
    for p in res['results']:
        pr = {k: _val(v) for k, v in p['properties'].items()}
        out.append({'kind': kind, 'name': pr.get('Name', ''), 'role': pr.get('Role', ''), 'company': pr.get('Company', ''),
                    'location': pr.get('Location', ''), 'dates': pr.get('Dates', ''), 'type': pr.get('Type') or pr.get('Kind', ''),
                    'tech': [t.strip() for t in re.split(r',(?![^()]*\))', pr.get('Tech', '')) if t.strip()],
                    'best_for': pr.get('Best for', []), 'links': pr.get('Repos / links', ''), 'order': pr.get('Order'),
                    'key_numbers': pr.get('Key numbers', ''), 'bullets': bullets(p['id'], pr.get('Name', ''))})
    return out


def main():
    data = {'synced_at': dt.datetime.now().isoformat(timespec='seconds'),
            'experience': rows(EXPERIENCE_DS, 'experience'), 'projects': rows(PROJECTS_DS, 'project')}
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    n = sum(len(r['bullets']) for k in ('experience', 'projects') for r in data[k])
    print(f'{OUT}: {len(data["experience"])} experience, {len(data["projects"])} projects, {n} usable bullets')


if __name__ == '__main__':
    sys.exit(main())
