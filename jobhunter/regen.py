"""Rebuild an Applications 2026 row's tailored resume with the current generator (gen_v6.VERSION).

Oct 28 2026: the generator changed (15 evidence-only skills, calmer bullets, Houston header), but ~2,000 open rows
still carry older resumes. apply_prep calls refresh() right before filling a row whose Notes don't name the current
version, so every application goes out with the current resume; old resume blocks are relabelled, never deleted.
Usage: .venv/bin/python jobhunter/regen.py --days 3      (batch: open rows created in the last N days)
"""
import applicant as P
import argparse, os, re, time
import config, gen_v6, jd as JD, notion, notion_pages, score

ON = os.environ.get('AUTO_REGEN', '1') == '1'


def current(row):
    return f'resume {gen_v6.VERSION}' in (row.get('Notes') or '')


def page_terms(page_id):
    """Required / Preferred skills fast_hunter wrote under "## Role analysis" (packet.page_markdown), or None."""
    got = {}
    for b in notion_pages.children(page_id):
        m = re.match(r'(Required|Preferred): (.*)', notion_pages._text(b))
        if m and m.group(1) not in got:
            got[m.group(1)] = [] if m.group(2).strip() == 'n/a' else [t.strip() for t in re.split(r',\s*(?![^()]*\))', m.group(2)) if t.strip()]
    return {'required': got['Required'], 'preferred': got.get('Preferred', [])} if 'Required' in got else None


def refresh(row, render=False):
    """New resume on the row's page + Notes updated. Returns the new PDF path, or None (JD unreadable: keep the old one)."""
    kind = 'intern' if row.get('Type') == 'Internship' else 'newgrad'
    try: text = (JD.fetch(row.get('Link') or '', render=render) or {}).get('text', '')
    except Exception: text = ''
    if len(text) >= 300:
        sc = score.prescore({'title': row.get('Position') or '', 'company': row.get('Company') or '', 'source': '',
                             'kind': kind, 'location': row.get('Location') or ''}, text)
    else:   # posting unreadable here (no browser inside apply_prep, closed page): the page's "Role analysis" has the skills
        sc = page_terms(row['id'])
        if sc is None: return None
    keep = (lambda ts: ts) if config.SKILLS_MODE == 'all' else (lambda ts: [t for t in ts if t in score.EVIDENCE])
    fid = 'R' + time.strftime('%m%d%H%M') + '-' + re.sub(r'[^a-z0-9]', '', (row.get('Company') or '').lower())[:6] + row['id'][-4:]
    gen_v6.OUT = str(config.RESUMES)
    res = gen_v6.make({'id': fid, 'file': fid, 'position': row.get('Position') or '', 'required': keep(sc['required']),
                       'preferred': keep(sc['preferred']), 'kind': kind, 'location': row.get('Location') or ''})
    pdf = os.path.join(res['folder'], P.file('Resume', 'pdf'))
    pdf_id, docx_id = notion.upload_file(pdf), notion.upload_file(os.path.join(res['folder'], P.file('Resume', 'docx')))
    stamp = time.strftime('%b %d')
    for b in notion_pages.children(row['id']):   # older resume stays on the page, relabelled
        if b['type'] == 'heading_3' and 'tailored resume' in notion_pages._text(b).lower():
            notion._req('PATCH', f"/blocks/{b['id']}", {'heading_3': {'rich_text': notion._rt(f'Older resume (replaced {stamp})')}})
    blocks = notion.file_blocks(pdf_id, docx_id)
    blocks[0]['heading_3']['rich_text'] = notion._rt(f'Tailored resume (attach this one) — {gen_v6.VERSION}, {stamp}')
    notion.append(row['id'], blocks)
    notes = row.get('Notes') or ''
    notes = re.sub(r'resume v[\d.]+', f'resume {gen_v6.VERSION}', notes) if re.search(r'resume v[\d.]+', notes) \
        else f'{notes} | resume {gen_v6.VERSION}'
    if res['pages'] != 1: notes += ' | resume 2 pages?'
    notion.update_row(row['id'], notion.props(Notes=notes[:1990]))
    row['Notes'] = notes
    return pdf


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=3)
    ap.add_argument('--max', type=int, default=10000)
    a = ap.parse_args()
    since = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(time.time() - a.days * 86400))
    rows = notion.query({'and': [{'or': [{'property': 'Status', 'select': {'equals': 'To apply'}},
                                         {'property': 'Status', 'select': {'equals': 'Needs you'}}]},
                                 {'timestamp': 'created_time', 'created_time': {'on_or_after': since}}]})
    todo = [r for r in rows if not current(r)][:a.max]
    print(f'{len(rows)} open rows in {a.days} days, {len(todo)} to rebuild')
    done = kept = 0
    for i, r in enumerate(todo):
        try:
            if refresh(r, render=True): done += 1
            else: kept += 1
        except Exception as e:
            kept += 1; print('failed', r.get('Company'), str(e)[:120])
        if i % 25 == 0: print(i, 'rebuilt', done, 'kept old', kept, flush=True)
    print('done: rebuilt', done, 'kept old (JD unreadable or error)', kept)


if __name__ == '__main__':
    main()
