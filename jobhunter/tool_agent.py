"""Tool-restricted `claude -p` agent call shared by outreach.py, auth.py (Gmail code lookup) and apply_prep.py
(confirmation-email lookup). Keeps the claude.ai connectors but allows only the listed tools; send/delete are denied.
(Moved out of manager.py when the Manager was removed, Nov 7 2026.)"""
import applicant as P
import json, os, re, subprocess, tempfile
import config
CT = config.TZ   # local time zone (JOBHUNTER_TZ, default America/Chicago)
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


SYSTEM = ("You are an autonomous job-search assistant running unattended. Follow the user prompt's rules exactly. "
          "Load tools with ToolSearch (select:<names>) before calling them. Never send email; drafts only. "
          "Finish with only the JSON object required by the schema.")


def agent(prompt, tools, schema, model=None, budget_usd=2.0, timeout=900, system=SYSTEM):
    """Run the selected provider with only the job's permitted connectors."""
    if os.environ.get('LLM_PROVIDER', 'claude').lower() == 'codex':
        import llm
        from codex_connectors import SYSTEM as CODEX_SYSTEM
        return llm.ask_codex(prompt, CODEX_SYSTEM + '\n' + system, schema, timeout, schema.get('title', 'agent'),
                             connectors=True, calendar=any(t.startswith(CAL) for t in tools),
                             read_only=not any(t.endswith(('create_draft', 'create_event')) for t in tools))
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

