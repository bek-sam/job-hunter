"""Role analysis + Application packet for one job. Form answers are copied from the Answer Bank (code);
only the cover letter + a 4-line role analysis come from one small LLM call."""
import re
import applicant as P, config, llm

ANSWERS = P.get('packet', 'answers', default={})            # Answer Bank (FORM FIELDS ONLY), from profile.json
INTERN_EXTRA = P.get('packet', 'intern_extra', default={})

ROUTES = [(rx, names) for rx, names in P.get('packet', 'project_routes', default=[])] or [(r'.', [])]  # Project Library routing
BANNED = re.compile(r'permanent resident|green card|authorized to work|work authori[sz]ation|sponsor|visa|citizen', re.I)


def _library():
    f = config.REF / 'Job Hunter — Project Library.md'   # your local project facts (reference/ is git-ignored)
    return f.read_text() if f.exists() else ''


def project_facts(title, jd_text):
    lib = _library()
    names = next(n for rx, n in ROUTES if re.search(rx, title + ' ' + jd_text[:1500], re.I))
    parts = []
    for n in names:
        if n in {w['company'] for w in P.get('work_history', default=[])}:   # a job: its '- **Company' entry
            m = re.search(r'- \*\*' + re.escape(n) + r'.*?(?=\n- \*\*)', lib, re.S)
        else:
            m = re.search(r'### ' + re.escape(n) + r'.*?(?=\n### |\n---)', lib, re.S)
        if m: parts.append(m.group(0).strip())
    rules = re.search(r'## Rules.*', lib, re.S)
    return '\n\n'.join(parts) + '\n\n' + (rules.group(0) if rules else '')


SYSTEM = (f"You write for {P.NAME}, {P.get('llm', 'bio', default='a candidate')} applying to software roles. "
          "Use ONLY the facts given. Never invent experience, metrics, users, or deployments. Plain, polite, specific English, "
          "no flattery. NEVER mention work authorization, residency, green card, visa, citizenship or sponsorship.")

SCHEMA = {'type': 'object', 'required': ['team', 'what_they_value', 'cover_letter'], 'properties': {
    'team': {'type': 'string', 'description': 'one line: team/product from the JD'},
    'what_they_value': {'type': 'string', 'description': 'one line'},
    'cover_letter': {'type': 'string', 'description': f'3 short paragraphs, signed "{P.NAME}"'},
    'skills_required': {'type': 'array', 'items': {'type': 'string'}, 'description': 'every technical skill/tool/language/framework/concept the JD lists as REQUIRED, short canonical names (e.g. "Python", "React", "Kubernetes", "REST APIs")'},
    'skills_preferred': {'type': 'array', 'items': {'type': 'string'}, 'description': 'every technical skill the JD lists as preferred / nice to have / bonus, same format'}}}


def write(role, jd_text, sc, kind, term=None):
    avail = (P.get('llm', 'availability_intern', default='').replace('{term}', term or '')
             if kind == 'intern' else P.get('llm', 'availability_newgrad', default=''))
    prompt = (f"Job: {role['title']} at {role['company']} ({role.get('location', '')}).\n"
              f"JD (trimmed):\n{jd_text[:5000]}\n\nHis matching facts:\n{project_facts(role['title'], jd_text)}\n\n"
              f"Write: (1) the team/product in one line, (2) what they value in one line, (3) a cover letter: one line on the "
              f"team/product, 2 real projects mapped to their needs, then exactly this availability line: \"{avail}\" "
              f"Sign it \"{P.NAME}\". Max 170 words.")
    out = llm.ask(prompt, system=SYSTEM, schema=SCHEMA, job='cover letter')
    if BANNED.search(out['cover_letter']):  # hard rule: strip any work-auth wording
        out['cover_letter'] = '\n'.join(l for l in out['cover_letter'].splitlines() if not BANNED.search(l))
    return out


def page_markdown(role, jd, sc, kind, term, enroll, w):
    ans = dict(ANSWERS)
    if kind == 'intern':
        ans.update(INTERN_EXTRA); ans['Earliest start date'] = f'Available for the full {term} internship'
    posted = jd.get('posted') or role.get('posted_utc')
    lines = ['## Role analysis', f"- Team / product: {w['team']}", f"- Required: {', '.join(sc['required']) or 'n/a'}",
             f"- Preferred: {', '.join(sc['preferred']) or 'n/a'}", f"- What they value: {w['what_they_value']}",
             f"- Gaps (no evidence yet): {', '.join(sc['missing']) or 'none'}",
             f"- Source: {role.get('source')} · posted {posted}",
             f"- Fit {sc['fit']} (role {sc['parts']['role']}, level {sc['parts']['level']}, skills {sc['parts']['skill']}, "
             f"employer {sc['parts']['employer']}, location {sc['parts']['loc']}) · match {sc['match']}% · evidence {sc['evidence']}%"]
    if kind == 'intern':
        lines.append(f"- Internship term: {term}" + (' · ELIGIBILITY? enrollment rule' if enroll else ''))
    lines += ['## Application packet'] + [f'- {k}: {v}' for k, v in ans.items()] + ['### Cover letter'] + w['cover_letter'].splitlines()
    return '\n'.join(lines)
