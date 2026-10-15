"""Recruiter Outreach & Follow-ups, local edition (rules: reference/schedules/Recruiter_Outreach__Follow-ups.md).
Python picks the rows (funnel.py, 0 tokens) and applies every Notion/queue/CSV change; ONE `claude -p` call finds
recruiters, verifies public emails (WebFetch), checks Gmail and creates Gmail DRAFTS (send/forward/reply are blocked).

  python outreach.py                    # normal run
  python outreach.py --dry              # build context, print prompt size, no LLM, no writes
  python outreach.py --fixture data/test/funnel_fixture.json --dry
"""
import applicant as P
import argparse, csv, datetime as dt, json, re, sys
from zoneinfo import ZoneInfo
import config, funnel
from manager import agent, prompt_for, size_line, match_row, GMAIL

CT = ZoneInfo('America/Chicago')
OUT = config.DATA / 'outreach'
QUEUE = config.DATA / 'linkedin_queue.json'
TOOLS = [f'{GMAIL}search_threads', f'{GMAIL}get_thread', f'{GMAIL}get_message', f'{GMAIL}create_draft',
         'WebSearch', 'WebFetch', 'ToolSearch']
STAGE_ORDER = ['queued', 'invited', 'connected', 'messaged', 'replied']

SCHEMA = {'type': 'object', 'required': ['report_md', 'push', 'notion_updates', 'drafts'], 'properties': {
    'report_md': {'type': 'string'}, 'push': {'type': 'string'},
    'bounces': {'type': 'array', 'items': {'type': 'string'}},
    'notion_updates': {'type': 'array', 'items': {'type': 'object', 'required': ['notion_id'], 'properties': {
        'notion_id': {'type': 'string'},
        'outreach': {'type': 'string', 'enum': ['Sent', 'Follow-up 1', 'Follow-up 2', 'Replied', 'No contact found']},
        'recruiters': {'type': 'string'}, 'last_contact': {'type': 'string'}, 'note': {'type': 'string'},
        'status': {'type': 'string', 'enum': ['Needs you', 'Rejected', 'OA', 'Interview']}}}},
    'drafts': {'type': 'array', 'items': {'type': 'object', 'properties': {
        'email': {'type': 'string'}, 'first_name': {'type': 'string'}, 'company': {'type': 'string'}, 'role': {'type': 'string'},
        'subject': {'type': 'string'}, 'body': {'type': 'string'}, 'stage': {'type': 'string'}}}},
    'linkedin_notes': {'type': 'array', 'items': {'type': 'object', 'required': ['name', 'company', 'note'], 'properties': {
        'company': {'type': 'string'}, 'role': {'type': 'string'}, 'notion_id': {'type': 'string'}, 'name': {'type': 'string'},
        'title': {'type': 'string'}, 'profile_url': {'type': 'string'}, 'note': {'type': 'string'}, 'message': {'type': 'string'},
        'priority': {'type': 'integer'}, 'job_url': {'type': 'string'}}}},
    'linkedin_events': {'type': 'array', 'items': {'type': 'object', 'properties': {
        'name': {'type': 'string'}, 'company_hint': {'type': 'string'}, 'event': {'type': 'string', 'enum': ['accepted', 'message']},
        'date': {'type': 'string'}}}}}}


def facts():
    """Compact project facts: the '### Project' header + first paragraph of each real project (Project Library)."""
    f = config.REF / 'Job Hunter — Project Library.md'   # your local project facts (reference/ is git-ignored)
    lib = f.read_text() if f.exists() else ''
    out = []
    for m in re.finditer(r'### (.+?)\n(.+?)(?=\n\*Tags|\n### |\n---)', lib, re.S):
        if any(x in m.group(1) for x in P.get('llm_skip_projects', default=[])): continue
        out.append(f"{m.group(1).strip()}: {' '.join(m.group(2).split())[:420]}")
    jobs = [w['company'] for w in P.get('work_history', default=[])]
    if jobs:   # the current job's '- **Company' entry, up to the next one
        exp = re.search(r'- \*\*' + re.escape(jobs[0]) + r'.*?(?=\n- \*\*)', lib, re.S)
        if exp: out.append(' '.join(exp.group(0).split())[:420])
    return '\n'.join(out)


# ---------- local LinkedIn queue (same fields as the cloud artifact "contacts" docs) ----------
def load_queue():
    return json.loads(QUEUE.read_text()) if QUEUE.exists() else []


def save_queue(q):
    QUEUE.write_text(json.dumps(q, indent=1, ensure_ascii=False))


def slug(s):
    return re.sub(r'[^a-z0-9]+', '-', (s or '').lower()).strip('-')


def queue_add(q, n, status_by_id, today):
    key = (slug(n['name']), slug(n['company']))
    for d in q:
        if (slug(d['name']), slug(d['company'])) == key:
            if n.get('notion_id') and n['notion_id'] != d.get('notionId') and \
                    n['notion_id'] not in [x.get('notionId') for x in d.get('alsoRoles', [])]:
                d.setdefault('alsoRoles', []).append({'role': n.get('role'), 'jobUrl': n.get('job_url'), 'notionId': n.get('notion_id')})
            return False
    nid = (n.get('notion_id') or '').replace('-', '')
    q.append({'doc_id': f"{nid[-8:]}-{slug(n['name'])}", 'company': n['company'], 'role': n.get('role'), 'jobUrl': n.get('job_url'),
              'notionId': nid, 'notionUrl': f'https://www.notion.so/{nid}' if nid else '', 'name': n['name'],
              'title': n.get('title', ''), 'profileUrl': n.get('profile_url', ''), 'note': n['note'][:300],
              'message': n.get('message', ''), 'priority': n.get('priority', 2),
              'applied': status_by_id.get(nid) in ('Applied', 'OA', 'Interview'), 'stage': 'queued', 'batch': today,
              'stageAt': {}, 'createdAt': dt.datetime.now(dt.timezone.utc).isoformat()})
    return True


def queue_event(q, ev):
    first = slug(ev.get('name')).split('-')[0]
    hits = [d for d in q if slug(d['name']).startswith(first) and (not ev.get('company_hint') or slug(ev['company_hint'])[:5] in slug(d['company']))]
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    for d in hits[:1]:
        new = 'connected' if ev['event'] == 'accepted' else 'replied'
        if STAGE_ORDER.index(new) > STAGE_ORDER.index(d.get('stage', 'queued')) if d.get('stage') in STAGE_ORDER else True:
            d['stage'] = new; d.setdefault('stageAt', {})[new] = now
            if new == 'connected': d['stageAt'].setdefault('invited', now)
        return d
    return None


def sync_queue_to_notion(q, rows_by_id, dry):
    """Most advanced stage per Notion row -> 'LinkedIn' select; applied flag + wording from the row Status."""
    import notion
    best, changed = {}, []
    for d in q:
        if d.get('stage') == 'skipped': continue
        st = rows_by_id.get(d.get('notionId'), {}).get('Status')
        if not d.get('applied') and st in ('Applied', 'OA', 'Interview'):
            d['applied'] = True
            for k in ('note', 'message'): d[k] = (d.get(k) or '').replace("I'm applying to", 'I applied to')
        if st in ('Rejected', 'Closed'): d['stage'] = 'skipped'; continue
        for nid in [d.get('notionId')] + [x.get('notionId') for x in d.get('alsoRoles', [])]:
            if nid and STAGE_ORDER.index(d['stage']) >= STAGE_ORDER.index(best.get(nid, 'queued')): best[nid] = d['stage']
    for nid, stage in best.items():
        r = rows_by_id.get(nid)
        if r is not None and (r.get('LinkedIn') or '').lower() != stage:
            cur = (r.get('LinkedIn') or 'queued').lower()
            if cur in STAGE_ORDER and STAGE_ORDER.index(cur) > STAGE_ORDER.index(stage): continue
            if not dry: notion.update_row(r['id'], notion.props(LinkedIn=stage.capitalize()))
            changed.append(f"{r.get('Company')}: LinkedIn {stage.capitalize()}")
    return changed


def apply(out, rows, dry):
    import notion
    rows_by_id = {r['id'].replace('-', ''): r for r in rows}
    today = dt.datetime.now(CT).strftime('%Y-%m-%d')
    log = []
    # bounces -> mark in Recruiters; Outreach = No contact found if no other working email remains
    for addr in out.get('bounces', []) or []:
        for r in rows:
            rec = r.get('Recruiters') or ''
            if addr.lower() in rec.lower() and f'{addr} BOUNCED' not in rec:
                rec2 = re.sub(re.escape(addr), f'{addr} BOUNCED {today}', rec, flags=re.I)
                left = [e for e in re.findall(r'[\w.+-]+@[\w-]+\.[\w.]+', rec2) if f'{e} BOUNCED' not in rec2]
                if not dry: notion.update_row(r['id'], notion.props(Recruiters=rec2, Outreach=None if left else 'No contact found'))
                log.append(f"bounce {addr} -> {r.get('Company')}")
    for u in out.get('notion_updates', []):
        r = rows_by_id.get((u.get('notion_id') or '').replace('-', ''))
        if not r: log.append(f"skip update {u.get('notion_id')}: row not found"); continue
        note = u.get('note')
        props = notion.props(Outreach=u.get('outreach'), Recruiters=u.get('recruiters'), LastContact=u.get('last_contact'),
                             Status=u.get('status'), Notes=((note + ' | ' + (r.get('Notes') or ''))[:1990]) if note else None)
        if props and not dry: notion.update_row(r['id'], props)
        log.append(f"{r.get('Company')}: {', '.join(k for k in props)}")
    q = load_queue(); added = 0
    status_by_id = {k: v.get('Status') for k, v in rows_by_id.items()}
    for n in out.get('linkedin_notes', []) or []:
        added += queue_add(q, n, status_by_id, today)
        r = rows_by_id.get((n.get('notion_id') or '').replace('-', ''))
        if r is not None and not r.get('LinkedIn') and not dry: notion.update_row(r['id'], notion.props(LinkedIn='Queued'))
    for ev in out.get('linkedin_events', []) or []:
        queue_event(q, ev)
    log += sync_queue_to_notion(q, rows_by_id, dry)
    if not dry: save_queue(q)
    log.append(f'LinkedIn queue: +{added} new, {len(q)} total')
    return log


EMAIL_RX = re.compile(r'[\w.+-]+@[\w-]+\.[\w.]+')


def email_history(rows, days=14):
    """(bounced, recently_emailed) from the Notion Recruiters/Outreach/Last Contact fields — enforced here in Python,
    not left to the model (Oct 8-9: 5 emails to one Apple recruiter in a minute, 12 TCS staff in a burst, ~15% bounces)."""
    cutoff = (dt.datetime.now(CT).date() - dt.timedelta(days=days)).isoformat()
    bounced, recent = set(), set()
    for r in rows:
        rec = r.get('Recruiters') or ''
        for e in EMAIL_RX.findall(rec):
            if f'{e} BOUNCED' in rec: bounced.add(e.lower())
        contacted = r.get('Outreach') in ('Sent', 'Follow-up 1', 'Follow-up 2', 'Replied')
        if contacted and (not r.get('Last Contact') or r['Last Contact'][:10] >= cutoff):
            recent |= {e.lower() for e in EMAIL_RX.findall(rec)}
    return bounced, recent


CONTACTS = OUT / 'company_contacts.json'


def _co(name):
    return re.sub(r'[^a-z0-9]', '', (name or '').split('(')[0].lower())[:20]


def load_contacts():
    try: return json.loads(CONTACTS.read_text())
    except Exception: return {}


def remember_contacts(out, rows_by_id, bounced):
    """Cache every verified recruiting address the model reported, per company, so the next role at the same company
    reuses it instead of a fresh search (more coverage, fewer tokens). Bounced addresses are never cached."""
    cache = load_contacts()
    for u in out.get('notion_updates', []) or []:
        r = rows_by_id.get((u.get('notion_id') or '').replace('-', ''))
        if not r or not u.get('recruiters'): continue
        for line in u['recruiters'].splitlines():
            for e in EMAIL_RX.findall(line):
                if e.lower() in bounced or 'BOUNCED' in line: continue
                src = (re.findall(r'https?://\S+', line) or [''])[-1]
                lst = cache.setdefault(_co(r.get('Company')), [])
                if not any(x['email'] == e.lower() for x in lst):
                    lst.append({'email': e.lower(), 'line': line.strip()[:200], 'source': src, 'found': dt.date.today().isoformat()})
    OUT.mkdir(parents=True, exist_ok=True); CONTACTS.write_text(json.dumps(cache, indent=1))


def screen_drafts(drafts, bounced, recent, per_company=2):
    """Split drafts into (send, hold): never a bounced address, one email per person per run, no new email to someone
    emailed in the last 14 days (follow-ups are fine), and at most `per_company` new emails per company domain per run."""
    send, hold, seen, per_dom = [], [], set(), {}
    for d in drafts:
        e = (d.get('email') or '').strip().lower()
        follow = 'follow' in (d.get('stage') or '').lower()
        dom = e.split('@')[-1]
        why = ('no address' if not e else 'bounced before' if e in bounced else 'duplicate in this run' if e in seen
               else 'emailed in the last 14 days' if (e in recent and not follow)
               else f'over {per_company} new emails to {dom}' if (not follow and per_dom.get(dom, 0) >= per_company) else '')
        if why: hold.append((d, why)); continue
        seen.add(e)
        if not follow: per_dom[dom] = per_dom.get(dom, 0) + 1
        send.append(d)
    return send, hold


def write_csv(drafts):
    if not drafts: return None
    OUT.mkdir(parents=True, exist_ok=True)
    f = OUT / f"Outreach Queue — {dt.datetime.now(CT):%Y-%m-%d %H%M}.csv"
    with open(f, 'w', newline='') as fh:
        fh.write('# Send max 25 per day, spread over the day (not in one burst). Skip any row marked BOUNCED.\n')
        w = csv.writer(fh); w.writerow(['Email', 'FirstName', 'Company', 'Role', 'Subject', 'Body', 'Stage'])
        for d in drafts:
            w.writerow([d.get('email'), d.get('first_name'), d.get('company'), d.get('role'), d.get('subject'), d.get('body'), d.get('stage')])
    return f


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry', action='store_true'); ap.add_argument('--fixture')
    ap.add_argument('--max', type=int, default=10); ap.add_argument('--model', default=config.LLM_MODEL)
    ap.add_argument('--budget', type=float, default=3.0)
    a = ap.parse_args()
    rows = []
    try:
        if a.fixture: fun = funnel.get(a.fixture)
        else: rows = funnel.load_rows(); fun = funnel.compute(rows, max_outreach=a.max)
    except funnel.FunnelError as e:
        print('ERROR:', e); sys.exit(2)
    base = {'today_ct': dt.datetime.now(CT).strftime('%Y-%m-%d'),
            'already_queued_linkedin': [f"{d['name']} @ {d['company']}" for d in load_queue()][-200:]}
    bounced, recent = email_history(rows)
    base['never_email'] = sorted(bounced | recent)[:400]   # the model must not draft to these (Python re-checks below)
    cands = fun['outreach_candidates'][:a.max]
    # 1 call for follow-ups / bounces / replies / LinkedIn sync, then 1 focused call PER ROLE for new outreach
    jobs = [('follow-ups', dict(base, outreach_candidates=[], followups_due=fun['followups_due']), FOLLOWUP_DIRECTIVE)]
    known = load_contacts()
    for c in cands:
        kc = [x for x in known.get(_co(c.get('Company')), []) if x['email'] not in bounced]
        jobs.append((f"{c.get('Company')} – {(c.get('Position') or '')[:40]}",
                     dict(base, outreach_candidates=[c], followups_due=[], known_contacts=kc),
                     RESEARCH_DIRECTIVE.format(company=c.get('Company'), role=c.get('Position'))))
    print(f"jobs: {len(jobs)} (1 follow-up pass + {len(cands)} roles) | followups={len(fun['followups_due'])}")
    if a.dry:
        print(size_line(prompt_for('outreach', jobs[-1][1], FACTS=facts()) + jobs[-1][2])); return
    all_drafts, pushes, reports = [], [], []
    for name, ctx, extra in jobs:
        if name == 'follow-ups' and not ctx['followups_due']: continue
        ctx['never_email'] = sorted(bounced | recent | {(d.get('email') or '').lower() for d in all_drafts})[:400]
        p = prompt_for('outreach', ctx, FACTS=facts()) + extra + NEVER_EMAIL_RULE
        try:
            out = agent(p, TOOLS, dict(SCHEMA, title='outreach'), model=a.model, budget_usd=a.budget)
        except Exception as e:
            print(f'[{name}] failed: {str(e)[:200]}'); continue
        log = apply(out, rows, dry=False)
        try: remember_contacts(out, {r['id'].replace('-', ''): r for r in rows}, bounced)
        except Exception as e: print('contact cache failed:', e)
        all_drafts += out.get('drafts') or []
        reports.append(f"### {name}\n{out.get('report_md', '')}\n" + '\n'.join(log))
        if out.get('push'): pushes.append(out['push'])
    all_drafts, held = screen_drafts(all_drafts, bounced, recent)
    f = write_csv(all_drafts)
    print('\n\n'.join(reports))
    if f: print('mail-merge CSV:', f)
    if held:
        print('DO NOT SEND these Gmail drafts (kept out of the CSV):')
        for d, why in held: print(f"  - {d.get('email')} ({d.get('company')}): {why}")
        pushes.append(f"{len(held)} drafts held back (duplicate/bounced/too many per company) — see outreach log")
    import notify
    if all_drafts or pushes:
        notify.push(f"Outreach: {len(all_drafts)} new drafts in Gmail. " + ' | '.join(pushes)[:300], title='Recruiter Outreach')


NEVER_EMAIL_RULE = """

HARD LIMITS (checked again in code): never draft to any address in context "never_email" (bounced, or emailed in the
last 14 days); at most ONE email per person per run even if they recruit for several roles; at most 2 new emails per
company per run — prefer the early-careers/university recruiter or the recruiter named on the posting over other staff.
"""

FOLLOWUP_DIRECTIVE = """

THIS CALL = FOLLOW-UPS, BOUNCES, REPLIES, LINKEDIN EVENTS. First action: call ToolSearch with
"select:mcp__claude_ai_Gmail__search_threads,mcp__claude_ai_Gmail__get_thread,mcp__claude_ai_Gmail__create_draft"
to load the Gmail tools, then do Part E (bounces), Part B (replies + follow-up drafts, only to verified public addresses
— never to pattern-guessed ones; mark those rows so they stop coming back) and Part D step 1 (LinkedIn emails).
"""

RESEARCH_DIRECTIVE = """

THIS CALL = ONE ROLE: {company} — {role}. Doing the research is the whole job; do not skip it.
Mandatory steps (use the tools):
1. WebSearch: "{company} university recruiting email", "{company} campus recruiting", "{company} early careers contact",
   and "{company} recruiter {role} linkedin". Open (WebFetch) the company's careers / students / early-careers pages.
2. Look for: a recruiter named on the posting, a published recruiting inbox (campus@, university.recruiting@, careers@,
   talent@ ...), or recruiters who publicly posted about this role or new-grad/intern hiring.
3. For every email you plan to use: WebFetch the page and confirm the exact address string is on it (else no email).
4. Load Gmail first: ToolSearch "select:mcp__claude_ai_Gmail__search_threads,mcp__claude_ai_Gmail__create_draft"
   (the Gmail tools exist but are deferred). Then draft the email(s) with create_draft for each verified address; otherwise write the LinkedIn note(s) (<=300 chars)
   for the people you found (with their public profile URL).
Shortcut: if context "known_contacts" or the row's Recruiters field already lists a published address (job post or a page
URL), WebFetch that source once to confirm it is still there and reuse it — skip the web searches for that address.
Only report "No contact found" after you actually ran the searches and fetched the pages above.
"""


if __name__ == '__main__':
    main()
