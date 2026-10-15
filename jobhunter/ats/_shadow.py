"""Shadow-DOM-aware filler (SmartRecruiters web components, Eightfold, other Lit/Stencil forms).
Playwright CSS locators pierce open shadow roots; labels are read from the field's own shadow root / host chain.
Same Answer Bank rules + hard limits as formfill (never consent/legal/password/ID; unknown required -> unknown)."""
import re
import formfill

LABEL_JS = r"""e => {
  // text including slotted (light-DOM) content of web components
  const deep = x => { if (!x) return ''; if (x.nodeType === 3) return x.textContent;
    if (x.tagName === 'SLOT') return x.assignedNodes({flatten: true}).map(deep).join(' ');
    if (x.tagName === 'STYLE' || x.tagName === 'SCRIPT') return '';
    let s = ''; for (const c of (x.shadowRoot ? x.shadowRoot.childNodes : x.childNodes)) s += deep(c) + ' '; return s; };
  const txt = x => (x ? deep(x) : '').replace(/\s+/g, ' ').trim();
  const r = e.getRootNode();
  let t = e.getAttribute('aria-label') || '';
  const lb = e.getAttribute('aria-labelledby');
  if (!t && lb && r.getElementById) t = lb.split(' ').map(id => txt(r.getElementById(id))).join(' ');
  if (!t && e.id && r.querySelector) { const l = r.querySelector(`label[for="${CSS.escape(e.id)}"]`); if (l) t = txt(l); }
  if (!t) { const l = e.closest('label'); if (l) t = txt(l); }
  let h = r.host;
  for (let i = 0; i < 5 && !t && h; i++) {
    t = h.getAttribute('label') || h.getAttribute('aria-label') || '';
    if (!t && h.shadowRoot) { const l = h.shadowRoot.querySelector('label, legend'); if (l && !l.contains(e)) t = txt(l); }
    if (!t) { const p = h.parentElement; const l = p && p.querySelector(':scope > label, :scope > legend'); if (l) t = txt(l); }
    h = h.parentElement || (h.getRootNode && h.getRootNode().host);
  }
  if (!t) { let p = e.parentElement; for (let i = 0; i < 4 && p && !t; i++, p = p.parentElement) {
    const l = p.querySelector('label, legend, [class*="label" i], [class*="question" i]'); if (l && !l.contains(e)) t = txt(l); } }
  if (t.replace(/[*\s]/g, '').length < 2) {   // label was only "*": use the nearest question container text
    let h2 = r.host, q = '';
    for (let i = 0; i < 6 && h2 && q.replace(/[*\s]/g, '').length < 2; i++) {
      const c = h2.closest && h2.closest('[data-test*="question" i], [class*="question" i], fieldset');
      if (c) q = txt(c.querySelector('label, legend, [class*="title" i], [class*="label" i], p') || c).slice(0, 300);
      h2 = h2.parentElement || (h2.getRootNode && h2.getRootNode().host);
    }
    if (q) t = q;
  }
  // section heading (for telling resume vs cover-letter dropzones apart)
  let sec = '', s = e;
  for (let i = 0; i < 12 && s && !sec; i++) {
    const host = s.getRootNode && s.getRootNode().host;
    s = s.parentElement || host;
    if (s && s.querySelector) { const hd = s.querySelector(':scope > h1, :scope > h2, :scope > h3, :scope > h4, :scope > header, :scope > legend'); if (hd) sec = txt(hd); }
  }
  const vis = !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
  const req = e.required || e.getAttribute('aria-required') === 'true' || /\*\s*$/.test(t) || (r.host && r.host.hasAttribute('required'));
  let val = e.value || '';
  const role = e.getAttribute('role') || '';
  return {role, label: (t || e.placeholder || e.name || '').slice(0, 300), section: sec.slice(0, 80), tag: e.tagName.toLowerCase(),
          type: (e.type || '').toLowerCase(), required: !!req, visible: vis, value: val, checked: !!e.checked,
          options: e.tagName === 'SELECT' ? [...e.options].map(o => o.text.trim()) : null};
}"""


def fill(page, job, scope='input, textarea, select'):
    """Returns (done, unknown)."""
    done, unknown = [], []
    locs = page.locator(scope)
    radio_groups = {}
    resume_done = False
    for i in range(locs.count()):
        loc = locs.nth(i)
        try:
            d = loc.evaluate(LABEL_JS)
        except Exception:
            continue
        label = d['label']; typ = d['type']
        if typ in ('hidden', 'submit', 'button', 'image', 'reset'): continue
        if typ != 'file' and not d['visible']: continue
        if formfill.NEVER.search(label):
            if d['required']: unknown.append(label)
            continue
        try:
            if typ == 'file':
                key = label + ' ' + d['section']
                if re.search(r'cover', key, re.I):
                    if job.get('cover_txt'): loc.set_input_files(job['cover_txt']); done.append('Cover letter (file)')
                elif job.get('pdf') and not resume_done and (re.search(r'resume|cv\b|curriculum', key, re.I) or i == locs.count() - 1):
                    loc.set_input_files(job['pdf']); done.append('Resume'); resume_done = True
                continue
            if typ == 'radio':
                radio_groups.setdefault(label or f'group{i}', []).append((loc, d)); continue
            if typ == 'checkbox':
                if formfill.CONSENT.search(label) and d['required']: unknown.append(label + ' (legal/consent — yours)')
                continue
            if d['value'] and d['tag'] != 'select': continue
            if formfill.CONSENT.search(label):
                if d['required']: unknown.append(label + ' (legal/consent — yours)')
                continue
            if d['tag'] == 'textarea' and re.search(r'cover letter|message to the hiring|let the company know|why .* interest', label, re.I):
                if job.get('cover_text'): loc.fill(job['cover_text']); done.append('Cover letter / message')
                continue
            val, opt = formfill.rule_for(label)
            if val is None and opt is None:
                if d['required']: unknown.append(label)
                continue
            if d['tag'] == 'select':
                o = formfill.pick(d['options'] or [], opt, val)
                if o: loc.select_option(label=o); done.append(label)
                elif d['required']: unknown.append(label)
            elif d.get('role') == 'combobox':
                o = combobox_pick(page, loc, val, opt)
                if o: done.append(label)
                elif d['required']: unknown.append(label)
            else:
                loc.fill(val); done.append(label)
        except Exception as e:
            if d.get('required'): unknown.append(f'{label} (error: {str(e)[:60]})')
    return done, unknown


def autocomplete_pick(page, loc, text, opt_rx=None, wait=1200):
    """Type into an autocomplete field and click the first matching suggestion (role=option / listbox item)."""
    loc.click(); loc.fill(''); loc.press_sequentially(text, delay=40); page.wait_for_timeout(wait)
    opts = page.locator('[role="option"]')
    texts = [t.strip() for t in opts.all_inner_texts()] if opts.count() else []
    o = formfill.pick(texts, opt_rx, text) if texts else None
    if o:
        opts.nth(texts.index(o)).click(); return o
    if texts:
        opts.first.click(); return texts[0]
    return None


OPT_TEXT_JS = "els => els.map(e => ((e.getRootNode().host || e).textContent || e.textContent || '').replace(/\\s+/g, ' ').trim())"


def combobox_pick(page, loc, val, opt_rx):
    """Open a (shadow) combobox, pick the option matching the rule; type to filter long lists. Returns option text or None."""
    loc.scroll_into_view_if_needed(); loc.click(); page.wait_for_timeout(900)
    ctrl = loc.get_attribute('aria-controls')
    opts = page.locator(f'[id="{ctrl}"] [role="option"]') if ctrl else page.locator('[role="option"]')
    if not opts.count(): opts = page.locator('[role="option"]')
    texts = opts.evaluate_all(OPT_TEXT_JS) if opts.count() else []
    o = formfill.pick(texts, opt_rx, val) if texts else None
    if o is None and val:
        loc.press_sequentially(val.split(' (')[0][:25], delay=30); page.wait_for_timeout(1200)
        texts = opts.evaluate_all(OPT_TEXT_JS) if opts.count() else []
        o = formfill.pick(texts, opt_rx, val) if texts else None
    if o is not None:
        opts.nth(texts.index(o)).click(); page.wait_for_timeout(500); return o
    try:
        loc.fill(''); loc.press('Escape')
    except Exception:
        pass
    return None
