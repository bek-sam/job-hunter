"""jobright.ai rows (aggregator). The real apply link sits behind a jobright login, so find the same posting on the
company's own ATS by company + title (same resolver as LinkedIn guest pages) and hand it to that adapter."""
import re
from ats.common import result
from ats import linkedin as LI

MATCH = r'jobright\.ai'


def run(ctx, page, job):
    company, title = job.get('company') or '', job.get('title') or ''
    try:   # the jobright page title usually reads "<Title> - <Company> | Jobright"
        h1 = page.locator('h1').first.inner_text(timeout=5000).strip()
        if h1 and not title: title = h1
    except Exception:
        pass
    url = LI.resolve_offsite(company, re.sub(r'\s*\(.*?\)\s*$', '', title) or title)
    if not url:
        return result('NEEDS YOU', f'jobright: could not find {company} — {title[:60]} on the company ATS (open the jobright page, '
                                   f'click Apply, then mark FILL NOW) (tab open)', page=page)
    newp = ctx.new_page()
    newp.goto(url, wait_until='domcontentloaded', timeout=60000)
    return LI.route(ctx, page, newp, job, 'jobright → company site')
