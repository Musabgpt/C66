"""The phone never loops blind (vital.py): actions that leave the screen as it was are noted, then stop the turn with
the reason; a failing phone call is counted per screen; and stops are explained in the user's language."""
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from . import tools  # noqa: F401 - registers every tool first, as the engine does

HOME = "App: Launcher (com.android.launcher)\n[1] WhatsApp · tap\n[2] 09:41"
CHATS = "App: WhatsApp (com.whatsapp)\n[1] Chats · tap\n[2] (search) · tap\n[3] Ahmad · tap"


class StuckPhone:
    """A phone whose screen only changes on open_app; every tap 'works' but changes nothing (a dead button)."""
    def __init__(self):
        self.screen = HOME
        self.clock = 41

    def __call__(self, action, timeout=90, **args):
        if action == "screen":
            return {"ok": True, "text": self.screen}
        if action == "open_app":
            self.screen = CHATS
        elif action == "swipe":
            self.screen = CHATS.replace("Ahmad", "Ahmad\n[4] Maryam · tap") if "Maryam" not in self.screen \
                else self.screen
        if action == "tap" and args.get("text") == "fail":
            return {"ok": False, "error": "nothing on the screen says \"fail\""}
        self.clock += 1                                   # the status bar clock moves: still the same screen
        self.screen = self.screen.replace("09:%d" % (self.clock - 1), "09:%d" % self.clock)
        return {"ok": True, "text": "done", "screen": self.screen}


class VitalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.realpath(self.tmp.name)
        from . import settings
        for p in (mock.patch.object(settings, "HOME", os.path.join(self.root, "home")),
                  mock.patch.object(settings, "CONFIG", os.path.join(self.root, "home", "config.json"))):
            p.start()
            self.addCleanup(p.stop)
        self.fake = StuckPhone()
        from . import phone
        for p in (mock.patch.object(phone, "call", self.fake), mock.patch.object(phone, "available", lambda: True)):
            p.start()
            self.addCleanup(p.stop)

    def agent(self, request="افتح واتساب واضغط على الزر"):
        from . import agent, circuit, session
        s = session.Session(self.root)
        a = agent.Agent(s, client=SimpleNamespace(spec={}, local=False, on_device=False, context=lambda: 128000))
        self.addCleanup(a.memory.close)
        a.breaker = circuit.Breaker()
        s.messages.append({"role": "user", "content": request})
        return a

    def ctx(self, a):
        return __import__(__package__ + ".agent", fromlist=["ToolContext"]).ToolContext(a)

    def test_the_wrapper_is_installed_around_the_phone_tool(self):
        self.assertTrue(getattr(tools.REGISTRY["phone"].fn, "_vital", False))

    def test_a_clock_does_not_make_a_new_screen(self):
        from . import vital
        self.assertEqual(vital.fingerprint("[1] x\n[2] 09:41"), vital.fingerprint("[1] x\n[2] 09:42"))
        self.assertNotEqual(vital.fingerprint("[1] x"), vital.fingerprint("[1] y"))
        self.assertEqual(vital.fingerprint(""), "")

    def test_actions_without_effect_are_noted_then_stop_the_turn_with_the_reason(self):
        from . import vital
        a = self.agent()
        c = self.ctx(a)
        out, _ = tools.call(c, "phone", {"action": "open_app", "name": "WhatsApp"})
        self.assertNotIn("has not changed", out)
        outs = [tools.call(c, "phone", {"action": "tap", "text": "Chats"})[0] for _ in range(3)]
        self.assertNotIn("has not changed", outs[1])
        self.assertIn("(Note: the screen has not changed after your last 3 actions", outs[2])
        self.assertFalse(a.breaker.open)
        outs += [tools.call(c, "phone", {"action": "tap", "text": "Chats"})[0] for _ in range(3)]
        self.assertIn("(Stopping: the screen has not changed after your last 6 actions", outs[5])
        self.assertTrue(a.breaker.open)
        self.assertEqual(a.breaker.reason, "the phone screen did not change after 6 actions in a row")
        text = vital.stop_text(a, "breaker", a.breaker.reason)
        self.assertTrue(text.startswith("توقفت: شاشة الهاتف ما تغيّرت بعد 6 أفعال متتالية"), text)
        self.assertIn("اكتب «كمّل»", text)

    def test_a_screen_that_changes_resets_the_count(self):
        a = self.agent()
        c = self.ctx(a)
        tools.call(c, "phone", {"action": "open_app", "name": "WhatsApp"})
        for _ in range(2):
            tools.call(c, "phone", {"action": "tap", "text": "Chats"})
        tools.call(c, "phone", {"action": "swipe", "direction": "up"})       # Maryam shows up: a new screen
        out, _ = tools.call(c, "phone", {"action": "tap", "text": "Chats"})
        self.assertNotIn("has not changed", out)
        self.assertEqual(a._phone_turn["same"], 1)
        self.assertFalse(a.breaker.open)

    def test_reading_the_screen_is_not_an_action(self):
        a = self.agent()
        c = self.ctx(a)
        tools.call(c, "phone", {"action": "open_app", "name": "WhatsApp"})
        for _ in range(5):
            out, _ = tools.call(c, "phone", {"action": "screen"})
        self.assertNotIn("has not changed", out)
        self.assertFalse(a.breaker.open)

    def test_a_failing_phone_call_counts_per_screen(self):
        from . import vital
        a = self.agent()
        a._phone_turn = {"turn": a.session.turn, "log": [], "screen": HOME, "routine": None}
        on_home = vital.change_mark(a, "phone")
        a._phone_turn["screen"] = CHATS
        self.assertNotEqual(vital.change_mark(a, "phone"), on_home)
        a._phone_turn["screen"] = HOME.replace("09:41", "09:42")
        self.assertEqual(vital.change_mark(a, "phone"), on_home)
        self.assertEqual(vital.change_mark(a, "bash"), a.last_change_step)       # other tools: as before

    def test_stops_keep_the_engine_wording_in_english(self):
        from . import vital
        a = self.agent("open whatsapp")
        self.assertEqual(vital.stop_text(a, "budget", 60), "Stopped after 60 steps without finishing.")
        self.assertEqual(vital.stop_text(a, "breaker", "bash failed 3 times the same way (exit 1)"),
                         "Stopped: bash failed 3 times the same way (exit 1).")
        self.assertIn("task timeout", vital.stop_text(a, "timeout"))
        self.assertIn("the model provider did not answer", vital.stop_text(a, "stall", ("text_delta", "")))

    def test_stops_say_the_real_cause_in_arabic(self):
        from . import vital
        a = self.agent()
        self.assertTrue(vital.stop_text(a, "budget", 60).startswith("توقفت: انتهت خطوات هذا الدور (60)"))
        self.assertTrue(vital.stop_text(a, "breaker", "bash failed 3 times the same way (exit 1: no such file)")
                        .startswith("توقفت: الأداة bash فشلت 3 مرات بنفس الطريقة (exit 1: no such file)."))
        self.assertIn("المزود ما رد", vital.stop_text(a, "stall", ("watchdog-recovery", "")))
        self.assertIn("الأداة bash ما رجّعت نتيجة", vital.stop_text(a, "stall", ("tool_start", "bash")))
        self.assertNotIn("راجع حالة المزودين", vital.stop_text(a, "stall", ("tool_start", "bash")))
