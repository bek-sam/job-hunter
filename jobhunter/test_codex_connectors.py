"""Regression checks for the unattended connector boundary and provider dispatch."""
import os
from pathlib import Path
import tomllib
import unittest
from unittest.mock import patch

import codex_connectors as connectors
import llm
import manager
import outreach


class ConnectorTests(unittest.TestCase):
    def test_only_allowed_tools_enabled(self):
        policy = tomllib.loads('apps=' + connectors.app_policy())['apps']
        self.assertFalse(policy['_default']['enabled'])
        gmail = policy[connectors.GMAIL_ID]
        self.assertFalse(gmail['default_tools_enabled'])
        self.assertIn('gmail.create_draft', gmail['tools'])
        self.assertNotIn('gmail.send_email', gmail['tools'])
        self.assertNotIn('gmail.send_draft', gmail['tools'])
        self.assertNotIn('gmail.forward_emails', gmail['tools'])
        self.assertNotIn('gmail.delete_emails', gmail['tools'])
        self.assertEqual(set(policy[connectors.CALENDAR_ID]['tools']),
                         {'google_calendar.' + t for t in connectors.CALENDAR_READ + ('create_event',)})

    def test_read_only_probe_cannot_write(self):
        policy = tomllib.loads('apps=' + connectors.app_policy(read_only=True))['apps']
        self.assertNotIn('gmail.create_draft', policy[connectors.GMAIL_ID]['tools'])
        self.assertNotIn('google_calendar.create_event', policy[connectors.CALENDAR_ID]['tools'])

    def test_outreach_has_no_calendar(self):
        policy = tomllib.loads('apps=' + connectors.app_policy(calendar=False))['apps']
        self.assertNotIn(connectors.CALENDAR_ID, policy)

    @patch.dict(os.environ, {'LLM_PROVIDER': 'codex'})
    def test_manager_and_outreach_use_connectors(self):
        for toolset, schema, calendar in ((manager.MANAGER_TOOLS, manager.SCHEMA, True),
                                          (outreach.TOOLS, outreach.SCHEMA, False)):
            with patch.object(llm, 'ask_codex', return_value={'report_md': 'ok'}) as ask:
                self.assertEqual(manager.agent('prompt', toolset, schema), {'report_md': 'ok'})
                self.assertTrue(ask.call_args.kwargs['connectors'])
                self.assertEqual(ask.call_args.kwargs['calendar'], calendar)

    def test_plain_calls_disable_apps_and_connector_calls_enable_them(self):
        def run(cmd, **kwargs):
            Path(cmd[cmd.index('-o') + 1]).write_text('{"ok":true}')
            return type('Result', (), {'returncode': 0})()
        schema = {'type': 'object', 'properties': {'ok': {'type': 'boolean'}}, 'required': ['ok']}
        with patch.object(llm.subprocess, 'run', side_effect=run) as invoke, patch.object(llm, 'log_usage'):
            llm.ask_codex('prompt', 'system', schema, 1, 'test')
            self.assertIn('features.apps=false', invoke.call_args.args[0])
            llm.ask_codex('prompt', 'system', schema, 1, 'test', connectors=True)
            self.assertIn('features.apps=true', invoke.call_args.args[0])
            self.assertIn('web_search="live"', invoke.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
