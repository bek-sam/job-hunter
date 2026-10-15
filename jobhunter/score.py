"""BEST-MATCH gate as code (rubric from the Fast Hunter prompt, Oct 4/9 2026). No LLM tokens.
pre-score = role type (35) + level (25) + skill overlap (25) + employer (10) + location (5); build if >= FIT_GATE."""
import applicant as P
import re
from gen_v5 import CANON, BASE, PROJ

# Terms you have real evidence for: resume base skills + project stacks + profile.json skills.evidence_extra
EVIDENCE = {x for _, items in BASE for x in items} | set(P.get('skills', 'evidence_extra', default=[]))
for _, stack, _, _ in PROJ:
    for t in stack.split(','):
        n = CANON.get(t.strip().lower())
        if n: EVIDENCE.add(n)

BLOCK = [  # never written to Notion (Oct 4 2026 rule) -> just counted
    (r'\b(u\.?s\.?|united states) citizen(ship)?\b(?! or)|must be a u\.?s\.? citizen|citizenship is required', 'citizenship'),
    (r'security clearance|secret clearance|ts/sci|top secret|active clearance|clearance (is )?required|obtain (and maintain )?a (security )?clearance', 'clearance'),
    (r'\bitar\b|u\.?s\.? person', 'ITAR/US person'),
    (r'\b([2-9]|1[0-9])\+? (or more )?years?(?: of)? (professional |industry |relevant |hands-on )?(software |engineering |work )?experience', 'seniority'),
    (r'\b(ph\.?d|doctoral|master\'?s degree) (is )?(required|only)', 'PhD/MS required'),
]
STAFFING = re.compile(r'taskverse|cybercoders|jobot|dice\b|robert half|insight global|teksystems|staffing|talent ?pluto|recruit(ing|ers) (llc|inc)|hirequest|actalent|revature|synergisticit|executiveplacements|placements?\b|afterquery|\bexperts$|beaconfire|smartiplace|ampcus|apex systems|kforce|randstad|adecco|aerotek|hays\b|motion recruitment|crossover|outlier|mercor|data ?annotation|turing\b|braintrust|talent ?hop|remote ?hunter|jobgether|haystack|portfolio ?future|the ?ladders|kansasworks|jooble|lensa|jobs ?via|hiring ?cafe|v-?soft|staff ?xpert|selby jennings|harnham|oscar technology|jefferson frank|nigel frank', re.I)  # Oct 13: aggregator reposts close within hours / AI-apply walls

ROLE35 = re.compile(r'full[ -]?stack|back[ -]?end|backend|\bai\b|machine learning|\bml\b|genai|generative|\bllm|ios|mobile|android|front[ -]?end|frontend|\bweb\b|software (engineer|developer|development engineer)|\bswe\b|forward deployed|product engineer|application (engineer|developer)', re.I)
ROLE25 = re.compile(r'data engineer|cloud|platform|devops|\bsre\b|site reliability|infrastructure', re.I)
ROLE10 = re.compile(r'\bqa\b|quality|test|sdet|embedded|firmware|it |programmer analyst|systems analyst', re.I)
ENTRY = re.compile(r'new grad|new college|entry|early career|university|graduate|campus|\b2027\b|\bassociate\b|engineer i\b|\bi\b$|\b(engineer|developer) 1\b|junior|jr\.?\b|intern|co-?op', re.I)
NON_SWE = re.compile(r'analyst|analytics|pricing|business|sales|marketing|finance|accounting|operations|recruit|\bhr\b|designer|product manager|consultant', re.I)
TXREMOTE = re.compile(P.get('identity', 'preferred_locations_rx') or r'remote', re.I)   # +5 fit points for these locations
GOOD_SRC = {'greenhouse', 'ashby', 'lever', 'smartrecruiters', 'workday', 'amazon', 'google', 'apple', 'microsoft', 'ibm',
            'goldman', 'tiktok', 'eightfold', 'oracle', 'simplify', 'simplify-intern'}

PREF_SPLIT = re.compile(r'(?im)^\W*(preferred|nice[ -]to[ -]have|bonus|plus(es)?|desired|what would be great|additional qualifications)\b.*$')
_TERMS = sorted(CANON, key=len, reverse=True)
_TERM_RX = re.compile(r'(?<![\w+#.])(' + '|'.join(re.escape(t) for t in _TERMS) + r')(?![\w+#])', re.I)
_AMBIG = {'c', 'r', 'go', 'node', 'rest', 'apis', 'agents', 'security', 'spring', 'express', 'ts', 'js', 'ml', 'git'}


def terms_in(text):
    """Canonical tech terms named in a JD chunk (ambiguous short words need a tech-ish context)."""
    out = []
    for m in _TERM_RX.finditer(text or ''):
        k = m.group(1).lower()
        if k in _AMBIG and not re.search(r'[A-Z]', m.group(1)) and k not in ('rest', 'apis', 'ml', 'git'): continue
        if k == 'go' and not re.search(r'\bGo\b|golang', m.group(0)): continue
        n = CANON[k]
        if n not in out: out.append(n)
    return out


def split_terms(jd_text):
    m = PREF_SPLIT.search(jd_text or '')
    req_txt, pref_txt = (jd_text[:m.start()], jd_text[m.start():]) if m else (jd_text, '')
    req = terms_in(req_txt)
    pref = [t for t in terms_in(pref_txt) if t not in req]
    return req, pref


def blocked(jd_text, title=''):
    t = (jd_text or '') + '\n' + title
    for rx, why in BLOCK:
        m = re.search(rx, t, re.I)
        if not m: continue
        if why == 'seniority':
            # "2+ years ... preferred" / internships listing years are not hard blocks
            ctx = t[max(0, m.start() - 80): m.end() + 80].lower()
            if 'prefer' in ctx or 'nice' in ctx or 'bonus' in ctx or re.search(r'intern|co-?op', title, re.I): continue
        if why == 'citizenship':
            ctx = t[max(0, m.start() - 60): m.end() + 60].lower()
            if 'or permanent resident' in ctx or 'green card' in ctx or 'or lawful' in ctx: continue
        return why
    if re.search(r'\bsenior\b|\bsr\.?\b|\bstaff\b|\bprincipal\b|\blead\b|\bmanager\b|\bdirector\b|\bii\b|\biii\b|'
                 r'\bvp\b|vice president|\bhead of\b|\bchief\b|\barchitect\b', title, re.I):
        return 'seniority'
    # Oct 13: "Softgoods Product Design Engineer", "IT and Cyber Security - VP" were applied to -> non-software titles out
    if re.search(r'mechanical|electrical|hardware|softgoods|manufacturing|\bcivil\b|chemical|structural|mechatronic|'
                 r'product design engineer|process engineer|field service|technician', title, re.I) \
            and not re.search(r'software|machine learning|\bml\b|\bai\b|data|firmware|embedded|developer', title, re.I):
        return 'not a software role'
    # language requirements the applicant can't meet (profile identity.languages_spoken_rx): "Jr. Front-End Developer (Korean Bilingual)"
    m = re.search(r'bilingual|fluent in|native (speaker|level)|(korean|japanese|mandarin|chinese|spanish|french|german|portuguese|vietnamese|'
                  r'arabic|hindi|italian|dutch|polish)[- ](speaking|speaker|bilingual)', title, re.I)
    if m and not re.search(P.get('identity', 'languages_spoken_rx') or 'english', title, re.I):
        return 'language requirement'
    return None


def prescore(role, jd_text):
    title = role['title']; text = jd_text or ''
    role_pts = 35 if ROLE35.search(title) else 25 if ROLE25.search(title) else 10 if ROLE10.search(title) else 18
    if ROLE10.search(title) and not re.search(r'full|back|front|ai|ml', title, re.I): role_pts = 10
    if NON_SWE.search(title) and not re.search(r'engineer|developer|programmer|swe\b', title, re.I): role_pts = 0
    stretch = False
    if ENTRY.search(title) or role.get('kind') == 'intern' or re.search(r'new grad|new college grad|recent graduate|entry[- ]level|class of 202[67]', text, re.I):
        level = 25
    elif re.search(r'\b1\+? (or more )?years?\b|\bone year\b|\b0-[12] years?\b|\b1-[23] years?\b', text, re.I):
        level, stretch = 10, True
    else:
        level = 18
    req, pref = split_terms(text)
    jd_terms = req + pref
    ev = [t for t in jd_terms if t in EVIDENCE]
    evidence_pct = round(100 * len(ev) / len(jd_terms)) if jd_terms else 0
    req_ev = [t for t in req if t in EVIDENCE]
    match_pct = round(100 * len(req_ev) / len(req)) if req else evidence_pct
    skill = 25 if evidence_pct >= 60 else 18 if evidence_pct >= 40 else 10 if evidence_pct >= 20 else 4
    if not jd_terms: skill = 10
    src = role.get('source', '')
    co = role.get('company', '')
    employer = 0 if STAFFING.search(co) else 10 if src in GOOD_SRC else 5
    loc = 5 if TXREMOTE.search(role.get('location', '')) else 3
    fit = role_pts + level + skill + employer + loc
    return dict(fit=fit, match=match_pct, evidence=evidence_pct, stretch=stretch, required=req, preferred=pref,
                missing=[t for t in jd_terms if t not in EVIDENCE], parts=dict(role=role_pts, level=level, skill=skill, employer=employer, loc=loc))


def enrollment_rule(text):
    return bool(re.search(r'return(ing)? to school|enrolled (in|during|for) the (full )?(internship|term)|graduat(e|ing|ion) (date )?(in |between )?(the )?((spring|summer|fall|winter)(/(spring|summer|fall|winter))? (of )?)?(2027|2028)|at least one (semester|term) remaining|continuing (your )?education after', text or '', re.I))


def intern_term(title, text):
    m = re.search(r"(winter|spring|summer|fall|autumn)\s*'?(20)?(2[5-9])\b", title, re.I) or \
        re.search(r"(winter|spring|summer|fall|autumn)\s*'?(20)?(2[5-9])\b", (text or '')[:3000], re.I)
    return f"{m.group(1).title().replace('Autumn', 'Fall')} 20{m.group(3)}" if m else 'term?'


def term_too_early(term):
    """Internships must start Jan 2027 or later (Intern Rules). 'Winter 2026' etc. -> too early."""
    m = re.match(r'\w+ 20(\d\d)', term or '')
    return bool(m and int(m.group(1)) <= 26)
