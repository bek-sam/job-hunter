"""Light, constrained bullet rewording for one job (upgrade 4): swap a word you already use for the job's exact
term ("REST services" -> "REST APIs", "Postgres" -> "PostgreSQL"). One cheap LLM call per resume, then a strict
code check on every suggestion -- the LLM can propose, only the validator decides:

- only bank bullets (pinned / hand-approved bullets are never touched)
- every number stays exactly the same, nothing is added except words that appear in the job's own terms
- at most 3 words changed, length within +20 characters, >= 75% of the words unchanged

  BULLET_REWORD=0 in .env turns it off. The Notion page lists every change (old -> new) for review.
"""
import hashlib, json, os, re
import config

STOP = {'a', 'an', 'the', 'and', 'or', 'with', 'for', 'of', 'to', 'in', 'on', 'by', 'using', 'via', 'into', 'from', 'as', 'at'}
CACHE = config.DATA / 'reword_cache.json'
SYSTEM = ("You adjust resume bullets to a job description's exact vocabulary. For each bullet, either return it "
          "unchanged or swap at most 3 words for the JD's exact term for the SAME thing (synonym, spelling, or "
          "canonical name: 'Postgres' -> 'PostgreSQL', 'REST services' -> 'REST APIs'). Never add a new skill, tool, "
          "claim, number or result; never remove a number; keep the meaning identical. Return only bullets you changed.")
SCHEMA = {'type': 'object', 'required': ['changes'], 'properties': {'changes': {'type': 'array', 'items': {
    'type': 'object', 'required': ['id', 'text'], 'properties': {'id': {'type': 'integer'}, 'text': {'type': 'string'}}}}}}


def on():
    return os.environ.get('BULLET_REWORD', '1') == '1'


def _words(s):
    return re.findall(r"[A-Za-z0-9+#./-]+", s)


def valid(old, new, allowed):
    """True if `new` only swaps a few words of `old` for words in `allowed` (lower-case JD vocabulary)."""
    if not new or new == old: return False
    if re.findall(r'\d+(?:[.,]\d+)?%?', old) != re.findall(r'\d+(?:[.,]\d+)?%?', new): return False
    if len(new) > len(old) + 20 or len(new) < len(old) - 40: return False
    ow, nw = [w.lower() for w in _words(old)], [w.lower() for w in _words(new)]
    added = [w for w in nw if w not in ow and w not in STOP]
    if len(added) > 3 or any(w.strip('.,') not in allowed for w in added): return False
    kept = sum(1 for w in set(ow) if w in nw)
    return kept >= 0.75 * len(set(ow))


def vocabulary(spec):
    out = set()
    for t in spec.get('required', []) + spec.get('preferred', []):
        out |= {w.lower().strip('.,') for w in _words(t)}
    out |= {w.lower().strip('.,') for w in _words(spec.get('jd_text') or '')}
    return out


def plan(spec, bullets):
    """bullets = bank bullet texts (not pinned) -> {old text: new text} of validated rewrites."""
    if not on() or not bullets or not (spec.get('required') or spec.get('jd_text')): return {}
    allowed = vocabulary(spec)
    key = hashlib.sha1(json.dumps([sorted(spec.get('required', []) + spec.get('preferred', [])), bullets]).encode()).hexdigest()
    try: cache = json.loads(CACHE.read_text())
    except Exception: cache = {}
    if key in cache: return cache[key]
    terms = ', '.join(dict.fromkeys(spec.get('required', []) + spec.get('preferred', [])))
    prompt = (f"JOB TITLE: {spec.get('position')}\nJD TERMS: {terms}\n\nBULLETS:\n" +
              '\n'.join(f'[{i}] {b}' for i, b in enumerate(bullets)))
    try:
        import llm
        out = llm.ask(prompt, system=SYSTEM, schema=SCHEMA, model=config.LLM_MODEL_CHEAP, job='bullet reword', timeout=180)
    except Exception as e:
        print('bullet reword skipped:', str(e)[:100]); return {}
    got = {}
    for c in out.get('changes', []):
        i = c.get('id')
        if isinstance(i, int) and 0 <= i < len(bullets) and valid(bullets[i], (c.get('text') or '').strip(), allowed):
            got[bullets[i]] = c['text'].strip()
    cache[key] = got
    try: CACHE.write_text(json.dumps(cache, ensure_ascii=False))
    except Exception: pass
    return got
