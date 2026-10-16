"""The uploaded behavior profile reaches real default and resumed agent requests."""
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from . import agent, prompts, session, settings


class PromptTests(unittest.TestCase):
    def test_cloud_prompt_uses_musabai_profile_and_actual_tools(self):
        text = prompts.system('sh')
        self.assertTrue(text.startswith('MusabAI behavior profile'))
        self.assertIn('When you have enough information to act, act.', text)
        self.assertIn('memory_recall', text)
        self.assertIn('self_evolve_verify', text)
        self.assertNotIn('claude-fable-5-1', text)
        self.assertNotIn('asgeirtj', text)
        self.assertNotIn('ToolSearch', text)
        self.assertLess(len(text), 16000)

    def test_local_profile_keeps_budget_and_phone_environment(self):
        text = prompts.system('sh', local=True, steps=25, phone=True)
        self.assertIn('a turn has 25 steps', text)
        self.assertIn('Android phone', text)
        self.assertIn('MusabAI behavior profile', text)

    def test_existing_builtin_session_migrates_without_losing_messages(self):
        with tempfile.TemporaryDirectory() as root, patch.object(settings, 'HOME', root):
            s = session.Session(root)
            s.system = "You are NewAl Code, a coding agent working in the user's project on their computer."
            s.messages = [{'role': 'user', 'content': 'keep the current task'}]
            a = agent.Agent(s, client=SimpleNamespace(on_device=False, local=False, spec={}))
            self.addCleanup(a.memory.close)
            messages = a.request_messages()
            self.assertTrue(messages[0]['content'].startswith('MusabAI behavior profile'))
            self.assertEqual(messages[1]['content'], 'keep the current task')
