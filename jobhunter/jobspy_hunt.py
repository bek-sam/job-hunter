"""JobSpy hunter (speedyapply/JobSpy, MIT; pip install python-jobspy, Python >= 3.10). Add-on to hunt.py / bigtech.py.
Adds sources the other hunters lack: Indeed, ZipRecruiter, Google Jobs (guest only: no login, no CAPTCHA solving --
a blocked site is just skipped). Same output format as bigtech.py; fast_hunter applies the normal filters after.
  .venv/bin/python jobhunter/jobspy_hunt.py --hours 1 --out new_jobspy.json"""
import argparse, datetime as dt, json, math, os
import bigtech

SITES = os.environ.get('JOBSPY_SITES', 'indeed,zip_recruiter,google').split(',')
SEARCHES = [s.strip() for s in os.environ.get('JOBSPY_SEARCHES', 'software engineer new grad|entry level software engineer|software engineer intern 2027').split('|')]


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--hours', type=float, default=1)
    ap.add_argument('--out', default='new_jobspy.json'); ap.add_argument('--results', type=int, default=40); a = ap.parse_args()
    try:
        from jobspy import scrape_jobs
    except ImportError:
        print('python-jobspy not installed: .venv/bin/pip install python-jobspy'); json.dump([], open(a.out, 'w')); return
    hours = max(1, math.ceil(a.hours)); rows, seen = [], set()
    for q in SEARCHES:
        for site in SITES:
            try:
                df = scrape_jobs(site_name=[site], search_term=q, google_search_term=f'{q} jobs posted in the last day',
                                 location='United States', results_wanted=a.results, hours_old=hours, country_indeed='USA')
            except Exception as e:
                print(f'{site}: {type(e).__name__} {str(e)[:80]} (skipped)'); continue
            for _, j in df.iterrows():
                co, title = str(j.get('company') or ''), str(j.get('title') or '')
                url = j.get('job_url_direct') if isinstance(j.get('job_url_direct'), str) and j.get('job_url_direct') else j.get('job_url')
                loc = str(j.get('location') or '')
                if not (co and title and url) or url in seen: continue
                kind, tier = bigtech.classify(co, title, loc)
                if not kind: continue
                seen.add(url)
                d = j.get('date_posted'); posted = dt.datetime.now(dt.timezone.utc) if d is None or str(d) in ('NaT', 'nan', 'None') else dt.datetime.combine(d, dt.time(), dt.timezone.utc)
                r = dict(source=site, kind=kind, tier=tier, company=co, title=title, location=loc, url=url, exact=False,
                         posted_utc=posted.strftime('%Y-%m-%d %H:%M'))
                if j.get('min_amount') == j.get('min_amount') and j.get('min_amount'):   # salary when the posting gives one
                    r['salary'] = f"{j.get('min_amount')}-{j.get('max_amount')} {j.get('interval') or ''}".strip()
                rows.append(r)
            print(f'{site} "{q}": {len(df)} found')
    json.dump(rows, open(a.out, 'w'), indent=1)
    for r in rows: print(f"[{r['kind']}/{r['tier']}] {r['posted_utc']} | {r['source']} | {r['company']} | {r['title']} | {r['url']}")


if __name__ == '__main__':
    main()
