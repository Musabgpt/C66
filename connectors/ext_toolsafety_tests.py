"""Tool safety (apply_ext_99_toolsafety.py): the phone tool refuses the app's internal actions, an install's approval names
its APK, the phone's description names its actions, and the file reader stays in the project folder."""
import os
import re
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from . import tools  # noqa: F401 - first, as the engine does: it installs the phone tool that phone_fast wraps
from . import permissions, phone, phone_fast, server

REPO = Path(__file__).resolve().parents[2]      # the repository, when CI builds the engine inside it


class ToolSafetyTests(unittest.TestCase):
    def ctx(self):
        return types.SimpleNamespace(cancel=None)    # a tool call outside an agent turn

    def test_the_phone_tool_refuses_the_apps_internal_actions(self):
        tool = tools.REGISTRY["phone"]
        for action in ("provider_secret_get", "provider_secret_set", "provider_secret_remove", "connector",
                       "document_pdf"):
            with self.subTest(action=action), mock.patch.object(phone, "call") as call:
                with self.assertRaises(tools.ToolError) as err:
                    tool.fn(self.ctx(), action=action, provider="openai")
                call.assert_not_called()
                self.assertIn("no action", str(err.exception))

    def test_the_phone_tool_still_runs_the_actions_the_model_uses(self):
        tool = tools.REGISTRY["phone"]
        with mock.patch.object(phone, "call", return_value={"ok": True, "text": "2 apps"}) as call:
            text, _ = tool.fn(self.ctx(), action="apps")
        call.assert_called_once_with("apps")
        self.assertIn("2 apps", text)

    def test_every_listed_action_is_one_the_app_runs(self):
        java = REPO / "android-lite/app/src/main/java/dev/newal/code/lite/Phone.java"
        if not java.is_file():
            self.skipTest("the app's source is not in this checkout")
        cases = set(re.findall(r'case "([a-z_]+)":', java.read_text(encoding="utf-8")))
        self.assertEqual([a for a in phone.ACTIONS if a not in cases], [])
        self.assertNotIn("provider_secret_get", phone.ACTIONS)

    def test_the_schema_has_the_install_path_and_the_description_names_the_new_actions(self):
        self.assertIn("path", phone.PARAMS)
        for name in ("install_apk", "notifications_read", "crash_reports", "automation_replay"):
            self.assertIn(name, phone.ACTIONS)
        for text in ("install_apk (path)", "notifications_read", "crash_reports", "automation_start/stop/list/replay"):
            self.assertIn(text, phone_fast.DOC)
        self.assertNotIn("screenshot", phone_fast.DOC)     # a screenshot is a file: screen_look is how the model looks

    def test_an_install_approval_names_the_apk(self):
        d = permissions.decide("default", "phone", "phone",
                               {"action": "install_apk", "path": "/sdcard/Download/app-debug.apk"}, "/proj", {})
        self.assertEqual(d.action, permissions.ASK)
        self.assertIn("/sdcard/Download/app-debug.apk", d.reason)

    def test_the_file_reader_stays_in_the_project_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "proj")
            sibling = os.path.join(tmp, "proj-secret")
            os.makedirs(root)
            os.makedirs(sibling)
            with open(os.path.join(root, "a.txt"), "w", encoding="utf-8") as f:
                f.write("inside")
            with open(os.path.join(sibling, "key.txt"), "w", encoding="utf-8") as f:
                f.write("secret")
            self.assertIn("inside", server._read_file(root, "a.txt").get("text", ""))
            for rel in ("../proj-secret/key.txt", os.path.join(sibling, "key.txt")):
                with self.subTest(rel=rel):
                    self.assertEqual(server._read_file(root, rel), {"error": "outside the project"})
