# Job Hunter (local)

Finds new-grad and internship software roles minutes after they're posted, scores them, builds a tailored one-page
resume and cover letter for each, and tracks everything in Notion. Mechanical work is plain Python + Playwright
(0 LLM tokens); an LLM (`claude -p`) is used only for judgment text such as cover letters and custom questions.

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

Set `LLM_PROVIDER=codex` in `.env` to use `codex exec` with your existing
Codex login (`codex login`). Set it to `claude` to use Claude with Codex
fallback on usage limits (`CODEX_FALLBACK=0` disables fallback).
`CODEX_BIN` and `CODEX_MODEL` optionally override the executable and model.
Calls run in a temporary directory with a read-only sandbox and without user
configuration. Codex schemas are normalized for strict structured output.
Manager and Outreach use your connected Gmail account for search/read and
unsent draft creation, plus live web research. Manager can also read Calendar
and create private OA/interview events without attendees. The scheduled
connector configuration blocks all other apps and unlisted tools, including
email sending/forwarding/deletion and event updates/deletion. Existing Codex
account authentication is reused; no Google credentials are stored here.
If a connector disconnects, its failed checks are reported explicitly.

```bash
.venv/bin/python jobhunter/fast_hunter.py --dry-run --minutes 180   # test: no writes, no LLM
.venv/bin/python jobhunter/test_resumes.py                          # resume regression check (~50 s)
.venv/bin/python jobhunter/apply_prep.py --url <job url> --dry      # test-fill one form
scripts/schedule.sh install-hunter-15                               # run the hunter every 15 min (launchd)
scripts/schedule.sh install-awake                                   # keep the Mac from idle-sleeping
```

Auto-apply checks the eligible queue every five minutes and processes up to 15
rows per run. Jobs are ordered by **Date Posted, newest first**, then exact
Notion creation time. Creation time is used when the posting date is missing.
Fresh jobs, backlog jobs, and `FILL NOW` rows share this ordering; title and fit
score do not override recency. Retry cooldowns and eligibility checks still apply.
An already-running batch finishes its current order; subsequent runs use the new order.

## Layout

- `jobhunter/hunt.py`, `intern_hunt.py`, `bigtech.py` — job sources (ATS boards, LinkedIn, big-tech career sites)
- `jobhunter/fast_hunter.py` — freshness → hard filters → dedupe → `score.py` fit gate → resume → Notion row
- `jobhunter/gen_v6.py` — resume generator; `applicant.py` — loads `profile.json`
- `jobhunter/formfill.py` + `ats/<site>.py` — rule-based form filling per ATS (Workday, Greenhouse, Ashby, Lever, …)
- `jobhunter/auth.py` — sign-in / account creation / submit, each behind its own `.env` switch (all off by default)
- `jobhunter/manager.py`, `outreach.py`, `prompts/` — optional LLM coach and recruiter-outreach drafts

## Rules the code enforces

- Never invents experience, dates, metrics or answers: a question with no profile answer is left blank and flagged "Needs you".
- Never solves CAPTCHAs, never enters ID or payment numbers; an application that asks for a government ID is cancelled.
- Email is drafts only — nothing is ever sent automatically.
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
