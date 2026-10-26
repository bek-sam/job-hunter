"""jobright.ai rows (aggregator). Signed in (apply Chrome profile), the job page links the real posting as "Original Job
Post" -> open that and hand it to the company site's adapter. Signed out there is no link (not in __NEXT_DATA__ either),
so fall back to finding the posting by company + title (same resolver as LinkedIn guest pages)."""
import re
from ats.common import result
from ats import linkedin as LI

MATCH = r'jobright\.ai'


def origin_link(page):
    """href of the 'Original Job Post' link (Oct 24: a[class*=index_origin], only shown when signed in)."""
    for sel, wait in (('a[class*="index_origin"]', 8000), ('a:has-text("Original Job Post")', 2000)):
        try:
            a = page.locator(sel).first
            a.wait_for(state='attached', timeout=wait)
            href = a.get_attribute('href') or ''
            if href.startswith('http') and 'jobright.ai' not in href: return href
        except Exception:
            continue
    return None


def run(ctx, page, job):
    company, title = job.get('company') or '', job.get('title') or ''
    try:   # the jobright page title usually reads "<Title> - <Company> | Jobright"
        h1 = page.locator('h1').first.inner_text(timeout=5000).strip()
        if h1 and not title: title = h1
    except Exception:
        pass
    url, how = origin_link(page), 'jobright → original job post'
    if not url:
        url, how = LI.resolve_offsite(company, re.sub(r'\s*\(.*?\)\s*$', '', title) or title), 'jobright → company site'
    if not url:
        return result('NEEDS YOU', f'jobright: could not find {company} — {title[:60]} on the company ATS (no "Original Job '
                                   f'Post" link: is jobright signed in in the apply window?) (tab open)', page=page)
    newp = ctx.new_page()
    newp.goto(url, wait_until='domcontentloaded', timeout=60000)
    return LI.route(ctx, page, newp, job, how)
