"""Persist coverage failures separately from job results; partial scans stay recoverable."""
import datetime as dt
import json
from pathlib import Path


def write(output, failures, **counts):
    path = Path(str(output) + '.health.json')
    report = {'at': dt.datetime.now(dt.timezone.utc).isoformat(), 'complete': not bool(failures),
              'failures': failures, **counts}
    path.write_text(json.dumps(report, indent=2))
    return report


def read(output):
    path = Path(str(output) + '.health.json')
    try:
        report = json.loads(path.read_text())
        # An old sidecar must never silently describe a newer output file.
        if path.stat().st_mtime + 1 < Path(output).stat().st_mtime: return None
        return report
    except (OSError, ValueError):
        return None
