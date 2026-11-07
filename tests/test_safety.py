"""Safety tests for the apply pipeline: no browser, no Notion, no LLM, no network.

  .venv/bin/python -m unittest discover -s tests
Uses profile.example.json (Jane Doe) and a temporary data/ directory.
"""
import datetime as dt, importlib, json, os, pathlib, sys, tempfile, types, unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'jobhunter'))
os.environ.setdefault('PROFILE', str(ROOT / 'profile.example.json'))
os.environ.pop('APPLY_ATS_ONLY', None)

import config  # noqa: E402

TMP = pathlib.Path(tempfile.mkdtemp(prefix='jh_test_'))
config.DATA = TMP
(TMP / 'apply_files').mkdir(exist_ok=True)

import apply_prep, notion, fast_hunter, prune, ats, applicant as P  # noqa: E402


def write_jsonl(name, rows):
    f = TMP / name; f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(''.join(json.dumps(r) + '\n' for r in rows))


class PostingKey(unittest.TestCase):
    def test_greenhouse_variants_share_a_key(self):
        a = apply_prep.posting_key('https://job-boards.greenhouse.io/acme/jobs/123456?utm_source=x')
        b = apply_prep.posting_key('https://boards.greenhouse.io/acme/jobs/123456')
        self.assertEqual(a, b)

    def test_embeds_are_not_collapsed(self):   # Oct 25: every embed posting looked like one job
        a = apply_prep.posting_key('https://job-boards.greenhouse.io/embed/job_app?for=acme&token=111')
        b = apply_prep.posting_key('https://job-boards.greenhouse.io/embed/job_app?token=222&for=acme')
        self.assertNotEqual(a, b)
        self.assertEqual(a, 'acme:111')

    def test_tracking_params_dropped(self):
        a = apply_prep.posting_key('https://jobs.example.com/careers/42?src=linkedin&utm_medium=x')
        self.assertEqual(a, 'https://jobs.example.com/careers/42')


class DuplicateSubmitGuard(unittest.TestCase):
    def setUp(self):
        apply_prep._DONE_KEYS = {}
        apply_prep._SUBS.update(mtime=None, rows=[])

    def test_pending_submission_blocks_resubmit(self):
        write_jsonl('submissions.jsonl', [{'at': '2026-11-05T10:00:00', 'company': 'Acme', 'pending': True,
                                           'url': 'https://job-boards.greenhouse.io/acme/jobs/999'}])
        got = apply_prep.already_submitted('https://boards.greenhouse.io/acme/jobs/999?gh_src=abc')
        self.assertIn('confirmation never seen', got)

    def test_unrelated_posting_passes(self):
        write_jsonl('submissions.jsonl', [{'at': '2026-11-05T10:00:00', 'company': 'Acme',
                                           'url': 'https://job-boards.greenhouse.io/acme/jobs/999'}])
        self.assertIsNone(apply_prep.already_submitted('https://job-boards.greenhouse.io/acme/jobs/1000'))

    def test_notion_failure_fails_closed(self):
        write_jsonl('submissions.jsonl', [])
        apply_prep._DONE_KEYS = None
        with mock.patch.object(notion, 'query', side_effect=RuntimeError('network down')):
            got = apply_prep.already_submitted('https://job-boards.greenhouse.io/acme/jobs/5')
        self.assertTrue(got and got.startswith('(could not check'))
        self.assertIsNone(apply_prep._DONE_KEYS)   # retried on the next call, not cached as "nothing applied"

    def test_gmail_failure_fails_closed(self):
        old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=3)).isoformat()
        fake = types.ModuleType('manager'); fake.agent = mock.Mock(side_effect=RuntimeError('claude not logged in'))
        with mock.patch.dict(sys.modules, {'manager': fake}):
            got = apply_prep.emailed_confirmation({'created': old, 'Company': 'Acme', 'Position': 'SWE'})
        self.assertIn('could not check Gmail', got)

    def test_unconfirmed_reason_matches(self):
        import auth  # the exact wording auth.submit uses must be recognised as "maybe submitted"
        src = pathlib.Path(auth.__file__).read_text()
        self.assertIn('clicked Submit ONCE', src)
        self.assertTrue(apply_prep.UNCONFIRMED.search('clicked Submit ONCE but saw no confirmation — it may have gone through'))


class DryGuard(unittest.TestCase):
    def _handler(self, armed=None):
        page = mock.Mock(url='https://job-boards.greenhouse.io/acme/jobs/1'); blocked = []
        apply_prep.dry_guard(page, blocked, armed)
        return page.route.call_args[0][1], blocked

    def _req(self, url, method='POST', body='', ct='application/json'):
        return types.SimpleNamespace(url=url, method=method, post_data=body, headers={'content-type': ct})

    def test_submit_post_is_blocked(self):
        h, blocked = self._handler()
        route = mock.Mock()
        h(route, self._req('https://boards-api.greenhouse.io/v1/boards/acme/jobs/1/applications', body='{"x":1}'))
        route.abort.assert_called_once(); self.assertEqual(len(blocked), 1)

    def test_analytics_and_uploads_pass(self):
        h, blocked = self._handler()
        for u in ('https://jobright.ai/swan/event/submit', 'https://x.nr-data.net/submit'):
            route = mock.Mock(); h(route, self._req(u)); route.continue_.assert_called_once()
        self.assertEqual(blocked, [])

    def test_armed_submit_passes(self):
        h, blocked = self._handler({'on': True})
        route = mock.Mock(); h(route, self._req('https://job-boards.greenhouse.io/acme/jobs/1/submit'))
        route.continue_.assert_called_once(); self.assertEqual(blocked, [])


class KeepTabs(unittest.TestCase):
    def test_filled_kept_only_without_auto_submit(self):
        with mock.patch.dict(os.environ, {'AUTO_SUBMIT': '0'}):
            self.assertTrue(apply_prep.will_keep({'status': 'FILLED', 'reason': '12 fields'}))
        with mock.patch.dict(os.environ, {'AUTO_SUBMIT': '1'}):
            self.assertFalse(apply_prep.will_keep({'status': 'FILLED', 'reason': '12 fields'}))
        self.assertFalse(apply_prep.will_keep({'status': 'NEEDS YOU', 'reason': 'posting closed (page says the job is gone)'}))


class Notes(unittest.TestCase):
    def test_original_fast_note_survives_many_prepends(self):
        notes = 'FAST | greenhouse | fit 83 | match 70% | evidence 60% | resume v6'
        for i in range(12):
            notes = notion.join_notes(f'NEEDS YOU – attempt {i} ' + 'x' * 600, notes)
            self.assertLessEqual(len(notes), notion.NOTES_MAX)
        self.assertTrue(notes.endswith('FAST | greenhouse | fit 83 | match 70% | evidence 60% | resume v6'))
        self.assertTrue(notes.startswith('NEEDS YOU – attempt 11'))
        import apply_now
        self.assertEqual(apply_now.fit({'Notes': notes}), 83)

    def test_short_notes_unchanged(self):
        self.assertEqual(notion.join_notes('A', 'B'), 'A | B')
        self.assertEqual(notion.join_notes('A', ''), 'A')


class Hunter(unittest.TestCase):
    def test_label_kind(self):
        self.assertEqual(fast_hunter.label_kind('Posted 12 minutes ago'), 'minutes')
        self.assertEqual(fast_hunter.label_kind('Posted Today'), 'today')
        self.assertEqual(fast_hunter.label_kind('Posted 3 days ago'), 'old')

    def test_same_company(self):
        self.assertTrue(fast_hunter.same_co('principal', 'principalfin'))
        self.assertFalse(fast_hunter.same_co('meta', 'metabase'))   # < 5 chars: no prefix match

    def test_last_ok_advances_only_successful_scripts(self):
        t_old = dt.datetime(2026, 11, 1, tzinfo=dt.timezone.utc); t_new = dt.datetime(2026, 11, 6, tzinfo=dt.timezone.utc)
        (TMP / 'hunt').mkdir(exist_ok=True)
        with mock.patch.object(fast_hunter, 'LAST_OK_JSON', TMP / 'hunt' / 'last_ok.json'), \
                mock.patch.object(fast_hunter, 'LAST_OK', TMP / 'hunt' / 'last_ok'):
            (TMP / 'hunt' / 'last_ok').write_text(t_old.isoformat())   # old single-value file = migration default
            fast_hunter.save_last_ok({'hunt': (5, 0), 'intern': (2, 0), 'bigtech': (0, 'failed: timeout')}, t_new)
            got = fast_hunter.load_last_ok()
        self.assertEqual(got['hunt'], t_new); self.assertEqual(got['bigtech'], t_old)


class AnswerPolicy(unittest.TestCase):
    def tearDown(self):
        importlib.reload(P); import formfill; importlib.reload(formfill)

    def test_work_auth_fallback_follows_profile(self):
        import formfill
        self.assertTrue(any('authori' in rx for rx, _ in formfill.FALLBACK))   # example profile: Yes / No
        P.DATA['answers']['needs_sponsorship'] = ['Yes', '^yes']
        importlib.reload(formfill)
        self.assertFalse(any('authori' in rx for rx, _ in formfill.FALLBACK))

    def test_start_month_from_profile(self):
        import formfill
        q = formfill.start_month_queries()
        self.assertEqual([x for x, _ in q], ['June 2027', 'July 2027'])   # example start_date: June 1, 2027

    def test_no_personal_dates_in_code(self):
        for f in (ROOT / 'jobhunter').rglob('*.py'):
            self.assertNotIn('Dec 30, 2026', f.read_text(), f.name)


class AtsDefault(unittest.TestCase):
    def test_default_is_greenhouse_ashby(self):
        self.assertEqual(sorted(ats.ONLY), ['ashby', 'greenhouse'])

    def test_all_removes_limit(self):
        with mock.patch.dict(os.environ, {'APPLY_ATS_ONLY': 'all'}):
            m = importlib.reload(ats)
            self.assertEqual(m.ONLY, [])
        importlib.reload(ats)


class Prune(unittest.TestCase):
    def test_old_lines_dropped_undated_kept_submissions_untouched(self):
        old = (dt.datetime.now(config.TZ) - dt.timedelta(days=90)).isoformat()
        new = dt.datetime.now(config.TZ).isoformat()
        write_jsonl('apply_results.jsonl', [{'at': old}, {'at': new}, {'no_date': 1}])
        write_jsonl('submissions.jsonl', [{'at': old, 'url': 'u'}])
        prune.main(days=60)
        lines = (TMP / 'apply_results.jsonl').read_text().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(len((TMP / 'submissions.jsonl').read_text().splitlines()), 1)


if __name__ == '__main__':
    unittest.main()
