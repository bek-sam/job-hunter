# Job Hunter (local)

Finds new-grad and internship software roles minutes after they're posted, scores them, builds a tailored one-page
resume and cover letter for each, and tracks everything in Notion. Mechanical work is plain Python + Playwright
(0 LLM tokens); an LLM (`claude -p`) is used only for judgment text such as cover letters and custom questions.

**Runs on macOS only** (launchd schedules, Keychain passwords, `osascript` notifications, LibreOffice at
`/Applications/LibreOffice.app`, Google Chrome as the apply browser). Set `JOBHUNTER_TZ` in `.env` if you are not on
US Central time.

## Your data stays on your machine

Nothing personal is in this repo. Everything about you lives in git-ignored local files:

| File | What's in it |
|---|---|
| `profile.json` | name, contact, address, form answers (incl. EEO self-ID), education, work history, resume layout facts |
| `.env` | Notion token + database IDs, automation switches |
| `data/` | seen-jobs DB, generated resumes, run logs |
| `reference/docs/` | optional: your Project Library / Answer Bank notes used for cover letters |

`profile.example.json` and `.env.example` show the structure with placeholder values (a fictional "Jane Doe").

## Setup

**New here? Follow [SETUP.md](SETUP.md)** — a step-by-step guide (about 30–45 min). Short version:

```bash
git clone https://github.com/bek-sam/job-hunter.git && cd job-hunter
scripts/setup.sh                                          # installs everything, creates profile.json + .env, checks tools
.venv/bin/python jobhunter/notion_setup.py "<page link>"  # creates the Notion databases, prints the .env lines
# edit profile.json with your own details, add resume bullets in Notion, then:
.venv/bin/python jobhunter/test_resumes.py
```

## Commands

```bash
.venv/bin/python jobhunter/fast_hunter.py --dry-run --minutes 180   # test: no writes, no LLM
.venv/bin/python jobhunter/test_resumes.py                          # resume regression check (~50 s)
.venv/bin/python jobhunter/apply_prep.py --url <job url> --dry      # test-fill one form
scripts/schedule.sh install-5                                       # hunter + apply prep every 5 min (launchd)
scripts/schedule.sh install-awake                                   # keep the Mac from idle-sleeping
scripts/schedule.sh uninstall                                       # remove every schedule (incl. keep-awake)
.venv/bin/python -m unittest discover -s tests                      # safety unit tests (no browser, no Notion)
.venv/bin/python jobhunter/prune.py --dry                           # what the daily log cleanup would remove
```

## Layout

- `jobhunter/hunt.py`, `intern_hunt.py`, `bigtech.py` — job sources (ATS boards, LinkedIn, big-tech career sites)
- `jobhunter/fast_hunter.py` — freshness → hard filters → dedupe → `score.py` fit gate → resume → Notion row
- `jobhunter/gen_v6.py` — resume generator; `applicant.py` — loads `profile.json`
- `jobhunter/formfill.py` + `ats/<site>.py` — rule-based form filling per ATS (Workday, Greenhouse, Ashby, Lever, …)
- `jobhunter/auth.py` — sign-in / account creation / submit, each behind its own `.env` switch (all off by default)
- `jobhunter/reword.py` — swaps bank-bullet words for the job's exact terms (code-validated); `resume_check.py` — ATS
  parse check + required-term coverage / gap report on the final PDF (a failed parse check blocks auto-submit)
- `skills/tailor-resume/SKILL.md` — the tailoring rulebook (JD fields, bullet rubric, cover-letter rules; `packet.py`
  loads its cover-letter section). Link it into Claude Code: `ln -s "$PWD/skills/tailor-resume" ~/.claude/skills/`
- `jobhunter/reports.py` — daily apply report (10 PM) and a 14-day response-rate review, both in Notion Reports;
  every auto-submit attaches before/after screenshots to the row as proof
- `jobhunter/manager.py`, `outreach.py`, `prompts/` — optional LLM coach and recruiter-outreach drafts

## Rules the code enforces

- Form questions are answered from your profile first; questions it doesn't cover get the most reasonable answer
  (LLM, `answers.py`). Credentials you don't have (employers, degrees, dates, metrics) and legal/consent items are
  never filled — those rows are flagged "Needs you". Work-authorization answers come only from your `profile.json`.
- Never solves CAPTCHAs, never enters ID or payment numbers; an application that asks for a government ID is cancelled.
- Email is drafts only — nothing is ever sent automatically.
- Auto-apply runs only on `APPLY_ATS_ONLY` systems (default Greenhouse + Ashby). A Submit click with no confirmation is
  logged as a possible submission and blocks every later submit of that posting; if the Notion or Gmail duplicate check
  can't run, the form is filled but NOT submitted.
- `RESCUE=1` (default) lets Claude fill fields on a stuck single-page form, under the same answer rules.
- Automation switches (`AUTO_*`) are off by default. Respect each site's terms of use; you are responsible for what you submit.

## Contributing

`main` is protected: nobody pushes to it directly. Work on your own branch and open a pull request; the owner
reviews and merges it.

```bash
git clone https://github.com/bek-sam/job-hunter.git && cd job-hunter
git checkout -b yourname/short-description      # one branch per change
# ...edit, test (test_resumes.py, fast_hunter.py --dry-run)...
git add <files> && git commit -m "What and why"
git push -u origin yourname/short-description   # then open a Pull Request on GitHub
git checkout main && git pull                   # later: stay up to date with merged work
```

Collaborators can push branches here; everyone else can fork and open a PR from their fork. Never commit `profile.json`, `.env`, `data/` or anything with real personal details —
run `git status` before every commit. New form rules go in code as `(label regex, profile key)`; the answer goes in
your own `profile.json` (and a placeholder in `profile.example.json`).
