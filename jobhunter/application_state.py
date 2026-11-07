"""Observe application state without clicking, filling, or assuming extraction succeeded."""
import re

IRRELEVANT_FRAME = re.compile(r'textrecruit|jobchat|intercom|livechat|doubleclick|googletag|recaptcha|hcaptcha|analytics', re.I)
ATS_FRAME = re.compile(r'icims|greenhouse|ashbyhq|myworkday|smartrecruiters|taleo|eightfold|lever\.co', re.I)

SCAN_JS = r"""() => {
 const start = document.querySelector('[data-jh-root]') || document;
 const roots = [start];
 const all = [];
 for (let i=0; i<roots.length; i++) for (const e of roots[i].querySelectorAll('*')) {
   all.push(e); if(e.shadowRoot) roots.push(e.shadowRoot);
 }
 const up = e => e.parentElement || e.getRootNode()?.host;
 const text = e => (e?.innerText || e?.textContent || '').replace(/\s+/g,' ').trim();
 const visible = e => { for(let n=e;n;n=up(n)) if(n.nodeType===1 && (n.hidden || getComputedStyle(n).display==='none' || getComputedStyle(n).visibility==='hidden')) return false;
   return !!(e.getClientRects().length && (e.offsetWidth || e.offsetHeight)); };
 const label = e => {
   const root=e.getRootNode();
   const ids=e.getAttribute('aria-labelledby');
   if(ids) { const s=ids.split(/\s+/).map(id=>text(root.getElementById?.(id)||document.getElementById(id))).join(' ').trim(); if(s)return s; }
   if(e.id) { const l=root.querySelector?.('label[for="'+CSS.escape(e.id)+'"]'); if(l)return text(l); }
   const direct=e.getAttribute('label') || e.getAttribute('aria-label');
   if(direct && !/^(search|select|choose)$/i.test(direct)) return direct;
   const own=e.closest('label'); if(own)return text(own);
   for(let p=up(e),i=0;p&&i<7;p=up(p),i++) {
     if(p.tagName==='FORM'||p===document.body)break;
     const a=p.getAttribute('label') || p.getAttribute('aria-label'); if(a)return a;
     const l=p.querySelector?.(':scope > label, :scope > legend, :scope > [class*="label"], :scope > [class*="question-title"]');
     if(l && !l.contains(e))return text(l);
   }
   return direct || e.getAttribute('placeholder') || e.name || e.id || 'Unlabelled required field';
 };
 const controls=all.filter(e=>e.matches('input,textarea,select,[role="combobox"],[role="radio"],[role="checkbox"],button[aria-haspopup="listbox"]'));
 const fields=[], missing=[], invalid=[], groups=new Map();
 const placeholder = s => !s || /^(select|choose|please select|make a selection|search)(\b|\.\.\.)/i.test(s);
 for(const e of controls) {
   const type=(e.type||e.getAttribute('role')||e.tagName).toLowerCase();
   if(e.disabled || ['hidden','submit','button','reset','image'].includes(type) && !e.matches('[role="combobox"],button[aria-haspopup="listbox"]'))continue;
   if(e.getAttribute('aria-hidden')==='true')continue;
   const file=type==='file';
   let host=up(e), hostRequired=false, hostVisible=false;
   for(let i=0;host&&i<5;host=up(host),i++) {
     if(host.tagName==='FORM'||host===document.body)break;
     hostRequired ||= host.hasAttribute?.('required') || host.getAttribute?.('aria-required')==='true';
     hostVisible ||= visible(host);
   }
   if(!visible(e) && !(file && hostVisible))continue;
   let name=label(e), group=e.closest('fieldset,[role="radiogroup"]');
   if(type==='radio' || type==='checkbox' && group?.matches('.checkbox, [class*="checkbox-group"]')) {
     const legend=group?.querySelector('legend,[class*="question-title"]');
     if(legend)name=text(legend);
   }
   const required=!!(e.required || e.getAttribute('aria-required')==='true' || hostRequired || /\*/.test(name));
   let value=String(e.value || '');
   const combo=e.matches('[role="combobox"],button[aria-haspopup="listbox"]');
   if(combo) {
     // Selected chips belong to this field, not the menu or a neighbouring question.
     const c=e.closest('[class*="control"], [data-test*="select"], [data-pebble-select]') || up(e);
     const selected=c?.querySelector('[class*="singleValue"],[class*="single-value"],[class*="multi-value__label"],[aria-selected="true"]');
     if(selected)value=text(selected);
     else if(e.tagName==='BUTTON') value=text(e);
   }
   let empty=file ? !(e.files?.length) : placeholder(value);
   if(e.tagName==='SELECT')empty=!e.value || e.selectedOptions[0]?.disabled || placeholder(text(e.selectedOptions[0]));
   if(type==='checkbox'||type==='radio')empty=!(e.checked || e.getAttribute('aria-checked')==='true');
   if(file && empty) {
     let p=up(e);
     for(let i=0;p&&i<4;p=up(p),i++) {
       if(p.tagName==='FORM'||p===document.body)break;
       if(/\b[^\s/]+\.(pdf|docx?|rtf)\b/i.test(text(p)) && !/this field is required/i.test(text(p))) {empty=false;break;}
     }
   }
   const bad=e.getAttribute('aria-invalid')==='true' || (!file && e.validity && !e.validity.valid);
   fields.push({label:name.slice(0,300),type,required,empty,invalid:!!bad,value:value.slice(0,200),file});
   if((type==='radio'||type==='checkbox' && group?.matches('.checkbox, [class*="checkbox-group"]')) && (group||e.name)) {
     const key=group||('radio:'+e.name); const g=groups.get(key)||{name,required:false,selected:false};
     g.required ||= required;g.selected ||= !empty;groups.set(key,g);continue;
   }
   if(required&&empty)missing.push(name);
   else if(bad)invalid.push(name+' (invalid or not accepted)');
 }
 for(const g of groups.values())if(g.required&&!g.selected)missing.push(g.name);
 // ARIA-less required web components can have no inspectable control (e.g. closed shadow root).
 for(const e of all.filter(e=>e.tagName.includes('-') && (e.hasAttribute('required')||e.getAttribute('aria-required')==='true') && visible(e))) {
   const children=controls.filter(c=>e.contains(c)||e.shadowRoot?.contains(c));
   if(!children.length && !e.shadowRoot && !e.value && !e.getAttribute('value'))missing.push(label(e)+' (control not inspectable)');
 }
 const buttons=all.filter(e=>visible(e)&&e.matches('button,input[type="submit"],[role="button"]'))
   .map(e=>({text:(text(e)||e.value||e.getAttribute('aria-label')||'').trim(),disabled:!!(e.disabled||e.getAttribute('aria-disabled')==='true')}));
 return {fields, missing:[...new Set(missing)], invalid:[...new Set(invalid)], buttons,
   headings:all.filter(e=>e.matches('h1,h2,h3,legend')&&visible(e)).map(text).slice(0,20)};
}"""


def inspect(scope):
    try:
        state = scope.evaluate(SCAN_JS)
        if not isinstance(state, dict) or not isinstance(state.get('fields'), list):
            raise ValueError('invalid field snapshot')
        state['ok'] = True
        return state
    except Exception as error:
        return {'ok': False, 'fields': [], 'missing': ['Form inspection failed: ' + type(error).__name__],
                'invalid': [], 'buttons': [], 'headings': []}


def missing(scope):
    state = inspect(scope)
    return list(dict.fromkeys(state['missing'] + state['invalid']))


def frame_score(frame):
    if IRRELEVANT_FRAME.search(frame.url or ''):
        return -1
    state = inspect(frame)
    if not state['ok']:
        return -1
    fields = state['fields']
    relevant = [f for f in fields if not re.search(r'newsletter|job alert|search jobs', f['label'], re.I)]
    score = len(relevant) + sum(8 for f in relevant if f['file'])
    score += sum(3 for f in relevant if re.search(r'first name|last name|password|e.?mail', f['label'], re.I))
    if ATS_FRAME.search(frame.url or '') and relevant:
        score += 5
    return score


def form_frame(page):
    ranked = [(frame_score(fr), i, fr) for i, fr in enumerate(page.frames)]
    return max(ranked, key=lambda x: (x[0], -x[1]))[2] if ranked else page.main_frame


def submission_issues(scope):
    state = inspect(scope)
    issues = list(dict.fromkeys(state['missing'] + state['invalid']))
    if not state['ok']:
        return issues
    if any(re.fullmatch(r'\s*(next|continue|save (and|&) continue)\s*', b['text'], re.I)
           for b in state['buttons']):
        issues.append('Application has another Next/Continue step')
    if not state['fields'] and not any(re.search(r'review|application summary', h, re.I) for h in state['headings']):
        issues.append('No application fields inspected and no review summary found')
    return list(dict.fromkeys(issues))
