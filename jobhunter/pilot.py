"""Claude pilot: drives an application that the site adapter could not finish, step by step, until the page is ready
for the final Submit (Oct 28 2026, owner: "move to 100% success"). Reads the page as data (no screenshots).

Each step: rule-based fill + one batched answer call (formfill.fill) -> page snapshot (rescue.SNAP_JS + file inputs +
headings/text) -> Claude decides: fix fields, upload files, click ONE navigation button (Apply / Next / Continue /
Review ...), or report the page state -> Playwright acts -> repeat (PILOT_STEPS, default 10).

It never clicks the final Submit (auth.submit owns that click, AUTO_SUBMIT), Sign in / Create account (auth.py), CAPTCHA
widgets, Withdraw/Delete; never types into protected fields (rescue._do enforces formfill.NEVER, consent, password).
A government-ID question stops the run (CANCELLED by apply_prep). Returns dict(state, page, log, question).
States: ready (every required field filled and a final Submit button is visible) | login | closed | captcha | gov_id |
need_human | stuck | submitted (a click unexpectedly ended on a confirmation page -- recorded, never intended).
"""
import applicant as P
import json, os, re
import config, formfill, llm, rescue
from answers import GUESS
from ats.common import captcha

STEPS = int(os.environ.get('PILOT_STEPS', '10'))
ON = os.environ.get('PILOT', '1') == '1'
FINAL_RX = re.compile(r'^\s*(submit|send|finish|complete)\b|submit (my |your )?application|send application', re.I)
NEVER_CLICK = re.compile(r'sign ?in|log ?in|create (an )?account|register|sign ?up|withdraw|delete|remove (my )?(account|profile)|'
                         r'captcha|(continue|apply|sign in|log in) (with|using) (google|linkedin|indeed|apple|facebook|github|microsoft)',
                         re.I)   # social sign-in = account linking, auth.py's job

FILES_JS = r"""
() => [...document.querySelectorAll('input[type=file]')].map((el, i) => {
  const id = 'fx' + i; el.setAttribute('data-fx', id);
  const box = el.closest('fieldset, .field, .form-group, [data-automation-id], section, div');
  const lab = (el.getAttribute('aria-label') || (el.id && document.querySelector(`label[for="${CSS.escape(el.id)}"]`)?.innerText)
               || (box && box.innerText) || el.name || '').replace(/\s+/g, ' ').trim().slice(0, 160);
  return {id, label: lab, accept: el.accept || '', has_file: el.files && el.files.length > 0};
})
"""

SCHEMA = {'type': 'object', 'required': ['state', 'actions'], 'properties': {
    'state': {'type': 'string', 'enum': ['working', 'ready', 'login', 'closed', 'need_human', 'stuck']},
    'reason': {'type': 'string', 'description': 'short; for need_human: the exact question he must answer'},
    'actions': {'type': 'array', 'items': {'type': 'object', 'required': ['op', 'id'], 'properties': {
        'op': {'type': 'string', 'enum': ['fill', 'select', 'check', 'uncheck', 'click', 'upload', 'nav']},
        'id': {'type': 'string'}, 'value': {'type': 'string'}}}}}}

SYSTEM = (f"You drive a job application for {P.NAME} in a real browser, one page at a time. You see the page as data "
          "(fields with ids, buttons with ids, file inputs, headings, visible text). Goal: reach the page where every "
          "required field is filled and the FINAL submit button is visible, then answer state=ready. Per turn: fix or fill "
          "required empty / invalid fields truthfully from the facts (ops fill/select/check/uncheck; choice values copied "
          "exactly from the options), upload files (op upload, value 'resume' or 'cover_letter'), and at most ONE op nav "
          "= click the button that moves the application forward (Apply, Apply now, Start application, I'm interested, "
          "Apply manually / with resume, Next, Continue, Save and continue, Review). A server error page (500/503, 'try again'): op nav with id = the "
          "page URL reloads it (same site only). Use op click only for Add / Close / "
          "expand buttons and dismissing cookie banners or popups. Never press the final Submit / Send application "
          "button yourself -- report state=ready instead; on a page that already holds the application form, an "
          "'Apply' button IS the final submit (state=ready, never nav). Never use Sign in / Create account / social sign-in: report "
          "state=login. Posting closed or no longer accepting: state=closed. " + GUESS + " A required question in those "
          "never-invent groups with no answer in the facts: state=need_human with the question in reason. If the same "
          "page repeats with the same errors and you have nothing new to try: state=stuck. Return only the JSON.")


LINKS_JS = r"""
(title) => {
  const words = (title || '').toLowerCase().split(/[^a-z0-9+#]+/).filter(w => w.length > 2);
  const titleLike = t => { const l = t.toLowerCase(); return words.length && words.filter(w => l.includes(w)).length >= Math.min(3, words.length); };
  // fresh ids every step: stale data-rx from earlier snapshots made the pilot's clicks miss (32BJ, Oct 28)
  document.querySelectorAll('[data-rx]').forEach(e => e.removeAttribute('data-rx'));
  // Apply / Next often are links, not buttons -> give them role=button so the snapshot lists them
  for (const a of document.querySelectorAll('a[href], a[onclick], input[type=button]')) {
    const t = (a.innerText || a.value || a.getAttribute('aria-label') || '').trim();
    // ... and so are search results: the link whose text matches the job title (Deloitte, TikTok Oct 28)
    if (t && t.length < 120 && (/apply|start|next|continue|proceed|review|interested|begin/i.test(t) || titleLike(t)) && !a.getAttribute('role'))
      a.setAttribute('role', 'button');
  }
  // result cards that are divs with a click handler, not links (TikTok, Oct 29): pointer cursor + title-like text
  for (const d of document.querySelectorAll('div, li, span, h2, h3')) {
    if (d.getAttribute('role') || d.closest('a, button, [role=button]')) continue;
    const t = (d.innerText || '').trim();
    if (!t || t.length > 160 || !titleLike(t.split('\n')[0])) continue;
    if (getComputedStyle(d).cursor !== 'pointer' && !d.onclick) continue;
    if ([...d.children].some(c => (c.innerText || '').trim() === t)) continue;   // mark the innermost element only
    d.setAttribute('role', 'button');
  }
}
"""


def _files(frame):
    try: return frame.evaluate(FILES_JS)
    except Exception: return []


def _text(frame):
    try: return re.sub(r'\s+', ' ', frame.locator('body').inner_text(timeout=4000))[:2500]
    except Exception: return ''


def _act(frame, page, snap, a, job):
    op = a.get('op')
    if op == 'upload':
        loc = frame.locator(f'[data-fx="{a.get("id")}"]').first
        path = job.get('pdf') if a.get('value') != 'cover_letter' else job.get('cover_txt')
        if not path or not loc.count(): return 'upload skipped'
        loc.set_input_files(path); frame.wait_for_timeout(1500); return f'uploaded {os.path.basename(path)}'
    if op == 'nav' and str(a.get('id') or '').startswith('http'):   # reload / open a page on the SAME site (ADP 503, 32BJ Oct 28)
        from urllib.parse import urlsplit
        if urlsplit(a['id']).netloc != urlsplit(page.url).netloc:
            # another site only through a link that is ON the page (Deloitte -> its careers system, Oct 28)
            try: on_page = frame.evaluate('u => [...document.querySelectorAll("a[href]")].some(a => a.href === u)', a['id'])
            except Exception: on_page = False
            if not on_page: return 'refused nav to another site (not a link on this page)'
        page.goto(a['id'], wait_until='domcontentloaded', timeout=30000); page.wait_for_timeout(3000)
        return 'opened ' + a['id'][:60]
    if op == 'nav':
        loc = frame.locator(f'[data-rx="{a.get("id")}"]').first
        if not loc.count(): return 'nav: unknown id'
        t = ' '.join(x for x in (loc.inner_text(), loc.get_attribute('aria-label'), loc.get_attribute('value')) if x).strip()
        if not t or FINAL_RX.search(t) or NEVER_CLICK.search(t): return f'refused nav "{t[:30]}"'
        if APPLY_RX.search(t) and _has_form(snap):   # Garage/Dover Oct 28: on a form page "Apply" IS the final submit
            return f'refused nav "{t[:30]}" (final button on a form page)'
        n = len(page.context.pages)
        loc.click(timeout=8000)
        try: page.wait_for_load_state('domcontentloaded', timeout=10000)
        except Exception: pass
        page.wait_for_timeout(2500)
        if len(page.context.pages) > n: return 'NEWTAB:' + t[:30]
        return 'nav ' + t[:30]
    return rescue._do(frame, page, snap['fields'], a)


APPLY_RX = re.compile(r'^\s*(apply|apply now|apply for this (job|position|role))\s*$', re.I)


def _has_form(snap):
    """The page is an application form (fields to fill), not a job description with an Apply button."""
    return len([f for f in snap.get('fields', []) if f.get('type') not in ('checkbox', 'search')]) >= 3


def _confirmed(page):
    try:
        import auth
        return bool(auth.CONFIRM.search(page.locator('body').inner_text(timeout=4000)[:6000]))
    except Exception:
        return False


def _final_button(frame, snap=None):
    try:
        b = frame.get_by_role('button', name=FINAL_RX)
        if b.count() > 0 and b.first.is_visible(): return True
        if snap is not None and _has_form(snap):
            a = frame.get_by_role('button', name=APPLY_RX)
            return a.count() > 0 and a.first.is_visible()
    except Exception:
        pass
    return False


def run(page, job, problems=()):
    """Drive the application from the current page. job: url, company, title, pdf, cover_txt, cover_text."""
    log, seen = [], []
    if not ON: return dict(state='stuck', page=page, log=['pilot off'], question='')
    bank = (config.REF / 'Job Hunter — Answer Bank.md').read_text()[:14000]
    try:
        import answers
        facts = answers.resume_facts()
    except Exception:
        facts = ''
    for step in range(STEPS):
        if captcha(page): return dict(state='captcha', page=page, log=log, question='')
        frame = rescue._scope(page)
        try:   # rules + one batched answer call first (0-1 LLM calls); the pilot only handles what's left
            done, unknown = formfill.fill(frame, dict(job, url=page.url))
        except Exception as e:
            done, unknown = [], [f'fill failed: {str(e)[:80]}']
        gov = [u for u in unknown if str(u).startswith('GOV-ID asked') or formfill.GOV_ID.search(str(u))]
        if gov: return dict(state='gov_id', page=page, log=log, question=str(gov[0])[:200])
        try: frame.evaluate(LINKS_JS, job.get('title') or '')
        except Exception: pass
        try: snap = rescue.snapshot(frame)
        except Exception as e:
            log.append(f'snapshot failed: {str(e)[:80]}'); return dict(state='stuck', page=page, log=log, question='')
        empty = formfill.still_empty(frame)
        if not empty and _final_button(frame, snap):
            log.append(f'step {step + 1}: ready'); return dict(state='ready', page=page, log=log, question='')
        key = json.dumps([page.url.split('?')[0], [f['label'] for f in snap['fields']][:40], snap.get('errors')])[:4000]
        if seen.count(key) >= 2:
            log.append('same page 3 times'); return dict(state='stuck', page=page, log=log, question='; '.join(empty)[:200])
        seen.append(key)
        page_data = dict(url=page.url, headings=snap.get('heads'), errors=snap.get('errors'), required_empty=empty,
                         fields=snap['fields'], buttons=snap['buttons'], file_inputs=_files(frame), text=_text(frame))
        prompt = (f"ANSWER BANK:\n{bank}\n\nRESUME FACTS:\n{facts}\n\n"
                  f"APPLICATION: {job.get('title') or ''} at {job.get('company') or ''}\n"
                  f"EARLIER PROBLEMS: {'; '.join(str(p)[:200] for p in problems) or 'none'}\nSTEPS SO FAR: {'; '.join(log[-12:]) or 'none'}\n\n"
                  f"PAGE (step {step + 1}):\n{json.dumps(page_data, ensure_ascii=False)[:26000]}")
        try:
            out = llm.ask(prompt, system=SYSTEM, schema=SCHEMA, job='pilot')
        except Exception as e:
            log.append(f'claude failed: {str(e)[:80]}'); return dict(state='stuck', page=page, log=log, question='')
        rescue._log_rescue(page, job, ['pilot step %d' % (step + 1)] + list(problems)[:3], snap, out)
        st = out.get('state')
        if st in ('login', 'closed', 'need_human', 'stuck'):
            log.append(f'step {step + 1}: {st} {(out.get("reason") or "")[:120]}')
            return dict(state=st, page=page, log=log, question=out.get('reason') or '')
        navs = 0
        for a in (out.get('actions') or [])[:25]:
            if a.get('op') == 'nav':
                navs += 1
                if navs > 1: continue
            try: r = _act(frame, page, snap, a, job)
            except Exception as e: r = f"{a.get('op')} {a.get('id')} failed: {str(e)[:60]}"
            if r.startswith('NEWTAB:'):
                page = page.context.pages[-1]; page.wait_for_timeout(2000); r = 'nav (new tab) ' + r[7:]
                if job.get('dry'):   # --dry: the new tab gets the same no-submit network guard as the first one
                    import apply_prep
                    apply_prep.dry_guard(page, job.setdefault('blocked', []))
            log.append(r)
            if a.get('op') == 'nav' and _confirmed(page):   # never intended, but if it happened it must be recorded
                log.append('confirmation page after nav'); return dict(state='submitted', page=page, log=log, question='')
        if st == 'ready' and not formfill.still_empty(rescue._scope(page)) and _final_button(rescue._scope(page), snap):
            log.append(f'step {step + 1}: ready'); return dict(state='ready', page=page, log=log, question='')
    return dict(state='stuck', page=page, log=log + [f'{STEPS} steps used'], question='')
