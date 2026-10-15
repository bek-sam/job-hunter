"""Workday field map: data-automation-id (or its suffix) -> (value for text fields, regex to pick an option).
Checked BEFORE the generic label rules in formfill.RULES. Values are Answer Bank facts only."""
import re
import applicant as P

# matched with re.search against the field's data-automation-id / input id (lower-cased)
# (automation-id regex, profile key) -> profile.json["workday_answers"]; a missing key = no rule
ID_KEYED = [
    (r'legalname.*firstname|legalnamesection_firstname|^firstname$', 'first_name'),
    (r'legalname.*lastname|legalnamesection_lastname|^lastname$', 'last_name'),
    (r'addressline1', 'address_line1'),
    (r'address--city|addresssection_city|\bcity$', 'city'),
    (r'postalcode', 'zip'),
    (r'regionsubdivision|county', 'county'),
    (r'countryregion|addresssection_countryregion', 'state'),
    (r'country--country|countrydropdown|^country$', 'country'),
    (r'phonetype|phone-device-type|devicetype', 'phone_type'),
    (r'countryphonecode', 'phone_country_code'),
    (r'phonenumber--phonenumber|phone-number$|phonenumber$', 'phone_digits'),
    (r'phonenumber--extension', 'phone_extension'),
    (r'email', 'email'),
    (r'source--source|^source$|howdidyouhear', 'how_heard'),
    (r'candidateispreviousworker|previousworker', 'previous_worker'),
    (r'linkedin', 'linkedin'),
    (r'webaddress.*url|^webaddress', 'web_address'),
    (r'gender', 'gender'),
    (r'ethnicity|race', 'race'),
    (r'hispanic', 'hispanic_latino'),
    (r'veteran', 'veteran'),
]
ID_RULES = P.rules(ID_KEYED, 'workday_answers')

# the only work history / education that may appear (profile.json "work_history" / "school")
WORK = [dict(w, start=tuple(w['start']), end=tuple(w['end']) if w.get('end') else None) for w in P.get('work_history', default=[])]
SCHOOL = P.get('school', default={})


def id_rule(aid):
    a = (aid or '').lower()
    for rx, val, opt in ID_RULES:
        if re.search(rx, a): return val, opt
    return None, None


def norm(s):
    return re.sub(r'[^a-z0-9]', '', (s or '').lower())


def work_for(company):
    n = norm(company)
    for w in WORK:
        if n and (norm(w['company']) in n or n in norm(w['company'])): return w
    return None
