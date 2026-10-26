#!/bin/zsh
# Local schedules via launchd (runs while the Mac is awake; a missed slot runs on wake).
#   scripts/schedule.sh install    # Fast Hunter hourly at :24, Apply Prep hourly at :40
#   scripts/schedule.sh install-agents   # Manager 2:47 + 18:47, Recruiter Outreach 6:52 + 21:52 (CT)
#        -> only after the cloud Manager / Outreach tasks are paused, or drafts get duplicated
#   scripts/schedule.sh uninstall
#   scripts/schedule.sh status
# :24/:40 interleave with the cloud tasks (:54/:57) while both run; once the cloud ones are paused
# you can move Fast Hunter to :54 by editing FH_MIN below and re-installing.
set -e
ROOT=${0:A:h:h}
LA=~/Library/LaunchAgents
FH_MIN=24
AP_MIN=40

plist() { # label job minute
  cat > "$LA/$1.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$1</string>
  <key>ProgramArguments</key><array><string>$ROOT/scripts/run.sh</string><string>$2</string></array>
  <key>StartCalendarInterval</key><dict><key>Minute</key><integer>$3</integer></dict>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>/usr/local/bin:/usr/bin:/bin:$HOME/.local/bin</string></dict>
</dict></plist>
EOF
  launchctl bootout gui/$(id -u) "$LA/$1.plist" 2>/dev/null || true
  launchctl bootstrap gui/$(id -u) "$LA/$1.plist"
}

plist2() { # label job "H:M H:M"
  local slots=""
  for hm in ${=3}; do slots+="<dict><key>Hour</key><integer>${hm%%:*}</integer><key>Minute</key><integer>${hm##*:}</integer></dict>"; done
  cat > "$LA/$1.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$1</string>
  <key>ProgramArguments</key><array><string>$ROOT/scripts/run.sh</string><string>$2</string></array>
  <key>StartCalendarInterval</key><array>$slots</array>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>/usr/local/bin:/usr/bin:/bin:$HOME/.local/bin</string></dict>
</dict></plist>
EOF
  launchctl bootout gui/$(id -u) "$LA/$1.plist" 2>/dev/null || true
  launchctl bootstrap gui/$(id -u) "$LA/$1.plist"
}

plistm() { # label job "M M M ..." -> every hour at those minutes
  local slots=""
  for m in ${=3}; do slots+="<dict><key>Minute</key><integer>$m</integer></dict>"; done
  cat > "$LA/$1.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$1</string>
  <key>ProgramArguments</key><array><string>$ROOT/scripts/run.sh</string><string>$2</string></array>
  <key>StartCalendarInterval</key><array>$slots</array>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>/usr/local/bin:/usr/bin:/bin:$HOME/.local/bin</string></dict>
</dict></plist>
EOF
  launchctl bootout gui/$(id -u) "$LA/$1.plist" 2>/dev/null || true
  launchctl bootstrap gui/$(id -u) "$LA/$1.plist"
}

case $1 in
  install-awake)   # Oct 14: keep the Mac from idle-sleeping so the 15-min hunter never stalls (closing the lid still sleeps)
    mkdir -p $LA
    cat > "$LA/com.jobhunter.awake.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.jobhunter.awake</string>
  <key>ProgramArguments</key><array><string>/usr/bin/caffeinate</string><string>-i</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict></plist>
PLIST
    launchctl bootout gui/$(id -u) "$LA/com.jobhunter.awake.plist" 2>/dev/null || true
    launchctl bootstrap gui/$(id -u) "$LA/com.jobhunter.awake.plist"
    echo "installed: keep-awake (caffeinate -i); undo: launchctl bootout gui/$(id -u) $LA/com.jobhunter.awake.plist";;
  install-hunter-15)   # Oct 13: Fast Hunter every 15 minutes (WINDOW_MIN=20 in config.py); apply schedule untouched
    mkdir -p $LA
    plistm com.jobhunter.fasthunter fast_hunter "9 24 39 54"
    echo "installed: Fast Hunter every 15 min (:09 :24 :39 :54)";;
  install-5)   # Oct 22 (owner): Fast Hunter + Apply Prep every 5 minutes; each has a lock, so a slot that finds the last run still going is skipped
    mkdir -p $LA
    plistm com.jobhunter.fasthunter fast_hunter "0 5 10 15 20 25 30 35 40 45 50 55"
    plistm com.jobhunter.applyprep apply_prep "2 7 12 17 22 27 32 37 42 47 52 57"
    echo "installed: Fast Hunter every 5 min (:00 :05 ...), Apply Prep every 5 min (:02 :07 ...)";;
  install-agents)
    mkdir -p $LA
    plist2 com.jobhunter.manager manager "2:47 18:47"
    plist2 com.jobhunter.outreach outreach "6:52 21:52"
    echo "installed: Manager 2:47/18:47, Outreach 6:52/21:52";;
  install)
    mkdir -p $LA
    plist com.jobhunter.fasthunter fast_hunter $FH_MIN
    plist com.jobhunter.applyprep apply_prep $AP_MIN
    echo "installed: Fast Hunter :$FH_MIN, Apply Prep :$AP_MIN (logs in data/logs/)";;
  uninstall)
    for l in com.jobhunter.fasthunter com.jobhunter.applyprep com.jobhunter.manager com.jobhunter.outreach; do
      launchctl bootout gui/$(id -u) "$LA/$l.plist" 2>/dev/null || true; rm -f "$LA/$l.plist"; done
    echo "removed";;
  status)
    launchctl list | grep jobhunter || echo "not installed";;
  *) echo "usage: $0 install|uninstall|status";;
esac
