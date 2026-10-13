"""Tests for the Cursor-style extensions (review, project map, rules, mentions, lint gate, templates, ports)."""
import os
import subprocess
import tempfile
import unittest
from types import SimpleNamespace

from . import tools  # noqa: F401 - registers every tool first, as the engine does


class ExtensionTests(unittest.TestCase):
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

    def agent(self):
        from . import agent, session
        s = session.Session(self.root)
        a = agent.Agent(s, client=SimpleNamespace(spec={}, local=False, on_device=False))
        self.addCleanup(a.memory.close)
        return a

    def edit(self, a, turn, rel, old, new):
        a.session.turn = turn
        ctx = __import__(__package__ + ".agent", fromlist=["ToolContext"]).ToolContext(a)
        tools.call(ctx, "edit", {"path": rel, "old": old, "new": new})
        return ctx

    # ---------------------------------------------------------------- review: hunks, Undo, restore
    def test_hunk_undo_and_restore_to_before_a_message(self):
        from . import review
        self.write("a.py", "one = 1\ntwo = 2\nthree = 3\nfour = 4\nfive = 5\nsix = 6\n")
        a = self.agent()
        self.edit(a, 1, "a.py", "one = 1", "one = 10")
        self.edit(a, 1, "a.py", "six = 6", "six = 60")
        h = review.hunks(a.session, "a.py")
        self.assertEqual(len(h["hunks"]), 2)
        review.revert_hunk(a.session, "a.py", 1, h["sig"])          # undo only the second change
        with open(os.path.join(self.root, "a.py")) as f:
            text = f.read()
        self.assertIn("one = 10", text)
        self.assertIn("six = 6\n", text)
        with self.assertRaises(ValueError):
            review.revert_hunk(a.session, "a.py", 0, h["sig"])      # stale signature is refused
        self.edit(a, 2, "a.py", "two = 2", "two = 20")
        reverted = review.restore(a.session, 2)                       # back to before message 2
        with open(os.path.join(self.root, "a.py")) as f:
            text = f.read()
        self.assertEqual(reverted, ["a.py"])
        self.assertIn("two = 2\n", text)
        self.assertIn("one = 10", text)
        review.restore(a.session, 1)                                  # back to before message 1
        with open(os.path.join(self.root, "a.py")) as f:
            self.assertIn("one = 1\n", f.read())

    def test_review_paths_stay_in_the_project(self):
        from . import review
        a = self.agent()
        self.assertTrue(review.route(a.session, "hunks", {"path": "../../etc/passwd"})[1].get("error"))

    # ---------------------------------------------------------------- project map and rules
    def test_project_map_lists_files_and_definitions(self):
        from . import project_map
        self.write("src/app.py", "class Store:\n    pass\n\ndef main():\n    pass\n")
        self.write("web/ui.js", "export function render() {}\n")
        self.write("node_modules/x/index.js", "function hidden() {}\n")
        text = project_map.block(self.root)
        self.assertIn("src/app.py: Store, main", text)
        self.assertIn("web/ui.js: render", text)
        self.assertNotIn("node_modules", text)
        self.assertEqual(text, project_map.block(self.root))          # cached and stable

    def test_cursor_rules_are_instruction_files(self):
        from . import extensions
        self.write(".cursor/rules/style.mdc", "Use tabs.")
        self.write(".cursorrules", "Be brief.")
        self.write(".musabai/rules.md", "Arabic answers.")
        found = [os.path.relpath(p, self.root).replace(os.sep, "/") for p in extensions.instruction_files(self.root)]
        for rel in (".cursor/rules/style.mdc", ".cursorrules", ".musabai/rules.md"):
            self.assertIn(rel, found)

    # ---------------------------------------------------------------- @-mentions
    def test_mentions_expand_folder_problems_and_git(self):
        from . import mentions
        self.write("lib/x.py", "print(undefined_thing)\n")
        a = self.agent()
        out = mentions.expand(a.session, "look at @folder:lib please")
        self.assertIn('<folder path="lib">', out)
        self.assertIn("lib/x.py", out)
        self.assertIn("(no uncommitted changes)", mentions.expand(a.session, "@git-diff"))
        self.assertEqual(mentions.expand(a.session, "email me at a@b.com"), "email me at a@b.com")
        self.assertIn("no such folder", mentions.expand(a.session, "@folder:../../etc"))

    # ---------------------------------------------------------------- lint gate before finishing
    def test_changed_files_with_errors_hold_the_turn(self):
        from . import verify_lint
        bad = self.write("bad.py", "def f(:\n")
        good = self.write("good.py", "x = 1\n")
        ctx = SimpleNamespace(root=self.root, changed={bad, good})
        self.assertIn("bad.py", verify_lint.changed_problems(ctx))
        self.assertEqual(verify_lint.changed_problems(SimpleNamespace(root=self.root, changed={good})), "")

    # ---------------------------------------------------------------- templates and ports
    def test_scaffold_creates_a_runnable_starter_without_overwriting(self):
        from . import agent
        a = self.agent()
        ctx = agent.ToolContext(a)
        self.write("cli/main.py", "# mine\n")
        text, meta = tools.call(ctx, "scaffold_project", {"template": "python-cli", "folder": "cli", "name": "Tool"})
        self.assertIn("Kept existing: main.py", text)
        with open(os.path.join(self.root, "cli/main.py")) as f:
            self.assertEqual(f.read(), "# mine\n")
        tools.call(ctx, "scaffold_project", {"template": "python-cli", "folder": "cli2", "name": "Tool"})
        r = subprocess.run(["python3", "-m", "unittest", "-q"], cwd=os.path.join(self.root, "cli2"),
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_dev_ports_skip_the_apps_own_servers(self):
        from . import dev_ports
        self.assertNotIn(8780, dev_ports.open_ports(extra=(8780,)))

    # ---------------------------------------------------------------- smarter and faster
    def test_trim_shortens_only_earlier_turns_tool_results(self):
        from . import smart
        big = "x" * 5000
        msgs = [{"role": "user", "content": "one"}, {"role": "tool", "tool_call_id": "a", "content": big},
                {"role": "user", "content": "two"}, {"role": "tool", "tool_call_id": "b", "content": big}]
        out = smart.trim(msgs)
        self.assertLess(len(out[1]["content"]), 1500)
        self.assertIn("omitted", out[1]["content"])
        self.assertEqual(out[3]["content"], big)                       # the current turn stays whole
        self.assertEqual(msgs[1]["content"], big)                      # the thread itself is unchanged

    def test_reviewer_runs_once_and_reports_bugs(self):
        from . import smart
        self.write("m.py", "def add(a, b):\n    return a + b\n")
        a = self.agent()
        ctx = self.edit(a, 1, "m.py", "return a + b", "return a - b\n\n\ndef sub(a, b):\n    return a + b")
        calls = []

        def chat(messages, **kw):
            calls.append(messages)
            return SimpleNamespace(content="m.py:5 - sub adds instead of subtracting - use a - b")
        a.client = SimpleNamespace(chat=chat, on_device=False)
        found = smart.review(a, ctx)
        self.assertIn("sub adds", found)
        self.assertIn("return a - b", calls[0][0]["content"])           # the diff was reviewed
        self.assertEqual(smart.review(a, ctx), "")                      # once per turn
        ctx2 = self.edit(a, 2, "m.py", "def sub", "def subtract")
        a.client = SimpleNamespace(chat=lambda m, **kw: SimpleNamespace(content="LGTM"), on_device=False)
        self.assertEqual(smart.review(a, ctx2), "")                     # small or approved: nothing to fix


if __name__ == "__main__":
    unittest.main()
