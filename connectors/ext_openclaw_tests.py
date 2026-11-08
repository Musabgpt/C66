"""Tests for OpenClaw Node capabilities integrated into MusabAI's phone tool."""
import json
import unittest
from unittest import mock

from . import openclaw_node, phone, phone_fast, repair, tools


class OpenClawNodeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        openclaw_node.install()

    def test_node_actions_registered_in_phone_and_doc(self):
        for act in (
            "notification_reply",
            "notification_action",
            "notification_dismiss",
            "contacts",
            "contact_add",
            "calendar",
            "calendar_add",
            "location",
            "media",
            "sensors",
        ):
            self.assertIn(act, phone.ACTIONS)
            self.assertIn(act, phone_fast.SKIP)
        for ro in ("notifications_read", "contacts", "calendar", "location", "sensors"):
            self.assertTrue(phone.looks_only({"action": ro}), ro)
        self.assertFalse(phone.looks_only({"action": "notification_reply", "target": "Ahmed", "text": "ok"}))
        entry = tools.REGISTRY["phone"]
        self.assertIn("notification_reply", entry.description)
        self.assertIn("notification_reply", entry.params["action"]["enum"])
        for param in ("query", "target", "command", "days", "start_ms"):
            self.assertIn(param, entry.params)
        schema_json = json.dumps(entry.schema())
        self.assertLess(len(schema_json), 2000)

    def test_repair_normalizes_openclaw_aliases_and_args(self):
        name, args = repair.normalize(
            "notifications.reply",
            {"sender": "أحمد", "message": "جاي بالطريق"},
            ["phone"],
        )
        self.assertEqual(name, "phone")
        self.assertEqual(args["action"], "notification_reply")
        self.assertEqual(args["target"], "أحمد")
        self.assertEqual(args["text"], "جاي بالطريق")

        name2, args2 = repair.normalize(
            "phone",
            {"action": "contacts.search", "name": "مصعب"},
            ["phone"],
        )
        self.assertEqual(name2, "phone")
        self.assertEqual(args2["action"], "contacts")
        self.assertEqual(args2["query"], "مصعب")

    def test_phone_tool_executes_openclaw_node_actions(self):
        calls = []

        def fake_call(action, **kwargs):
            calls.append((action, kwargs))
            if action == "notification_reply":
                return {"ok": True, "text": f"replied to {kwargs.get('target')}: {kwargs.get('text')}"}
            if action == "contacts":
                return {"ok": True, "text": "- أحمد: +962790000000"}
            if action == "location":
                return {
                    "ok": True,
                    "text": "31.953900, 35.910600\nMaps: https://maps.google.com/?q=31.953900,35.910600",
                    "lat": 31.9539,
                    "lon": 35.9106,
                }
            if action == "sensors":
                return {"ok": True, "text": "steps since reboot: 4120"}
            return {"ok": True, "text": "done"}

        with mock.patch.object(phone, "call", side_effect=fake_call):
            fn = tools.REGISTRY["phone"].fn
            out1, _ = fn(None, "notification_reply", query="أحمد", message="تمام")
            self.assertIn("replied to أحمد: تمام", out1)
            self.assertEqual(calls[-1], ("notification_reply", {"target": "أحمد", "text": "تمام"}))

            out2, _ = fn(None, "contacts", name="أحمد")
            self.assertIn("+962790000000", out2)
            self.assertEqual(calls[-1][0], "contacts")
            self.assertEqual(calls[-1][1]["query"], "أحمد")

            out3, meta3 = fn(None, "location")
            self.assertIn("maps.google.com", out3)
            self.assertAlmostEqual(meta3["phone"]["lat"], 31.9539)

            out4, _ = fn(None, "sensors")
            self.assertIn("4120", out4)


if __name__ == "__main__":
    unittest.main()
