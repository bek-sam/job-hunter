#!/bin/zsh
# Wrapper used by launchd: run one job with the project venv and keep a log.
# usage: scripts/run.sh fast_hunter|apply_prep [args...]
cd "$(dirname "$0")/../jobhunter" || exit 1
mkdir -p ../data/logs
job=$1; shift
# caffeinate -i: the Mac must not idle-sleep mid-run (a paused run holds the lock and blocks the next 15-min slots)
exec /usr/bin/caffeinate -i ../.venv/bin/python "$job.py" "$@" >> "../data/logs/$job.log" 2>&1
