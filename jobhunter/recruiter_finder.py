"""Option A recruiter-email finder (owner, Oct 13 2026): PUBLIC addresses only, no guessing, no finder services.
Reads the company's OWN website (careers / students / university / early-careers / jobs / contact pages) and keeps
recruiting inboxes on the company's domain, each with the page it was published on. 0 tokens.

  python recruiter_finder.py "Company Name" [job_link]      # test one company
outreach.py calls find(company, link) before its per-role research and stores hits in data/outreach/company_contacts.json.
"""
import re, sys, urllib.request, urllib.error
from urllib.parse import urljoin, urlparse
import config  # noqa: F401  (SSL certs)

UA = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 Chrome/126 Safari/537.36'}
ATS_HOSTS = re.compile(r'linkedin|greenhouse|lever\.co|ashbyhq|myworkday|icims|smartrecruiters|successfactors|sapsf|taleo|'
                       r'oraclecloud|eightfold|jobvite|workable|jobright|indeed|glassdoor|ziprecruiter|dice|simplify|builtin|'
                       r'wellfound|handshake|ripplematch|bamboohr|recruitee|paylocity|adp\.com|ultipro|jazzhr|breezy|gem\.com', re.I)
PATHS = ['', '/careers', '/careers/students', '/careers/university', '/careers/early-careers', '/careers/campus', '/students',
         '/university', '/university-recruiting', '/early-careers', '/campus', '/jobs', '/join-us', '/contact', '/contact-us',
         '/about/contact', '/company/contact', '/careers/contact', '/careers/faq']
RECRUIT_LOCAL = re.compile(r'recruit|talent|career|campus|universit|college|intern|early|grad|jobs?|hiring|hr\b|^hr|people|staffing|apply', re.I)
SKIP_LOCAL = re.compile(r'no-?reply|privacy|legal|press|media|investor|ir@|support|security|abuse|sales|info@|webmaster|'
                        r'accommodat|disabilit|ethics|compliance|unsubscribe|example', re.I)
EMAIL_RX = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')


def _get(url, timeout=12):
    r = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(r, timeout=timeout) as f:
        if 'html' not in (f.headers.get('Content-Type') or 'html'): return f.geturl(), ''
        return f.geturl(), f.read(600000).decode('utf-8', 'replace')


def _norm(s):
    return re.sub(r'[^a-z0-9]', '', (s or '').lower())


def domain_for(company, link=''):
    """The company's own site: the job link's host when it is not a job board, else <slug>.com / <slug>.io checked
    against the company name. Returns 'https://host' or None."""
    host = urlparse(link or '').netloc
    if host and not ATS_HOSTS.search(host):
        return f'https://{host.split(":")[0]}'
    words = re.findall(r'[a-z0-9]+', re.sub(r'\(.*?\)|,? inc\.?|,? llc|,? corp(oration)?|,? ltd|co\.$', '', (company or '').lower()))
    words = [w for w in words if w not in ('the', 'group', 'company', 'companies', 'technologies', 'technology', 'holdings')] or words
    slugs = list(dict.fromkeys([''.join(words), words[0] if words else '', '-'.join(words)]))
    for slug in [s for s in slugs if len(s) > 2]:
        for tld in ('.com', '.io', '.ai', '.co'):
            url = f'https://www.{slug}{tld}'
            try:
                final, _ = _get(url, timeout=8)
            except urllib.error.HTTPError as e:      # 403/429 = the site exists but blocks scripts -> still the domain
                final = e.geturl() or url
            except Exception:
                continue
            host = urlparse(final).netloc.lower().removeprefix('www.')
            if slug.replace('-', '') in host.replace('-', '').replace('.', ''):   # final host must carry the company name
                return f'https://{host}'
    return None


def _unwrap(u):
    """Bing result links are tracking redirects (bing.com/ck/a?...&u=a1<base64url>) -> the real result URL."""
    import base64
    from urllib.parse import parse_qs
    if 'bing.com/ck/' not in u: return u
    v = (parse_qs(urlparse(u).query).get('u') or [''])[0]
    if v.startswith('a1'): v = v[2:]
    try: return base64.urlsafe_b64decode(v + '=' * (-len(v) % 4)).decode('utf-8', 'replace')
    except Exception: return None


def search_public(company, root, max_pages=10):
    """Public web search (Bing results page; DuckDuckGo blocks scripts with a bot check, which we never bypass).
    Reads the snippets AND the top result pages (job posts, careers/FAQ pages, event pages often print the recruiting
    inbox). An address is kept only with the page it is printed on. Returns {email: source_url}."""
    import html as H
    from urllib.parse import quote_plus
    out, pages = {}, []
    # Bing reads '"@domain"' as a question about the @ sign (Oct 13 test) -> plain-word queries
    queries = [f'{root} recruiting email', f'site:{root} careers contact email', f'{company} university recruiting email {root}']
    for q in queries:
        try:
            _, page = _get('https://www.bing.com/search?q=' + quote_plus(q) + '&count=15', timeout=15)
        except Exception:
            continue
        if re.search(r'captcha|unusual traffic|verify you are', page, re.I): break   # never bypass a bot check
        links = [_unwrap(H.unescape(u)) for u in re.findall(r'<li class="b_algo".*?<a[^>]+href="(https?://[^"]+)"', page, re.S)]
        pages += [u for u in links if u and u not in pages]
    for u in pages[:max_pages]:
        try:
            final, page = _get(u, timeout=12)
        except Exception:
            continue
        text = H.unescape(page).lower()
        for e in set(EMAIL_RX.findall(text)):
            e = e.strip('.')
            local, _, edom = e.partition('@')
            if (edom == root or edom.endswith('.' + root)) and not SKIP_LOCAL.search(e) and RECRUIT_LOCAL.search(local):
                out.setdefault(e, final)
    return out


def find(company, link='', max_pages=12):
    """[{email, source}] recruiting inboxes published on the company's own site (deduped)."""
    base = domain_for(company, link)
    if not base: return []
    dom = urlparse(base).netloc.removeprefix('www.')
    root = '.'.join(dom.split('.')[-2:])
    found, seen = {}, set()
    queue = [base + p for p in PATHS]
    while queue and len(seen) < max_pages:
        url = queue.pop(0)
        if url in seen: continue
        seen.add(url)
        try:
            final, html = _get(url)
        except Exception:
            continue
        for e in set(EMAIL_RX.findall(html)):
            e = e.lower().strip('.')
            local, _, edom = e.partition('@')
            if not edom.endswith(root) or SKIP_LOCAL.search(e) or not RECRUIT_LOCAL.search(local): continue
            found.setdefault(e, final)
        # follow same-site careers/contact links found on the page (one hop)
        for href in re.findall(r'href="([^"#]+)"', html)[:400]:
            u = urljoin(final, href)
            if urlparse(u).netloc.endswith(root) and re.search(r'career|student|universit|campus|early|intern|contact|recruit', u, re.I) \
                    and u not in seen and len(queue) < 30:
                queue.append(u)
    for e, src in search_public(company, root).items():
        found.setdefault(e, src)
    return [{'email': e, 'source': s} for e, s in found.items()]


if __name__ == '__main__':
    print(find(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else ''))
