"""An edit that breaks a file's syntax is refused and the file is put back exactly (guard.py); an edit that keeps it
valid, a file already broken, and a new file go through as before."""
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from . import tools  # noqa: F401 - registers every tool first, as the engine does

JAVA = """package demo;

public class Evaluator {
    // a { in a comment, and "}" in a string
    private String brace = "}";
    private char open = '{';

    double eval(String s) {
        if (s.isEmpty()) {
            return 0;
        }
        return Double.parseDouble(s);
    }
}
"""
KOTLIN = 'class A {\n    val s = """{ not code ${name} }"""\n    val t = "${map["k"]}"\n    fun f() { }\n}\n'
JS = "const ok = (s) => /^[{(]+$/.test(s);\nconst t = `a ${b ? `x` : 'y'} c`;\nfunction f() {\n  return 1;\n}\n"


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.realpath(self.tmp.name)
        from . import settings
        for p in (mock.patch.object(settings, "HOME", os.path.join(self.root, "home")),
                  mock.patch.object(settings, "CONFIG", os.path.join(self.root, "home", "config.json"))):
            p.start()
            self.addCleanup(p.stop)
        from . import agent, session
        s = session.Session(self.root)
        self.agent = agent.Agent(s, client=SimpleNamespace(spec={}, local=False, on_device=False,
                                                           context=lambda: 128000))
        self.addCleanup(self.agent.memory.close)
        self.ctx = agent.ToolContext(self.agent)

    def file(self, name, text, newline=None):
        path = os.path.join(self.root, name)
        with open(path, "w", encoding="utf-8", newline=newline) as f:
            f.write(text)
        return path

    def raw(self, path):
        with open(path, "rb") as f:
            return f.read()

    def test_the_guard_wraps_write_and_edit(self):
        for name in ("write", "edit"):
            self.assertTrue(getattr(tools.REGISTRY[name].fn, "_guard", False), name)

    def test_an_edit_that_drops_a_closing_brace_is_refused_and_undone(self):
        path = self.file("Evaluator.java", JAVA)
        before = self.raw(path)
        with self.assertRaises(tools.ToolError) as e:
            tools.call(self.ctx, "edit", {"path": "Evaluator.java", "old": "            return 0;\n        }\n",
                                          "new": "            return 0;\n"})
        self.assertIn("edit refused: it breaks the syntax of Evaluator.java", str(e.exception))
        self.assertIn("is never closed", str(e.exception))
        self.assertEqual(self.raw(path), before)

    def test_an_edit_that_keeps_the_file_valid_goes_through(self):
        path = self.file("Evaluator.java", JAVA)
        out, _ = tools.call(self.ctx, "edit", {"path": "Evaluator.java", "old": "return 0;", "new": "return -1;"})
        self.assertIn("Edited Evaluator.java", out)
        self.assertIn("return -1;", self.raw(path).decode())

    def test_python_and_json(self):
        self.file("calc.py", "def add(a, b):\n    return a + b\n")
        with self.assertRaises(tools.ToolError) as e:
            tools.call(self.ctx, "edit", {"path": "calc.py", "old": "return a + b", "new": "return (a + b"})
        self.assertIn("calc.py", str(e.exception))
        self.file("data.json", '{"a": 1}\n')
        with self.assertRaises(tools.ToolError):
            tools.call(self.ctx, "write", {"path": "data.json", "content": '{"a": 1,\n'})
        self.assertEqual(self.raw(os.path.join(self.root, "data.json")), b'{"a": 1}\n')

    def test_a_file_already_broken_and_a_new_file_are_never_blocked(self):
        self.file("Broken.java", "class Broken {\n    void f() {\n")
        out, _ = tools.call(self.ctx, "edit", {"path": "Broken.java", "old": "void f()", "new": "void g()"})
        self.assertIn("Edited Broken.java", out)
        out, _ = tools.call(self.ctx, "write", {"path": "New.java", "content": "class New {\n"})
        self.assertIn("Wrote New.java", out)

    def test_strings_comments_templates_and_regular_expressions_are_not_brackets(self):
        from . import guard
        self.assertIsNone(guard.problem("Evaluator.java", JAVA))
        self.assertIsNone(guard.problem("A.kt", KOTLIN))
        self.assertIsNone(guard.problem("a.js", JS))
        self.assertIsNone(guard.problem("App.js", "const a = <div>1) step</div>;\n"))   # JSX is not judged
        self.assertIn("is never closed", guard.problem("a.ts", "function f() {\n"))
        self.assertIn("closes nothing", guard.problem("a.go", "func f() }\n"))

    def test_line_endings_and_bytes_come_back_exactly(self):
        path = self.file("Win.java", JAVA, newline="\r\n")
        before = self.raw(path)
        self.assertIn(b"\r\n", before)
        with self.assertRaises(tools.ToolError):
            tools.call(self.ctx, "write", {"path": "Win.java", "content": JAVA.replace("}\n}\n", "}\n")})
        self.assertEqual(self.raw(path), before)
