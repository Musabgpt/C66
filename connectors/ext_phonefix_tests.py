"""Phone tasks on a free model (builds #382/#383): connected-service tools deferred, no summary when the fixed part
fills the window, tagged text calls run, the phone's argument names, thinking aloud kept out of the answer."""
import json
import unittest
from types import SimpleNamespace

from . import tools  # noqa: F401 - registers every tool first, as the engine does
from .ext_tests import ExtensionTests


class PhoneFixTests(unittest.TestCase):
    setUp = ExtensionTests.setUp
    write = ExtensionTests.write
    agent = ExtensionTests.agent

    # ---------------------------------------------------------------- phone tasks on a free model (#382/#383)
    def test_qwen_xml_call_in_text_is_run_with_the_phones_argument_names(self):
        from . import repair
        text = ("<tool_call>\n<function=phone>\n<parameter=action>\nopen_app\n</parameter>\n"
                "<parameter=package_name>\ncom.whatsapp\n</parameter>\n</function>\n</tool_call>\n"
                "The user wants me to open WhatsApp.")
        self.assertTrue(repair.tagged_call(text))
        calls, rest = repair.calls_in_text(text, ["phone", "screen_look"])
        self.assertEqual([(c["name"], json.loads(c["arguments"])) for c in calls],
                         [("phone", {"action": "open_app", "name": "com.whatsapp"})])
        self.assertEqual(rest, "The user wants me to open WhatsApp.")
        allowed = ["phone", "screen_look"]
        self.assertEqual(repair.normalize("phone", {"action": "open_settings", "settings_page": "accessibility"},
                                          allowed), ("phone", {"action": "settings", "page": "accessibility"}))
        self.assertEqual(repair.normalize("phone", {"action": "screen_look", "name": "WhatsApp"}, allowed),
                         ("screen_look", {}))
        self.assertEqual(repair.normalize("phone", {"action": "tap", "item": "7"}, allowed),
                         ("phone", {"action": "tap", "item": 7}))
        self.assertEqual(repair.normalize("phone", {"action": "screenshot"}, allowed),
                         ("phone", {"action": "screenshot"}))

    def test_thinking_aloud_next_to_a_call_is_reasoning_not_the_answer(self):
        from . import claude_parity
        a = self.agent()
        a.session.messages.append({"role": "user", "content": "افتح واتساب"})
        seen = []
        a.emit = seen.append
        call = SimpleNamespace(tool_calls=[{"id": "1"}])
        claude_parity.show_reply(a, call, "The user wants me to open WhatsApp. Let me do it.")
        self.assertEqual([e["type"] for e in seen], ["stream_reset", "reasoning_delta"])
        seen.clear()
        claude_parity.show_reply(a, call, "سأفتح واتساب")
        self.assertEqual(seen, [{"type": "assistant", "text": "سأفتح واتساب", "final": False}])
        seen.clear()
        claude_parity.show_reply(a, SimpleNamespace(tool_calls=[]), "Done: WhatsApp is open.")
        self.assertEqual(seen[-1]["final"], True)

    def test_connected_service_tools_are_deferred_not_sent_every_request(self):
        from . import claude_parity, connectors
        names = []
        for i in range(60):
            n = "connected_huggingface_t%d_%010d" % (i, i)
            tools.REGISTRY[n] = connectors.OfficialMcpTool(n, "huggingface", {
                "name": "t%d" % i, "description": "Model search %d " % i + "x" * 900,
                "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}}})
            connectors.OPERATIONS[n] = ("huggingface", "POST", "tools/call")
            names.append(n)

        def drop():
            for n in names:
                tools.REGISTRY.pop(n, None)
                connectors.OPERATIONS.pop(n, None)
        self.addCleanup(drop)
        a = self.agent()
        defs = [tools.REGISTRY[n].schema() for n in ["read", "bash"] + names]
        sent = [d["function"]["name"] for d in claude_parity.finalize(a, defs)]
        self.assertEqual(sent, ["read", "bash"])
        a.session.discovered_builtin_tools = [names[3]]
        self.assertIn(names[3], [d["function"]["name"] for d in claude_parity.finalize(a, defs)])

    def test_no_summary_when_the_fixed_part_fills_the_window(self):
        from . import claude_parity
        a = self.agent()
        a.schemas = lambda: [{"function": {"name": "x", "description": "y" * 400000}}]
        a.system_prompt = lambda: "s"
        a.session.messages = [{"role": "user", "content": "افتح واتساب"}] * 4
        notes = []
        a.emit = notes.append
        self.assertFalse(claude_parity.compaction_helps(a, 128000, 0.8))
        self.assertEqual(len(notes), 1)
        a.schemas = lambda: []
        self.assertTrue(claude_parity.compaction_helps(a, 128000, 0.8))


if __name__ == "__main__":
    unittest.main()
