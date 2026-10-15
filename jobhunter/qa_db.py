"""Application Q&A database (SQLite): every application, page, question, possible answer and OUR answer, from all sites.

  .venv/bin/python jobhunter/qa_db.py                 # (re)build data/applications.db from the logs (idempotent, 0 tokens)
  .venv/bin/python jobhunter/qa_db.py --stats         # summary
  sqlite3 data/applications.db "select * from v_questions limit 20"

Sources: data/apply_results.jsonl (attempts + outcomes), data/workday_corpus/*.jsonl (Workday pages),
data/answer_log/forms.jsonl (all other sites), data/answer_log/rescue.jsonl (Claude rescues).

Tables
  applications(app_key PK, notion_id, company, title, ats, url, first_at, last_at, attempts, outcome, last_reason)
  pages(page_id PK, app_key, ats, step, at, after_next, errors_json)
  questions(qid PK, norm_text, sample_text, topic)                         -- one row per distinct question
  answers(id PK, page_id, app_key, qid, raw_label, field_type, required, options_json, answer, answer_source, filled)
  rescues(id PK, app_key, at, url, problems_json, decision_json)
Views
  v_questions   -- each question: topic, #companies, #times, most common answer, field types
  v_unanswered  -- required questions left empty (what still blocks)
  v_app_summary -- per application: outcome, #questions, #empty required
"""
import argparse, glob, hashlib, json, re, sqlite3
import config

DB = config.DATA / 'applications.db'
TOPICS = [  # (topic, regex on the normalized question) -- first match wins
    ('work_authorization', r'authori[sz]ed to work|eligible to work|legally (permitted|allowed|able) to work|work permit|autoris'),
    ('sponsorship', r'sponsor'),
    ('immigration_status', r'immigration|visa status|residency|green card|permanent resident'),
    ('citizenship_export', r'citizen|export control|u s person|us person|itar|passport'),
    ('clearance', r'clearance|polygraph|poly type'),
    ('eeo_gender', r'\bgender\b|\bsex\b'), ('eeo_ethnicity', r'ethnic|race|hispanic|latino'),
    ('eeo_veteran', r'veteran|military'), ('eeo_disability', r'disabilit'), ('eeo_orientation', r'sexual orientation|transgender|gender identity|pronoun'),
    ('salary', r'salary|compensation|pay (range|expectation)|hourly rate|desired pay'),
    ('start_date', r'start date|available to start|earliest|when can you start'),
    ('relocation_location', r'relocat|location|commute|on ?site|hybrid|remote|in office|travel'),
    ('education', r'school|university|degree|gpa|graduat|field of study|major|enrolled|sat |act |gre '),
    ('experience', r'years of experience|how many years|experience with|proficien'),
    ('previous_relationship', r'previously (worked|employed|applied)|ever (worked|applied)|former employee|issued an offer|relative|family|\\breferr'),
    ('conflict_legal', r'conflict of interest|government official|non compet|restrict|criminal|convict|felony|background|drug|licen'),
    ('consent_privacy', r'consent|privacy|terms|acknowledg|agree|confidentialit|text messag|\bsms\b'),
    ('source', r'how did you hear|where did you (hear|find)|source'),
    ('availability_logistics', r'18 years|age 18|hours a week|full time|internship timeframe|transportation|housing|shift|weekend|overtime'),
    ('essay', r'why (do you|are you)|tell us|describe|explain|what makes you|cover letter|share'),
    ('contact_identity', r'name|e ?mail|phone|address|city|postal|zip|country|state|linkedin|website|url|github'),
]


def norm(t):
    t = re.sub(r'\*|\((optional|required)\)', '', (t or '').lower())
    t = re.sub(r'[^a-z0-9 ]', ' ', t)
    return re.sub(r'\s+', ' ', t).strip()[:200]


def topic(n):
    for name, rx in TOPICS:
        if re.search(rx, n): return name
    return 'other'


def qid(n):
    return hashlib.sha1(n.encode()).hexdigest()[:12]


def schema(c):
    c.executescript("""
    drop view if exists v_questions; drop view if exists v_unanswered; drop view if exists v_app_summary;
    drop table if exists answers; drop table if exists pages; drop table if exists questions;
    drop table if exists applications; drop table if exists rescues; drop table if exists claude_answers;
    create table applications(app_key text primary key, notion_id text, company text, title text, ats text, url text,
        first_at text, last_at text, attempts int, outcome text, last_reason text);
    create table pages(page_id text primary key, app_key text, ats text, step text, at text, after_next int, errors_json text);
    create table questions(qid text primary key, norm_text text, sample_text text, topic text);
    create table answers(id integer primary key, page_id text, app_key text, qid text, raw_label text, field_type text,
        required int, options_json text, answer text, answer_source text, filled int);
    create table rescues(id integer primary key, app_key text, at text, url text, problems_json text, decision_json text);
    create table claude_answers(id integer primary key, app_key text, company text, tenant text, step text, round int, at text,
        qid text, label_raw text, category text, q_type text, widget text, required int, options_json text, value_before text,
        answer text, source text, why text, action_ok int);
    create index ix_answers_q on answers(qid); create index ix_answers_app on answers(app_key);
    create view v_questions as
      select q.qid, q.topic, q.sample_text, count(distinct a.app_key) apps, count(*) times,
             group_concat(distinct a.field_type) types,
             (select answer from answers x where x.qid = q.qid and x.filled = 1 group by answer order by count(*) desc limit 1) top_answer,
             sum(a.required) required_times, sum(case when a.required = 1 and a.filled = 0 then 1 else 0 end) left_empty
      from questions q join answers a on a.qid = q.qid group by q.qid order by apps desc, times desc;
    create view v_unanswered as
      select q.topic, q.sample_text, a.app_key, a.field_type, a.options_json from answers a join questions q on q.qid = a.qid
      where a.required = 1 and a.filled = 0;
    create view v_app_summary as
      select ap.company, ap.title, ap.ats, ap.outcome, ap.attempts, count(a.id) questions,
             sum(case when a.required = 1 and a.filled = 0 then 1 else 0 end) empty_required, ap.last_reason
      from applications ap left join answers a on a.app_key = ap.app_key group by ap.app_key order by ap.last_at desc;
    """)


def app_key(company, title):
    return f"{(company or '?').strip().lower()}|{(title or '').strip().lower()}"


def add_q(c, label):
    n = norm(label)
    if len(n) < 3: return None
    q = qid(n)
    c.execute('insert or ignore into questions values (?,?,?,?)', (q, n, (label or '').strip()[:500], topic(n)))
    return q


def filled_of(v):
    if isinstance(v, list): return bool(v)
    if isinstance(v, bool): return v
    return bool(str(v or '').strip()) and not re.match(r'^\s*select( one)?\s*$', str(v), re.I)


def build(quiet=False):
    c = sqlite3.connect(DB); schema(c)
    # applications + outcomes
    res = [json.loads(l) for l in (config.DATA / 'apply_results.jsonl').read_text().splitlines() if l.strip()]
    ids = {}
    for r in res:
        ids.setdefault(r['id'], []).append(r)
    try:
        import notion
        meta = {}
    except Exception:
        meta = {}
    for nid, rs in ids.items():
        last = rs[-1]
        k = app_key(last.get('company'), nid)    # title unknown in results -> keyed by notion id until a form names it
        c.execute('insert or replace into applications values (?,?,?,?,?,?,?,?,?,?,?)',
                  (k, nid, last.get('company'), None, last.get('ats'), None, rs[0]['at'], last['at'], len(rs),
                   'SUBMITTED' if any(x['status'] == 'SUBMITTED' for x in rs) else last['status'], (last.get('reason') or '')[:500]))
    # Workday pages
    for fn in glob.glob(str(config.DATA / 'workday_corpus' / '*.jsonl')):
        for i, line in enumerate(open(fn)):
            d = json.loads(line)
            k = app_key(d.get('company'), d.get('title'))
            c.execute('insert or ignore into applications values (?,?,?,?,?,?,?,?,?,?,?)',
                      (k, None, d.get('company'), d.get('title'), 'workday', d.get('url'), d['at'], d['at'], 0, None, None))
            c.execute('update applications set title = coalesce(title, ?), url = coalesce(url, ?), last_at = max(last_at, ?) where app_key = ?',
                      (d.get('title'), d.get('url'), d['at'], k))
            pid = f"wd:{d['tenant']}:{i}"
            c.execute('insert or replace into pages values (?,?,?,?,?,?,?)',
                      (pid, k, 'workday', d.get('step'), d['at'], int(bool(d.get('after_next'))), json.dumps(d.get('errors') or [])))
            done = set(x.lower() for x in d.get('done') or [])
            for f in d['fields']:
                q = add_q(c, f.get('label'))
                if not q: continue
                v = f.get('value'); fl = filled_of(v)
                src = 'rule/claude' if (f.get('label') or '').lower() in done else ('prefilled/kept' if fl else 'empty')
                c.execute('insert into answers(page_id, app_key, qid, raw_label, field_type, required, options_json, answer, answer_source, filled) '
                          'values (?,?,?,?,?,?,?,?,?,?)', (pid, k, q, (f.get('label') or '')[:500], f.get('kind'), int(bool(f.get('required'))),
                                                           json.dumps(f.get('options') or [], ensure_ascii=False), str(v)[:500] if v is not None else None, src, int(fl)))
    # all other sites
    fl_path = config.DATA / 'answer_log' / 'forms.jsonl'
    for i, line in enumerate(fl_path.read_text().splitlines() if fl_path.exists() else []):
        d = json.loads(line)
        host = re.sub(r'^https?://([^/]+).*', r'\1', d.get('url') or '')
        ats = 'greenhouse' if 'greenhouse' in host else 'ashby' if 'ashby' in host else 'lever' if 'lever' in host else \
              'linkedin' if 'linkedin' in host else host
        k = app_key(d.get('company'), d.get('title'))
        c.execute('insert or ignore into applications values (?,?,?,?,?,?,?,?,?,?,?)',
                  (k, None, d.get('company'), d.get('title'), ats, d.get('url'), d['at'], d['at'], 0, None, None))
        pid = f'form:{i}'
        c.execute('insert or replace into pages values (?,?,?,?,?,?,?)', (pid, k, ats, d.get('source'), d['at'], 0, '[]'))
        done = set(x.lower() for x in d.get('done') or [])
        for f in d['fields']:
            q = add_q(c, f.get('label'))
            if not q: continue
            v = f.get('value'); fl = filled_of(v)
            src = 'rule/claude' if (f.get('label') or '').lower() in done else ('prefilled/kept' if fl else 'empty')
            c.execute('insert into answers(page_id, app_key, qid, raw_label, field_type, required, options_json, answer, answer_source, filled) '
                      'values (?,?,?,?,?,?,?,?,?,?)', (pid, k, q, (f.get('label') or '')[:500], f.get('kind'), int(bool(f.get('required'))),
                                                       json.dumps(f.get('options') or [], ensure_ascii=False),
                                                       json.dumps(v, ensure_ascii=False)[:500] if isinstance(v, list) else (str(v)[:500] if v is not None else None),
                                                       src, int(fl)))
    # rescues: mark answers Claude changed
    rp = config.DATA / 'answer_log' / 'rescue.jsonl'
    for line in rp.read_text().splitlines() if rp.exists() else []:
        d = json.loads(line)
        k = app_key(d.get('company'), d.get('title'))
        c.execute('insert into rescues(app_key, at, url, problems_json, decision_json) values (?,?,?,?,?)',
                  (k, d['at'], d.get('url'), json.dumps(d.get('problems')), json.dumps(d.get('decision'))))
    # Claude-driven Workday steps (schema jobapp.step.v1, data/answer_log/wd_claude.jsonl)
    cp = config.DATA / 'answer_log' / 'wd_claude.jsonl'
    for line in cp.read_text().splitlines() if cp.exists() else []:
        d = json.loads(line)
        ans = {x['q_ref']: x for x in (d.get('decision') or {}).get('answers') or []}
        ok = {x['q_ref']: x['ok'] for x in d.get('results') or [] if x.get('q_ref')}
        for q in d.get('questions') or []:
            a = ans.get(q['q_ref'])
            if not a: continue
            qd = add_q(c, q['label_raw'])
            c.execute('insert into claude_answers(app_key, company, tenant, step, round, at, qid, label_raw, category, q_type, widget, '
                      'required, options_json, value_before, answer, source, why, action_ok) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                      (d['app']['app_key'], d['app']['company'], d['app']['tenant'], d['step']['name_norm'], d['step']['round'], d['at'],
                       qd, q['label_raw'][:500], q['category'], q['type'], q['widget'], int(q['required']),
                       json.dumps([o['label'] for o in q.get('options') or []], ensure_ascii=False), str(q.get('value_before'))[:300],
                       a.get('value'), a.get('source'), a.get('why'), None if q['q_ref'] not in ok else int(ok[q['q_ref']])))
    c.commit()
    if not quiet: stats(c)
    c.close()


def stats(c=None):
    c = c or sqlite3.connect(DB)
    one = lambda q: c.execute(q).fetchone()[0]
    print(f"applications {one('select count(*) from applications')} | pages {one('select count(*) from pages')} | "
          f"distinct questions {one('select count(*) from questions')} | answers {one('select count(*) from answers')} | "
          f"rescues {one('select count(*) from rescues')}")
    print('\nquestions by topic:')
    for t, n in c.execute('select topic, count(*) from questions group by topic order by 2 desc'): print(f'  {n:4}  {t}')
    print('\ntop questions (asked by most applications):')
    for r in c.execute('select apps, topic, substr(sample_text,1,70), top_answer from v_questions limit 15'):
        print(f'  {r[0]:3} | {r[1]:22} | {r[2]:70} | {str(r[3])[:40]}')
    print('\nrequired questions left empty:', one('select count(*) from v_unanswered'))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--stats', action='store_true')
    a = ap.parse_args()
    stats() if a.stats else build()
