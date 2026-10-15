"""Post-fill sanity guard for formfill-based adapters. Undoes two known mis-answers of the shared rules:
1) long essay questions that merely MENTION a keyword (e.g. "Share your open-source contributions ... GitHub ...")
   got a short Answer Bank value (GitHub URL, LinkedIn, address, ...);
2) "Notice period" dropdowns answered by the start-date rule ("Available immediately" is not true: <1 month notice).
Cleared fields go back to `unknown` so the row becomes NEEDS YOU instead of carrying a wrong answer."""
import re
import formfill

RULE_VALUES = {v for _, v, _ in formfill.RULES if v}
ESSAY = re.compile(r'\b(share|describe|explain|tell us|why|what|how would|give (an|a) example|walk us through|in your own words)\b', re.I)


def check(frame, done, unknown):
    try:
        fields = frame.evaluate(formfill.LABEL_JS)
    except Exception:
        return done, unknown
    for d in fields:
        label = d.get('label') or ''
        if d.get('kind') in ('radio', 'buttons') or d.get('type') in ('file', 'checkbox'): continue
        loc = frame.locator(f'[data-jh="{d["n"]}"]').first
        val = (d.get('value') or '').strip()
        if not val:
            try: val = (loc.input_value(timeout=2000) or '').strip()   # combobox values are not in d['value']
            except Exception: val = ''
        if not val: continue
        try:
            if len(label) > 90 and ESSAY.search(label) and val in RULE_VALUES and not re.search(r'cover letter', label, re.I):
                loc.fill('')
                done[:] = [x for x in done if x != label]
                unknown.append(label[:160])
            elif re.search(r'notice period', label, re.I) and re.search(r'immediate', val, re.I):
                try:
                    loc.click(); loc.press('Backspace'); frame.wait_for_timeout(200); loc.press('Escape')
                except Exception:
                    pass
                still = ''
                try: still = loc.input_value(timeout=1000)
                except Exception: pass
                done[:] = [x for x in done if x != label]
                unknown.append(('Notice period: CHANGE the auto-picked "Available immediately" (wrong) — ' if still else 'Notice period — ')
                               + 'yours: current intern, < 1 month notice, start Dec 30, 2026')
        except Exception:
            unknown.append(label[:160] + ' (check this answer)')
    return done, unknown
