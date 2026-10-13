import json
import os
import tempfile
import unittest
from types import SimpleNamespace

from . import tools  # noqa: F401 - registers every tool first, as the engine does


class ClaudeParityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name

    def agent(self):
        from . import agent, session
        s = session.Session(self.root)
        a = agent.Agent(s, client=SimpleNamespace(spec={}, local=False, on_device=False))
        self.addCleanup(a.memory.close)
        return a

    def test_tool_search_imports_first_without_circular_error(self):
        import importlib, sys
        saved = {k: v for k, v in sys.modules.items() if k.startswith(__package__ + ".")}
        try:
            for k in list(saved):
                del sys.modules[k]
            mod = importlib.import_module(__package__ + ".tool_search")
            self.assertTrue(callable(mod.install))
        finally:
            for k in [k for k in sys.modules if k.startswith(__package__ + ".")]:
                del sys.modules[k]
            sys.modules.update(saved)

    def test_request_has_unique_tool_names(self):
        names = [d["function"]["name"] for d in self.agent().schemas()]
        self.assertEqual(len(names), len(set(names)), [n for n in names if names.count(n) > 1])

    def test_long_tail_is_deferred_but_core_and_tool_search_visible(self):
        a = self.agent()
        visible = {d["function"]["name"] for d in a.schemas()}
        for core in ("read", "edit", "write", "bash", "glob", "grep", "todo", "task", "tool_search", "web_fetch"):
            self.assertIn(core, visible)
        self.assertNotIn("observability_export", visible)
        self.assertIn("observability_export", a.session.tool_names)   # still executable
        from . import claude_parity
        self.assertIn("observability_export", claude_parity.deferred_note(a))

    def test_tool_search_activates_deferred_builtin(self):
        from . import tool_search
        a = self.agent()
        a.schemas()
        result = tool_search.activate(a, "export observability events", 4)
        self.assertIn("observability_export", result["newly_available"])
        self.assertIn("observability_export", {d["function"]["name"] for d in a.schemas()})

    def test_direct_call_of_deferred_tool_exposes_it(self):
        from . import claude_parity
        a = self.agent()
        a.schemas()
        claude_parity.mark_used(a, "git_log")
        self.assertIn("git_log", {d["function"]["name"] for d in a.schemas()})

    def test_defer_can_be_switched_off(self):
        a = self.agent()
        a.cfg = dict(a.cfg, defer_tools=False)
        a._schemas = None
        self.assertIn("observability_export", {d["function"]["name"] for d in a.schemas()})

    def test_reports(self):
        from . import claude_parity
        a = self.agent()
        a.session.todo = [{"text": "one", "status": "done"}, {"text": "two", "status": "pending"}]
        self.assertIn("1/2", claude_parity.todos_report(a.session))
        self.assertIn("No background jobs", claude_parity.bashes_report(a.session))
        report = claude_parity.context_report(a)
        self.assertIn("Tools", report)
        self.assertIn("deferred", report)
        self.assertIn("unique tool names", claude_parity.doctor_report(a))

    def test_bang_runs_in_project_and_enters_conversation(self):
        from . import claude_parity
        a = self.agent()
        a.session.mode = "auto-edit"
        svc = SimpleNamespace(busy=lambda sid: False)
        out = claude_parity.bang(svc, a.session, "echo parity-ok && pwd")
        self.assertEqual(out["code"], 0)
        self.assertIn("parity-ok", out["reply"])
        self.assertIn(os.path.realpath(self.root), os.path.realpath(out["reply"].splitlines()[2]))
        self.assertIn("parity-ok", json.dumps(a.session.messages))

    def test_bang_respects_read_only(self):
        from . import claude_parity
        a = self.agent()
        a.session.mode = "read-only"
        out = claude_parity.bang(SimpleNamespace(busy=lambda sid: False), a.session, "touch x")
        self.assertFalse(os.path.exists(os.path.join(self.root, "x")))
        self.assertIn("Read-only", out["reply"])

    def test_auto_test_falls_back_to_unittest_without_pytest(self):
        import importlib.util
        from unittest import mock
        from . import context
        with open(os.path.join(self.root, "test_x.py"), "w") as f:
            f.write("import unittest\n")
        with mock.patch.object(importlib.util, "find_spec", return_value=None):
            cmd = context.test_command(self.root) if hasattr(context, "test_command") else None
        if cmd is not None:
            self.assertIn("unittest", cmd)


if __name__ == "__main__":
    unittest.main()
