"""Hermetic tests for the personal assistant layer: saved details, confirmation rules, the page and the profile."""

import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from . import agent_policy, assistant, lean, permissions, tools

UI = Path(__file__).with_name("ui")


def as_the_app():
    """The engine as the Android app runs it: packaged, so it is the personal assistant."""
    return mock.patch.dict(os.environ, {"NEWAL_PACKAGED_ENGINE": "/data/app/engine"})


class _Saved(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.saved_path = assistant.PATH
        assistant.PATH = os.path.join(self.dir.name, "assistant.json")
        self.addCleanup(setattr, assistant, "PATH", self.saved_path)


class AssistantInfoTests(_Saved):
    """my_info: the user's own details, saved on the phone, read when a form needs them."""

    def test_nothing_saved_says_to_ask_the_user(self):
        text, meta = assistant.t_my_info(None, "get")
        self.assertIn("Nothing is saved", text)
        self.assertEqual(meta, {})

    def test_a_detail_is_saved_read_and_forgotten(self):
        self.assertEqual(assistant.t_my_info(None, "set", field="Passport", value="  N123  456 ")[0], "Saved passport.")
        assistant.t_my_info(None, "set", field="home address", value="12 Nile St")
        text, meta = assistant.t_my_info(None, "get")
        self.assertEqual(text, "home_address: 12 Nile St\npassport: N123 456")
        self.assertEqual(meta["fields"], ["home_address", "passport"])
        self.assertEqual(assistant.t_my_info(None, "forget", field="passport")[0], "Forgot passport.")
        self.assertEqual(assistant.t_my_info(None, "forget", field="passport")[0], "Nothing was saved as passport.")
        self.assertEqual(assistant.profile(), {"home_address": "12 Nile St"})

    def test_the_file_is_private_and_survives_a_bad_one(self):
        assistant.remember("name", "مصعب")
        self.assertEqual(os.stat(assistant.PATH).st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(Path(assistant.PATH).read_text(encoding="utf-8"))["profile"], {"name": "مصعب"})
        Path(assistant.PATH).write_text("not json", encoding="utf-8")
        self.assertEqual(assistant.profile(), {})
        self.assertTrue(assistant.confirm())

    def test_bad_input_is_refused_clearly(self):
        for action, args in (("set", {"field": "", "value": "x"}), ("set", {"field": "رقم", "value": "x"}),
                             ("set", {"field": "name", "value": "  "}),
                             ("set", {"field": "name", "value": "x" * (assistant.MAX_VALUE + 1)}), ("sell", {})):
            with self.assertRaises(tools.ToolError):
                assistant.t_my_info(None, action, **args)
        for i in range(assistant.MAX_FIELDS):
            assistant.remember("f%d" % i, "v")
        with self.assertRaises(tools.ToolError):
            assistant.remember("one_more", "v")
        assistant.remember("f0", "changed")                      # an existing one can still change

    def test_the_model_always_sees_what_is_saved_but_not_the_values(self):
        assistant.update({"profile": {"name": "Musab Ali", "city": "Riyadh", "passport": "N1234567"}})
        context = assistant.context()
        self.assertEqual(context["saved_details"], ["city", "name", "passport"])
        self.assertEqual((context["user_name"], context["user_city"]), ("Musab Ali", "Riyadh"))
        self.assertNotIn("N1234567", json.dumps(context))
        self.assertTrue(context["confirm_before_sending"])

    def test_my_info_is_registered_and_never_asks(self):
        self.assertIn("my_info", tools.REGISTRY)
        self.assertEqual(tools.REGISTRY["my_info"].kind, "meta")
        decision = permissions.decide("ask", "my_info", "meta", {"action": "set"}, "/", {})
        self.assertEqual(decision.action, permissions.ALLOW)


class AssistantRulesTests(_Saved):
    """What sends in the user's name with no tap of theirs is asked first, in every mode."""

    def setUp(self):
        super().setUp()
        app = as_the_app()
        app.start()
        self.addCleanup(app.stop)

    def decide(self, tool, kind, args):
        return permissions.decide("full-auto", tool, kind, args, "/", assistant.rules({})).action

    def test_outside_the_app_nothing_is_added(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("NEWAL_PACKAGED_ENGINE", None)
            self.assertEqual(assistant.rules({"ask": ["x"]}), {"ask": ["x"]})

    def test_a_background_reply_and_an_email_ask_first(self):
        self.assertEqual(self.decide("phone", "phone", {"action": "notification_reply", "text": "ok"}), permissions.ASK)
        self.assertEqual(self.decide("gmail_send", "connector_write", {"to": "a@b.c"}), permissions.ASK)

    def test_everything_else_just_runs(self):
        for args in ({"action": "tap", "item": 3}, {"action": "type", "text": "hi"}, {"action": "open_app", "name": "X"},
                     {"action": "notifications_read"}, {"action": "sms", "number": "1", "text": "hi"}):
            self.assertEqual(self.decide("phone", "phone", args), permissions.ALLOW, args)
        self.assertEqual(self.decide("phone_agent", "phone", {"goal": "x"}), permissions.ALLOW)
        self.assertEqual(self.decide("browser", "net", {"op": "open"}), permissions.ALLOW)

    def test_the_user_can_switch_the_question_off_and_their_own_rules_stay(self):
        assistant.update({"confirm": False})
        self.assertEqual(self.decide("phone", "phone", {"action": "notification_reply"}), permissions.ALLOW)
        self.assertEqual(assistant.rules({"ask": ["Phone(call)"], "deny": ["x"]}), {"ask": ["Phone(call)"], "deny": ["x"]})
        assistant.update({"confirm": True})
        merged = assistant.rules({"ask": ["Phone(call)", "gmail_send"]})
        self.assertEqual(merged["ask"], ["Phone(call)", "gmail_send", "Phone(notification_reply)"])


class _Handler:
    def __init__(self):
        self.sent = None

    def _json(self, data, status=200):
        self.sent = (status, data)


class AssistantPageTests(_Saved):
    """The page and its small API."""

    def call(self, method, path, body=None):
        handler = _Handler()
        self.assertTrue(assistant.route(handler, method, path, body))
        return handler.sent

    def test_other_paths_are_left_to_the_engine(self):
        self.assertFalse(assistant.route(_Handler(), "GET", "/api/state"))

    def test_the_settings_sheet_reads_and_saves(self):
        status, state = self.call("GET", "/api/assistant")
        self.assertEqual((status, state["profile"], state["confirm"]), (200, {}, True))
        self.assertEqual(state["fields"][:3], ["name", "email", "phone"])
        status, state = self.call("POST", "/api/assistant", {"profile": {"name": " Musab ", "city": "Riyadh"}, "confirm": False})
        self.assertEqual((state["profile"], state["confirm"]), ({"name": "Musab", "city": "Riyadh"}, False))
        status, state = self.call("POST", "/api/assistant", {"profile": {"city": ""}})          # emptied: forgotten
        self.assertEqual(state["profile"], {"name": "Musab"})
        self.assertFalse(state["confirm"])                                                      # untouched

    def test_bad_requests_get_a_reason(self):
        self.assertEqual(self.call("POST", "/api/assistant", {"profile": {"1bad": "x"}})[0], 400)
        status, out = self.call("POST", "/api/assistant/open", {"page": "developer"})
        self.assertEqual((status, out["ok"]), (400, False))
        self.assertEqual(self.call("GET", "/api/assistant/nothing")[0], 404)

    def test_the_page_is_one_screen_with_none_of_the_developer_layers(self):
        page = (UI / "index.html").read_text(encoding="utf-8")
        scripts = re.findall(r'<script src="([^"]+)"', page)
        self.assertEqual(scripts, ["markdown.js", "assistant.js"])
        self.assertEqual(re.findall(r'<link rel="stylesheet" href="([^"]+)"', page), ["assistant.css"])
        for gone in ("open-models", "open-github", "toggle-terminal", "review", "Plugins", "app.js"):
            self.assertNotIn(gone, page)
        for needed in ('id="tasks"', 'id="input"', 'id="mic"', 'id="approvals"', 'id="sheet"'):
            self.assertIn(needed, page)

    def test_the_page_speaks_both_languages_completely(self):
        script = (UI / "assistant.js").read_text(encoding="utf-8")
        arabic = script[script.index("    ar: {"):script.index("    en: {")]
        english = script[script.index("    en: {"):script.index("  function t(key, vars)")]
        keys = lambda block: set(re.findall(r'(?m)(?:^\s+|", )([a-z_0-9]+): "', block))   # a key follows a value or starts a line
        self.assertEqual(keys(arabic), keys(english))
        self.assertGreater(len(keys(arabic)), 90)
        used = set(re.findall(r'\bt\("([a-z_0-9]+)"[,)]', script))                         # whole names only
        self.assertFalse(used - keys(english), "words the page uses but does not have")
        for task in ("mail", "messages", "travel", "post", "form", "remind"):                    # the six daily tasks
            for suffix in ("", "_sub", "_text"):
                self.assertIn(task + suffix, keys(arabic))

    def test_the_page_keeps_what_the_app_calls(self):
        script = (UI / "assistant.js").read_text(encoding="utf-8")
        for hook in ("window.onPhoneVoice", "window.onPhoneShared", "window.onPhoneAccess", "window.openMusabConnectors"):
            self.assertIn(hook, script)
        for call in ("/api/sessions", "/api/events", "/api/approvals/", "/api/assistant", "/interrupt", '"full-auto"'):
            self.assertIn(call, script)
        self.assertNotIn("?.", script)                       # older Android WebViews do not parse it
        self.assertNotIn("??", script)


class AssistantProfileTests(unittest.TestCase):
    """In the app, the prompt and the tool list are an assistant's, whole, with no leftovers of the coding agent."""

    def test_the_apps_tools_are_an_assistants(self):
        for name in ("phone", "phone_agent", "phone_routine", "browser", "browser_agent", "web_search", "my_info",
                     "scheduled_task_create"):
            self.assertIn(name, lean.ASSISTANT)
        for name in ("bash", "read", "edit", "write", "glob", "grep", "github", "code_check", "runtime_exec",
                     "phone_power", "adb_connect", "toolchain_status"):
            self.assertNotIn(name, lean.ASSISTANT)
        with as_the_app():
            self.assertTrue(lean.assistant())
            self.assertEqual(lean.tools(["read", "phone", "bash", "my_info"]), ["phone", "my_info"])
            self.assertTrue(lean.connector("gmail_search"))
            self.assertTrue(lean.connector("calendar_create"))
            self.assertFalse(lean.connector("github_issues"))
            self.assertFalse(lean.connector("termux_exec"))

    def test_the_engine_run_from_source_keeps_the_developer_profile(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("NEWAL_PACKAGED_ENGINE", None)
            self.assertFalse(lean.assistant())
            self.assertEqual(lean.tools(["read", "phone", "bash", "my_info"]), ["read", "phone", "bash"])
            self.assertTrue(lean.connector("github_issues"))
            self.assertNotIn("the user's personal assistant on their own Android phone", agent_policy.lean_profile()[:300])
        with as_the_app(), mock.patch.object(lean, "assistant", return_value=False):      # settings "assistant": false
            self.assertEqual(lean.tools(["read", "my_info"]), ["read"])

    def test_every_tool_offered_exists(self):
        for name in lean.ASSISTANT:
            self.assertIn(name, tools.REGISTRY, name)

    def test_the_prompt_covers_the_daily_work_and_names_only_its_tools(self):
        with as_the_app():
            text = agent_policy.lean_profile()
        self.assertTrue(text.startswith(agent_policy.MARKER + "\n\nYou are MusabAI, the user's personal assistant"))
        for section in ("# Before anything leaves in the user's name", "# Email", "# Messages", "# Posts", "# Travel",
                        "# Forms", "# Everyday", "# Using the phone", "# Rules"):
            self.assertIn(section, text)
        for needed in ("notification_reply", "gmail_search", "phone_routine", "browser_agent", "my_info",
                       "confirm_before_sending", "never pay", "Task memory tm:"):
            self.assertIn(needed, text)
        for gone in ("BusyBox", "GitHub", "bash", "code_check", "toolchain_status", "phone_power", "adb_connect",
                     "AGENTS.md", "npm", "Building software"):
            self.assertNotIn(gone, text, gone)
        self.assertLess(len(text), 9000)                     # the earlier one was 14,000 characters

    def test_the_prompt_reaches_the_model_on_the_phone(self):
        env = {"os": "Android 14", "shell": "sh"}
        with as_the_app():
            text = agent_policy.system_text(env, phone=True, local=False)
            small = agent_policy.system_text(env, phone=True, local=True, steps=25)
        self.assertIn("personal assistant", text)
        self.assertIn("Environment: Android 14", text)
        self.assertIn("personal assistant", small)
        self.assertNotIn("coding agent", small)
        self.assertIn("25 steps", small)

    def test_the_model_is_told_who_it_works_for_only_in_the_app(self):
        source = Path(agent_policy.__file__).read_text(encoding="utf-8")
        self.assertIn("if assistant_mode():\n        try:\n            from . import assistant\n"
                      "            state['assistant'] = assistant.context()", source)

    def test_the_engine_is_not_replaced_from_another_projects_channel_on_its_own(self):
        """That channel builds the developer interface: an automatic update from it would undo the assistant."""
        from unittest import mock
        from . import auto_update, settings
        with mock.patch.object(settings, "user", return_value={}):
            self.assertFalse(auto_update.enabled())
        with mock.patch.object(settings, "user", return_value={"automatic_updates": True}):
            self.assertTrue(auto_update.enabled())                # the user's own choice still counts

    def test_old_threads_take_the_new_profile(self):
        self.assertTrue(agent_policy.stale_builtin("MusabAI behavior profile v17\n\nYou are MusabAI, an agent"))
        with as_the_app():
            self.assertFalse(agent_policy.stale_builtin(agent_policy.lean_profile()))


if __name__ == "__main__":
    unittest.main()
