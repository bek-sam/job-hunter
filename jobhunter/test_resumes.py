"""Regression check for the resume generator (gen_v6): builds 10 role resumes and checks the rules we fixed by hand.

Run after editing gen_v6.py or the Notion bullet bank (after resume_sync.py):
    .venv/bin/python jobhunter/test_resumes.py            # all 10 roles (~50 s)
    .venv/bin/python jobhunter/test_resumes.py fe ai      # only some
Exit code 0 = all pass. Writes only to a temp folder. 0 tokens, 0 Notion calls.
"""
import applicant as P
import json, os, re, sys, tempfile
from pathlib import Path
import pypdf
from docx import Document

sys.path.insert(0, str(Path(__file__).parent))
import gen_v6
import resume_sync

SPECS = Path(__file__).parent / 'testdata' / 'resume_specs.json'
SECTIONS = ['EDUCATION', 'TECHNICAL SKILLS', 'EXPERIENCE', 'PROJECTS', 'LEADERSHIP & INVOLVEMENT']
FIRST_PERSON = re.compile(r'\b(I|my|me)\b')
NOT_DEPLOYED = re.compile(r'deploy|in production|live users', re.I)
DATE_TAIL = re.compile(r'\s*(?:[A-Z][a-z]{2} )?\d{4}(?:\s*–\s*(?:(?:[A-Z][a-z]{2} )?\d{4}|Present))?$')


def pdf_lines(pdf):
    return [ln.strip() for ln in pypdf.PdfReader(pdf).pages[0].extract_text().splitlines() if ln.strip()]


def check_pins(facts):
    """Every entry the generator shows must have pinned bullets in Notion (catches a deleted/renamed pinned section)."""
    errs = []
    for prefix, *_ in gen_v6.EXP + gen_v6.LEAD:
        if not gen_v6.pins(facts, prefix): errs.append(f'no pinned bullets for {prefix} (Notion "Resume bullet bank — pinned")')
    for name, (prefix, *_) in gen_v6.PROJ.items():
        if not gen_v6.pins(facts, prefix): errs.append(f'no pinned bullets for project {name}')
    if gen_v6.ALWAYS and len([b for b in gen_v6.pins(facts, gen_v6.ALWAYS) if b.get('always')]) < 2:
        errs.append(f'{gen_v6.ALWAYS} needs 2 "always" pinned bullets')
    return errs


def check(spec, res, facts):
    errs = []
    pdf = os.path.join(res['folder'], P.file('Resume', 'pdf'))
    docx = os.path.join(res['folder'], P.file('Resume', 'docx'))
    if not (os.path.exists(pdf) and os.path.exists(docx)): return ['resume files missing or misnamed']
    if res['pages'] != 1: errs.append(f"{res['pages']} pages")
    paras = [p.text for p in Document(docx).paragraphs if p.text.strip()]
    heads = [p for p in paras if p in SECTIONS]
    if heads != SECTIONS: errs.append(f'section order {heads}')
    # header: city rule + no headline line
    city = gen_v6.location(spec)
    if not paras[1].startswith(city): errs.append(f'header city should be {city}: {paras[1][:30]}')
    if paras[2] != 'EDUCATION': errs.append(f'unexpected line under contact info: {paras[2][:40]}')
    # education: the optional second school only on resumes of kind resume_v6.grad_school_on
    if gen_v6.UT:
        has_ut = any(p.startswith(gen_v6.UT[0]) for p in paras)
        if has_ut != (spec.get('kind') == gen_v6.R6.get('grad_school_on', 'intern')): errs.append(f'{gen_v6.UT[0]} shown on wrong resume kind')
    # bullets: scope bullet first, every job >= 1 bullet, nothing unsafe
    bullets, cur, sec = {}, None, None
    for p in paras:
        if p in SECTIONS: sec = p; continue
        if sec in ('EXPERIENCE', 'PROJECTS', 'LEADERSHIP & INVOLVEMENT'):
            if p.startswith('•'): bullets.setdefault(cur, []).append(p.lstrip('• ').strip())
            elif '\t' in p and not p.split('\t')[0].strip().endswith(('Intern', 'President', 'Mentor')):
                cur = p.split('\t')[0].split(' |')[0].strip()
    for prefix, comp, *_ in gen_v6.EXP:
        got, pinned = bullets.get(comp, []), gen_v6.pins(facts, prefix)
        if not got: errs.append(f'{comp} has no bullets'); continue
        fixed = [b['text'] for b in pinned if b.get('always')]
        want = fixed[:2] if fixed else [pinned[0]['text']] if pinned else []
        if want and got[:len(want)] != want: errs.append(f'{comp} does not start with its pinned scope bullet(s)')
    for name in res['projects']:
        pinned = gen_v6.pins(facts, gen_v6.PROJ[name][0])
        if pinned and bullets.get(name, [''])[0] != pinned[0]['text']: errs.append(f'{name} does not start with its intro bullet')
        if name in resume_sync.NOT_DEPLOYED_ROWS and any(NOT_DEPLOYED.search(b) for b in bullets.get(name, [])):
            errs.append(f'{name} claims deployment')
    for b in (b for v in bullets.values() for b in v):
        if FIRST_PERSON.search(b): errs.append(f'first person: {b[:50]}')
        if resume_sync.DENY.search(b) or resume_sync.UNSAFE.search(b): errs.append(f'removed/unconfirmed claim: {b[:50]}')
    # layout: no orphan word lines (a wrapped line holding only 1-2 words, e.g. "evals." or "SQLite")
    for ln in pdf_lines(pdf)[1:]:
        if ln in SECTIONS or ln.startswith('•'): continue
        core = DATE_TAIL.sub('', ln).strip()  # a wrapped header shares its line with the right-aligned date
        if core and len(core.split()) <= 2 and len(core) < 20: errs.append(f'orphan line: "{ln}"')
    return errs


def main(only):
    specs = [s for s in json.loads(SPECS.read_text()) if not only or s['id'] in only]
    facts = gen_v6._facts()
    fails = check_pins(facts)
    for e in fails: print('FAIL pins:', e)
    os.environ['PATH'] = '/Applications/LibreOffice.app/Contents/MacOS:' + os.environ.get('PATH', '')
    with tempfile.TemporaryDirectory() as tmp:
        gen_v6.OUT = tmp
        for spec in specs:
            res = gen_v6.make(spec)
            errs = check(spec, res, facts)
            print(f"{'PASS' if not errs else 'FAIL'} {spec['id']:7} level {res['level']}  projects: {', '.join(res['projects'])}")
            for e in errs: print('      -', e)
            fails += errs
    print(f"\n{len(specs)} resumes, {len(fails)} problem(s)")
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
