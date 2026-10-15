"""Applicant profile: every personal fact (name, contact, address, form answers, EEO self-ID, education, work history,
resume layout facts) lives in ../profile.json, which is git-ignored and never leaves this machine.
Copy profile.example.json to profile.json and fill it in. Code reads values from here; nothing personal is hard-coded.

  import applicant as P
  P.NAME, P.EMAIL, P.PHONE ...           identity shortcuts
  P.get('resume_v6', 'cert')             any nested value (None when missing)
  P.rules(KEYED, 'answers')              [(label_rx, value, option_rx)] for the keys this profile answers
  P.file('Resume', 'pdf')                -> First_Last_Resume.pdf
  P.fill(text)                           replaces {{NAME}}, {{FIRST}}, ... placeholders in prompt templates
"""
import json, os, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PATH = pathlib.Path(os.environ.get('PROFILE', ROOT / 'profile.json'))
EXAMPLE = ROOT / 'profile.example.json'


def _load():
    f = PATH if PATH.exists() else EXAMPLE
    if f is EXAMPLE:
        print(f'profile: {PATH} not found -- using placeholder values from {EXAMPLE.name} (copy it to profile.json)', file=sys.stderr)
    return json.loads(f.read_text())


DATA = _load()


def get(*keys, default=None):
    v = DATA
    for k in keys:
        if not isinstance(v, dict) or k not in v: return default
        v = v[k]
    return v


ID = get('identity', default={})
FIRST, LAST = ID.get('first', ''), ID.get('last', '')
NAME = ID.get('full') or f'{FIRST} {LAST}'.strip()
EMAIL, PHONE = ID.get('email', ''), ID.get('phone', '')
CITY, STATE, LOCATION = ID.get('city', ''), ID.get('state', ''), ID.get('location', '')


def answer(key, section='answers'):
    """(value, option_rx) for one answer key, or None when the profile does not answer it."""
    v = get(section, key)
    if v is None: return None
    if isinstance(v, str): return v, None
    return (v[0], v[1] if len(v) > 1 else None)


def rules(keyed, section='answers'):
    """keyed = [(label_rx, key)] in code -> [(label_rx, value, option_rx)] for keys present in the profile.
    A key the profile leaves out simply has no rule: the field stays empty and the row goes to NEEDS YOU."""
    out = []
    for rx, key in keyed:
        a = answer(key, section)
        if a is not None: out.append((rx, a[0], a[1]))
    return out


def file(kind, ext):
    """Output file name, e.g. First_Last_Resume.pdf -- never the company name."""
    stem = '_'.join(x for x in (FIRST, LAST) if x) or 'Applicant'
    return f'{stem}_{kind}.{ext}'


def fill(text):
    """Fill {{KEY}} placeholders in prompt templates: NAME, FIRST plus every key of profile['llm']."""
    vals = {'NAME': NAME, 'FIRST': FIRST, **{k.upper(): str(v) for k, v in (get('llm', default={}) or {}).items()}}
    for k, v in vals.items():
        text = text.replace('{{' + k + '}}', v)
    return text
