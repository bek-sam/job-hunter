"""Resume generator v6 (Oct 13 2026) — same look as v5, structure from the Amazon recruiter guide.

Order: Header -> Education -> Technical Skills -> Experience -> Projects -> Leadership & Involvement.
- Education: school + city on line 1, degree + GPA + graduation month on line 2, coursework (6-8, ordered by role).
  An optional second school (profile resume_v6.grad_school) is shown above it on resumes of kind resume_v6.grad_school_on.
- Skills near the top, grouped by category, JD terms first; AWS certification listed with the skills.
- Experience/leadership entries use two lines: Company — City, State / Title — Dates.
- Bullets: the pinned (hand-approved) bullets plus the best role-matched bank bullets, all from Notion via
  data/resume_facts.json (resume_sync.py). No bullet text lives in this file; nothing is invented here.
- Projects: the 3 best for the role out of an approved pool, each with dates, link and stack.
Usage: python3 gen_v6.py specs.json   (spec: id, file, position, required, preferred, kind='newgrad'|'intern')
"""
import json, os, re, sys
from docx import Document
from docx.shared import Pt, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from gen_v5 import NAME, CONTACT, CANON, FAMILY, THEMES, canon, _run, _link, _para, _rule, to_pdf
import applicant as P, config

OUT = os.environ.get('RESUME_OUT', str(config.RESUMES))
FACTS = config.DATA / 'resume_facts.json'

# ---------- fixed facts: profile.json "resume_v6" (git-ignored) ----------
R6 = P.get('resume_v6', default={})
NAU = tuple(R6.get('school', ['', '', '', '', '']))         # (school, city, degree, GPA line, graduation) -- always shown
UT = tuple(R6['grad_school']) if R6.get('grad_school') else None   # optional 2nd school, shown on R6['grad_school_on'] resumes
COURSES = R6.get('courses', [])
COURSE_FIRST = R6.get('course_first', {})  # role key -> courses in order of relevance (recruiter guide: core first, then role-specific)
CERT = R6.get('cert', '')
HONORS = R6.get('honors', '')

# Bullets live ONLY in Notion: each Experience/Projects row has a "## Resume bullet bank — pinned" section
# (hand-approved, ordered; "### Tags: …, always, boost") plus the wider bank. resume_sync.py copies both into
# data/resume_facts.json; pins(prefix) returns the pinned ones in order. Entries below hold layout facts only.
EXP = [tuple(e) for e in R6.get('exp', [])]      # (bank name prefix, company, location, title, dates)
PROJ = {k: (v[0], [tuple(x) for x in v[1]], *v[2:]) for k, v in R6.get('proj', {}).items()}  # name -> (bank prefix, links, stack, dates, best_for tags, base priority)
LEAD = [tuple(e) for e in R6.get('lead', [])]     # (bank prefix, org, location, role, dates) — bullets: the row's pinned bullets
ALWAYS = R6.get('always_pins_entry', '')          # experience entry whose "always" pins come first


# ---------- role key + headline ----------
ROLEKEY = {'Machine Learning Engineer': 'ai', 'AI Engineer': 'ai', 'Data Engineer': 'data', 'Forward Deployed Engineer': 'fullstack',
           'iOS Software Engineer': 'ios', 'Mobile Software Engineer': 'ios', 'Full-Stack Software Engineer': 'fullstack',
           'Front-End Software Engineer': 'frontend', 'Backend Software Engineer': 'backend', 'DevOps Engineer': 'devops',
           'Site Reliability Engineer': 'devops', 'Cloud Software Engineer': 'devops', 'Infrastructure Software Engineer': 'devops',
           'Software Engineer in Test': 'testing', 'Embedded Software Engineer': 'embedded', 'Robotics Software Engineer': 'ai',
           'Quantitative Developer': 'backend'}
TAG_ORDER = {  # role key -> bullet-bank tags in order of preference
    'ai': ['ai', 'backend', 'data', 'agentdev', 'fullstack'], 'data': ['data', 'backend', 'ai'], 'fullstack': ['fullstack', 'frontend', 'backend', 'ai'],
    'frontend': ['frontend', 'fullstack', 'ios'], 'backend': ['backend', 'data', 'security', 'devops'], 'ios': ['ios', 'frontend', 'ai'],
    'devops': ['devops', 'backend', 'security', 'testing'], 'testing': ['testing', 'devops', 'backend'],
    'embedded': ['ios', 'backend', 'automation'], 'general': ['backend', 'fullstack', 'ai', 'general'],
}
OWN_THEME = {'ai': 'AI/ML', 'data': 'data pipelines', 'frontend': 'full-stack web', 'fullstack': 'full-stack web',
             'backend': 'backend APIs', 'ios': 'iOS/Swift', 'devops': 'cloud & DevOps'}
FIXED_FOCUS = {'ai': ['AI/ML', 'LLM applications']}  # strongest real evidence
RELATED = {  # themes allowed in the headline per role, so the pitch stays on-role (no "cloud & DevOps" on AI roles)
    'ai': ['data pipelines', 'backend APIs', 'full-stack web', 'system design'],
    'data': ['SQL databases', 'cloud & DevOps', 'AI/ML', 'backend APIs', 'distributed systems'],
    'frontend': ['backend APIs', 'AI/ML', 'system design'],
    'fullstack': ['backend APIs', 'AI/ML', 'SQL databases', 'cloud & DevOps', 'system design'],
    'backend': ['distributed systems', 'SQL databases', 'cloud & DevOps', 'data pipelines', 'AI/ML', 'system design'],
    'ios': ['full-stack web', 'AI/ML', 'backend APIs'],
    'devops': ['backend APIs', 'distributed systems', 'system design', 'SQL databases'],
}


# Oct 14: most SWE titles ("Junior Software Engineer", "Software Engineer, Securities Finance", "Graduate software
# engineer") fell into 'general' -> every resume came out the same. The JD body + skills decide the focus instead.
FOCUS_RX = {
    'frontend': r'\breact\b|front[- ]?end|\bui\b|\bux\b|\bcss\b|typescript|javascript|figma|accessib|web app',
    'backend': r'back[- ]?end|\bapis?\b|microservice|distributed|scalab|\bjava\b|\bgo(lang)?\b|kafka|grpc|postgres|database|latency|reliab',
    'data': r'\bdata\b|\betl\b|spark|airflow|snowflake|warehouse|pipeline|analytics|visuali[sz]|dashboard|tableau|power bi|\bd3\b|charts?\b',
    'ai': r'machine learning|\bml\b|\bllms?\b|\bai\b|pytorch|tensorflow|\bnlp\b|model|genai|generative|agents?\b|\brag\b',
    'ios': r'\bios\b|swift|mobile|android|kotlin|react native',
    'devops': r'devops|\bsre\b|kubernetes|terraform|ci/cd|infrastructure|\bcloud\b|linux|observab',
}


def jd_focus(spec):
    text = ' '.join([spec.get('position') or ''] * 3 + [spec.get('jd_text') or ''] + spec.get('required', []) + spec.get('preferred', [])).lower()
    hits = {k: len(re.findall(rx, text)) for k, rx in FOCUS_RX.items()}
    k = max(hits, key=hits.get)
    return k if hits[k] >= 3 else 'general'


def role_of(spec):
    pos = (spec.get('position') or '').lower()
    role = next((r for rx, r in FAMILY if re.search(rx, pos)), 'Software Engineer')
    key = ROLEKEY.get(role, 'general')
    if key == 'general' and (spec.get('jd_text') or spec.get('required')):
        key = jd_focus(spec)
    return role, key


def headline(spec):
    if spec.get('title'): return spec['title']
    role, key = role_of(spec)
    terms = [t.lower() for t in spec.get('required', []) + spec.get('preferred', [])]
    score = sorted((-sum(1 for t in terms if re.search(rx, t)), i, n) for i, (n, rx) in enumerate(THEMES))
    focus = list(FIXED_FOCUS.get(key) or ([OWN_THEME[key]] if key in OWN_THEME else []))
    allowed = RELATED.get(key)
    focus += [n for s, _, n in score if s < 0 and n not in focus and (allowed is None or n in allowed)]
    for d in ('full-stack systems', 'AI/ML', 'system design'):
        if d not in focus and not (d == 'full-stack systems' and 'full-stack web' in focus): focus.append(d)
    return role + ' — ' + ', '.join(focus[:3])


# ---------- bullet selection ----------
_FACTS_CACHE = {}


def _facts():
    """data/resume_facts.json, re-read only when the file changes."""
    try:
        m = FACTS.stat().st_mtime
        if _FACTS_CACHE.get('m') != m: _FACTS_CACHE.update(m=m, d=json.loads(FACTS.read_text()))
        return _FACTS_CACHE['d']
    except Exception:
        return {'experience': [], 'projects': []}


def _row(facts, prefix):
    for r in facts.get('experience', []) + facts.get('projects', []):
        if r['name'].startswith(prefix): return r['bullets']
    return []


def _bank(facts, prefix):
    """Wider bullet bank (pinned bullets excluded; they come from pins())."""
    return [b for b in _row(facts, prefix) if not b.get('pin')]


def pins(facts, prefix):
    """Hand-approved bullets for a row, in Notion order (first = scope/intro bullet)."""
    return [b for b in _row(facts, prefix) if b.get('pin')]


JD_TOPICS = ['dashboard', 'visualization', 'charts', 'analytics', 'maps', 'real-time', 'latency', 'scalability', 'reliability',
             'payments', 'trading', 'fintech', 'healthcare', 'privacy', 'security', 'compliance', 'mobile', 'offline', 'search',
             'testing', 'automation', 'agents', 'llm', 'rag', 'evaluation', 'pipeline', 'api', 'microservices', 'accessibility',
             'performance', 'inventory', 'logistics', 'e-commerce', 'customer']


def _terms(spec):
    out = set()
    for t in spec.get('required', []) + spec.get('preferred', []):
        out.add(t.lower()); n = canon6(t)
        if n: out.add(n.lower())
    jd = (spec.get('jd_text') or '').lower()
    out |= {w for w in JD_TOPICS if re.search(r'\b' + re.escape(w), jd)}
    return {t for t in out if len(t) > 1}


LISTY = re.compile(r'\bincluding\b(?:[^,]*,){3,}')


def _score(b, key, terms, maxlen):
    if len(b['text']) > maxlen: return -99
    order = TAG_ORDER.get(key, TAG_ORDER['general'])
    tag = max((len(order) - order.index(t) for t in b['tags'] if t in order), default=0)
    low = b['text'].lower()
    hits = sum(1 for t in terms if re.search(r'(?<![a-z])' + re.escape(t) + r'(?![a-z])', low))
    # recruiter guide: quantified impact wins; short task-only bullets ("Built forms with X") lose
    return (3 * tag + 3 * hits + (3 if re.search(r'\d', b['text']) else 0) - (3 if len(b['text']) < 90 else 0)
            + (4 if b.get('confirmed') and tag else 0) + (5 if b.get('boost') and tag else 0)  # boost: pinned bullet marked to outrank others
            - (4 if LISTY.search(b['text']) else 0))  # "Built 23 pages including A, B, C, D..." lists screens, not results  # confirmed facts (with results) beat repo-only bullets


def _words(s):
    return set(re.findall(r'[a-z0-9]+', s.lower())) - {'a', 'an', 'the', 'and', 'with', 'for', 'of', 'to', 'in', 'on', 'by', 'built', 'created'}


def _dup(text, taken):
    """Near-duplicate of a bullet already on the page (e.g. two 'Built X, an AI wardrobe assistant...' intros)."""
    w = _words(text)
    return any(len(w & _words(t)) / max(1, min(len(w), len(_words(t)))) > 0.45 for t in taken)


FIRST_PERSON = re.compile(r'\b(I|my|me)\b')


def pick(pool, n, key, terms, taken, maxlen=200, any_fit=False):
    """Best n bullets for the role. any_fit=True (approved bullets): role match not required, only length."""
    ranked = sorted((b for b in pool if b['text'] not in taken), key=lambda b: -_score(b, key, terms, maxlen))
    floor = -1 if any_fit else 0
    out = []
    for b in ranked:
        if len(out) == n or _score(b, key, terms, maxlen) <= floor: break
        if FIRST_PERSON.search(b['text']): continue  # resume bullets use implied first person
        if not _dup(b['text'], taken | set(out)): out.append(b['text'])
    taken.update(out)
    return out


def choose_projects(key, terms, n):
    facts = _facts()
    def s(name):
        prefix, _, stack, _, best, base = PROJ[name]
        st = stack.lower()
        bul = ' '.join(b['text'] for b in _row(facts, prefix)).lower()
        cover = sum(1 for t in terms if re.search(r'(?<![a-z])' + re.escape(t) + r'(?![a-z])', bul))
        return base + (4 if key in best else 0) + 2 * sum(1 for t in terms if t in st) + min(cover, 6) * 0.75
    return sorted(PROJ, key=lambda k: -s(k))[:n]


# ---------- skills ----------
CATS = ['Languages', 'Frameworks & Libraries', 'AI / ML', 'Databases & Data', 'Cloud & DevOps', 'Concepts']
BASE = {
    'Languages': ['Python', 'Java', 'C++', 'JavaScript', 'TypeScript', 'SQL', 'Swift', 'Bash'],
    'Frameworks & Libraries': ['React', 'Next.js', 'Node.js', 'FastAPI', 'Django', 'SwiftUI'],
    'AI / ML': ['PyTorch', 'LLM APIs (Claude, OpenAI)', 'AI Agents (LangGraph)', 'Retrieval-Augmented Generation (RAG)', 'Prompt Engineering'],
    'Databases & Data': ['PostgreSQL', 'SQLite', 'Redis'],
    'Cloud & DevOps': ['AWS', 'Docker', 'Terraform', 'GitHub Actions', 'CI/CD', 'Git', 'Linux'],
    'Concepts': ['REST APIs', 'GraphQL', 'System Design', 'Object-Oriented Programming (OOP)', 'Unit & Integration Testing'],
}
GENERIC = {'System Design', 'Object-Oriented Programming (OOP)', 'Unit & Integration Testing'}  # shown only when the job asks for concepts
ROLE_ADD = {  # role-specific base skills, all used in real work
    'frontend': {'Frameworks & Libraries': ['Redux', 'Tailwind CSS', 'Bootstrap']}, 'fullstack': {'Frameworks & Libraries': ['Redux', 'Tailwind CSS']},
}
ROLE_DROP = {  # base skills that are off-role noise (guide: don't list every technology; JD-named skills are never dropped)
    'frontend': {'Swift', 'SwiftUI', 'Django', 'FastAPI', 'Terraform', 'Bash'}, 'fullstack': {'Swift', 'SwiftUI'},
    'backend': {'Swift', 'SwiftUI'}, 'ai': {'Swift', 'SwiftUI'}, 'data': {'Swift', 'SwiftUI'},
    'devops': {'Swift', 'SwiftUI'}, 'testing': {'Swift', 'SwiftUI'}, 'ios': {'Django', 'Terraform'},
}
CATRX = [
    ('Concepts', r'(rest api|restful|^rest$|graphql|grpc|microservice|websocket|\betl\b|data pipeline|distributed|system design|object-oriented|design pattern|agile|scrum|accessib|wcag)'),
    ('Languages', r'^(python|java|c\+\+|c|c#|\.net|c#/\.net|t-sql|go|rust|kotlin|scala|ruby|php|r|javascript|typescript|sql|swift|bash|shell scripting|html|css|html/css|objective-c)$'),
    ('AI / ML', r'(machine|deep learning|pytorch|tensorflow|scikit|llm|generative|rag|langchain|langgraph|openai|prompt|agent|nlp|vision|hugging|xgboost|numpy|pandas|model context|fine-tuning|llamaindex)'),
    ('Databases & Data', r'(sql|postgres|mysql|sqlite|mongo|redis|kafka|spark|airflow|snowflake|etl|pipeline|warehous|queue|rabbitmq|dynamo|elastic|hive|hadoop|flink|nosql|dbt|tableau|power bi|looker|superset|bigquery|databricks|redshift|metabase|data model|star schema|semantic layer|data visuali|dashboard)'),
    ('Frameworks & Libraries', r'(react|next|node|express|django|flask|fastapi|spring|angular|vue|redux|swiftui|ios|android|tailwind|bootstrap|graphql|grpc|rest|microservices|websocket|oauth|nestjs|asp\.net|wcag|aria|selenium|playwright|pytest|junit|uikit|xcode|core data|^combine$|swiftdata|jest|cypress|figma|rails)'),
    ('Cloud & DevOps', r'(aws|amazon|google cloud|azure|docker|kubernetes|terraform|ansible|ci/cd|jenkins|github|git|linux|unix|serverless|lambda|rhel|grafana|prometheus|jira|powershell|monitoring|observability|datadog|sso|saml|helm)'),
    ('Concepts', r'.*'),
]
RENAME = {  # recruiter guide: name the tool, not the category
    'Amazon Web Services (AWS: S3, hosting)': 'AWS', 'Large Language Models (LLMs)': 'LLM APIs (Claude, OpenAI)',
    'OpenAI API': 'LLM APIs (Claude, OpenAI)', 'Machine Learning': 'Machine Learning (PyTorch)', 'AI Agents': 'AI Agents (LangGraph)',
    'Testing': 'Unit & Integration Testing', 'ETL': 'ETL Pipelines', 'Data Pipelines': 'ETL Pipelines', 'Unit Testing': 'Unit & Integration Testing', 'Integration Testing': 'Unit & Integration Testing',
}
# Unproven JD skills allowed per resume (-1 = all; the owner chose "all", Oct 13 2026). Proven ones are always listed first.
UNPROVEN_MAX = int(os.environ.get('SKILLS_UNPROVEN_MAX', '-1'))
EXTRA_CANON = {  # JD terms gen_v5.CANON doesn't know; without these they were silently dropped
    'xcode': 'Xcode', 'uikit': 'UIKit', 'core data': 'Core Data', 'combine': 'Combine', 'swiftdata': 'SwiftData',
    'rhel': 'RHEL', 'red hat enterprise linux': 'RHEL', 'api testing': 'API Testing', 'jest': 'Jest', 'cypress': 'Cypress',
    'grafana': 'Grafana', 'prometheus': 'Prometheus', 'llamaindex': 'LlamaIndex', 'dbt': 'dbt', 'tableau': 'Tableau',
    'power bi': 'Power BI', 'jira': 'Jira', 'powershell': 'PowerShell', 'ruby on rails': 'Ruby on Rails', 'figma': 'Figma',
    'accessibility': 'Accessibility (WCAG)', 'wcag': 'Accessibility (WCAG)', 'a11y': 'Accessibility (WCAG)',
}


def canon6(t):
    return canon(t) or EXTRA_CANON.get(t.strip().lower())


def proven():
    """Skills shown in a real job/project: score.EVIDENCE + base lists + Tech of Notion Experience/Projects."""
    import score
    out = {x.lower() for x in score.EVIDENCE} | {x.lower() for v in BASE.values() for x in v} | {x.lower() for x in RENAME.values()}
    f = _facts()
    for r in f.get('experience', []) + f.get('projects', []):
        for t in r.get('tech', []):
            out.add(t.lower()); n = canon(re.sub(r'\s*\(.*?\)', '', t))
            if n: out.add(RENAME.get(n, n).lower())
    return out


LINE = 122  # characters that fit on one skills line at 10 pt (label included)


def _covers(a, b):
    """Whole-word containment: 'langgraph' covers 'ai agents (langgraph)', but 'c' does not cover 'c++' or 'javascript'."""
    return re.search(r'(?<![a-z0-9+#.])' + re.escape(a) + r'(?![a-z0-9+#])', b) is not None


# soft skills / umbrella words a recruiter reads as filler (the specific tool is listed instead)
SOFT = re.compile(r'^(problem[- ]solving|communication|teamwork|collaboration|leadership|attention to detail|ownership|curiosity|'
                  r'critical thinking|apis?|databases?|ai tools|tools|software development|programming|coding|computer science|'
                  r'monolithic architectures?|debugging)$', re.I)


def _clean_skill(t):
    t = re.sub(r'\s+', ' ', (t or '').strip().strip('.,;:'))
    if not t or SOFT.match(t) or len(t) > 40 or len(t.split()) > 4 or re.search(r'years?|degree|experience|ability|skills?$', t, re.I): return None
    return t if any(c.isupper() for c in t) else t.title() if len(t) > 3 else t.upper()


def build_skills(spec, key, tight=0):
    drop = ROLE_DROP.get(key, set())
    lines = {c: [b for b in v if b not in drop] + ROLE_ADD.get(key, {}).get(c, []) for c, v in BASE.items()}; pri = {c: [] for c in CATS}
    ok, unproven = proven(), 0
    for t in spec.get('required', []) + spec.get('preferred', []):
        if SOFT.match(t.strip()): continue
        n = canon6(t) or _clean_skill(t)   # Oct 14: every required/preferred JD skill is listed (he knows them)
        if not n: continue
        n = RENAME.get(n, n)
        if n.lower() not in ok:
            if 0 <= UNPROVEN_MAX <= unproven: continue
            unproven += 1
        cat = next(c for c, rx in CATRX if re.search(rx, n.lower()))
        if n not in pri[cat]: pri[cat].append(n)
    for c in CATS:  # proven JD skills first; base items already covered by a JD item are dropped
        pri[c].sort(key=lambda n: n.lower() not in ok)
        low = [p.lower() for p in pri[c]]
        lines[c] = [b for b in lines[c] if not any(_covers(p, b.lower()) or _covers(b.lower(), p) for p in low)]
    order = list(CATS)
    if key in ('ai', 'data'): order.remove('AI / ML'); order.insert(1, 'AI / ML')
    out = []
    for c in order:
        items = pri[c] + lines[c]
        room = LINE - len(c) - 2 - tight
        # JD terms may wrap to a 2nd line; base extras are dropped so a line never spills by a word or two
        cap = room if len(', '.join(pri[c])) <= room else 2 * room
        while len(', '.join(items)) > cap and len(items) > len(pri[c]): items.pop()
        if c == 'Concepts' and not any(p in GENERIC or p.lower() in ('system design', 'distributed systems') for p in pri[c]):
            items = [i for i in items if i not in GENERIC]  # generic concepts only when the job asks for them (guide: no filler)
        if items: out.append((c, ', '.join(items)))
    out.append(('Certifications', CERT))
    return out


# ---------- layout ----------
START = {'exp': [3, 2, 2], 'proj': [2, 2, 2], 'lead': [2, 2], 'tight': 0}
TRIM = [  # applied in order until the page fits: least important content goes first
    ('lead', 1, 1), ('exp', 1, 1), ('proj', 2, 1), ('proj', 2, 0), ('tight', None, 12), ('proj', 1, 1),
    ('exp', 2, 1), ('lead', 0, 1), ('exp', 0, 2), ('proj', 0, 1),  # a 3rd project goes before a job bullet; every job keeps >= 1
]
LEVELS = len(TRIM) + 1
TRIM_WEB = list(TRIM)
# Non-web roles: the 2022 front-end job's 2nd bullet goes before a project's 2nd bullet (projects carry the on-role proof)
TRIM_OTHER = [t for t in TRIM if t != ('exp', 2, 1)]
TRIM_OTHER.insert(TRIM_OTHER.index(('proj', 1, 1)), ('exp', 2, 1))


def use_trims(key):
    """Pick the trim order for this role (mutates TRIM in place; counts/plan/revertible read it)."""
    TRIM[:] = TRIM_WEB if key in ('frontend', 'fullstack', 'ios') else TRIM_OTHER
COURSE_CHARS = 100  # keep coursework on one line


def counts(level):
    c = {k: (list(v) if isinstance(v, list) else v) for k, v in START.items()}
    for part, i, n in TRIM[:level]:
        if part == 'tight': c['tight'] = n
        else: c[part][i] = n
    return c


def revertible(level):
    """Applied TRIM steps that can be undone alone (no later applied step touches the same slot), most important first."""
    out = []
    for j in range(level - 1, -1, -1):
        part, i, _ = TRIM[j]
        if not any(TRIM[k][0] == part and TRIM[k][1] == i for k in range(j + 1, level)): out.append(j)
    return out


def plan(spec, level, revert=None):
    """level 0 = richest; each level applies one more TRIM step. revert=j undoes step j (to fill leftover space)."""
    facts = _facts(); role, key = role_of(spec); terms = _terms(spec); taken = set(); c = counts(level)
    if revert is not None:
        part, i, _ = TRIM[revert]; before = counts(revert)
        if part == 'tight': c['tight'] = before['tight']
        else: c[part][i] = before[part][i]
    exp = []
    for (prefix, comp, loc, title, dates), n in zip(EXP, c['exp']):
        pinned = pins(facts, prefix)
        fixed = [b['text'] for b in pinned if b.get('always')]
        if fixed:  # "always" pins first, then the best role pick from the other pins + bank
            alt = [b for b in pinned if not b.get('always')]
            bl = fixed[:n] + (pick(_bank(facts, prefix) + alt, n - len(fixed), key, terms, set(fixed)) if n > len(fixed) else [])
            taken.update(bl)
        elif pinned:  # scope bullet always first
            bl = [pinned[0]['text']] + pick(pinned[1:], n - 1, key, terms, taken, any_fit=True)
        else:
            bl = pick(_bank(facts, prefix), n, key, terms, taken, any_fit=True)
        exp.append((comp, loc, title, dates, bl))
    projs = []
    for name, n in zip(choose_projects(key, terms, 3), c['proj']):
        if n == 0: continue
        prefix, links, stack, dates, _, _ = PROJ[name]
        pinned = pins(facts, prefix)
        bl = [pinned[0]['text']] if pinned else pick(_bank(facts, prefix), 1, key, terms, taken, any_fit=True)
        taken.update(bl)
        if n > 1: bl += pick(pinned[1:] + _bank(facts, prefix), n - 1, key, terms, taken, maxlen=190)
        projs.append((name, links, stack, dates, bl))
    lead = [(org, loc, r, d, [b['text'] for b in pins(facts, prefix)][:n]) for (prefix, org, loc, r, d), n in zip(LEAD, c['lead'])]
    edu = ([UT] if UT and spec.get('kind') == R6.get('grad_school_on', 'intern') else []) + [NAU]
    first = COURSE_FIRST.get(key, ['Data Structures', 'Algorithms', 'Databases (SQL)', 'Operating Systems', 'Computer Networks', 'Software Engineering'])
    courses = []  # guide: 6-8 courses on one line, in role order; a long course is skipped for a shorter one that fits
    ordered = first + [x for x in COURSES if x not in first]
    for x in ordered:
        if len(courses) < 8 and len(', '.join(courses + [x])) <= COURSE_CHARS: courses.append(x)
    if len(courses) < 6:  # can't fit 6 on one line -> use two well-filled lines (8 courses) instead of one orphan word
        courses = ordered[:8]
    skills = build_skills(spec, key, tight=c['tight'])
    return dict(headline=headline(spec), location=location(spec), edu=edu, courses=courses, skills=skills, exp=exp, projs=projs, lead=lead)


def location(spec):
    """Header city: profile resume_v6.header_city_default, or the first header_city_rules [regex, city] matching the job location."""
    for rx, city in R6.get('header_city_rules', []):
        if re.search(rx, spec.get('location') or '', re.I): return city
    return R6.get('header_city_default', P.LOCATION)


def render(p, fs=10.0, width=7.5):
    doc = Document(); s = doc.sections[0]
    s.page_width, s.page_height = Inches(8.5), Inches(11)
    s.left_margin = s.right_margin = s.top_margin = s.bottom_margin = Inches(0.5)
    st = doc.styles['Normal']; st.font.name = 'Carlito'; st.font.size = Pt(fs)

    def two(left, right, before=0):
        q = _para(doc, before=before); q.paragraph_format.tab_stops.add_tab_stop(Inches(width), WD_TAB_ALIGNMENT.RIGHT)
        for txt, b, i in left: _run(q, txt, fs, bold=b, italic=i)
        _run(q, '\t' + right, fs)
        return q

    def head(t):
        q = _para(doc, before=4, after=2); _run(q, t.upper(), fs + 1, bold=True); _rule(q)

    def bul(t):
        q = _para(doc); f = q.paragraph_format; f.left_indent = Inches(0.18); f.first_line_indent = Inches(-0.13)
        _run(q, '•  ' + t, fs)

    _run(_para(doc, align=WD_ALIGN_PARAGRAPH.CENTER), NAME, fs + 9, True)
    cp_ = _para(doc, align=WD_ALIGN_PARAGRAPH.CENTER)
    for i, (txt, url) in enumerate([(p['location'], None)] + CONTACT[1:]):
        if i: _run(cp_, ' | ', fs)
        _link(cp_, url, txt, fs) if url else _run(cp_, txt, fs)

    head('Education')
    for i, (school, place, degree, gpa, date) in enumerate(p['edu']):
        two([(school, True, False)], place, 2 if i else 0)
        two([(degree + (' | ' + gpa if gpa else ''), False, False)], date)
        if school == NAU[0]:
            q = _para(doc); _run(q, 'Relevant Coursework: ', fs, True); _run(q, ', '.join(p['courses']), fs)

    head('Technical Skills')
    for c, items in p['skills']:
        q = _para(doc); _run(q, c + ': ', fs, True); _run(q, items, fs)

    head('Experience')
    for i, (comp, loc, title, dates, bl) in enumerate(p['exp']):
        two([(comp, True, False)], loc, 2 if i else 0)
        two([(title, False, True)], dates)
        for b in bl: bul(b)

    head('Projects')
    for i, (name, links, stack, dates, bl) in enumerate(p['projs']):
        q = _para(doc, before=2 if i else 0); q.paragraph_format.tab_stops.add_tab_stop(Inches(width), WD_TAB_ALIGNMENT.RIGHT)
        _run(q, name, fs, bold=True)
        for txt, url in links:
            _run(q, ' | ', fs); _link(q, url, txt, fs)
        _run(q, ' | ' + stack, fs - 1.0, italic=True); _run(q, '\t' + dates, fs)
        for b in bl: bul(b)

    head('Leadership & Involvement')
    for i, (org, loc, role, dates, bl) in enumerate(p['lead']):
        two([(org, True, False)], loc, 2 if i else 0)
        two([(role, False, True)], dates)
        for b in bl: bul(b)
    q = _para(doc, before=2); _run(q, 'Honors: ', fs, True); _run(q, HONORS, fs)

    cp = doc.core_properties; cp.title = f'{NAME} — Resume'; cp.author = NAME; cp.last_modified_by = NAME
    cp.comments = cp.subject = cp.keywords = cp.category = ''
    return doc


def _gain(spec, level, j):
    """Value of undoing TRIM step j: role score of the best bullet it brings back (experience/leadership +2 tie-break; spacing last)."""
    if TRIM[j][0] == 'tight': return -50
    _, key = role_of(spec); terms = _terms(spec)
    have = lambda p: {b for part in ('exp', 'projs', 'lead') for e in p[part] for b in e[4]}
    new = have(plan(spec, level, j)) - have(plan(spec, level))
    if not new: return -60
    bonus = 2 if TRIM[j][0] in ('exp', 'lead') else 0
    return bonus + max(_score(_bullet_of(t), key, terms, 999) for t in new)


def _bullet_of(text):
    for r in _facts().get('experience', []) + _facts().get('projects', []):
        for b in r['bullets']:
            if b['text'] == text: return b
    return {'text': text, 'tags': ['general']}


def make(spec):
    use_trims(role_of(spec)[1])
    folder = os.path.join(OUT, spec.get('file', spec['id'])); os.makedirs(folder, exist_ok=True)
    dp = os.path.join(folder, P.file('Resume', 'docx'))
    pages, last = {}, [None]

    def build(level, revert=None):  # page count is monotonic in level -> binary search (~4 LibreOffice runs, not 12)
        p = plan(spec, level, revert); render(p).save(dp)
        key = (level, revert) if revert is not None else level
        pages[key] = (to_pdf(dp)[1], p); last[0] = key
        return pages[key][0]

    lo, hi = 0, LEVELS - 1
    level = 0
    if build(lo) > 1:
        while lo + 1 < hi:
            mid = (lo + hi) // 2
            if build(mid) == 1: hi = mid
            else: lo = mid
        level = hi
    chosen = level
    for j in sorted(revertible(level), key=lambda j: -_gain(spec, level, j))[:3]:  # leftover space: give back the most role-relevant trimmed bullet that fits
        if build(level, j) == 1:
            chosen = (level, j); break
    if last[0] != chosen: build(*(chosen if isinstance(chosen, tuple) else (chosen,)))  # files on disk = chosen plan
    n, p = pages[chosen]
    bullets = [b for e in p['exp'] for b in e[4]] + [b for e in p['projs'] for b in e[4]] + [b for e in p['lead'] for b in e[4]]
    shown = (' '.join(bullets) + ' ' + ' '.join(x[2] for x in p['projs'])).lower()
    jd_sk = list(dict.fromkeys(spec.get('required', []) + spec.get('preferred', [])))
    def seen(t):
        names = {t.lower()} | ({RENAME.get(canon6(t), canon6(t)).lower()} if canon6(t) else set())
        names |= {re.sub(r'\s*\(.*?\)', '', n).strip() for n in names}
        return any(n and re.search(r'(?<![a-z0-9])' + re.escape(n) + r'(?![a-z0-9+#])', shown) for n in names)
    unbacked = list({t.lower(): t for t in reversed(jd_sk) if t and not SOFT.match(t.strip()) and not seen(t)}.values())[::-1]
    return dict(id=spec['id'], folder=folder, pages=n, level=level, skills=p['skills'], headline=p['headline'], key=role_of(spec)[1],
                projects=[x[0] for x in p['projs']], numbers=[b for b in bullets if re.search(r'\d', b)], unbacked=unbacked)


if __name__ == '__main__':
    os.makedirs(OUT, exist_ok=True)
    res = [make(s) for s in json.load(open(sys.argv[1]))]
    for r in res: print(json.dumps({k: r[k] for k in ('id', 'pages', 'level', 'headline', 'projects')}))
