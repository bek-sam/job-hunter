"""ATS adapters. Each module exposes:

    MATCH = r'<regex on the page URL>'
    def run(ctx, page, job) -> dict(status='FILLED'|'NEEDS YOU', reason=str, done=[...], unknown=[...], page=<final page>)

`page` is already open on the job link. `job` = dict(url, company, title, pdf, docx, cover_txt, cover_text, dry).
Contract for every adapter (hard limits): fill only; stop on the last review page BEFORE the final Submit/Apply;
leave the tab open; never type passwords, create accounts, solve CAPTCHAs, enter codes, tick consent/legal boxes,
or invent answers -- unknown questions go to `unknown` and the result is NEEDS YOU.
"""
import importlib, re

ORDER = ['workday', 'linkedin', 'jobright', 'greenhouse', 'lever', 'ashby', 'smartrecruiters', 'icims', 'oracle', 'eightfold',
         'taleo', 'jobvite', 'workable', 'successfactors', 'tiktok', 'generic']


def adapters():
    out = []
    for name in ORDER:
        try:
            m = importlib.import_module(f'ats.{name}')
            out.append(m)
        except ModuleNotFoundError:
            continue
    return out


def detect(url, html=''):
    for m in adapters():
        if m.__name__.endswith('.generic'): continue
        if re.search(m.MATCH, url, re.I) or (getattr(m, 'HTML_MATCH', None) and re.search(m.HTML_MATCH, html or '', re.I)):
            return m
    return importlib.import_module('ats.generic')
