"""Regression tests for repeated context compaction."""
import json
import os
import shutil
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from . import continuity


class TaskContinuityTests(unittest.TestCase):
    def test_preserves_original_and_latest_user(self):
        s = SimpleNamespace(
            messages=[
                {"role": "user", "content": "<context>Musab new</context>\nلعبة طيارة ودبابة بالـ HTML"},
                {"role": "tool", "content": "created index.html, missing game.js", "tool_call_id": "1"},
                {"role": "user", "content": "شغل اللعبة"},
            ], active_objective="شغل اللعبة", goal="", todo=[])
        first = s.messages[0]
        for _ in range(4):
            s.messages = continuity.compact_messages(s, "(no summary)")
            self.assertEqual([m["role"] for m in s.messages], ["user", "assistant", "user"])
            self.assertEqual(s.messages[0], first)
            self.assertEqual(s.messages[-1]["content"], "شغل اللعبة")
            self.assertIn("Active request: شغل اللعبة", s.messages[1]["content"])
            self.assertNotIn("Understood. I'll continue from here.", str(s.messages))

    def test_legacy_fake_user_messages_not_trusted(self):
        s = SimpleNamespace(messages=[
            {"role": "user", "content": "<context>x</context>\nSummary of the conversation so far:\n(none)"},
            {"role": "user", "content": "Conversation compacted: (no summary)"},
        ], active_objective="شغل اللعبة", goal="", todo=[])
        out = continuity.compact_messages(s, "")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["role"], "assistant")
        self.assertIn("شغل اللعبة", out[0]["content"])

    def test_lost_task_is_detected_once(self):
        self.assertTrue(continuity.lost_task_reply("I don't have a task.", "make a game"))
        self.assertTrue(continuity.lost_task_reply("ما عندي مهمة، شو بدك أعمل؟", "شغل اللعبة"))
        self.assertFalse(continuity.lost_task_reply("I don't have a task.", ""))
        self.assertFalse(continuity.lost_task_reply("Game needs game.js.", "make a game"))

    def test_repeated_compaction_without_tool_progress_stops_instead_of_looping(self):
        from .agent import Agent
        from . import providers
        s = SimpleNamespace(
            messages=[{"role":"user","content":"Build HTML plane and tank game"},
                      {"role":"assistant","content":"Started work"},
                      {"role":"assistant","content":"Still inspecting"},
                      {"role":"user","content":"Continue testing"}],
            goal="", todo=[{"content":"Run browser tests","status":"pending"}],
            active_objective="Continue testing", last_prompt_tokens=980,
            compact_since_tool=0, id="test")
        s.replace_messages = lambda msgs, note="": setattr(s, "messages", msgs)
        s.save_meta = Mock()
        a = Agent.__new__(Agent)
        a.session = s
        a.cfg = {"auto_compact": .8}
        a.client = SimpleNamespace(context=lambda: 1000,
                                   chat=Mock(return_value=SimpleNamespace(content="Summary")))
        a.request_messages = lambda: list(s.messages)
        a.cancel = threading.Event()
        a.emit = Mock()
        a._prune_outputs = lambda: 0
        for turn in range(2):
            self.assertTrue(a._maybe_compact())
            self.assertEqual(s.compact_since_tool, turn + 1)
            s.messages.append({"role":"assistant","content":"Waiting without tool progress"})
            s.last_prompt_tokens = 950
        with self.assertRaises(providers.ProviderError):
            a._maybe_compact()
        self.assertEqual(a.client.chat.call_count, 2,
                         "No third summary request may be sent without tool progress")

    def test_patched_agent_compaction(self):
        from .agent import Agent
        s = SimpleNamespace(
            messages=[{"role":"user","content":"اعمل لعبة HTML"},
                      {"role":"assistant","content":"بدأت بناء الملفات"},
                      {"role":"tool","content":"index.html exists; game.js missing"},
                      {"role":"user","content":"شغل اللعبة"}],
            goal="", todo=[], active_objective="شغل اللعبة", last_prompt_tokens=980, id="test")
        s.replace_messages = lambda msgs, note="": setattr(s, "messages", msgs)
        s.save_meta = Mock()
        s.compact_since_tool = 0
        agent = Agent.__new__(Agent)
        agent.session = s
        agent.cfg = {"auto_compact": .8}
        agent.client = SimpleNamespace(context=lambda: 1000,
                                       chat=Mock(return_value=SimpleNamespace(content="(no summary)")))
        agent.request_messages = lambda: list(s.messages)
        agent.cancel = threading.Event()
        agent.emit = Mock()
        self.assertTrue(agent._maybe_compact(force=True))
        self.assertEqual([m["role"] for m in s.messages], ["user", "assistant", "user"])
        self.assertIsNone(agent.client.chat.call_args.kwargs["tools"])


class PruneRecordAndScreenTests(unittest.TestCase):
    def setUp(self):
        from . import settings
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        sessions = patch.object(settings, "SESSIONS", os.path.join(self.dir, "sessions"))
        sessions.start()
        self.addCleanup(sessions.stop)

    def _session(self):
        from . import session
        s = session.Session(self.dir)
        s.save_meta()
        return s

    def _tool_turn(self, s, n, size=500):
        call = "call-%d" % n
        name = ("phone", "read", "grep")[n % 3]
        s.add({"role": "assistant", "content": "", "tool_calls": [
            {"id": call, "type": "function", "function": {"name": name, "arguments": "{}"}}]})
        s.add({"role": "tool", "tool_call_id": call, "content": "result %d " % n + "x" * size})

    def _prune(self, s):
        from .agent import Agent
        return Agent._prune_outputs(SimpleNamespace(session=s, PRUNE_KEEP=Agent.PRUNE_KEEP, emit=Mock()))

    def test_pruned_results_resume_to_the_same_conversation(self):
        from . import session
        s = self._session()
        s.add({"role": "user", "content": "open the chat with Ahmad and send hi"})
        for n in range(10):
            self._tool_turn(s, n)
        self.assertEqual(self._prune(s), 4)
        s.replace_messages(continuity.compact_messages(s, "summary", "send hi to Ahmad"))
        for n in range(10, 18):
            self._tool_turn(s, n)
        self.assertEqual(self._prune(s), 2)
        with open(s.path, encoding="utf-8") as f:
            kinds = [json.loads(line)["t"] for line in f]
        self.assertIn("prune", kinds)
        self.assertEqual(kinds.count("reset"), 1, "pruning must not write the whole conversation again")
        self.assertEqual(session.Session.load(s.id).messages, s.messages)

    def test_a_prune_adds_a_short_line_not_the_conversation(self):
        s = self._session()
        s.add({"role": "user", "content": "open the chat with Ahmad and send hi"})
        for n in range(12):
            self._tool_turn(s, n, size=3000)
        before = os.path.getsize(s.path)
        self.assertGreater(before, 30000)
        self.assertEqual(self._prune(s), 6)
        self.assertLess(os.path.getsize(s.path) - before, 2000)

    def test_compaction_note_carries_the_phone_screen_as_content(self):
        s = SimpleNamespace(messages=[
            {"role": "user", "content": "send hi to Ahmad on WhatsApp"},
            {"role": "tool", "tool_call_id": "1", "content": "opened WhatsApp"},
            {"role": "user", "content": "send hi to Ahmad on WhatsApp"}],
            active_objective="send hi to Ahmad", goal="", todo=[])
        with_screen = continuity.compact_messages(s, "summary", "send hi to Ahmad", screen="App: WhatsApp\n[1] Ahmad")
        self.assertEqual([m["role"] for m in with_screen], ["user", "assistant", "user"])
        self.assertIn("Phone screen now", with_screen[1]["content"])
        self.assertIn("[1] Ahmad", with_screen[1]["content"])
        plain = continuity.compact_messages(s, "summary", "send hi to Ahmad")
        self.assertNotIn("Phone screen now", plain[1]["content"])

    def test_screen_is_read_only_when_this_turn_used_the_phone(self):
        from . import tools, phone_fast  # tools first: it loads phone_fast while it is being imported
        s = SimpleNamespace(turn=3)
        agent = SimpleNamespace()
        agent.last_ctx = SimpleNamespace(agent=agent, session=s)
        with patch.object(phone_fast, "read_screen", return_value="App: WhatsApp\n[1] Ahmad") as read:
            self.assertEqual(phone_fast.compact_screen(agent), "")
            read.assert_not_called()
            agent._phone_turn = {"turn": 3, "log": [("tap", {"item": 1}, True)], "screen": "", "routine": None}
            self.assertEqual(phone_fast.compact_screen(agent), "App: WhatsApp\n[1] Ahmad")
            read.assert_called_once()
            self.assertEqual(agent._phone_turn["screen"], "App: WhatsApp\n[1] Ahmad")
        self.assertEqual(phone_fast.compact_screen(SimpleNamespace()), "")

    def test_automatic_compaction_after_phone_work_keeps_the_screen_now(self):
        from . import tools, phone_fast  # tools first: it loads phone_fast while it is being imported
        from .agent import Agent
        s = self._session()
        for role, text in (("user", "send hi to Ahmad on WhatsApp"), ("assistant", "opening WhatsApp"),
                           ("assistant", "chat list open"), ("user", "send hi to Ahmad on WhatsApp")):
            s.add({"role": role, "content": text})
        s.last_prompt_tokens = 980
        a = Agent.__new__(Agent)
        a.session = s
        a.cfg = {"auto_compact": .8}
        a.client = SimpleNamespace(context=lambda: 1000,
                                   chat=Mock(return_value=SimpleNamespace(content="Opened the chat list")))
        a.request_messages = lambda: list(s.messages)
        a.cancel = threading.Event()
        a.emit = Mock()
        a._prune_outputs = lambda: 0
        a.last_ctx = SimpleNamespace(agent=a, session=s)
        a._phone_turn = {"turn": s.turn, "log": [("open_app", {"name": "com.whatsapp"}, True)],
                         "screen": "", "routine": None}
        with patch.object(phone_fast, "read_screen", return_value="App: WhatsApp\n[1] Ahmad\n[2] Sara") as read:
            self.assertTrue(a._maybe_compact())
            read.assert_called_once()
            self.assertIn("Phone screen now", s.messages[1]["content"])
            self.assertIn("[1] Ahmad", s.messages[1]["content"])
            s.add({"role": "assistant", "content": "waiting"})
            self.assertTrue(a._maybe_compact(force=True))      # the /compact command does not read the phone
            read.assert_called_once()


if __name__ == "__main__":
    unittest.main()
