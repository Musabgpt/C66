"""code_check: language servers without VS Code. The real pyright runs where it is installed (skipped elsewhere);
the Termux route, the first-time install and the finishing gate are checked with the bridge stood in for."""
import os
import shutil
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from . import tools  # noqa: F401 - registers every tool first, as the engine does

HAS_PYRIGHT = bool(shutil.which("pyright-langserver"))
BROKEN = "def add(a, b):\n    return a + b\n\nprint(add(1))\nundefined_name\nimport not_installed_pkg\n"


class CodeCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.realpath(self.tmp.name)

    def write(self, rel, text):
        p = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        return p

    def ctx(self, changed=()):
        return SimpleNamespace(root=self.root, cwd=self.root, changed=set(changed), cancel=None,
                               session=SimpleNamespace(turn=1, changes=lambda turn: [], root=self.root, dirs=[]))

    def test_offered_with_the_tools(self):
        self.assertIn("code_check", tools.default_set({}, self.root))

    def test_problems_leave_out_missing_packages_and_the_gate_keeps_only_certain_errors(self):
        from . import code_check
        results = {"python": {"diagnostics": {os.path.join(self.root, "a.py"): [
            {"line": 4, "column": 7, "severity": "error", "message": "Argument missing", "code": "reportCallIssue"},
            {"line": 6, "column": 8, "severity": "error", "message": "Import could not be resolved",
             "code": "reportMissingImports"},
            {"line": 5, "column": 1, "severity": "warning", "message": "Expression value is unused",
             "code": "reportUnusedExpression"},
            {"line": 7, "column": 1, "severity": "error", "message": "Type mismatch", "code": "reportArgumentType"}]}}}
        lines, n = code_check.problems(results, self.root)
        self.assertEqual(n, {"errors": 2, "warnings": 1, "missing_packages": 1})
        self.assertEqual(lines[0], "a.py:4:7: error: Argument missing (reportCallIssue)")
        certain, _ = code_check.problems(results, self.root, certain=True)
        self.assertEqual(certain, ["a.py:4:7: error: Argument missing (reportCallIssue)"])

    def test_first_use_in_termux_installs_the_servers_once(self):
        from . import code_check, runtime_manager
        self.write("a.py", "x = 1\n")
        started = []
        with mock.patch.object(code_check, "where", lambda kind: ""), \
                mock.patch.object(code_check, "_termux", lambda: True), \
                mock.patch.object(runtime_manager, "process_start",
                                  lambda cmd: started.append(cmd) or {"id": "p1", "status": "running"}), \
                mock.patch.object(runtime_manager, "process_status", lambda pid: {"status": "running"}), \
                mock.patch.dict(code_check._install, {"id": None, "started": 0.0}):
            out, _ = tools.REGISTRY["code_check"].fn(self.ctx(), paths=["a.py"])
            self.assertIn("Installing the language servers in Termux", out)
            out2, _ = tools.REGISTRY["code_check"].fn(self.ctx(), paths=["a.py"])
            self.assertIn("being installed", out2)
        self.assertEqual(len(started), 1)
        self.assertIn("npm install -g", started[0])

    @unittest.skipUnless(HAS_PYRIGHT, "pyright is not installed here")
    def test_real_errors_definition_and_references(self):
        self.write("a.py", BROKEN)
        self.write("b.py", "from a import add\nprint(add(2, 3))\n")
        fn = tools.REGISTRY["code_check"].fn
        out, meta = fn(self.ctx(), paths=["a.py", "b.py"])
        self.assertIn("a.py:4:7: error: Argument missing for parameter \"b\"", out)
        self.assertIn("\"undefined_name\" is not defined", out)
        self.assertIn("not installed where the checker runs", out)
        self.assertEqual(meta["errors"], 2)
        out, _ = fn(self.ctx(), action="definition", path="b.py", line=2, symbol="add")
        self.assertTrue(out.startswith("a.py:1: def add(a, b):"), out)
        out, _ = fn(self.ctx(), action="references", path="a.py", line=1, symbol="add")
        self.assertIn("b.py:2: print(add(2, 3))", out)

    @unittest.skipUnless(HAS_PYRIGHT, "pyright is not installed here")
    def test_through_the_termux_bridge(self):
        """The client goes to Termux inside the command (a heredoc), with no file to copy first."""
        from . import code_check, runtime_manager

        def bridge(command):
            p = subprocess.run(["bash", "-lc", command], capture_output=True, text=True, timeout=90)
            return {"stdout": p.stdout, "stderr": p.stderr, "exit_code": p.returncode}
        path = self.write("a.py", BROKEN)
        with mock.patch.object(runtime_manager, "execute", bridge):
            res = code_check.check(self.root, [path], place="termux")
        rows = res["python"]["diagnostics"][path]
        self.assertIn("reportCallIssue", [r["code"] for r in rows])

    @unittest.skipUnless(HAS_PYRIGHT, "pyright is not installed here")
    def test_the_gate_holds_a_turn_back_only_for_certain_errors(self):
        from . import code_check
        bad = self.write("a.py", BROKEN)
        lines = code_check.gate(self.ctx([bad]))
        self.assertTrue(any("reportCallIssue" in x for x in lines))
        self.assertFalse(any("reportMissingImports" in x or "warning" in x for x in lines))
        good = self.write("c.py", "def add(a, b):\n    return a + b\n\nprint(add(1, 2))\n")
        self.assertEqual(code_check.gate(self.ctx([good])), [])


if __name__ == "__main__":
    unittest.main()
