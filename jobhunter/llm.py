"""Single-shot Codex/Claude calls, isolated from project files and tools."""
import json, os, subprocess, tempfile, shutil
import config

_EMPTY = tempfile.mkdtemp(prefix='jh_llm_')   # run outside the repo so no CLAUDE.md gets loaded


def ask(prompt, system='You are a concise assistant. Answer only with what is asked.', model=None, schema=None, timeout=240, job=None):
    provider = os.environ.get('LLM_PROVIDER', 'claude').lower()
    if provider == 'codex':
        return ask_codex(prompt, system, schema, timeout, job)
    if provider != 'claude':
        raise ValueError(f'Unknown LLM_PROVIDER: {provider}')
    try:
        return ask_claude(prompt, system, model, schema, timeout, job)
    except RuntimeError as error:
        if os.environ.get('CODEX_FALLBACK', '1') != '1' or not any(marker in str(error).lower() for marker in ('usage_limit_reached', 'weekly limit', 'rate_limit', 'rate limit', 'api_error_status":429')):
            raise
        return ask_codex(prompt, system, schema, timeout, job)


def codex_schema(schema):
    """Close every object and require nullable optional fields for strict output."""
    if isinstance(schema, list):
        return [codex_schema(value) for value in schema]
    if not isinstance(schema, dict):
        return schema
    result = {key: codex_schema(value) for key, value in schema.items()}
    if schema.get('type') == 'object':
        required = schema.get('required', [])
        properties = result.get('properties', {})
        for key, value in list(properties.items()):
            if key not in required:
                properties[key] = {'anyOf': [value, {'type': 'null'}]}
        result.update(additionalProperties=False, required=list(properties))
    return result


def restore_optional(value, schema):
    """Restore omitted optionals without dropping explicitly nullable required answers."""
    if isinstance(value, dict):
        required = schema.get('required', [])
        props = schema.get('properties', {})
        return {key: restore_optional(item, props.get(key, {})) for key, item in value.items()
                if item is not None or key in required}
    if isinstance(value, list):
        return [restore_optional(item, schema.get('items', {})) for item in value]
    return value


def ask_codex(prompt, system, schema, timeout, job, connectors=False, read_only=False, calendar=True):
    binary = os.environ.get('CODEX_BIN') or shutil.which('codex') or os.path.expanduser('~/.npm-global/bin/codex')
    with tempfile.TemporaryDirectory(prefix='jh_codex_') as directory:
        output = os.path.join(directory, 'answer.txt')
        cmd = [binary, 'exec', '--ignore-user-config', '--ephemeral', '--skip-git-repo-check', '--sandbox', 'read-only', '-C', directory, '-o', output]
        if connectors:
            from codex_connectors import config_args
            cmd += config_args(read_only=read_only, calendar=calendar)
        else:
            cmd += ['-c', 'features.apps=false', '-c', 'web_search="disabled"']
        selected_model = os.environ.get('CODEX_MODEL')
        if selected_model:
            cmd += ['--model', selected_model]
        if schema:
            schema_path = os.path.join(directory, 'schema.json')
            with open(schema_path, 'w') as stream:
                json.dump(codex_schema(schema), stream)
            cmd += ['--output-schema', schema_path]
        cmd += ['-']
        guard = ('Use only the permitted connectors and web tools. No shell commands or local files.' if connectors
                 else 'Do not use tools, read files, or execute commands.')
        request = system + '\n\nAnswer the supplied request only. ' + guard + '\n\n' + prompt
        result = subprocess.run(cmd, input=request, capture_output=True, text=True, timeout=timeout, cwd=directory)
        if result.returncode:
            raise RuntimeError(f'codex exec failed: {result.stderr[-500:]}')
        with open(output) as stream:
            answer = stream.read().strip()
        if not answer:
            raise RuntimeError('codex exec returned an empty answer')
        parsed = restore_optional(json.loads(answer), schema) if schema else answer
        log_usage(selected_model or 'codex-default', {}, None, job)
        return parsed


def ask_claude(prompt, system, model, schema, timeout, job):
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
