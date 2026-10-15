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

## Setup (macOS)

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/playwright install chromium
cp profile.example.json profile.json     # then replace every value with your own
cp .env.example .env                     # Notion token + IDs
brew install --cask libreoffice          # DOCX -> PDF for resumes (needs the Carlito font)
```

Resume bullets come from your Notion bullet bank (`jobhunter/resume_sync.py`), not from code.

## Commands

```bash
.venv/bin/python jobhunter/fast_hunter.py --dry-run --minutes 180   # test: no writes, no LLM
.venv/bin/python jobhunter/test_resumes.py                          # resume regression check (~50 s)
.venv/bin/python jobhunter/apply_prep.py --url <job url> --dry      # test-fill one form
scripts/schedule.sh install-hunter-15                               # run the hunter every 15 min (launchd)
scripts/schedule.sh install-awake                                   # keep the Mac from idle-sleeping
```

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

Open an issue or PR. Never commit `profile.json`, `.env`, `data/` or anything with real personal details —
run `git status` before every commit. New form rules go in code as `(label regex, profile key)`; the answer goes in
your own `profile.json` (and a placeholder in `profile.example.json`).
