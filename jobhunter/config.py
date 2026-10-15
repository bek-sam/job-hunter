"""Shared settings for the local Job Hunter. Secrets live in ../.env (never committed)."""
import os, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
PKG = ROOT / 'jobhunter'
DATA = ROOT / 'data'            # local state: seen jobs DB, run logs, daily summaries
RESUMES = DATA / 'resumes'      # one folder per role, files always First_Last_Resume.pdf/.docx (applicant.file)
REF = ROOT / 'reference' / 'docs'
for p in (DATA, RESUMES):
    p.mkdir(parents=True, exist_ok=True)


def _load_env():
    f = ROOT / '.env'
    if f.exists():
        for line in f.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()

# python.org builds of Python ship without CA certs -> use certifi for every urllib call
try:
    import certifi
    os.environ.setdefault('SSL_CERT_FILE', certifi.where())
except ImportError:
    pass

NOTION_TOKEN = os.environ.get('NOTION_TOKEN', '')
NOTION_VERSION = '2025-09-03'
# Your Notion workspace (IDs in .env -- see .env.example). Empty = that feature is off.
APPS_DS = os.environ.get('NOTION_APPS_DS', '')            # data source "Applications 2026"
JOBS_HUB = os.environ.get('NOTION_JOBS_HUB', '')          # page "Jobs 2026" (links only — never write content here)
REPORTS_DS = os.environ.get('NOTION_REPORTS_DS', '')      # data source "Reports" (Name, Date, Kind, Slot, Source)
LIBRARY_DS = os.environ.get('NOTION_LIBRARY_DS', '')      # data source "Library" (Name, Category, Company, Event date)
COMMUNITY_DS = os.environ.get('NOTION_COMMUNITY_DS', '')  # Community Job Board (public job info only)
EXPERIENCE_DS = os.environ.get('NOTION_EXPERIENCE_DS', '')  # resume bullet bank: Experience
PROJECTS_DS = os.environ.get('NOTION_PROJECTS_DS', '')      # resume bullet bank: Projects

# LLM calls go through headless Claude Code (your subscription). Small, single-shot prompts only.
CLAUDE_BIN = os.environ.get('CLAUDE_BIN', os.path.expanduser('~/.local/bin/claude'))
LLM_MODEL = os.environ.get('LLM_MODEL', 'sonnet')        # cover letters / custom answers
LLM_MODEL_CHEAP = os.environ.get('LLM_MODEL_CHEAP', 'haiku')

# Hunting rules (from the owner, Oct 4-9 2026) -- see reference/schedules/Fast_Hunter*.md
WINDOW_MIN = int(os.environ.get('WINDOW_MIN', '20'))     # runs every 15 min (Oct 13): roles posted in the last 20 min (5 min overlap)
# max posting age accepted (Oct 14): LinkedIn shows a new posting in search 15-45 min late, so a 20-min cut marked
# ~85% of real new roles stale forever. 60 min = the original freshness rule; Notion dedupe stops double builds.
FRESH_MIN = int(os.environ.get('FRESH_MIN', '60'))
FIT_GATE = int(os.environ.get('FIT_GATE', '70'))
PER_COMPANY_WEEK = int(os.environ.get('PER_COMPANY_WEEK', '2'))   # max new roles per company per 7 days (Oct 13)
# 'all' = pass every JD tech term to the generator (same as the cloud Fast Hunter);
# 'evidence' = only terms he has real evidence for
SKILLS_MODE = os.environ.get('SKILLS_MODE', 'all')

# Playwright apply browser: a separate Chrome profile you sign into once (Workday, Greenhouse, ...)
CHROME_PROFILE = pathlib.Path(os.environ.get('CHROME_PROFILE', str(pathlib.Path.home() / '.jobhunter' / 'chrome-profile')))
CDP_PORT = int(os.environ.get('CDP_PORT', '9333'))
