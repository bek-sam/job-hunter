"""Fetch a job description + exact posted time. ATS JSON APIs first (fast, exact), then the
LinkedIn guest page, then headless Playwright for anything else. No LLM tokens."""
import html as H, json, re, urllib.request, datetime as dt
import config  # noqa: F401  (sets SSL_CERT_FILE)

UA = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 Chrome/126 Safari/537.36'}


def _get(url, timeout=25, accept='*/*'):
    r = urllib.request.Request(url, headers=dict(UA, Accept=accept))
    with urllib.request.urlopen(r, timeout=timeout) as f:
        return f.read().decode('utf-8', 'replace')


def text_of(h):
    h = re.sub(r'(?is)<(script|style).*?</\1>', ' ', h or '')
    h = re.sub(r'(?i)<br\s*/?>|</(p|li|div|h\d|tr)>', '\n', h)
    h = re.sub(r'(?i)<li[^>]*>', '\n- ', h)
    t = H.unescape(re.sub(r'<[^>]+>', ' ', h))
    t = re.sub(r'[ \t\xa0]+', ' ', t)
    return re.sub(r'\n\s*\n+', '\n', t).strip()


def _iso(s):
    try: return dt.datetime.fromisoformat(s.replace('Z', '+00:00')).astimezone(dt.timezone.utc)
    except Exception: return None


def greenhouse(url):
    m = re.search(r'greenhouse\.io/([\w-]+)/jobs/(\d+)', url)
    if not m: return None
    j = json.loads(_get(f'https://boards-api.greenhouse.io/v1/boards/{m.group(1)}/jobs/{m.group(2)}', accept='application/json'))
    return dict(text=text_of(H.unescape(j.get('content', ''))), posted=_iso(j.get('first_published') or j.get('updated_at') or ''),
                exact=bool(j.get('first_published')), title=j.get('title'), ats='greenhouse')


def lever(url):
    m = re.search(r'jobs\.lever\.co/([\w.-]+)/([\w-]{36})', url)
    if not m: return None
    j = json.loads(_get(f'https://api.lever.co/v0/postings/{m.group(1)}/{m.group(2)}', accept='application/json'))
    txt = j.get('descriptionPlain', '') + '\n' + '\n'.join(
        l.get('text', '') + '\n' + text_of(l.get('content', '')) for l in j.get('lists', [])) + '\n' + j.get('additionalPlain', '')
    ts = j.get('createdAt')
    return dict(text=txt, posted=ts and dt.datetime.fromtimestamp(ts / 1000, dt.timezone.utc), exact=bool(ts),
                title=j.get('text'), ats='lever')


def ashby(url):
    m = re.search(r'jobs\.ashbyhq\.com/([^/?#]+)/([\w-]{36})', url)
    if not m: return None
    j = json.loads(_get(f'https://api.ashbyhq.com/posting-api/job-board/{m.group(1)}', accept='application/json'))
    for p in j.get('jobs', []):
        if p.get('id') == m.group(2):
            return dict(text=p.get('descriptionPlain') or text_of(p.get('descriptionHtml', '')), posted=_iso(p.get('publishedAt', '')),
                        exact=bool(p.get('publishedAt')), title=p.get('title'), ats='ashby')
    return None


def workday(url):
    m = re.search(r'https://([\w.-]+\.myworkdayjobs\.com)/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)/job/(.+?)(?:/apply.*)?(?:\?.*)?$', url)
    if not m: return None
    host, site, path = m.groups(); tenant = host.split('.')[0]
    j = json.loads(_get(f'https://{host}/wday/cxs/{tenant}/{site}/job/{path}', accept='application/json'))
    i = j.get('jobPostingInfo', {})
    return dict(text=text_of(i.get('jobDescription', '')), posted=None, exact=False, title=i.get('title'),
                posted_label=i.get('postedOn'), ats='workday')


def linkedin(url):
    m = re.search(r'linkedin\.com/jobs/view/(?:[\w-]*-)?(\d{6,})', url)
    if not m: return None
    # the guest page only includes the JSON-LD datePosted for a plain client on the full URL
    r = urllib.request.Request(url.split('?')[0], headers={'User-Agent': 'curl/8.7.1', 'Accept': '*/*'})
    with urllib.request.urlopen(r, timeout=25) as f:
        h = f.read().decode('utf-8', 'replace')
    d = re.search(r'"datePosted"\s*:\s*"([^"]+)"', h)
    ago = re.search(r'posted-time-ago__text[^>]*>\s*([^<]+)<', h)
    body = re.search(r'(?s)show-more-less-html__markup[^>]*>(.*?)</div>', h)
    apply_url = re.search(r'companyApplyUrl[^"]*"\s*:\s*"([^"]+)"', h)
    return dict(text=text_of(body.group(1) if body else h), posted=_iso(d.group(1)) if d else None,
                exact=bool(d and 'T' in d.group(1)), posted_label=ago and ago.group(1).strip(),
                apply_url=apply_url and H.unescape(apply_url.group(1)), ats='linkedin')


def browser(url):
    """Last resort: render with headless Chromium and take the page text."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        pg = b.new_page(user_agent=UA['User-Agent'])
        pg.goto(url, wait_until='domcontentloaded', timeout=45000)
        pg.wait_for_timeout(2500)
        t = pg.inner_text('body')
        b.close()
    return dict(text=t, posted=None, exact=False, ats='page')


def fetch(url, render=True):
    """render=False: never start headless Chromium (callers already inside a sync Playwright block can't)."""
    for fn in (greenhouse, lever, ashby, workday, linkedin):
        try:
            r = fn(url)
            if r and len(r.get('text', '')) > 200: return r
        except Exception:
            continue
    if not render: return dict(text='', posted=None, exact=False, ats='none')
    try:
        return browser(url)
    except Exception as e:
        return dict(text='', posted=None, exact=False, ats='error', error=str(e)[:200])


if __name__ == '__main__':
    import sys
    r = fetch(sys.argv[1]); r['posted'] = str(r.get('posted'))
    print(json.dumps({k: (v[:600] if isinstance(v, str) else v) for k, v in r.items()}, indent=1))
