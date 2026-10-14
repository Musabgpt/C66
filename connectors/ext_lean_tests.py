"""The lean phone build: only Termux, GitHub, code checks, Chrome/web and the phone are offered; phone actions come back
with the new screen; a phone task that ended well becomes a routine that the next request like it replays."""
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from . import tools  # noqa: F401 - registers every tool first, as the engine does


WHATSAPP_HOME = "App: WhatsApp (com.whatsapp)\n[1] Chats · tap\n[2] (search) · tap\n[3] Ahmad · tap"
SEARCH = "App: WhatsApp (com.whatsapp)\n[1] hint: Search… · field, focused"
RESULT = "App: WhatsApp (com.whatsapp)\n[1] مروة · tap\n[2] مروان · tap"
CHAT = "App: WhatsApp (com.whatsapp)\n[1] مروة\n[2] hint: Message · field\n[3] Send · button, tap"


class FakePhone:
    """The app's phone server: a WhatsApp that walks through home, search, results and a chat."""
    def __init__(self):
        self.calls = []
        self.screen = "App: Launcher (com.android.launcher)\n[1] WhatsApp · tap"

    def __call__(self, action, timeout=90, **args):
        self.calls.append((action, args))
        r = self.act(action, args)
        if action in ("open_app", "tap", "type") and r.get("ok"):
            r["screen"] = self.screen                 # the app sends the new screen with the action
        return r

    def act(self, action, args):
        if action == "screen":
            return {"ok": True, "text": self.screen}
        if action == "open_app":
            self.screen = WHATSAPP_HOME
            return {"ok": True, "text": "opened WhatsApp"}
        if action == "tap":
            label = args.get("text") or {2: "search", 1: "first"}.get(args.get("item"), "")
            if label in ("search", "(search)"):
                self.screen = SEARCH
            elif label in ("first", "مروة", "أحمد"):
                self.screen = CHAT
            elif label == "Send":
                self.screen = CHAT + "\n[4] sent ✓"
            else:
                return {"ok": False, "error": "nothing on the screen says \"%s\"" % label}
            return {"ok": True, "text": "tapped %s" % label}
        if action == "type":
            if self.screen == SEARCH:
                self.screen = RESULT
            return {"ok": True, "text": "typed"}
        return {"ok": True, "text": "done"}


class LeanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.realpath(self.tmp.name)
        from . import settings
        p = mock.patch.object(settings, "HOME", os.path.join(self.root, "home"))
        p.start()
        self.addCleanup(p.stop)
        self.fake = FakePhone()
        from . import phone
        for patch in (mock.patch.object(phone, "call", self.fake), mock.patch.object(phone, "available", lambda: True)):
            patch.start()
            self.addCleanup(patch.stop)

    def agent(self):
        from . import agent, session
        s = session.Session(self.root)
        a = agent.Agent(s, client=SimpleNamespace(spec={}, local=False, on_device=False, context=lambda: 128000))
        self.addCleanup(a.memory.close)
        return a

    def ctx(self, a):
        return __import__(__package__ + ".agent", fromlist=["ToolContext"]).ToolContext(a)

    # ---------------------------------------------------------------- the four, and the phone
    def test_only_termux_github_code_checks_web_and_the_phone_are_offered(self):
        from . import lean, connectors
        a = self.agent()
        with mock.patch.object(connectors, "names", lambda: ["github_list_repos", "termux_exec", "gmail_send",
                                                              "connected_huggingface_x_0000000000"]):
            names = [d["function"]["name"] for d in a.schemas()]
        self.assertTrue(set(names) - {"github_list_repos", "termux_exec"} <= set(lean.LEAN), names)
        for want in ("read", "edit", "bash", "phone", "phone_routine", "web_search", "code_check", "runtime_exec",
                     "github_monitor", "github_list_repos", "termux_exec"):
            self.assertIn(want, names)
        for gone in ("gmail_send", "connected_huggingface_x_0000000000", "background_agent", "task", "tool_search",
                     "document_create", "self_evolve", "memory_recall", "skill", "vscode_open"):
            self.assertNotIn(gone, names)
        system = a.request_messages()[0]["content"]
        self.assertIn("Termux → GitHub → Chrome", system)
        self.assertNotIn("VS Code", system)
        self.assertNotIn("Deferred tools", system)

    def test_the_full_set_comes_back_when_lean_is_off(self):
        from . import settings
        settings.save({"lean": False})
        a = self.agent()
        self.assertIn("background_agent", a.tool_names())

    def test_an_action_returns_the_new_screen(self):
        a = self.agent()
        out, meta = tools.call(self.ctx(a), "phone", {"action": "open_app", "name": "واتساب"})
        self.assertEqual(self.fake.calls, [("open_app", {"name": "com.whatsapp"})])     # one call, no extra look
        self.assertNotIn("screen", meta["phone"])
        self.assertIn("Screen now:", out)
        self.assertIn("[3] Ahmad", out)

    # ---------------------------------------------------------------- routines
    def run_task(self, a, request, calls, error=""):
        from . import phone_fast
        a.session.turn += 1
        a._user_content(request, [])
        ctx = self.ctx(a)
        for args in calls:
            tools.call(ctx, "phone", args)
        return phone_fast.learn(a, error)

    def test_a_task_that_worked_becomes_a_routine_and_is_replayed_with_new_values(self):
        from . import phone_fast
        a = self.agent()
        rid = self.run_task(a, "افتح واتساب وارسل الى مروة كلمة مرحبا", [
            {"action": "open_app", "name": "WhatsApp"}, {"action": "tap", "item": 2},
            {"action": "type", "text": "مروة"}, {"action": "tap", "item": 1},
            {"action": "type", "text": "مرحبا"}, {"action": "tap", "text": "Send"}])
        self.assertEqual(rid, "r1")
        r = phone_fast.load()[0]
        self.assertEqual(r["values"], ["مروة", "مرحبا"])
        self.assertEqual([s["action"] for s in r["steps"]], ["open_app", "tap", "type", "tap", "type", "tap"])
        self.assertEqual(r["steps"][1], {"action": "tap", "item": 2})          # an item without text keeps its number
        self.assertEqual(r["steps"][2]["text"], "{1}")
        self.assertEqual(r["steps"][3], {"action": "tap", "text": "{1}"})       # the number became the text
        self.assertEqual(r["steps"][4]["text"], "{2}")

        a.session.turn += 1
        content, _ = a._user_content("افتح واتساب وارسل الى أحمد كلمة كيفك", [])
        self.assertIn("Saved phone routine r1", content)
        self.fake.calls.clear()
        self.fake.screen = "App: Launcher (com.android.launcher)"
        out, _ = tools.call(self.ctx(a), "phone_routine", {"id": "r1", "values": ["أحمد", "كيفك"]})
        self.assertIn("Routine r1 done", out)
        typed = [c[1].get("text") for c in self.fake.calls if c[0] == "type"]
        self.assertEqual(typed, ["أحمد", "كيفك"])
        self.assertIn("sent ✓", out)
        self.assertEqual(phone_fast.load()[0]["uses"], 1)

    def test_a_routine_stops_where_the_screen_differs(self):
        from . import phone_fast
        phone_fast.save([{"id": "r1", "request": "x", "pattern": "x", "values": [],
                          "steps": [{"action": "open_app", "name": "WhatsApp"},
                                    {"action": "tap", "text": "Nowhere"}], "uses": 0}])
        out, _ = tools.call(self.ctx(self.agent()), "phone_routine", {"id": "r1"})
        self.assertIn("stopped at step 2 of 2", out)
        self.assertIn("Continue by hand", out)

    def test_a_failed_turn_or_a_turn_without_taps_saves_nothing(self):
        from . import phone_fast
        a = self.agent()
        self.assertIsNone(self.run_task(a, "افتح واتساب", [{"action": "open_app", "name": "WhatsApp"}]))
        self.assertIsNone(self.run_task(a, "ارسل لمروة", [{"action": "open_app", "name": "WhatsApp"},
                                                         {"action": "tap", "item": 2}], error="stopped"))
        self.assertEqual(phone_fast.load(), [])

    def test_deepseek_v4_keys_connect(self):
        from . import connect
        self.assertEqual(connect.pick("deepseek", ["deepseek-v4-flash", "deepseek-v4-pro"]),
                         ["deepseek-v4-flash", "deepseek-v4-pro"])
        self.assertEqual(connect.pick("deepseek", ["deepseek-v5-chat"]), ["deepseek-v5-chat"])
        self.assertEqual(connect.spec("deepseek", "deepseek-v4-flash")["context"], 1000000)

    def test_matching_needs_most_of_the_routines_words(self):
        from . import phone_fast
        r = {"id": "r1", "pattern": "افتح واتساب وارسل الي … كلمه …", "values": ["مروة", "مرحبا"]}
        self.assertIs(phone_fast.match("افتح واتساب وارسل الى سامي كلمة اهلا", [r]), r)
        self.assertIsNone(phone_fast.match("افتح يوتيوب وشغل اغنية", [r]))


if __name__ == "__main__":
    unittest.main()
