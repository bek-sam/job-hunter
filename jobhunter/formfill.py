"""Shared form-filling core used by every ATS adapter (0 LLM tokens).
fill(frame, job) labels every visible control, answers from the Answer Bank by rule, uploads the resume and cover
letter, never touches consent/legal/password/ID fields, and returns (filled_labels, unknown_required_labels)."""
import re
import applicant as P

LOCATION_RX = r'^(current )?location|where are you (currently )?(located|based)|city.*state'
# (label regex, profile key). Values live in profile.json["answers"] as "value" or ["value", "option regex"]:
# the regex picks the option for select/radio/combobox fields. A key missing from the profile = no rule (NEEDS YOU).
HOME_RX = r'(live|living|reside|residing|located|based) (in|near|within) .{0,60}(' + (P.get('identity', 'home_area_rx') or r'(?!x)x') + ')'
KEYED = [
    (r'first (and|&|/) last name|first,? (middle,? )?(and )?last|full (legal )?name', 'full_name_first_last'),
    (r'^(legal )?first name|given name|^first\b', 'first_name'),
    (r'^(legal )?last name|family name|surname|^last\b', 'last_name'),
    (r'preferred last name', 'last_name'),
    (r'preferred (first )?name', 'preferred_name'),
    (r'^(full |legal )?name\b(?!.*(school|company|employer|reference|referr))', 'full_name'),
    (r'e-?mail', 'email'),
    (r'number at which .{0,40}(text|call|contact)|text(ed|ing)? .{0,40}(phone )?number|sms .{0,20}number', 'sms_phone'),
    (r'country (phone )?code|phone code|dial(ing)? code|calling code', 'phone_country_code'),
    (r'phone (device )?type|type of phone', 'phone_type'),
    (r'phone|mobile', 'phone'),
    (r'linkedin', 'linkedin'),
    (r'github', 'github'),
    (r'website|portfolio|personal (site|url)|other url', 'website'),
    (r'address line 1|street address|^street', 'address_line1'),
    (r'address line 2|^apt|apartment|suite', 'address_line2'),
    (r'\baddress\b', 'address_full'),
    # Oct 20: the owner's answers to questions that sent rows to NEEDS YOU (profile.json holds the values)
    (r'what city do you (live|reside)|city (do you|you) (live|reside) in|city of residence', 'city_of_residence'),
    (r'supports remote work in|remote work .{0,120}(drop ?down|select)|(location|state) you (will|would) (be )?work(ing)? remotely', 'remote_location'),
    (r'(outstanding|current|other|competing|pending|existing) (job |internship )?offers?|have any offers|offers? or deadlines', 'current_offers'),
    (r'(graduate|graduation|finish).{0,20}(from )?high school|high school.{0,25}(graduat|completion)', 'high_school_grad'),
    (r'high ?school (name|attended)|name of (your )?high school|^high school$|^secondary school( name)?$', 'high_school_name'),
    (r'(may|can) (we|\w+) contact (your )?(current|most recent|past|previous|former)\b.{0,25}employers?|contact (your )?(current|past|previous|former) employers?', 'contact_employer'),
    (r'(current|your) (job |career |seniority )?level\b|^job level|^seniority level|^career level', 'job_level'),
    (r'outside (work|employment|activit|business)|advisory (disclosure|role|position|board)', 'outside_work'),
    (r'(been )?(terminated|dismissed|fired|discharged)\b|asked to resign', 'terminated'),
    (r'disciplined by (a|any) regulat|regulatory (authority|body|agency).{0,60}(disciplin|sanction|suspend)', 'regulatory_discipline'),
    (r'gaps? in (your )?employment|employment (history )?gaps?', 'employment_gaps'),
    (r'experience (with|in) (financial )?invest', 'investing_experience'),
    (r'(external )?customers? or clients? in a technical|(client|customer)[- ]facing', 'customer_facing'),
    (r'career fair|talent connect', 'career_fair'),
    (r'whatsapp', 'whatsapp_optin'),
    (r'first[- ]generation (professional|college|student)', 'first_gen_professional'),
    (r'^(oncology research|immune (&|and) inflammatory|cardiovascular)', 'research_area_experience'),
    (r'hands-on in production', 'production_tools'),
    (r'programming language.{0,30}(for|during) (your |the )?interview|interview.{0,40}programming language', 'interview_language'),
    (r'programming languages? (you are|you.re|are you) proficient|proficient in.{0,30}programming language', 'proficient_languages'),
    (r'ideal work location|preferred work (location|arrangement)|^preferred location', 'ideal_work_location'),
    (r'\?\s*if not,? (are you )?(willing|open) to relocate', 'relocate_combo'),
    (r'^city\b', 'city'),
    (r'^county\b|county of residence', 'county'),
    (r'zip|postal', 'zip'),
    (r'(select|choose) the (location|state) you (intend|plan|will) to work from|location you (intend|plan) to work from', 'state'),
    (r'^(?!.*\bcity\b)(state\b|.*province|.*state (in which|where) you (reside|live)|.*state of residence|.*which state do you (live|reside))', 'state'),
    (r'^country\b', 'country'),
    (r'^(current )?location|where are you (currently )?(located|based)|city.*state', 'current_location'),
    # Oct 19: Gallup "Do you currently have temporary work authorization (F-1, OPT, CPT, H-1B...)" was answered Yes by the
    # general work-authorization rule below -> its own rule first
    (r'temporary (work )?authori[sz]ation|currently (have|hold|on) (a |an )?(temporary|f-1|opt|cpt|j-1|tn|h-1b)\b', 'temporary_work_auth'),
    (r'(legally )?authori[sz]ed to work|eligible to work|right to work|work authori[sz]ation', 'authorized_to_work'),
    (r'sponsor', 'needs_sponsorship'),
    (r'^text message updates \(sms\)$', 'sms_updates_exact'),
    (r'(agree|consent|opt.?in|receive).{0,60}(text messag|\bsms\b)|(text messag|\bsms\b).{0,80}(agree|consent|opt.?in)', 'sms_consent'),
    (r'^\s*if (yes|so)\b.*(relocat|location|where)', 'relocation_details'),
    (r'relocat|willing to work (from|at|in) (the |our )?(required|stated|listed|job|office)? ?location|work from the (required|office) location', 'willing_to_relocate'),
    (HOME_RX, 'lives_in_home_area'),
    (r'(do|are) you (currently )?(live|living|reside|residing|located|based) (in|near|within) (the )?.{0,60}(area|region|metro|city|state)', 'lives_in_other_area'),
    # Oct 20 AKASA: "are you currently located in or within commuting distance of the office hub" was answered Yes by
    # the hybrid/commute rule below -> where he LIVES is a fact (No unless HOME_RX above matched his area)
    (r'^(?!.*(willing|able|open) to relocate)(?!.*\bor relocat).*(live|living|located|reside|residing|based)\b.{0,40}commut', 'lives_in_other_area'),
    (r'in[- ]person|on-?site|in (the |our )?office|at (the |our )?[\w ]{0,30}office|hybrid|commut|days (a|per) week', 'onsite_ok'),
    (r'(18|eighteen) years|of legal age|at least 18', 'over_18'),
    (r'drug (screen|test)|background (check|screen)|pre-?employment screening', 'background_check_ok'),
    (r'(family|relative)s?( member)?s? .{0,60}(work|employ|retain)|related to (anyone|an employee)|know anyone (who )?works', 'relatives_at_company'),
    (r'government official|public official|government[- ](owned|controlled)|politically exposed|ability to influence|conflict of interest', 'government_official'),
    (r'non-?compete|employment restriction|restrictive covenant|contract .{0,40}restrict', 'non_compete'),
    (r'(ever been )?(convicted|felony)|criminal (record|conviction|offen)', 'criminal_record'),
    (r'(previously|ever|currently)( been)?( \w+)? (employed|worked) (by|for|at|with)|former (employee|worker)|worked (here|for us) before|(worked|work|working) as an? (contractor|consultant|temp)|contractor (at|for|with)', 'worked_here_before'),
    (r'notice period', 'notice_period'),
    (r'^start date year$', 'edu_start_year'),
    (r'^(end|graduation) date$', 'graduation_date'),
    (r'^end date month$', 'edu_end_month'),
    (r'^end date year$', 'edu_end_year'),
    (r'^start date month$', 'edu_start_month'),
    (r'start date(?! (month|year)$)|available to start|earliest|when can you start|start working|date (you are |you.re )?available', 'start_date'),
    (r'compensation expectations? align|align with (our|the) (posted )?(range|pay)|comfortable with (the|this) (pay|salary) range', 'comp_range_ok'),
    (r'hourly (rate|pay|wage)|per hour|expected hourly', 'hourly_rate'),
    (r'salary|compensation|pay expectation|rate of pay|desired pay|pay rate|hourly rate|expected pay|desired wage', 'salary'),
    (r'how did you (hear|find|learn|discover)|source|referr(al|ed) source|where did you', 'how_heard'),
    (r'(have|hold|completed?|earned|obtained).{0,40}degree.{0,40}(or|and/or)\b.{0,25}(pursuing|working towards|enrolled)|(pursuing|expect(ed|ing)? to (complete|receive|earn)).{0,40}degree', 'degree_completed_or_pursuing'),
    (r'^(do|did) you (have|hold|possess) an? (college|university|bachelor.?s?|undergraduate|4-year|four-year)? ?degree|^have you (completed|earned|obtained|received) (an?|your) (college |university |bachelor.?s? |undergraduate )?degree', 'degree_completed'),
    (r'highest (completed|level of completed|finished) (degree|education)|highest degree (completed|earned|obtained)|highest (level of )?education (completed|attained)', 'highest_completed_degree'),
    (r'high school (diploma|equivalen)|\bged\b|secondary school (diploma|certificate)', 'high_school_diploma'),
    (r'school|university|college|institution', 'school'),
    (r'will you be (actively )?enrolled|enrolled .{0,60}(during|entire|duration|after the internship)|return(ing)? to school|at least one (semester|term) remaining', 'enrolled_after_internship'),
    (r'currently enrolled|are you (a )?(current )?student|enrolled in|still (a )?student', 'currently_enrolled'),
    (r'^(highest )?(level of )?degree\b|degree (type|level|earned|obtained|pursuing)|^education level', 'degree_level'),
    (r'discipline|major|field of study', 'major'),
    (r'gpa', 'gpa'),
    (r'graduation month|month of graduation', 'graduation_month'),
    (r'graduation year|year of graduation', 'graduation_year'),
    (r'graduation (date|year)|expected graduation|grad(uation)? date|when do you graduate', 'graduation_date'),
    (r'current (company|employer)|most recent (company|employer)', 'current_employer'),
    (r'current (job )?title|most recent title', 'current_title'),
    (r'gender', 'gender'),
    (r'hispanic|latino', 'hispanic_latino'),
    (r'race|ethnicit', 'race'),
    (r'veteran', 'veteran'),
    (r'^\s*if (yes|so)\b,? (please )?(provide|give|explain|describe|list|specify|share)', 'if_yes_details'),
    (r'(current|your) (immigration|visa|residency|work authori[sz]ation) status|status in the u\.?s|^citizenship status', 'immigration_status'),
    (r'(active|pending) (immigration|visa|green card) (case|petition|application)|immigration case', 'pending_immigration_case'),
    (r'(u\.?s\.?|united states) citizen,? or (a )?(lawful |legal )?permanent (resident|citizen)|citizen or (green card|permanent resident)', 'citizen_or_lpr'),
    (r'^(are you )?(a )?(u\.?s\.?|united states) citizen\b(?!.{0,6}\bor\b)', 'us_citizen'),
    (r'deemed export', 'deemed_export'),
    (r'\b(cuba|iran|north korea|syria|crimea)\b', 'sanctioned_country'),
    (r'(hold|have|possess)\b.{0,30}\b(active|current)\b.{0,30}clearance|^active (security )?clearance', 'active_clearance'),
    (r'u\.?s\.? person', 'us_person'),
    (r'what is your nationality|^nationality|country of citizenship|citizenship country|what is your citizenship|which countr(y|ies).{0,20}citizen', 'citizenship_country'),
    (r'willing(ness)? to travel|travel (requirement|up to|percentage)|how much.{0,20}travel', 'travel'),
    (r'driver.?s? licen[cs]e', 'drivers_license'),
    (r'(require|need).{0,40}(mode of )?transportation|reliable transportation', 'needs_transportation'),
    (r'(require|need).{0,40}(temporary )?housing|relocation assistance', 'needs_housing'),
    (r'overtime|weekends?|on-?call|shift work|night shifts?', 'overtime_ok'),
    (r'(other|current) (work|job)s? .{0,60}(maintain|keep|continue)|work engagements', 'other_jobs'),
    (r'\b(sat|act|gre) (score|total)|(score|result) on (the )?(sat|act|gre)\b|standardized test', 'test_scores'),
    (r'military (service|status)|served in the (armed forces|military)', 'military_service'),
    (r'(were you|have you been) referred|employee referral|referred by', 'referred'),
    (r'interview transcri|transcription (tool|of (the |your )?interview)|^transcription$|'
     r'\bai\b.{0,60}(note|transcri|record)|(note|transcri).{0,40}\bai\b', 'ai_notetaker_consent'),
    (r'\bage (of )?18|18 (years|or over|or older)|over the age of 18|legal working age', 'age_18_alt'),
    (r'non-?compet|non-?solicit|non-?disclosure.{0,80}(restrict|preclude)|agreement.{0,80}(restrict|preclude|prevent)|restrictions? on compet', 'non_compete_alt'),
    (r'commitments? to (another|other) (employer|organi[sz]ation)|interfere with .{0,40}employment', 'other_commitments'),
    (r'(ever|previously) (worked|been employed) (at|for|with)|worked (at|for) .{0,60}(affiliates?|subsidiar)|former employee of', 'worked_at_affiliate'),
    (r'(ever|previously) applied (for work )?(at|to|with)|applied (to|at) .{0,30} before', 'applied_before'),
    (r'valid (work|residency|residence) permit|legally (permitted|allowed) to work|work permit', 'work_permit'),
    (r'(federal|foreign) government.{0,60}(united nations )?employee|united nations employee|'
     r'government or public institution employment|worked for (a|the) (federal|state|local) government|public sector employment', 'government_employment'),
    (r'licen[cs]es?.{0,60}(withdrawn|revoked|suspended)|disciplinary action', 'license_revoked'),
    (r'(list|please list).{0,40}(licen[cs]es|certifications)', 'certifications'),
    (r'(what|which) degree are you (currently )?(studying|pursuing)|current(ly)? (degree|program) of study', 'current_degree'),
    (r'available for the (pre-?determined |entire |full )?(timeframe|duration|dates)|available for the (entire )?(internship|program)', 'available_full_program'),
    (r'(work|commit to) 40 hours|full-?time.{0,40}40 hours', 'forty_hours'),
    (r'(1st|2nd|3rd|first|second|third) location preference|location preference', 'location_preference'),
    (r'other languages|languages? (do you speak|proficien)', 'other_languages'),
    (r'essential functions .{0,60}(with or without|accommodation)', 'essential_functions'),
    (r'(ever been|previously been) (issued|extended|given) an? (job )?offer', 'offer_before'),
    (r'(ownership|own|financial interest|investment) in (a|any) (company|business|competitor)|ownership .{0,40}same industry', 'business_ownership'),
    (r'transgender', 'transgender'),
    (r'sexual orientation', 'sexual_orientation'),
    (r'gender identity', 'gender_identity'),
    (r'disabilit', 'disability'),
    (r'pronoun', 'pronouns'),
]
RULES = P.rules(KEYED)
NEVER = re.compile(r'password|captcha|verification code|one[- ]time|ssn|social security|date of birth|birth ?date|'
                   r'driver.?s licen|passport (number|no)|bank (account|routing|details|name)|routing number|account number|credit card|signature|sign here', re.I)   # 'bank' alone hit 'World Bank' (GM, Oct 13)
# fields that ask for a government ID number / document / ID verification (not yes/no questions about having one)
GOV_ID = re.compile(r'social security|\bssn\b|last (4|four) (digits )?of (your )?(ssn|social)|passport (number|no\b|#)|'
                    r'driver.?s? licen[cs]e (number|no\b|#)|(state|national|government)[- ]?(issued )?(photo )?id(entification)? (number|card|document|upload)|'
                    r'government[- ]issued (photo )?id|upload .{0,30}(photo )?(id|identification) (card|document)|'
                    r'identity verification|verify your identity|id verification|tax (id|identification) number|\bitin\b|alien (registration )?number|'
                    r'green card number|\ba-number\b|uscis number', re.I)
# a form question that makes it a clearance role (CHAOS / Bespoke Oct 13): he can't hold a clearance -> the row is Closed
CLEARANCE_ROLE = re.compile(r'(obtain|maintain|eligible (for|to obtain)|able to obtain|qualify for).{0,40}(security )?clearance|polygraph|poly(graph)? type|'
                            r'ts/sci|top secret|\bitar\b', re.I)
NON_US_ROLE = re.compile(r'(eligible|authori[sz]ed|entitled|right|permit(ted)?) to (live and )?work in (canada|the uk|the united kingdom|ireland|germany|'
                         r'india|australia|the eu|europe|the netherlands|france|spain|poland|singapore|israel|mexico|brazil)|uk right to work|'
                         r'(canadian|uk|british|eu) (work permit|citizen(ship)? or)', re.I)
CONSENT = re.compile(r'i (agree|acknowledge|certify|consent|attest|understand|confirm|have read)|code of conduct|terms|privacy|policy|consent|attest|certif|arbitrat|agreement|acknowledg|\be-?sign|confidentialit|j.ai lu|accepte|aviso de privacidad|acepto', re.I)   # +FR/ES (Unity Oct 13)   # \b: 'Object Oriented Design' is not e-sign

LABEL_JS = r"""
() => {
  const vis = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length) && getComputedStyle(e).visibility !== 'hidden';
  const txt = e => (e ? (e.innerText || e.textContent || '') : '').replace(/\s+/g, ' ').trim();
  const labelOf = el => {
    let t = el.getAttribute('aria-label') || '';
    const lb = el.getAttribute('aria-labelledby');
    if (lb) t = lb.split(' ').map(id => txt(document.getElementById(id))).join(' ');
    if (!t && el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) t = txt(l); }
    if (!t) { const l = el.closest('label'); if (l) t = txt(l); }
    if (!t) { const f = el.closest('fieldset'); if (f && f.querySelector('legend')) t = txt(f.querySelector('legend')); }
    if (!t) { let p = el.parentElement; for (let i = 0; i < 4 && p && !t; i++, p = p.parentElement) {
      const l = p.querySelector('label, legend, .application-label, [class*="label"], [class*="question"]'); if (l && !l.contains(el)) t = txt(l); } }
    if (!t) { for (let p = el.parentElement, i = 0; p && i < 10 && !t; i++, p = p.parentElement) {   // TikTok ud-formily: label ~7 levels up
      if (!/form-?item|formItem|FormItem/.test(p.className || '') && !p.hasAttribute('data-form-field-id')) continue;
      const l = p.querySelector('[class*="item-label"], label'); if (l && !l.contains(el)) t = txt(l); } }
    return (t || el.getAttribute('placeholder') || el.name || '').slice(0, 300);
  };
  window.__jhLabel = labelOf;
  const out = []; let n = 0; const radios = {};
  for (const el of document.querySelectorAll('input, textarea, select')) {
    const type = (el.type || el.tagName).toLowerCase();
    if (['hidden', 'submit', 'button', 'image', 'reset'].includes(type)) continue;
    if (type === 'search' && el.getAttribute('role') !== 'combobox') continue;   // TikTok ud__select = search-type combobox
    if (type !== 'file' && !vis(el)) continue;
    if (el.tabIndex === -1 && el.tagName === 'INPUT' && type !== 'file' && type !== 'radio' && type !== 'checkbox' && el.getAttribute('role') !== 'combobox') continue;  // react-select shadow input
    if (el.getAttribute('aria-hidden') === 'true') continue;
    const entry = el.closest('[class*="field-entry"], [class*="fieldEntry"], fieldset');
    const title = entry && entry.querySelector('[class*="question-title"], legend');
    const req = el.required || el.getAttribute('aria-required') === 'true' || /\*\s*$/.test(labelOf(el))
      || !!(title && (/required/i.test(title.className) || title.querySelector('[class*="required" i]')));
    if (type === 'radio') {
      // LinkedIn (Oct 2026): <div role="radio" aria-label="<question>">Yes</div> around an unlabeled input -> click the div
      const rr = el.closest('[role="radio"]');
      if (rr && /\.(pdf|docx?)$/i.test(rr.getAttribute('aria-label') || '')) continue;   // resume picker: the adapter handles it
      if (rr && rr.getAttribute('aria-label')) {
        const g = 'rr:' + (el.name || rr.getAttribute('aria-label'));
        rr.setAttribute('data-jh', String(++n));
        (radios[g] = radios[g] || {kind: 'buttons', label: rr.getAttribute('aria-label'), required: true, options: []})
          .options.push({n, text: txt(rr), checked: rr.getAttribute('aria-checked') === 'true' || el.checked});
        continue;
      }
      const g = el.name || labelOf(el); const f = el.closest('fieldset');
      let q = f && f.querySelector('legend') ? txt(f.querySelector('legend')) : '';
      if (!q) { let p = el.parentElement; for (let i = 0; i < 5 && p && !q; i++, p = p.parentElement) {
        const l = p.querySelector('legend, .application-label, [class*="question"], [class*="label"]:not(label)'); if (l) q = txt(l); } }
      // texting opt-in under a phone field (Ashby "communicationConsent", Zip Oct 12): own label, answered No by a rule
      if (/communication|sms|text.?message/i.test(el.name || '') || /text messages?/i.test(labelOf(el))) q = 'Text message updates (SMS)';
      el.setAttribute('data-jh', String(++n));
      (radios[g] = radios[g] || {kind: 'radio', label: q, required: req || /\*\s*$/.test(q), options: []}).options.push({n, text: labelOf(el), checked: el.checked});
      continue;
    }
    // checkbox groups = one question. Greenhouse: <fieldset class="checkbox"><legend> + inputs named "question_N[]";
    // Ashby: <fieldset class="...checkbox-group"> + a question-title label, inputs named after their option text (so
    // names can't be used there: "Other" repeats across questions)
    const cg = type === 'checkbox' && (el.closest('fieldset[class*="checkbox-group"], fieldset.checkbox')
      || (/\[\]$/.test(el.name || '') && document.querySelectorAll(`input[type="checkbox"][name="${CSS.escape(el.name)}"]`).length > 1 && el.closest('fieldset')));
    if (cg && cg.querySelectorAll('input[type="checkbox"]').length > 1) {
      // Twilio/Gallup/PMG Oct 13: each option was read as its own required checkbox -> "Careers Website (still empty)" x9
      const ql = cg.querySelector('legend, [class*="question-title"]');
      const q = ql ? txt(ql) : '';
      if (!cg.dataset.jhg) cg.dataset.jhg = String(Object.keys(radios).length + 1);
      const g = 'cb:' + cg.dataset.jhg;
      el.setAttribute('data-jh', String(++n));
      (radios[g] = radios[g] || {kind: 'checks', type: 'checkbox-group', label: q, options: [],
        required: req || cg.getAttribute('aria-required') === 'true' || /\*\s*$/.test(q) || !!(ql && /required/i.test(ql.className))})
        .options.push({n, text: labelOf(el), checked: el.checked});
      continue;
    }
    el.setAttribute('data-jh', String(++n));
    const d = {n, kind: el.getAttribute('role') === 'combobox' ? 'combobox' : el.tagName.toLowerCase(), type,
               label: labelOf(el), required: !!req, value: el.value || '', id: el.id || '', name: el.name || '',
               maxlength: el.maxLength > 0 ? el.maxLength : null};
    if (d.kind === 'combobox') { const c = el.closest('[class*="select__control"]') || el.closest('[class*="-control"]') || el.closest('[class*="__control"]')
        || (el.parentElement && (el.parentElement.closest('.ud__select__selector') || el.parentElement.closest('[class*="select-selector"]')))
        || el.parentElement && el.parentElement.parentElement;
      const sv = c && c.querySelector('[class*="single-value"], [class*="singleValue"], [class*="selector__selectItem"], [class*="selector__item"], [class*="selection-item"]');
      // multi-selects show chips, not a single value (Neuralink "How did you hear" Oct 12: chip not seen -> re-picked -> toggled off)
      const mv = c ? [...c.querySelectorAll('[class*="multi-value__label"], [class*="multiValue"] > div:first-child, [class*="multi-value"]:not([class*="multi-value__"]), '
        + '[class*="selector__tag"], [class*="select__tag"], [class*="selection-overflow-item"]:not([class*="suffix"]) [class*="item-content"]')] : [];
      d.value = sv ? txt(sv) : (mv.length ? txt(mv[0]) : (el.value || '')); }   // Ashby autocomplete: value is in the input
    if (el.tagName === 'SELECT') d.options = [...el.options].map(o => o.text.trim());
    out.push(d);
  }
  for (const g of Object.values(radios)) out.push(g);
  // Ashby-style Yes/No button pairs (any nesting)
  const seenBox = new Set();
  for (const yes of [...document.querySelectorAll('button')].filter(b => vis(b) && /^yes$/i.test(txt(b)))) {
    let box = yes.parentElement, no = null;
    for (let i = 0; i < 3 && box && !no; i++, box = box.parentElement)
      no = [...box.querySelectorAll('button')].find(b => b !== yes && /^no$/i.test(txt(b)));
    if (!no || seenBox.has(box)) continue; seenBox.add(box);
    // the question's own block: climb while the container holds only THIS Yes button (stop before the whole form)
    let q = '', p = box, ql = null;
    for (let i = 0; i < 6 && p && !q; i++, p = p.parentElement) {
      if ([...p.querySelectorAll('button')].filter(b => /^yes$/i.test(txt(b))).length > 1) break;
      const l = p.querySelector('label, legend, [class*="question-title"], [class*="label"]'); if (l && !l.contains(yes)) { q = txt(l); ql = l; } }
    const qreq = /\*\s*$/.test(q) || !!(ql && (/required/i.test(ql.className) || ql.querySelector('[class*="required" i]')));
    const pressed = /active|selected|checked/i.test(yes.className + no.className) || yes.getAttribute('aria-pressed') === 'true' || no.getAttribute('aria-pressed') === 'true';
    yes.setAttribute('data-jh', String(++n)); no.setAttribute('data-jh', String(++n));
    out.push({kind: 'buttons', label: q, required: qreq, options: [{n: n - 1, text: 'Yes', checked: pressed}, {n, text: 'No', checked: pressed}]});
  }
  return out;
}
"""


LONG_OK = re.compile(r'supports remote work|offers? or deadlines|gaps? in (your )?employment|if not,? (are you )?(willing|open) to relocate|intend to work from|plan to work from|nationality|cuba|iran|north korea|syria|crimea|deemed export|clearance|citizen|united nations|essential functions|issued an offer|ownership|non-?compet|preclude|restrict|commitments|worked at|worked for|applied for work|work permit|residency permit|public institution|licen[cs]e|location preference|languages|immigration|visa status|u\.?s\.? person|citizenship|travel|transportation|housing|criminal|convicted|\bai\b|government official|ability to influence|conflict of interest|text messag|\bsms\b|sponsor|authori[sz]ed to work|eligible to work|relocat|required location|veteran|sexual orientation|disabilit|hispanic|race|ethnicit|gender|'
                     r'in[- ]person|on-?site|hybrid|(18|eighteen) years|previously|former employee|start date|earliest|'
                     r'drug|background (check|screen)|pre-?employment|family|relative|non-?compete|restriction|enrolled|semester', re.I)


AGREE = re.compile(r'^\s*(yes\b|i (agree|accept|acknowledge|consent|understand|certify|have read|will read|will comply|confirm)|agree|accept|acknowledged?|consent|confirm)', re.I)


def open_combo(f, loc):
    """Open a dropdown: normal click; covered/readonly inputs (TikTok ud__select under a sticky footer) -> its selector box,
    then a forced click."""
    try: loc.scroll_into_view_if_needed(timeout=3000)
    except Exception: pass
    try:
        loc.click(timeout=4000)
    except Exception:
        try: loc.evaluate("e => (e.closest('[class*=\"selector\"], [class*=\"control\"]') || e).scrollIntoView({block: 'center'})"); loc.click(timeout=4000)
        except Exception: loc.click(force=True, timeout=4000)
    f.wait_for_timeout(600)


def options_of(f, loc):
    """Options of THIS open dropdown only (aria-controls/aria-owns listbox), not every [role=option] on the page."""
    try:
        lid = loc.get_attribute('aria-controls') or loc.get_attribute('aria-owns')
        if lid:
            o = f.locator(f'[id="{lid}"] [role="option"]')
            if o.count(): return o
    except Exception:
        pass
    vis = f.locator('[role="listbox"]:visible [role="option"]')
    if vis.count(): return vis
    vis = f.locator('[role="option"]:visible')
    if vis.count(): return vis
    # option lists without ARIA roles (TikTok ud__select, antd/rc-select, MUI menus)
    return f.locator('[class*="select__list__item"]:visible:not([class*="__check"]), .ant-select-item-option:visible, '
                     '[class*="select-item-option"]:visible, [class*="dropdown"] li:visible, [class*="menu"] [class*="option"]:visible')


# owner, Oct 20 2026: may type his full name where a form asks to show agreement that way (Rubrik NDA). Not a signature
# field, initials or date: NEVER above still blocks 'signature' / 'sign here'.
TYPED_NAME = re.compile(r'(indicate|confirm|signify|show) (your )?(agreement|acceptance|consent).{0,40}typing your (full )?(legal )?name|'
                        r'typ(e|ing) your (full )?(legal )?name.{0,60}(agree|accept|acknowledg|consent)', re.I)


def consent_on():
    """AUTO_CONSENT=1 in .env (authorized by the owner, Oct 12 2026): accept terms/privacy/arbitration items."""
    import os
    return os.environ.get('AUTO_CONSENT') == '1'


def rule_for(label):
    l = re.sub(r'\s*\((required|optional)\)\s*$', '', (label or '').strip().lower().rstrip('*').strip()).rstrip('*').strip()
    if len(l) > 120 and not LONG_OK.search(l):
        return None, None   # long essay-style labels: keyword rules misfire (e.g. "GitHub PRs" essay) -> answer step
    for rx, val, opt in RULES:
        if re.search(rx, l): return val, opt
    return None, None


def amount(val, hourly=False):
    """First money amount in an answer: '$110k–$130k, negotiable' -> 110000; '$30–$45 per hour' -> 30."""
    m = re.search(r'\$?\s*(\d[\d,]*(?:\.\d+)?)\s*(k\b)?', str(val or ''), re.I)
    if not m: return None
    n = float(m.group(1).replace(',', '')) * (1000 if m.group(2) else 1)
    return n


def pick_range(options, amt):
    """Option whose money range holds `amt` ('$40,000 - $49,999', '$100,000+', 'Under $50k', '$110k-$130k')."""
    if amt is None: return None
    for o in options:
        t = (o['text'] if isinstance(o, dict) else o).replace(',', '')
        nums = [float(a) * (1000 if k else 1) for a, k in re.findall(r'(\d+(?:\.\d+)?)\s*(k\b)?', t, re.I)]
        if not nums: continue
        if len(nums) >= 2: lo, hi = nums[0], nums[1]
        elif re.search(r'\+|or more|and (above|up)|above|over|more than', t, re.I): lo, hi = nums[0], float('inf')
        elif re.search(r'under|less than|below|up to', t, re.I): lo, hi = 0, nums[0]
        else: continue
        if lo <= amt <= hi: return o
    return None


FALLBACK = [   # when the Answer Bank value matches no option (owner, Oct 19)
    (r'work authori[sz]ation|authori[sz]ed to work', [r'authori[sz]ed to work (for any|in the|without)|do not (require|need) (visa )?sponsor|without (visa )?sponsor']),
    (r'(current|former).{0,20}employee|(ever|previously) (worked|been employed)|worked (for|at) .{0,30} before', [r'^never\b|have never|^no\b|not (a )?(current|former)']),
    (r'\bstate\b|region in which you', [r'another state|other (us |u\.s\. )?state|^other\b']),
    (r'how did you (hear|find|learn)|where did you (hear|find|see)|source', [r'linkedin', r'job ?board|online|website|career', r'^other\b']),
    (r'clearance', [r'^none\b|^no\b|no (active )?clearance|not applicable|^n/?a\b', r'do not wish|prefer not|decline']),
    (r'\b(sat|act|gre|gmat|lsat)\b|standardized test', [r"did ?n.?o?t take|not taken|have not taken|do not recall|^n/?a\b|not applicable|^none\b"]),
]


TYPE_FALLBACK = [(['LinkedIn', 'Website', 'Career', 'Job board', 'Online', 'Other'], r'how did you (hear|find|learn|discover)|where did you (hear|find|see)')]


def choose(label, options, opt_rx, val):
    """pick() by the Answer Bank; then a money range for salary/rate questions; then FALLBACK option patterns."""
    l = re.sub(r'[*:\s]+$', '', (label or '').lower()).strip()
    texts0 = [(x['text'] if isinstance(x, dict) else x).strip() for x in options]
    if re.match(r'^(start|end|graduation) date$', l) and options:   # paired Month / Year selects of a school entry (Amplify)
        start = l.startswith('start')
        if sum(t.lower()[:3] in MONTHS for t in texts0) >= 6:
            want = (P.answer('edu_start_month') or ['September'])[0] if start else (P.answer('edu_end_month') or ['December'])[0]
            o = pick(options, r'^' + re.escape(str(want)[:3]), None)
            if o: return o
        if sum(bool(re.match(r'^(19|20)\d\d$', t)) for t in texts0) >= 4:
            want = (P.answer('edu_start_year') or ['2023'])[0] if start else (P.answer('edu_end_year') or ['2026'])[0]
            o = pick(options, r'^' + re.escape(str(want)) + r'$', None)
            if o: return o
    o = pick(options, opt_rx, val) if (val or opt_rx) else None
    if o or not options: return o
    if re.search(r'salary|compensation|pay|rate|wage', l):
        key = 'hourly_rate' if re.search(r'hour', l) else 'salary'
        a = P.answer(key)
        o = pick_range(options, amount(a[0]) if a else None)
        if o: return o
    texts = [(x['text'] if isinstance(x, dict) else x).strip() for x in options]
    real = [t for t in texts if t and not re.match(r'^(select|choose|please select)\b|^-+$', t, re.I)]
    if consent_on() and len(real) == 1 and AGREE.search(real[0]):   # single "I acknowledge the above policies" (Waymo)
        return options[texts.index(real[0])]
    for rx, prefs in FALLBACK:
        if re.search(rx, l):
            for pr in prefs:
                o = pick(options, pr, None)
                if o: return o
    return None


def pick(options, opt_rx, val):
    rx = re.compile(opt_rx or re.escape(val or '§'), re.I)
    for o in options:
        t = o['text'] if isinstance(o, dict) else o
        if rx.search(t.strip()): return o
    return None


ESSAY = re.compile(r'\bwhy\b|tell us|describe|explain|what (excites|interests|draws|motivates)|interest(ed)? in|anything else|additional information|share (a|an|more)', re.I)


def fill_files(page_or_frame, resume_pdf, cover_txt, cover_text, pending=None):
    """Fill one form; a second pass catches fields that appear after earlier answers (e.g. race after Hispanic=No).
    Fields the rules can't answer are appended to `pending` (descriptors) for the answer step."""
    p1 = []
    done, unknown = fill_pass(page_or_frame, resume_pdf, cover_txt, cover_text, p1)
    page_or_frame.wait_for_timeout(800)
    p2 = []
    d2, u2 = fill_pass(page_or_frame, None, None, cover_text, p2)
    done += [x for x in d2 if x not in done]
    if pending is not None: pending.extend(p2)   # second pass sees the final set of still-empty fields
    return done, [u for u in u2 if u not in done] if u2 else []


def same_field(f, d):
    """True if the element tagged data-jh=n still carries the label we planned for (React may have re-used the node)."""
    try:
        now = f.evaluate("(n) => { const e = document.querySelector('[data-jh=\"' + n + '\"]'); "
                         "return e && window.__jhLabel ? window.__jhLabel(e) : null; }", d['n'])
    except Exception:
        return True
    if now is None: return False
    norm = lambda x: re.sub(r'\W+', '', (x or '').lower())[:60]
    return norm(now) == norm(d.get('label'))


APPLIED_HERE = [False]   # our own data/submissions.jsonl has an application to this company (set by fill())
APPLIED_RX = re.compile(r'(completed|submitted) an application|previously applied|applied (to|at|for|with) .{0,60}(before|previously|in the (past|last))|'
                        r'(ever|previously) applied', re.I)
FORCE = set()   # labels to re-enter even though the DOM shows a value (Ashby "Missing entry for required field: Email")


def _forced(label):
    n = re.sub(r'\W+', '', (label or '').lower())[:60]
    return bool(n) and any(m and (m in n or n in m) for m in FORCE)


def tick(f, loc):
    """Tick a checkbox so the app's state sees it: check(), then a click on its <label> if it didn't stick
    (Wikimedia "I certify..." Oct 20 stayed empty after a forced check on a visually hidden input)."""
    # a real click on the <label> first: Ashby keeps a forced check() in the DOM only, and the server then says
    # "Missing entry" (Commure Oct 20); force-check only when there is no clickable label
    try:
        if loc.is_checked(): return True
        i = loc.get_attribute('id')
        lab = f.locator(f'label[for="{i}"]').first if i else loc.locator('xpath=ancestor::label[1]')
        if lab.count() and lab.is_visible():
            lab.click(timeout=4000)
            if loc.is_checked(): return True
    except Exception:
        pass
    try: loc.check(force=True)
    except Exception:
        try: loc.click(force=True)
        except Exception: pass
    try: return loc.is_checked()
    except Exception: return False


def fill_pass(page_or_frame, resume_pdf, cover_txt, cover_text, pending=None):
    f = page_or_frame
    fields = f.evaluate(LABEL_JS)
    done, unknown = [], []
    pend = pending if pending is not None else []
    for d in fields:
        label = d.get('label') or ''
        if GOV_ID.search(label):   # Oct 13 (owner): a form asking for any government ID -> cancel the application
            unknown.append('GOV-ID asked: ' + label[:120]); continue
        if CLEARANCE_ROLE.search(label):   # owner rule: clearance/ITAR roles are blocked -> apply_prep closes the row
            unknown.append('INELIGIBLE (clearance role): ' + label[:120]); continue
        if NON_US_ROLE.search(label):   # owner rule: non-US roles are blocked (Solink "eligible to work in Canada", Oct 20)
            unknown.append('INELIGIBLE (non-US role): ' + label[:120]); continue
        if NEVER.search(label):
            if d.get('required'): unknown.append(label)
            continue
        try:
            if d['kind'] == 'checks':   # checkbox group = one question: tick ONE option
                if any(o.get('checked') for o in d['options']) and not _forced(label): continue
                o = None
                if CONSENT.search(label) and consent_on():
                    o = next((x for x in d['options'] if AGREE.search(x['text'])), None) or (d['options'][0] if len(d['options']) == 1 else None)
                if not o:
                    val, opt = rule_for(label)
                    o = choose(label, d['options'], opt, val)
                if o:
                    lo = f.locator(f'[data-jh="{o["n"]}"]').first
                    if _forced(label):
                        try: lo.evaluate("e => { e.checked = false; }")
                        except Exception: pass
                    tick(f, lo)
                    if opt and re.search(r'select all|all that apply|mark all', label, re.I):   # IMC / n8n multi-select (Oct 20)
                        for x in d['options']:
                            if x is not o and not x.get('checked') and re.search(opt, x['text'].strip(), re.I):
                                tick(f, f.locator(f'[data-jh="{x["n"]}"]').first)
                    done.append(label)
                elif d.get('required'): unknown.append(label); pend.append(d)
                continue
            if d['kind'] in ('radio', 'buttons'):
                if any(o.get('checked') for o in d['options']) and not _forced(label): continue
                if CONSENT.search(label) and consent_on():
                    o = next((x for x in d['options'] if AGREE.search(x['text'])), None)
                    if o:
                        lo = f.locator(f'[data-jh="{o["n"]}"]').first
                        lo.check(force=True) if d['kind'] == 'radio' else lo.click()
                        done.append(label + ' (consent)'); continue
                val, opt = rule_for(label)
                if APPLIED_HERE[0] and APPLIED_RX.search(label): val, opt = 'Yes', r'^yes\b'
                o = choose(label, d['options'], opt, val)
                if o and _forced(label) and d['kind'] == 'radio':   # React ignores a click on an already-checked radio
                    try: f.locator(f'[data-jh="{o["n"]}"]').first.evaluate("e => { e.checked = false; }")
                    except Exception: pass
                if o: f.locator(f'[data-jh="{o["n"]}"]').first.check(force=True) if d['kind'] == 'radio' \
                    else f.locator(f'[data-jh="{o["n"]}"]').first.click(); done.append(label)
                elif d.get('required'): unknown.append(label); pend.append(d)
                continue
            loc = f.locator(f'[data-jh="{d["n"]}"]').first
            if d['type'] not in ('file', 'radio') and d['kind'] not in ('buttons',) and not same_field(f, d):
                continue   # node was re-used for another question after a re-render; the second pass re-labels it
            if d['type'] == 'file':
                key = ' '.join([label, d.get('id', ''), d.get('name', '')])
                if re.search(r'cover', key, re.I):
                    if cover_txt: loc.set_input_files(cover_txt); done.append('Cover letter (file)')
                    continue
                if re.search(r'resume|cv\b|curriculum', key, re.I) and resume_pdf:
                    loc.set_input_files(resume_pdf); done.append('Resume')
                continue
            if d['type'] == 'checkbox':
                if CONSENT.search(label):
                    if consent_on():
                        tick(f, loc)
                        done.append(label + ' (consent)')
                    elif d.get('required'): unknown.append(label + ' (consent box — yours to tick)')
                    continue
                val, opt = rule_for(label)   # a lone Yes-checkbox question ("Still Student?" Amplify Oct 19)
                if val and re.match(r'^yes\b', str(val), re.I) and (d.get('required') or re.search(r'\?\s*\*?$', label)):
                    tick(f, loc)
                    done.append(label)
                continue
            if d.get('value') and d['kind'] != 'select' and not _forced(label): continue        # already filled (e.g. by resume parse)
            if CONSENT.search(label) and not (d['kind'] == 'input' and re.search(r'number|phone', label, re.I)):
                if consent_on() and d['kind'] == 'select':
                    o = next((x for x in d.get('options') or [] if AGREE.search(x)), None)
                    if o: loc.select_option(label=o); done.append(label + ' (consent)'); continue
                if consent_on() and d['kind'] == 'combobox':
                    loc.scroll_into_view_if_needed(); loc.click(); f.wait_for_timeout(500)
                    opts = options_of(f, loc); texts = [t.strip() for t in opts.all_inner_texts()]
                    o = next((t for t in texts if AGREE.search(t)), None)
                    if o: opts.nth(texts.index(o)).click(); done.append(label + ' (consent)'); continue
                    loc.press('Escape')
                # a required free-text box whose label is only a privacy/terms NOTICE (Vestmark on LinkedIn, Oct 13):
                # AUTO_CONSENT acknowledges it. Never when it asks for a name/initials/signature/date (those stay his).
                if consent_on() and d['kind'] in ('input', 'textarea') and len(label) > 150 \
                        and not re.search(r'\b(full |legal )?name\b|initials|signature|sign here|\bdate\b|type (your|the)', label[-250:], re.I):
                    loc.fill('I acknowledge'); done.append(label[:60] + '… (acknowledged)'); continue
                if consent_on() and d['kind'] in ('input', 'textarea') and TYPED_NAME.search(label) and P.answer('typed_name_agreement'):
                    loc.fill(P.answer('typed_name_agreement')[0]); done.append(label[:60] + '… (typed name)'); continue
                if d.get('required'): unknown.append(label + ' (legal/consent — yours)')
                continue
            if re.search(r'cover letter', label, re.I) and d['kind'] == 'textarea':
                loc.fill(cover_text); done.append('Cover letter'); continue
            val, opt = rule_for(label)
            if APPLIED_HERE[0] and APPLIED_RX.search(label):   # Gallup Oct 20: we applied there earlier tonight -> truthful Yes
                val, opt = 'Yes', r'^yes\b'
            if val is None and opt is None and d['kind'] == 'select':
                o = choose(label, d.get('options') or [], None, None)   # FALLBACK patterns / single acknowledgement option
                if o: loc.select_option(label=o); done.append(label); continue
            if val is None and opt is None:
                if d.get('required'): unknown.append(label); pend.append(d)
                elif d['kind'] == 'textarea' and ESSAY.search(label): pend.append(d)   # optional essays: answer like a strong applicant
                continue
            if d['kind'] == 'select':
                o = choose(label, d['options'], opt, val)
                if o: loc.select_option(label=o); done.append(label)
                elif d.get('required'): unknown.append(label); pend.append(d)
            elif d['kind'] == 'combobox':
                open_combo(f, loc)
                opts = options_of(f, loc)
                texts = [t.strip() for t in opts.all_inner_texts()]
                o = choose(label, texts, opt, val) if texts else None
                if not o:  # long lists (country, school): type to search; async lists need time to load
                    q = f'{P.CITY}, {P.STATE}' if re.search(LOCATION_RX, label, re.I) else val.split(' (')[0][:30]   # place pickers want the state name
                    loc.press_sequentially(q, delay=20)
                    for _ in range(5):
                        f.wait_for_timeout(600)
                        opts = options_of(f, loc); texts = [t.strip() for t in opts.all_inner_texts()]   # list type can change once results load
                        o = pick(texts, opt, val)
                        if o: break
                if not o:   # type-to-search lists (Ashby "How did you hear": "Start typing...") -> short search words
                    for q, rx in TYPE_FALLBACK:
                        if not re.search(rx, label, re.I): continue
                        for word in q:
                            loc.fill(''); loc.press_sequentially(word, delay=20)
                            for _ in range(4):
                                f.wait_for_timeout(600)
                                opts = options_of(f, loc); texts = [t.strip() for t in opts.all_inner_texts()]
                                o = pick(texts, re.escape(word), None) or (texts[0] if len(texts) == 1 and word == 'Other' else None)
                                if o: break
                            if o: break
                        break
                if not o and re.search(r'school|university|college|institution', label, re.I) and not re.search(r'high school', label, re.I):
                    # school not in the site's list (Hasbro Oct 12: the school was missing) -> 'Other'
                    loc.fill(''); loc.press_sequentially('Other', delay=20)
                    for _ in range(5):
                        f.wait_for_timeout(600)
                        opts = options_of(f, loc); texts = [t.strip() for t in opts.all_inner_texts()]   # list type can change once results load
                        o = pick(texts, r'^other\b', None)
                        if o: break
                if not o and re.search(r'start date|available to start|when can you start|start working', label, re.I):
                    # month pickers that begin after Dec 2026 (Zip "ideal start date": January 2027 ...): earliest start is
                    # Dec 30 2026, so January 2027 is the truthful nearest option
                    for q, rx in (('December 2026', r'^dec(ember)?\b.*2026'), ('January 2027', r'^jan(uary)?\b.*2027')):
                        loc.fill(''); loc.press_sequentially(q, delay=20)
                        for _ in range(4):
                            f.wait_for_timeout(600)
                            opts = options_of(f, loc); texts = [t.strip() for t in opts.all_inner_texts()]
                            o = pick(texts, rx, None)
                            if o: break
                        if o: break
                if not o and re.search(LOCATION_RX, label, re.I):
                    # country-only location pickers (Ashby "Location: country you're residing in", ElevenLabs Oct 12)
                    loc.fill(''); loc.press_sequentially('United States', delay=20)
                    for _ in range(5):
                        f.wait_for_timeout(600)
                        opts = options_of(f, loc); texts = [t.strip() for t in opts.all_inner_texts()]   # list type can change once results load
                        o = pick(texts, r'^(united states( of america)?|usa?)$', None)   # the country itself, never 'United States Air Force Academy'
                        if o: break
                if o: opts.nth(texts.index(o)).click(); done.append(label)
                else:
                    loc.press('Escape')
                    if d.get('required'): unknown.append(label); pend.append(d)
            else:
                if (loc.get_attribute('type') or '').lower() == 'date':   # <input type=date> takes YYYY-MM-DD only (Deloitte)
                    val = iso_date(val)
                    if val is None:
                        if d.get('required'): unknown.append(label)
                        continue
                if (loc.get_attribute('type') or '').lower() == 'number':
                    val = numeric(val, label)   # Greenhouse "Start date year" etc. only take digits (Oct 13: TerraClear)
                    if val is None:
                        if d.get('required'): unknown.append(label)
                        continue
                if _forced(label):   # retype so React sees real key events
                    loc.fill(''); loc.press_sequentially(str(val), delay=15)
                else:
                    loc.fill(val)
                done.append(label)
        except Exception as e:
            if d.get('required'): unknown.append(f'{label} (error: {str(e)[:60]})')
    return done, unknown


MONTHS = {m: i for i, m in enumerate(['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'], 1)}


def iso_date(val):
    """'December 30, 2026' / '12/30/2026' / 'December 2026' -> '2026-12-30' (day 1 when only a month is given)."""
    v = str(val)
    m = re.search(r'\b(\d{4})-(\d{2})-(\d{2})\b', v)
    if m: return m.group(0)
    m = re.search(r'\b(\d{1,2})/(\d{1,2})/(\d{4})\b', v)
    if m: return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    mon = next((n for k, n in MONTHS.items() if re.search(rf'\b{k}', v.lower())), None)
    y = re.search(r'\b(?:19|20)\d{2}\b', v); d = re.search(r'\b([0-3]?\d)\b,', v)
    if mon and y: return f"{y.group(0)}-{mon:02d}-{int(d.group(1)) if d else 1:02d}"
    return None


def numeric(val, label):
    """The number a digits-only box wants from a text answer: 'December 30, 2026' -> year 2026 / month 12 / day 30."""
    v, lab = str(val), (label or '').lower()
    if re.search(r'salary|compensation|pay|rate|wage', lab):
        a = amount(v)
        return str(int(a)) if a is not None else None
    years = re.findall(r'\b(19|20)\d{2}\b', v); full_years = re.findall(r'\b(?:19|20)\d{2}\b', v)
    mon = next((n for k, n in MONTHS.items() if re.search(rf'\b{k}', v.lower())), None)
    if 'year' in lab and full_years: return full_years[0]
    if 'month' in lab and mon: return str(mon)
    if re.search(r'\bday\b', lab):
        d = re.search(r'\b([0-3]?\d)\b(?!\d)', re.sub(r'\b(?:19|20)\d{2}\b', '', v)); return d and d.group(1)
    nums = re.findall(r'-?\d+(?:\.\d+)?', v)
    return nums[0] if len(nums) == 1 else (full_years[0] if full_years and len(nums) > 1 and 'date' in lab else None)


def form_frame(page):
    """Greenhouse forms are often embedded in an iframe on the company site."""
    for fr in page.frames:
        try:
            if fr.locator('input[type="file"], input[name*="first"], input[id*="first_name"]').count(): return fr
        except Exception:
            pass
    return page.main_frame




def fill(frame, job):
    """job: dict with pdf, cover_txt, cover_text, url, company, title (see ats/__init__.py).
    Rules first (0 tokens); then ONE batched LLM call answers the remaining questions truthfully (answers.py);
    whatever it cannot answer from his facts stays in `unknown` -> NEEDS YOU."""
    pending = []
    try:
        import apply_prep
        APPLIED_HERE[0] = bool(job.get('company')) and apply_prep.company_submitted(job.get('company'))
    except Exception:
        APPLIED_HERE[0] = False
    done, unknown = fill_files(frame, job.get('pdf'), job.get('cover_txt'), job.get('cover_text') or '', pending)
    if pending and not job.get('no_llm'):
        try:
            import answers
            got = answers.resolve(frame, pending, job)
            done += got
            unknown = [u for u in unknown if u not in got]
        except Exception as e:
            print('answer step failed:', str(e)[:200]); import traceback; traceback.print_exc()
    frame.wait_for_timeout(800)
    for lab in still_empty(frame):        # final truth check: a click that didn't stick is NOT filled
        if lab not in unknown: unknown.append(lab + ' (still empty)')
    log_form(frame, job, done, unknown)
    return done, unknown


def log_form(frame, job, done, unknown, source='fill'):
    """Oct 13 (the owner): keep every question + the answer actually on the form, for every site, as training data:
    data/answer_log/forms.jsonl (one line per form fill). Never raises."""
    try:
        import json, datetime as dt, config
        fields = frame.evaluate(LABEL_JS)
        out = config.DATA / 'answer_log'; out.mkdir(exist_ok=True)
        rec = {'at': dt.datetime.now().isoformat(timespec='seconds'), 'source': source, 'url': frame.url,
               'company': job.get('company'), 'title': job.get('title'),
               'fields': [{'label': (d.get('label') or '')[:400], 'kind': d.get('kind'), 'type': d.get('type'),
                           'required': d.get('required'), 'value': d.get('value') if d['kind'] not in ('radio', 'buttons', 'checks')
                           else [o.get('text') for o in d.get('options', []) if o.get('checked')],
                           'options': [o.get('text') if isinstance(o, dict) else o for o in (d.get('options') or [])][:30]}
                          for d in fields if d.get('type') not in ('file', 'password')],
               'done': done[:80], 'unknown': unknown[:40]}
        with open(out / 'forms.jsonl', 'a') as fh: fh.write(json.dumps(rec, ensure_ascii=False) + '\n')
    except Exception as e:
        print('form log failed:', str(e)[:100])


def still_empty(frame):
    """Labels of required fields that are visibly empty right now (text/select/combobox/radio groups/checkboxes)."""
    out = []
    try:
        fields = frame.evaluate(LABEL_JS)
    except Exception:
        return out
    for d in fields:
        if not d.get('required') or d.get('type') == 'file': continue
        lab = (d.get('label') or '').strip()
        if not lab: continue
        if d['kind'] in ('radio', 'buttons', 'checks'):
            if not any(o.get('checked') for o in d.get('options', [])): out.append(lab)
        elif d.get('type') == 'checkbox':
            try:
                if not frame.locator(f'[data-jh="{d["n"]}"]').first.is_checked(): out.append(lab)
            except Exception:
                pass
        elif not (d.get('value') or '').strip():
            out.append(lab)
    seen = set()
    return [x for x in out if not (x in seen or seen.add(x))]
