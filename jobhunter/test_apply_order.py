"""Newest job postings must take priority across the entire automated queue."""
import os
import unittest
from unittest.mock import patch
import apply_prep


def row(id, posted, created, **extra):
    return dict(id=id, **{'Date Posted': posted}, created=created, Status='To apply',
                Link='https://example.com/job/' + id, Notes='FAST | fit 80', **extra)


class ApplyOrderTests(unittest.TestCase):
    def test_posted_date_beats_import_time_and_entry_title(self):
        old = row('old', '2026-11-01', '2026-11-05T02:00:00Z', Position='New Grad', Type='New Grad')
        new = row('new', '2026-11-04', '2026-11-04T01:00:00Z', Position='Software Engineer')
        self.assertEqual(sorted([old, new], key=apply_prep.newest_first, reverse=True), [new, old])

    def test_exact_time_tiebreak_and_missing_date_fallback(self):
        earlier = row('earlier', '2026-11-04', '2026-11-04T21:01:00Z')
        later = row('later', '2026-11-04', '2026-11-04T21:02:00Z')
        missing = row('missing', None, '2026-11-03T01:00:00Z')
        invalid = row('invalid', 'unknown', None)
        self.assertEqual([r['id'] for r in sorted([earlier, invalid, missing, later],
                         key=apply_prep.newest_first, reverse=True)], ['later', 'earlier', 'missing', 'invalid'])

    @patch.dict(os.environ, {'BACKLOG_DAYS': '14', 'APPLY_FOCUS': '.'})
    def test_global_queue_order_preserves_dedupe_and_retry_guards(self):
        fast = row('fast', '2026-11-03', '2026-11-05T01:00:00Z')
        backlog = row('backlog', '2026-11-04', '2026-11-01T01:00:00Z')
        manual = row('manual', '2026-11-05', '2026-11-05T02:00:00Z')
        retried = row('retried', '2026-11-06', '2026-11-05T03:00:00Z')
        done = row('done', '2026-11-07', '2026-11-05T04:00:00Z'); done['Status'] = 'Applied'
        with patch.object(apply_prep.notion, 'query', side_effect=[[fast, retried], [manual, fast, done], [backlog]]), \
             patch.object(apply_prep, 'attempts', return_value={'retried': 10000}), \
             patch.object(apply_prep, 'recent_fails', return_value={}), \
             patch.object(apply_prep.time, 'time', return_value=10000), \
             patch.object(apply_prep.ats, 'ONLY', []):
            self.assertEqual([r['id'] for r in apply_prep.rows_to_fill()], ['manual', 'backlog', 'fast'])


if __name__ == '__main__':
    unittest.main()
