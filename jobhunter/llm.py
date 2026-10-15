"""One-shot LLM calls through headless Claude Code (`claude -p`), kept as small as possible:
custom system prompt, no tools, no MCP servers, no skills, no project files, no saved session."""
import json, os, subprocess, tempfile
import config

_EMPTY = tempfile.mkdtemp(prefix='jh_llm_')   # run outside the repo so no CLAUDE.md gets loaded


def ask(prompt, system='You are a concise assistant. Answer only with what is asked.', model=None, schema=None, timeout=240, job=None):
    cmd = [config.CLAUDE_BIN, '-p', prompt, '--model', model or config.LLM_MODEL, '--output-format', 'json',
           '--system-prompt', system, '--tools', '', '--strict-mcp-config', '--disable-slash-commands',
           '--setting-sources', '', '--no-session-persistence']
    if schema: cmd += ['--json-schema', json.dumps(schema)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=_EMPTY,
                       env=dict(os.environ, CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC='1'))
    if r.returncode != 0:
        raise RuntimeError(f'claude -p failed: {r.stderr[-500:] or r.stdout[-500:]}')
    out = json.loads(r.stdout)
    if out.get('is_error'):
        raise RuntimeError(f"claude -p error: {out.get('result')}")
    usage = out.get('usage', {})
    log_usage(model or config.LLM_MODEL, usage, out.get('total_cost_usd'), job)
    if schema:
        so = out.get('structured_output')
        if so is not None: return so
        txt = out.get('result', '').strip().strip('`')
        return json.loads(txt[txt.find('{'):txt.rfind('}') + 1])
    return out.get('result', '').strip()


def log_usage(model, usage, cost, job=None):
    import datetime as dt
    with open(config.DATA / 'llm_usage.jsonl', 'a') as f:
        f.write(json.dumps({'at': dt.datetime.now().isoformat(timespec='seconds'), 'job': job or 'llm', 'model': model, 'in': usage.get('input_tokens'), 'cache_read': usage.get('cache_read_input_tokens'),
                            'cache_write': usage.get('cache_creation_input_tokens'), 'out': usage.get('output_tokens'),
                            'cost_usd': cost}) + '\n')


if __name__ == '__main__':
    print(ask('Reply with the single word: ok', model=config.LLM_MODEL_CHEAP))
