#!/bin/zsh
# One-time setup + health check for a new user (macOS). Safe to re-run: it never overwrites your profile.json / .env.
#   scripts/setup.sh
set -u
ROOT=${0:A:h:h}
cd "$ROOT" || exit 1
ok()   { print -P "%F{green}✓%f $1"; }
warn() { print -P "%F{yellow}!%f $1"; }
bad()  { print -P "%F{red}✗%f $1"; FAIL=1; }
FAIL=0

print "== Job Hunter setup ($ROOT)"

# 1. Python 3.11+ and the project venv
PY=$(command -v python3 || true)
if [[ -z $PY ]]; then bad "python3 not found — install it: brew install python@3.13"; exit 1; fi
if ! $PY -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then bad "Python 3.11+ needed (have $($PY --version))"; exit 1; fi
[[ -d .venv ]] || { $PY -m venv .venv && ok "created .venv"; }
.venv/bin/pip install -q --upgrade pip && .venv/bin/pip install -q -r requirements.txt && ok "Python packages installed" || bad "pip install failed"
.venv/bin/playwright install chromium >/dev/null 2>&1 && ok "Playwright Chromium installed" || warn "playwright install chromium failed (only needed for job-page reading)"

# 2. Your private files (copied from the templates once, never overwritten)
[[ -f profile.json ]] && ok "profile.json exists" || { cp profile.example.json profile.json && warn "created profile.json from the template — EDIT IT with your own details"; }
[[ -f .env ]] && ok ".env exists" || { cp .env.example .env && warn "created .env from the template — add NOTION_TOKEN and the NOTION_* ids"; }
grep -q '"jane.doe@example.com"' profile.json && warn "profile.json still has the Jane Doe placeholders"

# 3. Outside tools
[[ -x /Applications/LibreOffice.app/Contents/MacOS/soffice ]] && ok "LibreOffice found (DOCX -> PDF)" || bad "LibreOffice missing: brew install --cask libreoffice"
if [[ -e /System/Library/PrivateFrameworks/FontServices.framework/Versions/A/Resources/Fonts/ApplicationSupport/Carlito.ttc ]] || \
   ls ~/Library/Fonts /Library/Fonts 2>/dev/null | grep -qi carlito; then ok "Carlito font found (resume font)"
else warn "Carlito font not found: brew install --cask font-carlito"; fi
[[ -x "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" ]] && ok "Google Chrome found (form filling)" || warn "Google Chrome missing (only needed for apply_prep form filling)"
CL=${CLAUDE_BIN:-$HOME/.local/bin/claude}
[[ -x $CL ]] || CL=$(command -v claude || true)
[[ -n $CL && -x $CL ]] && ok "Claude Code CLI found (cover letters): $CL" || warn "Claude Code CLI missing — install from claude.com/claude-code, then run 'claude' once to log in (cover letters are skipped without it)"

# 4. Config sanity
set -a; [[ -f .env ]] && source <(grep -E '^[A-Z_]+=' .env | sed 's/[[:space:]]*#.*$//'); set +a
[[ -n ${NOTION_TOKEN:-} ]] && ok "NOTION_TOKEN set" || bad "NOTION_TOKEN empty in .env (see SETUP.md step 3)"
[[ -n ${NOTION_APPS_DS:-} ]] && ok "NOTION_APPS_DS set" || bad "NOTION_APPS_DS empty — run: .venv/bin/python jobhunter/notion_setup.py <page link>"
if [[ -n ${NOTION_TOKEN:-} && -n ${NOTION_APPS_DS:-} ]]; then
  (cd jobhunter && ../.venv/bin/python -c "import notion; notion.query(limit=1); print('ok')" >/dev/null 2>&1) \
    && ok "Notion connection works" || bad "Notion call failed — is the page shared with your integration (… → Connections)?"
fi
(cd jobhunter && ../.venv/bin/python -c "import applicant as P; assert P.NAME" 2>/dev/null) && ok "profile.json loads" || bad "profile.json is not valid JSON"

print ""
if (( FAIL )); then print "Fix the ✗ items above, then re-run scripts/setup.sh"; exit 1; fi
print "All set. Next: .venv/bin/python jobhunter/test_resumes.py   (then see SETUP.md step 6)"
