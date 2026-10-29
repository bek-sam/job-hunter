"""Career-Ops-style job evaluation (ported from santifer/career-ops, MIT — vendor/career-ops).
One LLM call per job that already passed score.py (FIT_GATE): scores 5 dimensions -> global 1-5.
Career-Ops rule: apply only at >= 4.0 (EVAL_GATE). Results logged to data/evals.jsonl.
Fails open: if the LLM call breaks, the job is NOT dropped."""
import json, re, datetime as dt
import config, llm, applicant as P

VENDOR = config.ROOT / 'vendor' / 'career-ops' / 'modes' / '_shared.md'
LOG = config.DATA / 'evals.jsonl'

SCHEMA = {'type': 'object', 'required': ['score', 'dimensions', 'confidence', 'red_flags', 'verdict', 'why'], 'properties': {
    'score': {'type': 'number', 'minimum': 1, 'maximum': 5},
    'dimensions': {'type': 'object', 'required': ['cv_match', 'north_star', 'comp', 'culture', 'red_flags'],
                   'properties': {k: {'type': 'number', 'minimum': 1, 'maximum': 5} for k in ['cv_match', 'north_star', 'comp', 'culture', 'red_flags']}},
    'confidence': {'type': 'string', 'enum': ['high', 'medium', 'low']},
    'red_flags': {'type': 'array', 'items': {'type': 'string'}},
    'verdict': {'type': 'string', 'enum': ['apply', 'skip']},
    'why': {'type': 'string'}}}


def _rules():
    """The 'Scoring System' section of career-ops modes/_shared.md (falls back to a short copy)."""
    try:
        t = VENDOR.read_text()
        m = re.search(r'## Scoring System\n(.*?)(?=\n### Evidence confidence|\n## )', t, re.S)
        if m: return m.group(1).strip()
    except OSError: pass
    return ('Five dimensions -> one global 1-5 score: CV match, North Star alignment, Comp, Cultural signals, Red flags. '
            '4.5+ strong, 4.0-4.4 apply, 3.5-3.9 skip unless a specific reason, <3.5 skip.')


def _candidate():
    try:
        f = json.loads((config.DATA / 'resume_facts.json').read_text())
    except OSError:
        f = {}
    lines = []
    for sec in ('experience', 'projects'):
        for row in (f.get(sec) or [])[:12]:
            if isinstance(row, dict):
                name = row.get('name') or row.get('title') or row.get('company') or ''
                bl = [b if isinstance(b, str) else b.get('text', '') for b in (row.get('bullets') or [])][:3]
                lines.append(f"- {name}: " + ' | '.join(bl))
    try:
        from score import EVIDENCE
        skills = ', '.join(sorted(EVIDENCE))
    except Exception:
        skills = ''
    return (f"New-grad CS student (graduating Dec 2026), Houston TX area. Targets: SWE new grad (full-stack, backend, AI/ML, mobile) "
            f"and SWE internships for 2027.\nSkills with evidence: {skills}\nExperience & projects:\n" + '\n'.join(lines))[:6000]


SYSTEM = ('You evaluate one job posting for one candidate, Career-Ops style. Be strict and honest; missing evidence is not neutral. '
          'Candidate is new grad/intern level: comp is often not posted - score comp 3 if unknown, do not punish it. '
          'Ignore work-authorization questions (handled elsewhere). Return only the JSON asked for.')


def evaluate(r, jd_text, job=None):
    prompt = (f"SCORING RULES (from Career-Ops):\n{_rules()}\n\nCANDIDATE:\n{_candidate()}\n\n"
              f"JOB: {r.get('title')} at {r.get('company')} ({r.get('location', '')})\nJD:\n{jd_text[:9000]}\n\n"
              "Score each dimension 1-5 (red_flags: 5 = none), give the global score (holistic, not an average), "
              "verdict apply if global >= 4.0 else skip, and 'why' in one sentence.")
    ev = llm.ask(prompt, system=SYSTEM, model=config.EVAL_MODEL, schema=SCHEMA, job=job or 'career_eval')
    with open(LOG, 'a') as fh:
        fh.write(json.dumps({'at': dt.datetime.now().isoformat(timespec='seconds'), 'company': r.get('company'),
                             'title': r.get('title'), 'url': r.get('url'), **ev}) + '\n')
    return ev


if __name__ == '__main__':
    import argparse, jd as JD
    a = argparse.ArgumentParser(); a.add_argument('--url', required=True); a.add_argument('--title', default=''); a.add_argument('--company', default='')
    a = a.parse_args()
    j = JD.fetch(a.url)
    print(json.dumps(evaluate({'url': a.url, 'title': a.title or j.get('title', ''), 'company': a.company or j.get('company', '')}, j.get('text', '')), indent=2))
