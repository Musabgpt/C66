"""Tool calls a model writes as text in the attribute layout are run (apply_ext_99_textcalls.py)."""
import json
import unittest

from . import repair


class TextCallTests(unittest.TestCase):
    TOOLS = ["phone", "read", "edit", "web_search"]

    def test_attribute_layout_seen_on_the_phone_is_a_call(self):
        text = '<tool_call name="functions.phone">\n<arguments>{"action":"battery"}</arguments>\n</tool_call>'
        self.assertTrue(repair.tagged_call(text))
        calls, rest = repair.calls_in_text(text, self.TOOLS)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["name"], "phone")
        self.assertEqual(json.loads(calls[0]["arguments"]), {"action": "battery"})
        self.assertEqual(rest, "")

    def test_attribute_layout_without_an_arguments_tag(self):
        calls, _ = repair.calls_in_text("<tool_call name='web_search'>{\"query\": \"طقس برمنغهام\"}</tool_call>",
                                        self.TOOLS)
        self.assertEqual([c["name"] for c in calls], ["web_search"])
        self.assertEqual(json.loads(calls[0]["arguments"])["query"], "طقس برمنغهام")

    def test_text_around_the_call_stays_and_the_call_is_stripped(self):
        text = 'حاضر، رح شوف البطارية.\n<tool_call name="phone"><arguments>{"action":"battery"}</arguments></tool_call>'
        calls, rest = repair.calls_in_text(text, self.TOOLS)
        self.assertEqual(len(calls), 1)
        self.assertEqual(rest, "حاضر، رح شوف البطارية.")
        self.assertEqual(repair.strip_calls(text), "حاضر، رح شوف البطارية.")

    def test_namespaced_tool_names_are_the_tool(self):
        self.assertEqual(repair.normalize("functions.read", {"path": "a.py"}, self.TOOLS)[0], "read")
        self.assertEqual(repair.normalize("default_api.edit", {}, self.TOOLS)[0], "edit")

    def test_the_older_layouts_still_work(self):
        calls, _ = repair.calls_in_text('<tool_call>{"name": "phone", "arguments": {"action": "battery"}}</tool_call>',
                                        self.TOOLS)
        self.assertEqual([c["name"] for c in calls], ["phone"])
        calls, _ = repair.calls_in_text('<function=read>{"path": "a.py"}</function>', self.TOOLS)
        self.assertEqual([c["name"] for c in calls], ["read"])

    def test_an_unknown_tool_or_plain_prose_is_not_a_call(self):
        self.assertEqual(repair.calls_in_text('<tool_call name="rm_rf"><arguments>{}</arguments></tool_call>',
                                              self.TOOLS)[0], [])
        self.assertFalse(repair.tagged_call("To check the battery I would use the phone tool."))


if __name__ == "__main__":
    unittest.main()
