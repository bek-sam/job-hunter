"""Resume generator v5 — fixed template (Oct 6 2026).
Header, headline, Education, Experience, Projects, Awards are FIXED (copied from the approved PDF).
Only TECHNICAL SKILLS is tailored: JD terms are moved to the front of their category / added.
Usage: python3 gen_v5.py specs.json   -> <RESUME_OUT>/<file>/First_Last_Resume.pdf/.docx
Spec: {"id","file","position":"<job title>","required":[...],"preferred":[...],"title"?:"<override headline>"}
"""
import json, os, re, shutil, subprocess, sys
from docx import Document
from docx.shared import Pt, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
import pikepdf
import applicant as P

OUT = os.environ.get('RESUME_OUT', '/home/claude/out_v5')
FONT = 'Carlito'
R5 = P.get('resume_v5', default={})        # resume facts from profile.json (git-ignored)
NAME = P.NAME
CONTACT = [tuple(c) for c in R5.get('contact', [])]
PLINKS = {k: [tuple(x) for x in v] for k, v in R5.get('plinks', {}).items()}  # project -> [(text, url)] (public links only)
HEADLINE = R5.get('headline', 'Software Engineer')  # default (no job title)
FAMILY = [  # (regex on job title, headline role) — first match wins
 (r'machine learning|\bml\b|applied scientist', 'Machine Learning Engineer'),
 (r'data engineer|data developer|data platform|analytics engineer|data office|\betl\b|data & ai', 'Data Engineer'),
 (r'forward deploy', 'Forward Deployed Engineer'),
 (r'generative ai|genai|\bai\b|\bllm\b|agent', 'AI Engineer'),
 (r'\bios\b', 'iOS Software Engineer'),
 (r'android|mobile', 'Mobile Software Engineer'),
 (r'full[ -]?stack', 'Full-Stack Software Engineer'),
 (r'front[ -]?end|frontend|\bweb\b|\bui\b|accessibility|javascript', 'Front-End Software Engineer'),
 (r'back[ -]?end|backend', 'Backend Software Engineer'),
 (r'devops|devsecops', 'DevOps Engineer'),
 (r'\bsre\b|site reliability', 'Site Reliability Engineer'),
 (r'\bcloud\b', 'Cloud Software Engineer'),
 (r'infra', 'Infrastructure Software Engineer'),
 (r'\btest\b|sdet|\bqa\b', 'Software Engineer in Test'),
 (r'embedded|firmware|flight software', 'Embedded Software Engineer'),
 (r'robotic', 'Robotics Software Engineer'),
 (r'quant', 'Quantitative Developer'),
]
THEMES = [  # (focus phrase, regex on JD terms)
 ('AI/ML', r'machine|deep learning|pytorch|tensorflow|llm|language model|generative|genai|rag|langchain|langgraph|openai|prompt|agent|nlp|vision|hugging|mcp|\bml\b|\bai\b'),
 ('backend APIs', r'rest|api|microservice|grpc|spring|django|flask|fastapi|node|express|\bgo\b|golang|java\b|c#|\.net'),
 ('cloud & DevOps', r'aws|amazon|azure|gcp|google cloud|docker|kubernetes|k8s|terraform|ansible|ci/cd|jenkins|devops|linux'),
 ('data pipelines', r'spark|kafka|etl|pipeline|airflow|snowflake|warehous|hadoop|hive|flink|pandas'),
 ('full-stack web', r'react|next|angular|vue|typescript|javascript|html|css|frontend|front-end'),
 ('iOS/Swift', r'swift|ios|xcode'),
 ('distributed systems', r'distributed|scalab|concurren|multithread'),
 ('SQL databases', r'\bsql\b|postgres|mysql|sql server|database|nosql|mongo|redis'),
 ('system design', r'system design|architecture'),
]
DEFAULT_FOCUS = ['full-stack systems', 'AI/ML', 'system design']

def headline(spec):
    if spec.get('title'): return spec['title']
    pos = (spec.get('position') or '').lower()
    role = next((r for rx, r in FAMILY if re.search(rx, pos)), 'Software Engineer')
    terms = [t.lower() for t in spec.get('required', []) + spec.get('preferred', [])]
    score = []
    for i, (name, rx) in enumerate(THEMES):
        n = sum(1 for t in terms if re.search(rx, t))
        if n: score.append((-n, i, name))
    focus = [n for _, _, n in sorted(score)][:3]
    for d in DEFAULT_FOCUS:
        if len(focus) >= 3: break
        if d in focus or (d == 'full-stack systems' and 'full-stack web' in focus): continue
        focus.append(d)
    return role + ' — ' + ', '.join(focus[:3])
EDU = [tuple(e) for e in R5.get('edu', [])]
EXP = [tuple(e) for e in R5.get('exp', [])]
PROJ = [tuple(e) for e in R5.get('proj', [])]
AWARDS = [tuple(e) for e in R5.get('awards', [])]
BASE = [tuple(e) for e in R5.get('base', [])]
# canonical name for JD terms -> how it is written in the skills list
CANON = {
 'python':'Python','java':'Java','c++':'C++','cpp':'C++','c':'C','c#':'C#','.net':'.NET','c#/.net':'C#/.NET','asp.net':'ASP.NET Core','asp.net core':'ASP.NET Core',
 'go':'Go','golang':'Go','rust':'Rust','kotlin':'Kotlin','scala':'Scala','ruby':'Ruby','php':'PHP','r':'R',
 'javascript':'JavaScript','js':'JavaScript','typescript':'TypeScript','ts':'TypeScript','sql':'SQL','t-sql':'T-SQL','swift':'Swift','bash':'Bash','shell':'Shell Scripting',
 'html':'HTML','css':'CSS','html/css':'HTML/CSS','objective-c':'Objective-C',
 'machine learning':'Machine Learning','ml':'Machine Learning','deep learning':'Deep Learning','pytorch':'PyTorch','tensorflow':'TensorFlow','scikit-learn':'scikit-learn',
 'llm':'Large Language Models (LLMs)','llms':'Large Language Models (LLMs)','large language models':'Large Language Models (LLMs)','genai':'Generative AI','generative ai':'Generative AI',
 'rag':'Retrieval-Augmented Generation (RAG)','langchain':'LangChain','langgraph':'LangGraph','openai':'OpenAI API','openai api':'OpenAI API','prompt engineering':'Prompt Engineering',
 'ai agents':'AI Agents','agents':'AI Agents','agentic':'AI Agents','nlp':'NLP','computer vision':'Computer Vision','hugging face':'Hugging Face','huggingface':'Hugging Face','xgboost':'XGBoost',
 'numpy':'NumPy','pandas':'pandas','mcp':'Model Context Protocol (MCP)','evals':'LLM Evaluation','llm evaluation':'LLM Evaluation','fine-tuning':'Fine-Tuning',
 'react':'React','react.js':'React','reactjs':'React','next.js':'Next.js','nextjs':'Next.js','node.js':'Node.js','node':'Node.js','nodejs':'Node.js','express':'Express',
 'django':'Django','flask':'Flask','fastapi':'FastAPI','spring':'Spring Boot','spring boot':'Spring Boot','angular':'Angular','vue':'Vue.js','vue.js':'Vue.js','redux':'Redux',
 'swiftui':'SwiftUI','ios':'iOS','android':'Android','tailwind':'Tailwind CSS','bootstrap':'Bootstrap','graphql':'GraphQL','grpc':'gRPC','rest':'REST APIs','rest apis':'REST APIs',
 'restful apis':'REST APIs','apis':'REST APIs','microservices':'Microservices','websockets':'WebSockets','oauth':'OAuth 2.0','nestjs':'NestJS','wcag':'WCAG','aria':'WAI-ARIA',
 'postgresql':'PostgreSQL','postgres':'PostgreSQL','mysql':'MySQL','sql server':'SQL Server','sqlite':'SQLite','mongodb':'MongoDB','redis':'Redis','kafka':'Kafka',
 'spark':'Spark','pyspark':'PySpark','airflow':'Airflow','snowflake':'Snowflake','etl':'ETL','data pipelines':'Data Pipelines','data warehousing':'Data Warehousing',
 'message queues':'Message Queues','rabbitmq':'RabbitMQ','dynamodb':'DynamoDB','elasticsearch':'Elasticsearch','nosql':'NoSQL','hive':'Hive','hadoop':'Hadoop','flink':'Flink',
 'aws':'Amazon Web Services (AWS: S3, hosting)','amazon web services':'Amazon Web Services (AWS: S3, hosting)','gcp':'Google Cloud (GCP)','google cloud':'Google Cloud (GCP)',
 'azure':'Microsoft Azure','docker':'Docker','kubernetes':'Kubernetes','k8s':'Kubernetes','terraform':'Terraform','ansible':'Ansible','ci/cd':'CI/CD','jenkins':'Jenkins',
 'github actions':'GitHub Actions','git':'Git','linux':'Linux','unix':'Unix','serverless':'Serverless','lambda':'AWS Lambda',
 'data structures':'Data Structures & Algorithms','algorithms':'Data Structures & Algorithms','system design':'System Design','distributed systems':'Distributed Systems',
 'oop':'Object-Oriented Programming (OOP)','object-oriented programming':'Object-Oriented Programming (OOP)','testing':'Testing','unit testing':'Unit Testing',
 'integration testing':'Integration Testing','test automation':'Test Automation','selenium':'Selenium','playwright':'Playwright','pytest':'pytest','junit':'JUnit',
 'agile':'Agile/Scrum','scrum':'Agile/Scrum','operating systems':'Operating Systems','networking':'Computer Networks','computer networks':'Computer Networks',
 'multithreading':'Multithreading','concurrency':'Concurrency','security':'Application Security','sdlc':'SDLC',
}
CATRX = [
 ('Languages', r'^(python|java|c\+\+|c|c#|\.net|c#/\.net|t-sql|go|rust|kotlin|scala|ruby|php|r|javascript|typescript|sql|swift|bash|shell scripting|html|css|html/css|objective-c)$'),
 ('AI / ML', r'(machine|deep learning|pytorch|tensorflow|scikit|llm|generative|rag|langchain|langgraph|openai|prompt|agent|nlp|vision|hugging|xgboost|numpy|pandas|model context|fine-tuning)'),
 ('Frameworks & Web', r'(react|next|node|express|django|flask|fastapi|spring|angular|vue|redux|swiftui|ios|android|tailwind|bootstrap|graphql|grpc|rest|microservices|websocket|oauth|nestjs|asp\.net|wcag|aria)'),
 ('Data', r'(sql|postgres|mysql|sqlite|mongo|redis|kafka|spark|airflow|snowflake|etl|pipeline|warehous|queue|rabbitmq|dynamo|elastic|hive|hadoop|flink)'),
 ('Cloud & DevOps', r'(aws|amazon|google cloud|azure|docker|kubernetes|terraform|ansible|ci/cd|jenkins|github|git|linux|unix|serverless|lambda)'),
 ('Core CS', r'.*'),
]
STACK_DROP = float(os.environ.get('STACK_DROP', '1.0'))
MAXLEN = 200  # skills line may wrap to 2 lines, like the template

def canon(t):
    k = t.strip().lower()
    return CANON.get(k)

def build_skills(spec, maxlen=None):
    maxlen = maxlen or MAXLEN
    lines = {c: list(v) for c, v in BASE}
    pri = {c: [] for c, _ in BASE}
    for t in spec.get('required', []) + spec.get('preferred', []):
        n = canon(t)
        if not n: continue
        cat = next(c for c, rx in CATRX if re.search(rx, n.lower()))
        if n in lines[cat]: lines[cat].remove(n)
        if n not in pri[cat]: pri[cat].append(n)
    out = []
    for c, _ in BASE:
        items = pri[c] + lines[c]
        while len(', '.join(items)) > maxlen and len(items) > len(pri[c]):
            items.pop()  # drop lowest-priority base items first
        out.append((c, ', '.join(items)))
    return out

def _run(p, text, size, bold=False, italic=False):
    r = p.add_run(text); r.font.name = FONT; r.font.size = Pt(size); r.bold = bold; r.italic = italic
    r._element.get_or_add_rPr().get_or_add_rFonts().set(qn('w:eastAsia'), FONT); return r

def _link(p, url, text, size, bold=False, italic=False):
    rid = p.part.relate_to(url, 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink', is_external=True)
    h = OxmlElement('w:hyperlink'); h.set(qn('r:id'), rid)
    r = OxmlElement('w:r'); rp = OxmlElement('w:rPr')
    f = OxmlElement('w:rFonts')
    for a in ('ascii', 'hAnsi', 'eastAsia', 'cs'): f.set(qn('w:' + a), FONT)
    rp.append(f)
    if bold: rp.append(OxmlElement('w:b'))
    if italic: rp.append(OxmlElement('w:i'))
    sz = OxmlElement('w:sz'); sz.set(qn('w:val'), str(int(size * 2))); rp.append(sz)
    r.append(rp); t = OxmlElement('w:t'); t.text = text; t.set(qn('xml:space'), 'preserve'); r.append(t)
    h.append(r); p._p.append(h)

def _para(doc, after=0, before=0, align=None):
    p = doc.add_paragraph(); f = p.paragraph_format
    f.space_after = Pt(after); f.space_before = Pt(before); f.line_spacing = 1.0
    if align is not None: p.alignment = align
    return p

def _rule(p):
    pPr = p._p.get_or_add_pPr(); b = OxmlElement('w:pBdr'); bt = OxmlElement('w:bottom')
    for k, v in (('val', 'single'), ('sz', '6'), ('space', '1'), ('color', '000000')): bt.set(qn('w:' + k), v)
    b.append(bt); pPr.append(b)

def render(skills, fs=10.0, width=7.5, hl=HEADLINE):
    doc = Document(); s = doc.sections[0]
    s.page_width, s.page_height = Inches(8.5), Inches(11)
    s.left_margin = s.right_margin = s.top_margin = s.bottom_margin = Inches(0.5)
    st = doc.styles['Normal']; st.font.name = FONT; st.font.size = Pt(fs)
    def two(parts, right, before=0):
        p = _para(doc, before=before); p.paragraph_format.tab_stops.add_tab_stop(Inches(width), WD_TAB_ALIGNMENT.RIGHT)
        for txt, b, i in parts: _run(p, txt, fs, bold=b, italic=i)
        _run(p, '\t' + right, fs)
    def head(t):
        p = _para(doc, before=4, after=2); _run(p, t.upper(), fs + 1, bold=True); _rule(p)
    def bul(t):
        p = _para(doc); f = p.paragraph_format; f.left_indent = Inches(0.18); f.first_line_indent = Inches(-0.13)
        _run(p, '•  ' + t, fs)
    _run(_para(doc, align=WD_ALIGN_PARAGRAPH.CENTER), NAME, fs + 9, True)
    cp_ = _para(doc, align=WD_ALIGN_PARAGRAPH.CENTER)
    for i, (txt, url) in enumerate(CONTACT):
        if i: _run(cp_, ' | ', fs)
        _link(cp_, url, txt, fs) if url else _run(cp_, txt, fs)
    _run(_para(doc, align=WD_ALIGN_PARAGRAPH.CENTER), hl, fs, True)
    head('Education')
    for i, (sch, place, right, ls) in enumerate(EDU):
        two([(sch, True, False), (', ' + place, False, False)], right, 2 if i else 0)
        for l in ls: _run(_para(doc), l, fs)
    head('Experience')
    for i, (t, org, d, bl) in enumerate(EXP):
        two([(t, True, False), (' | ' + org, False, False)], d, 2 if i else 0)
        for b in bl: bul(b)
    head('Projects')
    for i, (n, stack, d, bl) in enumerate(PROJ):
        p = _para(doc, before=2 if i else 0); p.paragraph_format.tab_stops.add_tab_stop(Inches(width), WD_TAB_ALIGNMENT.RIGHT)
        _run(p, n, fs, bold=True)
        for txt, url in PLINKS.get(n, []):
            _run(p, ' | ', fs); _link(p, url, txt, fs)
        _run(p, ' | ' + stack, fs - STACK_DROP, italic=True); _run(p, '\t' + d, fs)
        for b in bl: bul(b)
    head('Technical Skills')
    for c, items in skills:
        p = _para(doc); _run(p, c + ': ', fs, True); _run(p, items, fs)
    head('Awards & Leadership')
    for a, b in AWARDS:
        p = _para(doc); _run(p, a, fs, True); _run(p, b, fs)
    cp = doc.core_properties; cp.title = f'{NAME} — Resume'; cp.author = NAME; cp.last_modified_by = NAME
    cp.comments = cp.subject = cp.keywords = cp.category = ''
    return doc

# launchd's PATH has no LibreOffice link (lost in the Oct 21 app cleanup): fall back to the app bundle itself
SOFFICE = shutil.which('soffice') or '/Applications/LibreOffice.app/Contents/MacOS/soffice'

def to_pdf(dp):
    prof = 'file:///tmp/lo_' + str(os.getpid())
    subprocess.run([SOFFICE, '-env:UserInstallation=' + prof, '--headless', '--convert-to', 'pdf', '--outdir', os.path.dirname(dp), dp], capture_output=True, timeout=180)
    pp = dp[:-5] + '.pdf'
    with pikepdf.open(pp, allow_overwriting_input=True) as pdf:
        with pdf.open_metadata(set_pikepdf_as_editor=False) as m:
            m['dc:title'] = f'{NAME} — Resume'; m['dc:creator'] = [NAME]
            for k in ('xmp:CreatorTool', 'pdf:Producer'):
                if k in m: del m[k]
        pdf.docinfo['/Title'] = f'{NAME} — Resume'; pdf.docinfo['/Author'] = NAME
        for k in ('/Creator', '/Producer'):
            if k in pdf.docinfo: del pdf.docinfo[k]
        pdf.save(pp)
        n = len(pdf.pages)
    return pp, n

def make(spec):
    folder = os.path.join(OUT, spec.get('file', spec['id'])); os.makedirs(folder, exist_ok=True)
    dp = os.path.join(folder, P.file('Resume', 'docx'))
    for ml in (200, 165, 128, 110, 95):
        skills = build_skills(spec, ml); render(skills, hl=headline(spec)).save(dp)
        pp, n = to_pdf(dp)
        if n == 1: break
    return dict(id=spec['id'], folder=folder, pages=n, skills=skills, headline=headline(spec))

if __name__ == '__main__':
    os.makedirs(OUT, exist_ok=True)
    res = [make(s) for s in json.load(open(sys.argv[1]))]
    for r in res: print(json.dumps({k: r[k] for k in ('id', 'pages', 'headline')}))
