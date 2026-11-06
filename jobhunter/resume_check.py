"""Checks on the FINAL resume PDF, 0 tokens (upgrades 3 + 5):

parse_check(pdf)  -> list of problems an ATS parser would hit (empty = OK): no extractable text, name/email missing
                     from the body text, standard headings missing or out of order, file >= 2.5 MB (Greenhouse limit),
                     unrecognized glyphs (U+FFFD / private-use icon fonts).
match_report(...) -> how much of the job's required / preferred vocabulary the extracted text really shows,
                     what is missing, and terms repeated 4+ times (reads as keyword stuffing).
"""
import os, re
import applicant as P

HEADINGS = ['EDUCATION', 'EXPERIENCE', 'PROJECTS', 'TECHNICAL SKILLS']
MAX_BYTES = int(2.5 * 1024 * 1024)
BAD_GLYPH = re.compile('[�-]')


def pdf_text(pdf):
    import pypdf
    return '\n'.join((pg.extract_text() or '') for pg in pypdf.PdfReader(pdf).pages)


def parse_check(pdf, text=None):
    errs = []
    if not os.path.exists(pdf): return ['resume PDF missing']
    if os.path.getsize(pdf) >= MAX_BYTES: errs.append(f'file is {os.path.getsize(pdf) // 1024} KB (Greenhouse parses < 2.5 MB)')
    try: text = text if text is not None else pdf_text(pdf)
    except Exception as e: return errs + [f'PDF text could not be extracted ({str(e)[:60]})']
    if len(text.strip()) < 400: errs.append('almost no extractable text (scanned/image PDF?)')
    flat = re.sub(r'\s+', ' ', text)
    if P.NAME and P.NAME.lower() not in flat.lower(): errs.append('name not found in the PDF text')
    if P.EMAIL and P.EMAIL.lower() not in flat.lower().replace(' ', ''): errs.append('email not found in the PDF text')
    pos = [flat.upper().find(h) for h in HEADINGS]
    missing = [h for h, p in zip(HEADINGS, pos) if p < 0]
    if missing: errs.append('headings missing: ' + ', '.join(missing))
    elif pos != sorted(pos): errs.append('headings read out of order (columns/text boxes?)')
    if BAD_GLYPH.search(text): errs.append('unrecognized glyphs in the text (icon font / missing font)')
    return errs


def _has(term, low):
    t = term.lower().strip()
    variants = {t, re.sub(r'\s*\(.*?\)', '', t).strip()}
    return any(v and re.search(r'(?<![a-z0-9])' + re.escape(v) + r'(?![a-z0-9+#])', low) for v in variants)


def match_report(text, required, preferred):
    low = re.sub(r'\s+', ' ', text.lower())
    req = list(dict.fromkeys(t for t in required if t and t.strip()))
    pref = [t for t in dict.fromkeys(preferred) if t and t.strip() and t not in req]
    miss_r = [t for t in req if not _has(t, low)]
    miss_p = [t for t in pref if not _has(t, low)]
    stuffed = []
    for t in req + pref:
        n = len(re.findall(r'(?<![a-z0-9])' + re.escape(t.lower()) + r'(?![a-z0-9+#])', low))
        if n >= 4: stuffed.append(f'{t} x{n}')
    pct = lambda tot, miss: round(100 * (len(tot) - len(miss)) / len(tot)) if tot else 100
    return dict(required_pct=pct(req, miss_r), preferred_pct=pct(pref, miss_p), missing_required=miss_r,
                missing_preferred=miss_p, stuffed=stuffed)


def markdown(rep, errs, reworded=()):
    out = [f"### Resume check\n- Job terms shown on the resume: required {rep['required_pct']}%, preferred {rep['preferred_pct']}%"]
    if rep['missing_required']: out.append('- Missing required terms (gap): ' + ', '.join(rep['missing_required']))
    if rep['missing_preferred']: out.append('- Missing preferred terms: ' + ', '.join(rep['missing_preferred'][:15]))
    if rep['stuffed']: out.append('- Repeated a lot (reads as stuffing): ' + ', '.join(rep['stuffed']))
    out.append('- ATS parse check: ' + ('OK' if not errs else 'FAILED — ' + '; '.join(errs)))
    if reworded:
        out.append('### Bullets reworded to the job\'s terms (review)')
        out += [f'- {o}\n  → {n}' for o, n in reworded]
    return '\n'.join(out)
