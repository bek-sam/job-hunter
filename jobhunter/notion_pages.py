"""Read a row's page body: tailored resume file URLs + cover letter text."""
import urllib.request
import notion


def children(block_id):
    out, cur = [], None
    while True:
        q = f'/blocks/{block_id}/children?page_size=100' + (f'&start_cursor={cur}' if cur else '')
        r = notion._req('GET', q)
        out += r['results']
        if not r.get('has_more'): return out
        cur = r['next_cursor']


def _text(b):
    t = b['type']; v = b.get(t, {})
    return ''.join(x.get('plain_text', '') for x in v.get('rich_text', []))


def _file_url(b):
    v = b.get(b['type'], {})
    f = v.get(v.get('type')) or {}
    return f.get('url'), (v.get('name') or (f.get('url') or '').split('?')[0].rsplit('/', 1)[-1])


def read(page_id):
    blocks = children(page_id)
    pdf = docx = None; cover = []; in_cover = False; fresh = False
    for b in blocks:
        t = b['type']
        if t in ('pdf', 'file'):
            url, name = _file_url(b)
            # files under the newest "Tailored resume" heading win (regen.py appends a rebuilt resume at the end)
            if url and (name.lower().endswith('.pdf') or t == 'pdf') and (not pdf or fresh): pdf = url
            elif url and name.lower().endswith('.docx') and (not docx or fresh): docx = url
        elif t.startswith('heading'):
            h = _text(b).lower()
            in_cover = 'cover letter' in h
            fresh = 'tailored resume' in h and ' v6.' in h
        elif in_cover and t in ('paragraph', 'bulleted_list_item'):
            cover.append(_text(b))
    return dict(pdf=pdf, docx=docx, cover_letter='\n\n'.join(c for c in cover if c.strip()))


def download(url, path):
    with urllib.request.urlopen(url, timeout=60) as r, open(path, 'wb') as f:
        f.write(r.read())
    return path
