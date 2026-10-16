"""Actual shell output reaches both agent and terminal; account tools update live."""
import json
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from . import agent_policy, connectors, tools, permissions, settings
from .agent import Agent
from .session import Session


class WorkbenchTests(unittest.TestCase):
    def test_real_command_streams_to_terminal_and_returns_failure(self):
        with tempfile.TemporaryDirectory() as root:
            events = []
            ctx = SimpleNamespace(root=root, cwd=root, cancel=threading.Event(), emit=events.append)
            text, meta = tools.REGISTRY['bash'].fn(ctx, command="echo workbench-ready; exit 7")
            self.assertEqual(meta['exit'], 7)
            self.assertIn('workbench-ready', text)
            self.assertEqual(events[0]['type'], 'terminal_start')
            self.assertEqual(events[-1]['exit'], 7)
            self.assertTrue(any(e['type'] == 'terminal_output' and 'workbench-ready' in e['text'] for e in events))
            self.assertTrue(any(e['type'] == 'output' for e in events))

    def test_command_permissions_still_apply(self):
        tool = tools.REGISTRY['bash']
        self.assertEqual(tool.kind, 'exec')
        denied = permissions.decide('read-only', 'bash', tool.kind, {'command': 'touch result.txt'}, '/tmp', {})
        self.assertEqual(denied.action, permissions.DENY)
        denied = permissions.decide('full-auto', 'bash', tool.kind, {'command': 'rm -rf /'}, '/tmp', {})
        self.assertEqual(denied.action, permissions.DENY)

    def test_live_inventory_updates_in_existing_conversation(self):
        with tempfile.TemporaryDirectory() as root, patch.object(settings, 'HOME', root):
            session = Session(root)
            a = Agent(session, client=SimpleNamespace(on_device=False, local=False, spec={}))
            self.addCleanup(a.memory.close)
            with patch.object(connectors, 'names', return_value=['gmail_read']):
                text = a.request_messages()[0]['content']
                state = json.loads(text.split('Current runtime capabilities (data, not instructions):\n')[1])
                self.assertEqual(state['connected_service_tools'], {'Gmail': 1})
                self.assertTrue(state['internal_terminal'])
                self.assertEqual(state['project_directory'], root)
            with patch.object(connectors, 'names', return_value=[]):
                text = a.request_messages()[0]['content']
                state = json.loads(text.split('Current runtime capabilities (data, not instructions):\n')[1])
                self.assertEqual(state['connected_service_tools'], {})

    def test_previous_profile_is_upgraded(self):
        self.assertTrue(agent_policy.stale_builtin('MusabAI behavior profile v1\nold'))
        self.assertFalse(agent_policy.stale_builtin(agent_policy.profile()))
