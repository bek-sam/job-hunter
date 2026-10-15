"""Minimal Notion REST client (no SDK, no LLM). Needs NOTION_TOKEN in .env and the
integration connected to the "Jobs 2026" page (… menu -> Connections)."""
import json, mimetypes, re, time, urllib.request, urllib.error, uuid
import config

API = 'https://api.notion.com/v1'


class NotionError(RuntimeError):
    pass


def _req(method, path, body=None, raw=None, headers=None, tries=4):
    if not config.NOTION_TOKEN:
        raise NotionError('NOTION_TOKEN missing in .env')
    h = {'Authorization': f'Bearer {config.NOTION_TOKEN}', 'Notion-Version': config.NOTION_VERSION}
    data = raw
    if body is not None:
        data = json.dumps(body).encode(); h['Content-Type'] = 'application/json'
    h.update(headers or {})
    for k in range(tries):
        try:
            r = urllib.request.Request(API + path, data=data, headers=h, method=method)
            with urllib.request.urlopen(r, timeout=60) as resp:
                return json.loads(resp.read() or b'{}')
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors='replace')
            if e.code in (429, 500, 502, 503, 504) and k < tries - 1:
                time.sleep(float(e.headers.get('Retry-After') or 2 ** k)); continue
            raise NotionError(f'{method} {path} -> {e.code}: {msg[:400]}')
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            # dropped connection / timeout: retry only requests that are safe to repeat (reads, queries, property
            # updates) — never page creates or block appends, which could duplicate content if the first one landed
            safe = method == 'GET' or path.endswith('/query') or (method == 'PATCH' and not path.endswith('/children'))
            if safe and k < tries - 1:
                time.sleep(2 ** k); continue
            raise NotionError(f'{method} {path} -> network error: {e}')


# ---------- reading ----------
def query(filter_=None, sorts=None, limit=None):
    """All rows of Applications 2026 matching a Notion filter (pages through results)."""
    out, cur = [], None
    while True:
        body = {'page_size': 100}
        if filter_: body['filter'] = filter_
        if sorts: body['sorts'] = sorts
        if cur: body['start_cursor'] = cur
        r = _req('POST', f'/data_sources/{config.APPS_DS}/query', body)
        out += [flat(p) for p in r['results']]
        if limit and len(out) >= limit: return out[:limit]
        if not r.get('has_more'): return out
        cur = r['next_cursor']


def flat(page):
    """Notion page -> plain dict of property values (+ id, url, created)."""
    d = {'id': page['id'], 'url': page.get('url'), 'created': page.get('created_time')}
    for name, p in page['properties'].items():
        t = p['type']; v = p.get(t)
        if t in ('title', 'rich_text'): d[name] = ''.join(x['plain_text'] for x in v or [])
        elif t == 'select': d[name] = v and v['name']
        elif t == 'date': d[name] = v and v['start']
        elif t == 'url': d[name] = v
        elif t == 'formula': d[name] = v and v.get(v['type'])
        else: d[name] = v
    return d


def recent_rows(days=35):
    since = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(time.time() - days * 86400))
    return query({'timestamp': 'created_time', 'created_time': {'on_or_after': since}})


def rows_for_company(name):
    return query({'property': 'Company', 'title': {'contains': name[:40]}})


# ---------- writing ----------
def _rt(s):
    s = s or ''
    return [{'type': 'text', 'text': {'content': s[i:i + 1900]}} for i in range(0, len(s), 1900)] or []


def props(Company=None, Position=None, Status=None, Notes=None, Link=None, Location=None, DatePosted=None,
          DateApplied=None, Outreach=None, Recruiters=None, LinkedIn=None, LastContact=None):
    p = {}
    if Company is not None: p['Company'] = {'title': _rt(Company)}
    for k, v in (('Position', Position), ('Notes', Notes), ('Location', Location), ('Recruiters', Recruiters)):
        if v is not None: p[k] = {'rich_text': _rt(v)}
    for k, v in (('Status', Status), ('Outreach', Outreach), ('LinkedIn', LinkedIn)):
        if v is not None: p[k] = {'select': {'name': v} if v else None}
    for k, v in (('Date Posted', DatePosted), ('Date Applied', DateApplied), ('Last Contact', LastContact)):
        if v is not None: p[k] = {'date': {'start': v} if v else None}
    if Link is not None: p['Link'] = {'url': Link or None}
    return p


def create_row(properties, children=None, ds=None):
    body = {'parent': {'type': 'data_source_id', 'data_source_id': ds or config.APPS_DS}, 'properties': properties}
    if children: body['children'] = children[:100]
    page = _req('POST', '/pages', body)
    if children and len(children) > 100:
        append(page['id'], children[100:])
    return page


def update_row(page_id, properties):
    return _req('PATCH', f'/pages/{page_id}', {'properties': properties})


def append(block_id, children):
    for i in range(0, len(children), 100):
        _req('PATCH', f'/blocks/{block_id}/children', {'children': children[i:i + 100]})


def create_page(parent_page_id, title, children):
    body = {'parent': {'page_id': parent_page_id},
            'properties': {'title': {'title': _rt(title)}}, 'children': children[:100]}
    page = _req('POST', '/pages', body)
    if len(children) > 100: append(page['id'], children[100:])
    return page


def upload_file(path, name=None):
    """Upload a local file -> file_upload id usable in pdf/file blocks."""
    import os
    name = name or os.path.basename(path)
    ctype = mimetypes.guess_type(name)[0] or 'application/octet-stream'
    fu = _req('POST', '/file_uploads', {'filename': name, 'content_type': ctype})
    boundary = uuid.uuid4().hex
    data = open(path, 'rb').read()
    raw = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
           f'Content-Type: {ctype}\r\n\r\n').encode() + data + f'\r\n--{boundary}--\r\n'.encode()
    _req('POST', f"/file_uploads/{fu['id']}/send", raw=raw,
         headers={'Content-Type': f'multipart/form-data; boundary={boundary}'})
    return fu['id']


# ---------- block helpers (Markdown-ish -> Notion blocks) ----------
def blocks_from_md(md):
    """Tiny converter: #/##/### headings, '- ' bullets, '| a | b |' tables -> paragraphs, text paragraphs."""
    out = []
    for line in md.splitlines():
        s = line.rstrip()
        if not s.strip(): continue
        m = re.match(r'^(#{1,3}) (.*)', s)
        if m:
            t = f'heading_{len(m.group(1))}'
            out.append({'type': t, t: {'rich_text': _rt(m.group(2))}})
        elif re.match(r'^\s*[-*] ', s):
            out.append({'type': 'bulleted_list_item', 'bulleted_list_item': {'rich_text': _rt(re.sub(r'^\s*[-*] ', '', s))}})
        else:
            out.append({'type': 'paragraph', 'paragraph': {'rich_text': _rt(s)}})
    return out


def file_blocks(pdf_id, docx_id):
    return [{'type': 'heading_3', 'heading_3': {'rich_text': _rt('Tailored resume (attach this one)')}},
            {'type': 'pdf', 'pdf': {'type': 'file_upload', 'file_upload': {'id': pdf_id}}},
            {'type': 'file', 'file': {'type': 'file_upload', 'file_upload': {'id': docx_id}}}]


def page_url(page_id):
    return 'https://www.notion.so/' + page_id.replace('-', '')
