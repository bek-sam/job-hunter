"""Offline browser regressions derived from the Nov 4 failed applications."""
import datetime as dt
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from playwright.sync_api import sync_playwright
import application_state as state
import confirmation
import formfill
import auth
import apply_prep
from ats import _shadow, generic, icims, smartrecruiters

FIXTURES = Path(__file__).parent / 'testdata/application_failures'


class BrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(headless=True, executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')

    @classmethod
    def tearDownClass(cls):
        cls.browser.close(); cls.pw.stop()

    def setUp(self):
        self.ctx = self.browser.new_context()
        self.ctx.set_default_timeout(2000)
        self.ctx.route('**/*', lambda route: route.abort())
        self.page = self.ctx.new_page()

    def tearDown(self):
        self.ctx.close()

    def test_shadowdragon_missing_upload_radio_and_select(self):
        self.page.set_content((FIXTURES / 'shadowdragon.html').read_text())
        missing = state.missing(self.page)
        self.assertTrue(any('Résumé' in x for x in missing), missing)
        self.assertTrue(any('sponsorship' in x for x in missing), missing)
        self.assertTrue(any('SMS' in x for x in missing), missing)
        self.assertTrue(formfill.still_empty(self.page))

    def test_persisted_required_radio_group_and_file(self):
        self.page.set_content((FIXTURES / 'shadowdragon.html').read_text())
        self.page.locator('#resume').set_input_files({'name':'resume.pdf','mimeType':'application/pdf','buffer':b'fixture'})
        self.page.select_option('#sponsor', 'no')
        self.page.locator('input[value=no]').check()
        self.assertEqual(state.missing(self.page), [])

    def test_shadow_required_controls_are_not_invisible_to_validator(self):
        self.page.set_content((FIXTURES / 'smartrecruiters.html').read_text())
        self.assertIn('First name', state.missing(self.page))
        self.page.locator('spl-test input').fill('Test')
        self.assertFalse(state.missing(self.page))
        self.assertIn('Application has another Next/Continue step', state.submission_issues(self.page))
        self.page.click('#advance')
        self.assertTrue(any('authorized' in x for x in state.missing(self.page)))

    def test_extraction_failure_is_not_ready(self):
        with patch.object(self.page, 'evaluate', side_effect=RuntimeError('detached')):
            self.assertFalse(state.inspect(self.page)['ok'])
            self.assertTrue(state.submission_issues(self.page))
            self.assertTrue(formfill.still_empty(self.page))

    def test_chat_iframe_does_not_win_over_login(self):
        self.page.set_content('<iframe name="chat"></iframe><iframe name="application"></iframe>')
        self.ctx.route('https://assets.textrecruit.com/**', lambda route: route.fulfill(body='<label>First name<input></label><input type=file>', content_type='text/html'))
        self.page.frame(name='chat').goto('https://assets.textrecruit.com/chat')
        app = self.page.frame(name='application')
        app.set_content('<label>Email<input type=email required></label><label>Password<input type=password required></label>')
        self.assertIs(state.form_frame(self.page), app)
        fake = type('Frame', (), {'url':'https://assets.textrecruit.com/chat'})()
        self.assertEqual(state.frame_score(fake), -1)

    def test_placeholder_and_invalid_email_are_not_accepted(self):
        self.page.set_content('<label>Email<input type=email required value=bad></label><label>Question *<button role=combobox>Select</button></label>')
        self.assertTrue(any('invalid' in x for x in state.missing(self.page)))
        self.assertTrue(any('Question' in x for x in state.missing(self.page)))

    @patch.dict(os.environ, {'AUTO_SUBMIT':'1'})
    def test_submit_does_not_click_when_required_fields_are_empty(self):
        self.page.set_content('<label>Email *<input type=email required></label><button onclick="window.sent=true">Submit</button>')
        res = auth.submit(self.page, {'status':'FILLED','done':['Resume']})
        self.assertEqual(res['submission_state'], 'not_submitted')
        self.assertFalse(self.page.evaluate('!!window.sent'))

    @patch.dict(os.environ, {'AUTO_SUBMIT':'1'})
    def test_submit_waits_for_final_wizard_step(self):
        self.page.set_content('<input required value=ok><button onclick="window.sent=true">Next</button>')
        res = auth.submit(self.page, {'status':'FILLED'})
        self.assertEqual(res['status'], 'NEEDS YOU')
        self.assertFalse(self.page.evaluate('!!window.sent'))

    def test_shadow_radio_rules_persist(self):
        self.page.set_content('<div id=host></div><script>document.querySelector("#host").attachShadow({mode:"open"}).innerHTML=`<fieldset><legend>Are you legally authorized to work? *</legend><label><input type=radio required name=auth>Yes</label><label><input type=radio required name=auth>No</label></fieldset>`</script>')
        done, unknown = _shadow.fill(self.page, {'no_llm': True})
        self.assertFalse(unknown, unknown)
        self.assertTrue(self.page.locator('input').first.is_checked())

    def test_autocomplete_never_picks_wrong_first_option(self):
        self.page.set_content('<input role=combobox><div role=option onclick="window.wrong=true">Wrong City</div>')
        selected = _shadow.autocomplete_pick(self.page, self.page.locator('input'), 'Expected City', wait=0)
        self.assertIsNone(selected)
        self.assertFalse(self.page.evaluate('!!window.wrong'))

    def test_generic_redirect_dispatches_to_icims(self):
        page = type('Page', (), {'url':'https://careers-example.icims.com/jobs/12/login'})()
        with patch.object(icims, 'run', return_value={'status':'NEEDS YOU'}) as run:
            result = generic.run(None, page, {})
            run.assert_called_once()
            self.assertEqual(result['ats'], 'icims')

    def test_smartrecruiters_fills_every_step_without_submitting(self):
        self.page.set_content((FIXTURES / 'smartrecruiters.html').read_text())
        with patch.object(smartrecruiters, '_to_form', return_value=self.page), \
             patch.object(smartrecruiters, 'captcha', return_value=False):
            # URL check remains real, with no network navigation.
            self.page.evaluate("history.replaceState(null,'','about:blank#oneclick-ui')")
            res = smartrecruiters.run(self.ctx, self.page, {'no_llm':True})
        self.assertEqual(res['status'], 'FILLED', res)
        self.assertEqual(self.page.locator('#advance').inner_text(), 'Submit')
        self.assertFalse(self.page.evaluate('!!window.submitted'))


class ConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.row = {'Company':'Example','Position':'Software Engineer I','Link':'https://jobs.example.com/jobs/12345'}
        self.evidence = {'found':True,'message_id':'m1','is_application_confirmation':True,'company':'Example',
                         'role':'Software Engineer I','received_at':dt.datetime.now(dt.timezone.utc).isoformat(),
                         'excerpt':'Example received your application for Software Engineer I.'}

    def test_exact_role_and_employer_match(self):
        self.assertTrue(confirmation.matched(self.evidence, self.row))

    def test_same_employer_different_role_is_not_confirmation(self):
        self.evidence.update(role='Software Engineer II',excerpt='Example received your application for Software Engineer II.')
        self.assertFalse(confirmation.matched(self.evidence, self.row))

    def test_requisition_can_match_uuid_board_employer(self):
        self.evidence.update(company='Actual Employer',role='Different display title',excerpt='Application received for requisition 12345.')
        self.assertTrue(confirmation.matched(self.evidence, dict(self.row,Company='uuid')))

    def test_requisition_id_must_match_as_a_whole(self):
        self.row['Link'] = 'https://jobs.example.com/jobs/123'
        self.evidence.update(company='Wrong employer', role='Different role',
                             excerpt='Application received for requisition 1234.')
        self.assertFalse(confirmation.matched(self.evidence, self.row))

    def test_old_email_cannot_confirm_new_click(self):
        self.evidence['received_at']=(dt.datetime.now(dt.timezone.utc)-dt.timedelta(hours=3)).isoformat()
        self.assertFalse(confirmation.matched(self.evidence,self.row,dt.datetime.now(dt.timezone.utc).isoformat()))

    def test_unconfirmed_submit_remains_protected(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(apply_prep.config, 'DATA', Path(directory)):
            p=Path(directory)/'apply_results.jsonl'
            records=[{'id':'one','status':'NEEDS YOU','reason':'clicked Submit ONCE but saw no confirmation'},
                     {'id':'one','status':'ERROR','reason':'timeout'},
                     {'id':'two','status':'NEEDS YOU','reason':'no confirmation'},
                     {'id':'two','status':'SUBMITTED'}]
            p.write_text('\n'.join(json.dumps(r) for r in records))
            self.assertEqual(apply_prep.unconfirmed_jobs(), {'one'})

if __name__ == '__main__': unittest.main()
