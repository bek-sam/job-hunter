"""Daily housekeeping for data/ (0 tokens). Logs used to grow forever.

Pruned to the last KEEP_DAYS (default 60) by their 'at' timestamp: apply_results.jsonl, llm_usage.jsonl, kept_tabs.jsonl,
answer_log/rescue.jsonl. seen.db: non-built verdicts (stale / retry / low / blocked) older than KEEP_DAYS.
apply_files/: per-row resume downloads older than 7 days. Run logs (data/runs/*.md) older than KEEP_DAYS.
NEVER pruned: submissions.jsonl (duplicate-submission guard) and the form corpora (answer_log/forms.jsonl,
wd_claude.jsonl, workday_corpus/) that qa_db.py / question_bank.py learn from.

  python prune.py            # what fast_hunter runs once a day
  python prune.py --dry      # only print what would be removed
"""
import argparse, datetime as dt, json, os, shutil, sqlite3, time
import config

JSONL = ['apply_results.jsonl', 'llm_usage.jsonl', 'kept_tabs.jsonl', 'answer_log/rescue.jsonl']


def _when(d):
    try:
        t = dt.datetime.fromisoformat(d['at'])
        return t if t.tzinfo else t.replace(tzinfo=config.TZ)
    except Exception:
        return None


def prune_jsonl(path, cut, dry=False):
    if not path.exists(): return 0
    keep, drop = [], 0
    for line in path.read_text().splitlines():
        try: t = _when(json.loads(line))
        except Exception: t = None
        if t is not None and t < cut: drop += 1
        else: keep.append(line)   # unparsable lines are kept (never lose data we can't date)
    if drop and not dry:
        tmp = path.with_suffix(path.suffix + '.tmp')
        tmp.write_text('\n'.join(keep) + ('\n' if keep else '')); os.replace(tmp, path)
    return drop


def prune_seen(cut, dry=False):
    f = config.DATA / 'seen.db'
    if not f.exists(): return 0
    c = sqlite3.connect(f)
    q = "from seen where verdict != 'built' and at < ?"
    n = c.execute('select count(*) ' + q, (cut.isoformat(),)).fetchone()[0]
    if n and not dry: c.execute('delete ' + q, (cut.isoformat(),)); c.commit(); c.execute('vacuum')
    c.close(); return n


def prune_dirs(root, max_age_s, dry=False, pattern='*'):
    n = 0
    if not root.exists(): return 0
    for p in root.glob(pattern):
        try:
            if time.time() - p.stat().st_mtime > max_age_s:
                n += 1
                if not dry: shutil.rmtree(p) if p.is_dir() else p.unlink()
        except OSError:
            continue
    return n


def main(days=None, dry=False):
    days = days or int(os.environ.get('KEEP_DAYS', '60'))
    cut = dt.datetime.now(config.TZ) - dt.timedelta(days=days)
    out = {name: prune_jsonl(config.DATA / name, cut, dry) for name in JSONL}
    out['seen.db rows'] = prune_seen(cut.astimezone(dt.timezone.utc), dry)
    out['apply_files dirs'] = prune_dirs(config.DATA / 'apply_files', 7 * 86400, dry)
    out['run logs'] = prune_dirs(config.DATA / 'runs', days * 86400, dry, '*.md')
    print(('[dry] ' if dry else '') + 'prune: ' + ', '.join(f'{k}={v}' for k, v in out.items()))
    return out


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--days', type=int); ap.add_argument('--dry', action='store_true')
    a = ap.parse_args(); main(a.days, a.dry)
