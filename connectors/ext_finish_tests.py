"""Before a phone task is called done, the phone checks the work (finish.py): a text typed but never sent, or a failed
last action the reply hides, sends the model back once with the screen; and only a checked turn teaches a routine."""
import itertools
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from . import tools  # noqa: F401 - registers every tool first, as the engine does

REQUEST = "ارسل لأحمد على واتساب: مرحبا يا أحمد"
TEXT = "مرحبا يا أحمد"


class ChatPhone:
    """WhatsApp open on Ahmad's chat: type puts the text in the field, a tap on Send moves it into the chat."""
    def __init__(self):
        self.field = ""
        self.sent = []
        self.reads = 0
        self.taps_fail = False

    def render(self):
        lines = ["App: WhatsApp (com.whatsapp)", "[1] Ahmad · tap"]
        for m in self.sent:
            lines.append("[%d] %s" % (len(lines), m))
        if self.field:
            lines.append("[%d] %s · text field, field, tap, focused" % (len(lines), self.field))
        else:
            lines.append("[%d] hint: Message · text field, field, tap" % len(lines))
        lines.append("[%d] Send · button, tap" % len(lines))
        return "\n".join(lines)

    def __call__(self, action, timeout=90, **args):
        if action == "screen":
            self.reads += 1
            return {"ok": True, "text": self.render()}
        if action == "type":
            self.field = args.get("text") or ""
        elif action == "tap":
            if self.taps_fail:                            # the app answers a refused action with an error
                from . import phone
                raise phone.PhoneError("the screen changed: [3] Send is not on it any more")
            if args.get("text") == "Send" and self.field:
                self.sent.append(self.field)
                self.field = ""
        return {"ok": True, "text": "done", "screen": self.render()}


class FinishTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.realpath(self.tmp.name)
        from . import settings
        for p in (mock.patch.object(settings, "HOME", os.path.join(self.root, "home")),
                  mock.patch.object(settings, "CONFIG", os.path.join(self.root, "home", "config.json"))):
            p.start()
            self.addCleanup(p.stop)
        self.fake = ChatPhone()
        from . import phone
        for p in (mock.patch.object(phone, "call", self.fake), mock.patch.object(phone, "available", lambda: True)):
            p.start()
            self.addCleanup(p.stop)

    def agent(self):
        from . import agent, circuit, session
        s = session.Session(self.root)
        a = agent.Agent(s, client=SimpleNamespace(id="test", spec={}, local=False, on_device=False,
                                                  context=lambda: 128000))
        self.addCleanup(a.memory.close)
        a.breaker = circuit.Breaker()
        a._phone_request = REQUEST
        return a

    def act(self, a, **args):
        from . import agent
        return tools.call(agent.ToolContext(a), "phone", args)

    def test_a_text_typed_but_never_sent_sends_the_model_back_once(self):
        from . import finish
        a = self.agent()
        self.act(a, action="open_app", name="WhatsApp")
        self.act(a, action="tap", text="Ahmad")
        self.act(a, action="type", text=TEXT)
        note = finish.check(a, "تم إرسال الرسالة", False)
        self.assertIn('the text you typed last ("%s") is still in the field [2]' % TEXT, note)
        self.assertIn("Screen now:\nApp: WhatsApp", note)
        self.assertFalse(a._phone_turn["verified"])
        self.assertEqual(finish.check(a, "تم إرسال الرسالة", True), "")           # once a turn
        self.act(a, action="tap", text="Send")
        self.assertEqual(finish.check(a, "أرسلت الرسالة", True), "")
        self.assertTrue(a._phone_turn["verified"])

    def test_a_turn_without_typing_ends_as_before_without_reading_the_screen(self):
        from . import finish
        a = self.agent()
        self.act(a, action="open_app", name="WhatsApp")
        self.act(a, action="tap", text="Ahmad")
        reads = self.fake.reads
        self.assertEqual(finish.check(a, "فتحت محادثة أحمد", False), "")
        self.assertEqual(self.fake.reads, reads)
        self.assertTrue(a._phone_turn["verified"])

    def test_a_failed_last_action_the_reply_hides_is_shown_to_the_model(self):
        from . import finish
        a = self.agent()
        self.act(a, action="type", text=TEXT)
        self.fake.taps_fail = True
        with self.assertRaises(tools.ToolError):
            self.act(a, action="tap", text="Send")
        note = finish.check(a, "Sent.", False)
        self.assertIn("your last phone action (tap) failed", note)
        self.assertIn("still in the field", note)                 # the tap that failed sent nothing
        # a reply that says plainly what did not work is the honest end: no question, and no routine
        self.assertEqual(finish.check(a, "ما قدرت أرسل الرسالة: زر الإرسال اختفى", False), "")
        self.assertFalse(a._phone_turn["verified"])

    def test_a_reply_that_asks_the_user_is_never_pushed_on(self):
        from . import finish
        a = self.agent()
        self.act(a, action="type", text=TEXT)
        self.assertEqual(finish.check(a, "كتبت الرسالة، بدك أبعتها؟", False), "")     # a confirmation, not a stop
        self.assertEqual(finish.check(a, "Should I send it now?", False), "")
        self.assertEqual(self.fake.sent, [])
        self.assertFalse(a._phone_turn["verified"])
        self.assertFalse(finish.asks("Opened https://www.youtube.com/results?search_query=fairuz"))  # a link
        self.assertTrue(finish.asks("Which Ahmad do you mean?"))

    def test_only_the_field_that_holds_the_typed_text_counts(self):
        from . import finish
        screen = ("App: Notes (com.notes)\n[1] look at this · text field, field, tap\n[2] ok · button, tap\n"
                  "[3] مرحبا يا أحمد كيف حالك اليوم · text field, field, tap, focused")
        self.assertEqual(finish.field_with(screen, "ok"), 0)                 # "ok" is not what field [1] holds
        self.assertEqual(finish.field_with(screen, "مرحبا يا احمد"), 3)       # folded, and its start is enough
        self.assertEqual(finish.field_with(screen, "كيف حالك"), 0)

    def test_only_a_checked_turn_teaches_a_routine(self):
        from . import finish, phone_fast
        a = self.agent()
        self.act(a, action="open_app", name="WhatsApp")
        self.act(a, action="tap", text="Ahmad")
        self.act(a, action="type", text=TEXT)
        self.act(a, action="tap", text="Send")
        self.assertIsNone(phone_fast.learn(a))                       # its end was never checked
        finish.check(a, "تعذر إرسال الرسالة", False)
        self.assertIsNone(phone_fast.learn(a))                       # it says it failed
        self.assertEqual(phone_fast.load(), [])
        finish.check(a, "أرسلت الرسالة لأحمد", False)
        rid = phone_fast.learn(a)
        self.assertTrue(rid)
        self.assertEqual([r["id"] for r in phone_fast.load()], [rid])

    def test_replies_that_say_it_failed(self):
        from . import finish
        for text in ("I couldn't send it", "I wasn't able to open the chat", "The message was not sent",
                     "لم أستطع فتح التطبيق", "تعذّر الإرسال", "ما قدرت لاقي الزر", "فشل الإرسال"):
            self.assertTrue(finish.says_failed(text), text)
        for text in ("Sent the message to Ahmad.", "تم إرسال الرسالة", "فتحت واتساب وأرسلت الرسالة"):
            self.assertFalse(finish.says_failed(text), text)

    def test_the_agent_loop_sends_the_model_back_once_then_learns(self):
        from . import phone_fast, providers
        a = self.agent()
        a.session.tool_names = ["phone"]
        ids = itertools.count(1)

        def call(action, **args):
            comp = providers.Completion()
            comp.tool_calls = [{"id": "c%d" % next(ids), "name": "phone",
                                "arguments": json.dumps(dict(args, action=action), ensure_ascii=False)}]
            return comp

        def reply(text):
            comp = providers.Completion()
            comp.content = text
            return comp

        script = [call("open_app", name="WhatsApp"), call("tap", text="Ahmad"), call("type", text=TEXT),
                  reply("تم إرسال الرسالة"), call("tap", text="Send"), reply("أرسلت الرسالة لأحمد")]
        with mock.patch.object(a, "_call", side_effect=script), \
                mock.patch.object(a, "_user_content", return_value=(REQUEST, None)), \
                mock.patch.object(a, "_maybe_compact"), \
                mock.patch.object(a, "_permission", return_value=(True, "")):
            answer = a.run(REQUEST)
        self.assertEqual(answer, "أرسلت الرسالة لأحمد")
        notes = [m["content"] for m in a.session.messages if m.get("role") == "user"
                 and str(m.get("content") or "").startswith("Before you finish, check the phone")]
        self.assertEqual(len(notes), 1)
        self.assertEqual(self.fake.sent, [TEXT])
        self.assertEqual(len(phone_fast.load()), 1)                  # a checked success teaches its routine
