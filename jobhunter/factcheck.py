"""Fact check for LLM-written text (idea from career-ops verify-cv-facts / story-provenance-check, MIT).
Every number in a cover letter / answer / outreach draft must appear in his real facts (profile.json,
data/resume_facts.json, Project Library) or in the job description. Otherwise the line is flagged.
0 tokens. Usage: bad = factcheck.unsupported(text, extra=jd_text) -> list of numbers not backed by a source."""
import json, re
import applicant as P, config

NUM = re.compile(r'(?<![\w.])\d[\d,]*(?:\.\d+)?\s*(?:%|\+|k\b|K\b|x\b)?')
_cache = None


def _norm(n):
    return re.sub(r'[,\s+]', '', n).lower().rstrip('.')


def source_numbers():
    global _cache
    if _cache is None:
        parts = [json.dumps(P.DATA)]
        for f in (config.DATA / 'resume_facts.json', config.REF / 'Job Hunter — Project Library.md'):
            try: parts.append(f.read_text())
            except OSError: pass
        _cache = {_norm(m) for m in NUM.findall('\n'.join(parts))}
        _cache |= {_norm(m).rstrip('%k+x') for m in list(_cache)}
    return _cache


def unsupported(text, extra=''):
    ok = source_numbers() | {_norm(m) for m in NUM.findall(extra or '')}
    ok |= {_norm(m).rstrip('%k+x') for m in NUM.findall(extra or '')}
    bad = []
    for m in NUM.findall(text or ''):
        n = _norm(m)
        if len(n.rstrip('%k+x')) <= 1 and not n.endswith('%'): continue      # "3 paragraphs", "1 project": too generic
        if n in ok or n.rstrip('%k+x') in ok: continue
        bad.append(m.strip())
    return bad


def strip_unsupported(text, extra=''):
    """Drop sentences that carry an unsupported number. Returns (clean_text, removed_numbers)."""
    bad = unsupported(text, extra)
    if not bad: return text, []
    out = []
    for line in text.splitlines():
        sents = re.split(r'(?<=[.!?])\s+', line)
        keep = [s for s in sents if not unsupported(s, extra)]
        out.append(' '.join(keep))
    return '\n'.join(out), bad


LEAK = re.compile(r"\bas an ai\b|language model|i (cannot|can't) (help|assist)|here is (a|the|your) (draft|cover letter|answer)|"
                  r"\[(your|company|insert)[^\]]*\]|<[a-z_ ]+>|\bplaceholder\b|I hope this helps", re.I)


def leaks(text):
    """LLM self-talk or unfilled template bits (ApplyPilot LLM_LEAK_PHRASES idea)."""
    return bool(LEAK.search(text or ''))
