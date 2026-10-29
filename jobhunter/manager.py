"""Job Manager, local edition (rules: reference/schedules/Job_Manager_daily__coach__team_lead.md).
Python computes the funnel + team health (0 tokens) and applies all Notion changes; ONE `claude -p` call does the
Gmail/Calendar/web judgment work with a tight tool allowlist (no send, no delete, no Notion tools).

  python manager.py                    # normal run
  python manager.py --dry              # build context, print prompt size, no LLM, no writes
  python manager.py --fixture data/test/funnel_fixture.json --dry
"""
import applicant as P
import argparse, datetime as dt, json, os, re, subprocess, sys, tempfile
from zoneinfo import ZoneInfo
import config, funnel, notion

CT = ZoneInfo('America/Chicago')
PROMPTS = config.PKG / 'prompts'
REPORTS = config.DATA / 'reports'

GMAIL = 'mcp__claude_ai_Gmail__'
CAL = 'mcp__claude_ai_Google_Calendar__'
# never available to any local agent call
DENY = [f'{GMAIL}{t}' for t in ('send_message', 'forward', 'reply', 'trash_message', 'trash_thread', 'delete_draft',
                                'delete_label', 'mark_message_spam', 'mark_thread_spam', 'update_draft')] + \
       [f'{CAL}{t}' for t in ('delete_event', 'respond_to_event', 'update_event')] + \
       ['mcp__claude_ai_Notion', 'mcp__claude_ai_Higgsfield', 'mcp__claude_ai_Gamma', 'mcp__claude_ai_Claude_Docs',
        'mcp__claude_ai_Canva', 'mcp__claude_ai_Shopify', 'mcp__claude_ai_Uber', 'mcp__claude_ai_Uber_Eats',
        'mcp__claude_ai_Microsoft_365', 'mcp__claude_ai_Interactive_Brokers_IBKR', 'Bash', 'Write', 'Edit']
MANAGER_TOOLS = [f'{GMAIL}search_threads', f'{GMAIL}get_thread', f'{GMAIL}get_message', f'{GMAIL}create_draft',
                 f'{CAL}list_events', f'{CAL}search_events', f'{CAL}create_event', 'WebSearch', 'WebFetch', 'ToolSearch']

SCHEMA = {'type': 'object', 'required': ['report_md', 'push', 'notion_updates'], 'properties': {
    'report_md': {'type': 'string'},
    'push': {'type': 'string'},
    'notion_updates': {'type': 'array', 'items': {'type': 'object', 'required': ['company'], 'properties': {
        'company': {'type': 'string'}, 'position_hint': {'type': 'string'},
        'status': {'type': 'string', 'enum': funnel.STAGES},
        'date_applied': {'type': 'string'}, 'note': {'type': 'string'}}}},
    'actions_taken': {'type': 'array', 'items': {'type': 'string'}},
    'decisions_for_owner': {'type': 'array', 'items': {'type': 'string'}}}}

SYSTEM = ("You are an autonomous job-search assistant running unattended. Follow the user prompt's rules exactly. "
          "Load tools with ToolSearch (select:<names>) before calling them. Never send email; drafts only. "
          "Finish with only the JSON object required by the schema.")


def agent(prompt, tools, schema, model=None, budget_usd=2.0, timeout=900, system=SYSTEM):
    """llm.ask-compatible call that KEEPS the claude.ai connectors (no --strict-mcp-config) but allows only `tools`.
    (Suggested future change: give llm.ask `tools=`/`allowed=` params so both paths share one wrapper.)"""
    cwd = tempfile.mkdtemp(prefix='jh_agent_')
    cmd = [config.CLAUDE_BIN, '-p', prompt, '--model', model or config.LLM_MODEL, '--output-format', 'json',
           '--system-prompt', system, '--tools', 'ToolSearch,WebSearch,WebFetch',
           '--allowedTools', ','.join(tools), '--disallowedTools', ','.join(DENY),
           '--setting-sources', '', '--disable-slash-commands', '--no-session-persistence',
           '--max-budget-usd', str(budget_usd), '--json-schema', json.dumps(schema)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd,
                       env=dict(os.environ, CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC='1'))
    try:
        out = json.loads(r.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f'claude -p failed ({r.returncode}): {(r.stderr or r.stdout)[-600:]}')
    u = out.get('usage', {})
    with open(config.DATA / 'llm_usage.jsonl', 'a') as f:
        f.write(json.dumps({'at': __import__('datetime').datetime.now().isoformat(timespec='seconds'), 'job': schema.get('title', 'agent'), 'model': model or config.LLM_MODEL, 'in': u.get('input_tokens'),
                            'cache_read': u.get('cache_read_input_tokens'), 'cache_write': u.get('cache_creation_input_tokens'),
                            'out': u.get('output_tokens'), 'cost_usd': out.get('total_cost_usd'), 'turns': out.get('num_turns')}) + '\n')
    if out.get('is_error') and out.get('structured_output') is None:
        raise RuntimeError(f"claude -p error: {str(out.get('result'))[:600]}")
    so = out.get('structured_output')
    if so is None:
        txt = (out.get('result') or '').strip()
        so = json.loads(txt[txt.find('{'):txt.rfind('}') + 1])
    return so


def prompt_for(name, context, **extra):
    t = (PROMPTS / f'{name}.md').read_text()
    for k, v in extra.items():
        t = t.replace('{{' + k + '}}', v)
    t = P.fill(t)   # {{NAME}}, {{FIRST}}, {{CANDIDATE}}, ... from profile.json
    return t.replace('{{CONTEXT}}', json.dumps(context, separators=(',', ':'), ensure_ascii=False))


def size_line(p):
    return f'prompt: {len(p):,} chars ≈ {len(p) // 4:,} tokens (+ ~1-3k system/tool schemas; tool results add more)'


def norm(s):
    return re.sub(r'[^a-z0-9]', '', (s or '').lower())


def match_row(rows, company, position_hint=''):
    """Find the Notion row for a model-returned company + position words. Returns (row, ambiguous)."""
    c = norm(company)
    cands = [r for r in rows if c and (c in norm(r.get('Company')) or norm(r.get('Company')) in c) and norm(r.get('Company'))]
    if position_hint and len(cands) > 1:
        words = [w for w in re.findall(r'[a-z0-9]+', position_hint.lower()) if len(w) > 2]
        scored = sorted(cands, key=lambda r: -sum(w in (r.get('Position') or '').lower() for w in words))
        best = sum(w in (scored[0].get('Position') or '').lower() for w in words)
        top = [r for r in scored if sum(w in (r.get('Position') or '').lower() for w in words) == best]
        cands = top
    if len(cands) > 1:  # prefer active rows, newest
        act = [r for r in cands if r.get('Status') in ('To apply', 'Needs you', 'Applied', 'OA', 'Interview')] or cands
        act.sort(key=lambda r: r.get('created') or '', reverse=True)
        return act[0], True
    return (cands[0], False) if cands else (None, False)


def apply_status_updates(rows, updates, dry=False):
    import notion
    applied, skipped = [], []
    rank = {s: i for i, s in enumerate(['To apply', 'Needs you', 'Applied', 'OA', 'Interview', 'Offer'])}
    for u in updates:
        r, amb = match_row(rows, u.get('company'), u.get('position_hint', ''))
        if not r:
            skipped.append(f"{u.get('company')}: no matching row"); continue
        st = u.get('status')
        if amb and st:   # Oct 14: a Figma intern rejection was written to the Full Stack row -> never guess a Status
            note_amb = f"CHECK: email says {st} for '{u.get('position_hint', '')}' but it matches several {u.get('company')} rows"
            if not dry: notion.update_row(r['id'], notion.props(Notes=(note_amb + ' | ' + (r.get('Notes') or ''))[:1990]))
            skipped.append(f"{u.get('company')} – {u.get('position_hint', '')}: {st} NOT applied (several matching rows; check by hand)")
            continue
        # never move a row backwards in the funnel (e.g. a late "thanks for applying" after an OA invite)
        if st in rank and r.get('Status') in rank and rank[st] < rank[r['Status']] and st != 'Needs you':
            st = None
        note = u.get('note')
        props = notion.props(Status=st, DateApplied=(u.get('date_applied') or None) if u.get('date_applied') and not r.get('Date Applied') else None,
                             Notes=((note + ' | ' + (r.get('Notes') or ''))[:1990]) if note else None)
        if not props:
            continue
        if not dry: notion.update_row(r['id'], props)
        applied.append(f"{r.get('Company')} – {r.get('Position')}: {st or 'note'}" + (' (ambiguous match)' if amb else ''))
    return applied, skipped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry', action='store_true'); ap.add_argument('--fixture')
    ap.add_argument('--model', default=config.LLM_MODEL); ap.add_argument('--budget', type=float, default=2.0)
    a = ap.parse_args()
    now = dt.datetime.now(CT)
    rows = []
    try:
        if a.fixture:
            fun = funnel.get(a.fixture)
        else:
            rows = funnel.load_rows(); fun = funnel.compute(rows)
            ghost_days = int(os.environ.get('GHOST_DAYS', '30'))   # Oct 28: Applied + silent N days -> Ghosted (status only)
            for r in funnel.ghost_candidates(rows, ghost_days):
                note = f"Ghosted: no reply {ghost_days}+ days after applying ({now:%b %d})"
                if not a.dry:
                    notion.update_row(r['id'], notion.props(Status='Ghosted', Notes=(note + ' | ' + (r.get('Notes') or ''))[:1990]))
                r['Status'] = 'Ghosted'; print('ghosted:', r.get('Company'), '-', r.get('Position'))
            fun = funnel.compute(rows)
    except funnel.FunnelError as e:
        print('ERROR:', e); sys.exit(2)
    REPORTS.mkdir(parents=True, exist_ok=True)
    last = sorted(REPORTS.glob('Manager Report — *.md'))
    ctx = {'now_ct': now.strftime('%a %Y-%m-%d %H:%M CT'), 'sunday': now.weekday() == 6, 'funnel': fun,
           'team_health': funnel.team_health(),
           'last_report': last[-1].read_text()[:1500] if last else None}
    p = prompt_for('manager', ctx)
    # Oct 12 18:47 run reported "no Gmail or Calendar tools": the connectors are deferred -> load them first, explicitly
    load = ','.join(t for t in MANAGER_TOOLS if t.startswith('mcp__'))
    p = (f"FIRST ACTION (required): call ToolSearch with query \"select:{load}\" to load the Gmail and Google Calendar "
         f"tools; they exist but are deferred until loaded. Only if that call returns nothing may you report tools missing.\n\n") + p
    print(size_line(p))
    if a.dry:
        return
    out = agent(p, MANAGER_TOOLS, dict(SCHEMA, title='manager'), model=a.model, budget_usd=a.budget)
    applied, skipped = apply_status_updates(rows, out.get('notion_updates', []))
    md = out['report_md'] + ('\n\n## Notion updates applied\n' + '\n'.join(f'- {x}' for x in applied) if applied else '') + \
         ('\n\n## Not matched\n' + '\n'.join(f'- {x}' for x in skipped) if skipped else '')
    title = f"Manager Report — {now:%Y-%m-%d} ({'early' if now.hour < 12 else 'evening'}, local)"
    (REPORTS / f'{title}.md').write_text(md)
    try:
        import notion
        slot = 'early' if now.hour < 12 else 'evening'
        page = notion.create_row({'Name': {'title': notion._rt(title)}, 'Date': {'date': {'start': f'{now:%Y-%m-%d}'}},
                                  'Kind': {'select': {'name': 'Manager Report'}}, 'Slot': {'select': {'name': slot}},
                                  'Source': {'select': {'name': 'local'}}},
                                 notion.blocks_from_md(md), ds=config.REPORTS_DS)
        print('Notion:', page.get('url'))
    except Exception as e:
        print('Notion report page failed:', e)
    import notify
    if out.get('push'): notify.push(out['push'], title='Job Manager')
    print(md)


if __name__ == '__main__':
    main()
