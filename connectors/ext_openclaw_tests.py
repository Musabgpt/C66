"""Tests for OpenClaw Node capabilities integrated into MusabAI (openclaw_node.py)."""
import unittest
from unittest.mock import patch

from . import openclaw_node, phone, phone_fast, repair, tools


class _DummySession:
    id = "s-openclaw"
    turn = 1
    messages = []


class _DummyAgent:
    depth = 0

    def __init__(self):
        self.session = _DummySession()


class _DummyCtx:
    def __init__(self):
        self.agent = _DummyAgent()
        self.session = self.agent.session
        self.root = "."
        self.cancel = None


class OpenClawNodeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        openclaw_node.install()

    def test_node_actions_registered_in_phone_and_doc(self):
        for act in openclaw_node.NODE_ACTIONS:
            if hasattr(phone, "ACTIONS"):
                self.assertIn(act, phone.ACTIONS)
        self.assertIn("notification_reply", phone_fast.DOC)
        self.assertIn("contacts", phone_fast.DOC)
        self.assertIn("calendar_add", phone_fast.DOC)
        self.assertIn("location", phone_fast.DOC)
        self.assertIn("media", phone_fast.DOC)
        self.assertIn("sensors", phone_fast.DOC)
        for ro in openclaw_node.READ_ONLY_NODE_ACTIONS:
            self.assertIn(ro, phone_fast.SKIP)

    def test_repair_normalizes_openclaw_aliases_and_args(self):
        name, args = repair.normalize(
            "phone",
            {"action": "reply_notification", "sender": "واتساب", "message": "أهلاً بك"},
            {"phone"},
        )
        self.assertEqual(name, "phone")
        self.assertEqual(args["action"], "notification_reply")
        self.assertEqual(args["target"], "واتساب")
        self.assertEqual(args["text"], "أهلاً بك")

        name, args = repair.normalize(
            "phone",
            {"action": "find_contact", "search": "أحمد"},
            {"phone"},
        )
        self.assertEqual(args["action"], "contacts")
        self.assertEqual(args["query"], "أحمد")

        name, args = repair.normalize(
            "phone",
            {"action": "create_event", "summary": "اجتماع الفريق", "in_minutes": "30"},
            {"phone"},
        )
        self.assertEqual(args["action"], "calendar_add")
        self.assertEqual(args["title"], "اجتماع الفريق")
        self.assertEqual(args["in_minutes"], 30)

        name, args = repair.normalize(
            "phone",
            {"action": "get_location"},
            {"phone"},
        )
        self.assertEqual(args["action"], "location")

    def test_phone_tool_executes_openclaw_node_actions(self):
        calls = []

        def fake_call(action, timeout=20, **kwargs):
            calls.append((action, kwargs))
            if action == "notification_reply":
                return {"ok": True, "text": "replied to com.whatsapp: تم"}
            if action == "contacts":
                return {"ok": True, "text": "أحمد العتيبي: +966501112233 (Mobile)"}
            if action == "location":
                return {
                    "ok": True,
                    "text": "location: 24.713600, 46.675300 (±12 m, provider=gps, age=2s) https://maps.google.com/?q=24.713600,46.675300",
                }
            return {"ok": True, "text": f"{action} ok"}

        ctx = _DummyCtx()
        t = tools.REGISTRY.get("phone")
        if t is None:
            self.skipTest("phone tool not registered in this test context")
        with patch.object(phone, "call", side_effect=fake_call):
            out_contacts = t.fn(ctx, "contacts", query="أحمد")
            text_c = out_contacts[0] if isinstance(out_contacts, tuple) else out_contacts
            self.assertIn("+966501112233", text_c)

            out_reply = t.fn(ctx, "notification_reply", target="com.whatsapp", text="تم")
            text_r = out_reply[0] if isinstance(out_reply, tuple) else out_reply
            self.assertIn("replied to com.whatsapp", text_r)

        log_actions = [entry[0] for entry in ctx.agent._phone_turn["log"]]
        self.assertNotIn("contacts", log_actions)
        self.assertIn("notification_reply", log_actions)


if __name__ == "__main__":
    unittest.main()
