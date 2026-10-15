# Setup guide (about 30–45 minutes)

By the end you'll have: a hunter that checks for new-grad / intern SWE postings every 15 minutes, a tailored one-page
resume + cover letter for every good match, and a Notion tracker with one row per job. Everything personal stays on
your Mac.

Works on **macOS** (the scheduler uses launchd). Linux works for the scripts, but you'd need cron instead of step 7.

---

## 1. Install the tools (10 min)

| Tool | Why | Install |
|---|---|---|
| Homebrew | installs the rest | [brew.sh](https://brew.sh) |
| Python 3.11+ | runs everything | `brew install python@3.13` |
| LibreOffice | DOCX → PDF resumes | `brew install --cask libreoffice` |
| Google Chrome | form filling (optional) | [google.com/chrome](https://www.google.com/chrome/) |
| Claude Code CLI | cover letters (optional) | [claude.com/claude-code](https://claude.com/claude-code), then run `claude` once to log in |

The resume font (Carlito) ships with macOS; if `setup.sh` says it's missing: `brew install --cask font-carlito`.
Without the Claude CLI everything still runs, but cover letters and custom-question answers are skipped.

## 2. Get the code and run the setup check (5 min)

```bash
git clone https://github.com/bek-sam/job-hunter.git
cd job-hunter
scripts/setup.sh
```

`setup.sh` creates the Python environment, installs packages, copies `profile.example.json` → `profile.json` and
`.env.example` → `.env` (only if they don't exist yet), and checks every tool. Re-run it any time: it only reports.
Expect ✗ for Notion until you finish step 3.

## 3. Connect Notion (10 min)

1. Go to [notion.so/my-integrations](https://www.notion.so/my-integrations) → **New integration** → type *Internal* →
   copy the **Internal Integration Secret**. Paste it in `.env`: `NOTION_TOKEN=ntn_...`
2. In Notion, create an empty page, e.g. **Jobs 2026**. Open it → **⋯** (top right) → **Connections** → add your integration.
3. Copy the page link (**Share → Copy link**) and run:
   ```bash
   .venv/bin/python jobhunter/notion_setup.py "<page link>"
   ```
   It creates five databases with the exact columns the code expects (Applications, Experience, Projects, Reports,
   Community Job Board) and prints `NOTION_...=` lines. **Paste those lines into `.env`.**
4. Run `scripts/setup.sh` again — "Notion connection works" should be ✓.

## 4. Fill in your profile (15 min)

Open `profile.json` in any editor and replace every "Jane Doe" value with your own. It's plain JSON — keep the quotes
and commas. Sections:

| Section | What to put there |
|---|---|
| `identity` | name, email, phone, links, city/state. `home_area_rx` = words for where you live; `preferred_locations_rx` = locations that score higher |
| `answers` | answers to common application questions (start date, degree, EEO self-ID, work authorization…). Lists are `["answer", "option regex"]`: the regex picks the matching dropdown option |
| `workday_answers` | the same facts for Workday's field ids |
| `work_history`, `school` | the jobs and school allowed on forms — nothing else is ever entered |
| `resume_v6` | resume layout: school line, courses, certifications, honors, the experience / project / leadership entries (title, dates, links) |
| `skills` | skills you can show in real work. Keep `unproven_max` at `0` so resumes only list skills you have |
| `llm` | short sentences the AI may use: your one-line bio, availability, sign-off |
| `packet`, `cover_letter_header`, `resume_v5` | used in the application packet and cover letters |

Rules that keep you safe:
- **Only true answers.** If you're not sure, **delete the key**: that question is left blank and the job shows up as
  "Needs you" for you to answer by hand. The tool never guesses.
- EEO questions (gender, race, veteran, disability) are voluntary — "Decline to self-identify" is a valid answer.
- `profile.json` is git-ignored: it never goes to GitHub. Don't rename it.

## 5. Add your resume bullets in Notion (10 min)

Resume bullet text lives in Notion, so you can edit it without touching code.

1. In **Experience (resume bullet bank)** add one row per job. **Name** must start with the same text as that entry's
   first field in `profile.json` → `resume_v6.exp` (e.g. Name `Example Corp — Software Engineer Intern` for prefix `Example Corp`).
   Do the same in **Projects** for each `resume_v6.proj` entry.
2. Open each row and write, in the page body:
   ```
   ## Resume bullet bank — pinned          (heading 2)
   ### Tags: general, always               (heading 3)
   • Built … (one bullet: real work, a number if you have one)
   ### Tags: backend
   • Designed …
   ```
   `always` = shown first on every resume; `boost` = preferred for matching roles; tags like `backend`, `frontend`,
   `ai`, `data`, `ios`, `devops`, `testing` steer which bullets fit which job. Bullets containing "(stated)" or "confirm"
   are skipped on purpose — use that for claims you still need to check.
3. Pull them into the local cache and build test resumes:
   ```bash
   .venv/bin/python jobhunter/resume_sync.py
   .venv/bin/python jobhunter/test_resumes.py
   ```
   It builds 10 sample resumes and checks each fits one page with the right sections; every line should say PASS.
   You'll read real resumes after step 6 (they land in `data/resumes/<job>/`). Fix the Notion text or `profile.json`, repeat.

## 6. Test the hunter (5 min)

```bash
.venv/bin/python jobhunter/fast_hunter.py --dry-run --minutes 180   # shows what it would build; writes nothing
.venv/bin/python jobhunter/fast_hunter.py --minutes 60              # one real run: Notion rows + resumes
```

Check the new rows in your **Applications** database.

## 7. Run it every 15 minutes (2 min)

```bash
scripts/schedule.sh install-hunter-15    # hunter at :09 :24 :39 :54
scripts/schedule.sh install-awake        # stop the Mac from idle-sleeping (closing the lid still sleeps it)
scripts/schedule.sh status
```

Logs: `data/logs/fast_hunter.log`. Stop everything: `scripts/schedule.sh uninstall`.

## 8. Optional extras

**Form filling (`apply_prep.py`)** — opens your saved jobs in a separate Chrome profile and fills the forms from
`profile.json`; you review and click Submit. Try one first: `.venv/bin/python jobhunter/apply_prep.py --url <job url> --dry`.
Sign-in / account creation / submit are separate `.env` switches (`AUTO_LOGIN`, `AUTO_CREATE_ACCOUNT`, `AUTO_VERIFY`,
`AUTO_SUBMIT`, `AUTO_CONSENT`), all `0` by default. If you turn sign-in on, store the password in the macOS Keychain
(never in a file): `security add-generic-password -s jobhunter -a you@example.com -w`. It never solves CAPTCHAs, never
enters ID or payment numbers, and cancels any application that asks for a government ID. **You are responsible for what
gets submitted and for each site's terms of use** — start with switches off and watch it.

**Manager + recruiter outreach (`manager.py`, `outreach.py`)** — an AI coach that reads your Gmail for application
updates and drafts (never sends) recruiter emails. Needs the Claude CLI with the Gmail and Google Calendar connectors
enabled on your claude.ai account. Schedule: `scripts/schedule.sh install-agents`. Costs roughly $1–3 per day of usage.

**Community board** — set `NOTION_COMMUNITY_DS` to share found jobs (public info only) with friends in one Notion table.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Notion call failed` / 404 | the page isn't shared with your integration (step 3.2), or an ID in `.env` is wrong |
| `profile.json is not valid JSON` | a missing comma or quote — paste it into [jsonlint.com](https://jsonlint.com) (remove personal data first) or ask your editor to format it |
| resume is 2 pages / bullets missing | shorten bullets in Notion; check row **Name** prefixes match `resume_v6` (step 5.1) |
| no cover letters | Claude CLI not installed or not logged in (`claude` once) |
| runs stop overnight | the lid was closed or the Mac slept — keep it plugged in and open |
| a form question keeps landing in "Needs you" | add the answer to `profile.json` → `answers` (or open an issue so a rule can be added) |

Questions or bugs: open a GitHub issue. Want to improve the code: see **Contributing** in the README.
