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

    # ---------------------------------------------------------------- fixes: uv preparation, stale Termux bridge
    def test_service_that_never_updated_still_prepares_uv(self):
        import threading
        from unittest import mock
        from . import automation as au, runtime_manager as rm
        setup = au.DependencyPreparation()
        service = SimpleNamespace(lock=threading.RLock())            # no .updating: no engine update ran yet
        with mock.patch.object(rm, "_termux_record", return_value={"status": "connected"}), \
             mock.patch.object(au, "ensure_dependency", return_value={"ok": True, "exit_code": 0, "stdout": "uv 1\nuvx 1"}):
            setup.tick(service, threading.Event())
        self.assertEqual(setup.status()["state"], "ready")

    def test_bridge_from_an_earlier_install_is_replaced(self):
        import io
        import urllib.error
        from unittest import mock
        from . import runtime_manager as rm
        err = urllib.error.HTTPError("http://127.0.0.1:8799/health", 401, "x", {}, io.BytesIO(b'{"error":"unauthorized"}'))
        opener = mock.Mock()
        opener.open.side_effect = err
        with mock.patch.object(rm, "_bridge_token", return_value="t" * 64), \
             mock.patch.object(rm.urllib.request, "build_opener", return_value=opener):
            with self.assertRaises(rm.BridgeStaleToken):
                rm._bridge_request("GET", "/health")
        responses = [rm.BridgeStaleToken("unauthorized"),
                     {"ok": True, "bridge": "musabai-termux", "version": rm.termux_bridge_server.VERSION}]
        with mock.patch.object(rm, "_bridge_request", side_effect=responses), \
             mock.patch.object(rm, "_bootstrap_bridge") as bootstrap:
            self.assertEqual(rm._ensure_bridge()["version"], rm.termux_bridge_server.VERSION)
        bootstrap.assert_called_once()

    # ---------------------------------------------------------------- the phone
    def fake_phone(self, screens, apps=("Files (com.android.files)",)):
        from . import phone
        shot = self.write("shot.jpg", "jpegbytes")
        calls = []
        screens = list(screens)
        apps = list(apps)

        def call(action, timeout=90, **args):
            calls.append((action, args))
            if action == "screen":
                return {"text": screens.pop(0) if len(screens) > 1 else screens[0]}
            if action == "apps":
                return {"text": "\n".join(apps.pop(0) if len(apps) > 1 else apps[0])}
            if action == "screenshot":
                return {"path": shot, "preview_path": shot}
            if action == "crash_reports":
                return {"text": "(no local test reports)"}
            return {"ok": True, "text": "done"}
        from unittest import mock
        for p in (mock.patch.object(phone, "available", return_value=True),
                  mock.patch.object(phone, "call", side_effect=call), mock.patch("time.sleep")):
            p.start()
            self.addCleanup(p.stop)
        return calls

    def vision_agent(self, answer="A login screen with a blue Sign in button at the bottom."):
        a = self.agent()
        seen = []

        def chat(messages, **kw):
            seen.append(messages)
            return SimpleNamespace(content=answer)
        a.client = SimpleNamespace(chat=chat, on_device=False)
        return a, seen

    def test_screen_look_shows_the_screenshot_to_a_vision_model(self):
        from . import agent
        self.fake_phone(["App: Demo (com.demo)"])
        a, seen = self.vision_agent()
        text, meta = tools.call(agent.ToolContext(a), "screen_look", {"question": "is the button visible?"})[:2]
        self.assertIn("blue Sign in button", text)
        self.assertTrue(meta["vision"])
        part = seen[0][0]["content"][1]
        self.assertTrue(part["image_url"]["url"].startswith("data:image/jpeg;base64,"))
        self.assertIn("is the button visible?", seen[0][0]["content"][0]["text"])

    def test_android_test_app_installs_opens_and_reports_a_crash(self):
        from . import agent
        self.write("app/build.gradle.kts", 'android { defaultConfig { applicationId = "com.demo.app" } }')
        self.write("out/app.apk", "PK")
        calls = self.fake_phone(
            ["App: Package installer (com.google.android.packageinstaller)\nInstall",
             "App: Launcher (com.launcher)",
             "App: Demo (com.demo.app)\nDemo keeps stopping\nClose app"],
            apps=[("Files (com.android.files)",), ("Files (com.android.files)", "Demo (com.demo.app)")])
        a, _ = self.vision_agent("An Android crash dialog.")
        text, meta = tools.call(agent.ToolContext(a), "android_test_app", {"apk": "out/app.apk"})[:2]
        actions = [c[0] for c in calls]
        self.assertIn("install_apk", actions)
        self.assertIn(("open_app", {"name": "com.demo.app"}), calls)
        self.assertTrue(meta["crashed"])
        self.assertIn("CRASHED", text)
        self.assertIn("crash dialog", text)

    def test_finish_notification_summarises_long_turns_only(self):
        from . import phone_ext
        title, text = phone_ext.summary("**Done.** Fixed the login bug.\nMore detail", 95, "", [{"path": "a"}, {"path": "b"}])
        self.assertIn("انتهت", title)
        self.assertIn("Done. Fixed the login bug.", text)
        self.assertIn("1m 35s · 2 files changed", text)
        calls = self.fake_phone(["App: x (y)"])
        a = self.agent()
        phone_ext.finished(a, "ok", 5)                    # short: the user is still there
        import threading
        phone_ext.finished(a, "ok", 60)
        for t in threading.enumerate():
            if t.name == "musab-finish-notify":
                t.join(5)
        self.assertEqual([c for c in calls if c[0] == "notify"],
                         [("notify", {"title": "MusabAI: انتهت المهمة ✓", "text": "ok\n1m 0s", "only_background": True})])

    def test_waiting_approval_is_announced_once_unless_answered(self):
        import threading
        from . import phone_ext
        calls = self.fake_phone(["App: x (y)"])
        answered = threading.Event()
        phone_ext.approval_waiting({"tool": "bash", "args": {"command": "npm test"}}, answered, delay=0.05)
        late = threading.Event()
        late.set()
        phone_ext.approval_waiting({"tool": "bash", "args": {"command": "rm x"}}, late, delay=0.05)
        for t in threading.enumerate():
            if t.name == "musab-approval-notify":
                t.join(5)
        notes = [c for c in calls if c[0] == "notify"]
        self.assertEqual(len(notes), 1)
        self.assertIn("npm test", notes[0][1]["text"])
        self.assertTrue(notes[0][1]["only_background"])

    def test_reviewer_waits_out_a_rate_limit(self):
        from . import provider_pool, smart
        a = self.agent()
        a.client = SimpleNamespace(chat=lambda *x, **k: None, on_device=False)
        self.assertTrue(smart.enabled(a))
        provider_pool.HEALTH._data["groq"] = {"state": "rate_limited", "cooldown_until": provider_pool._now() + 60}
        self.addCleanup(provider_pool.HEALTH._data.pop, "groq", None)
        self.assertFalse(smart.enabled(a))

    def test_application_id_from_gradle(self):
        from . import phone_ext
        self.write("android/app/build.gradle", "defaultConfig {\n  applicationId 'org.x.y'\n}")
        self.assertEqual(phone_ext.application_id(self.root), "org.x.y")

    # ---------------------------------------------------------------- background agents
    def git_project(self):
        self.write("a.py", "x = 1\n")
        for args in (["init", "-q"], ["add", "-A"], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"]):
            subprocess.run(["git"] + args, cwd=self.root, check=True, capture_output=True)

    def test_background_agent_works_in_its_own_worktree_and_is_applied(self):
        import threading
        from . import fleet, service
        self.git_project()
        svc = service.Service()
        self.assertIs(fleet.SERVICE, svc)
        parent = svc.create(self.root)
        sent = []

        def send(sid, text, images=None, lang=""):
            sent.append(text)
            s = svc.get(sid)

            def work():
                with open(os.path.join(s.root, "a.py"), "w") as f:
                    f.write("x = 2\n")
                s.add({"role": "assistant", "content": "Changed a.py; checked it."})
            t = threading.Thread(target=work)
            svc.threads[sid] = t
            t.start()
            return {"started": True, "session": sid}
        svc.send = send
        sid, name, isolated = fleet.start(svc, parent, "set x to 2 in a.py")
        self.assertTrue(isolated)
        self.assertIn("background agent", sent[0])
        [done] = fleet.wait(svc, [sid], longest=20)
        self.assertEqual(done["state"], "done")
        self.assertIn("Changed a.py", done["answer"])
        with open(os.path.join(self.root, "a.py")) as f:
            self.assertEqual(f.read(), "x = 1\n")                       # the project is untouched until applied
        self.assertEqual(fleet.apply(svc, sid).get("applied"), ["a.py"])
        with open(os.path.join(self.root, "a.py")) as f:
            self.assertEqual(f.read(), "x = 2\n")
        with self.assertRaises(fleet.FleetError):
            fleet.start(svc, svc.get(sid), "more work")                  # no agents starting agents
        self.assertIn("/bg <task>", svc.command(parent, "/bg")["reply"])
        self.assertIn(name[:10], svc.command(parent, "/agents")["reply"])
        self.assertIn("/agents", svc.command(parent, "/help")["reply"])

    # ---------------------------------------------------------------- GitHub from the chat
    def fake_github(self, responses):
        from unittest import mock
        from . import github
        calls = []

        def api(method, path, body=None, tok=None):
            calls.append((method, path, body))
            for prefix, value in responses:
                if path.startswith(prefix):
                    return value.pop(0) if isinstance(value, list) and len(value) > 1 else (
                        value[0] if isinstance(value, list) else value)
            return {}
        for p in (mock.patch.object(github, "token", return_value="t"), mock.patch.object(github, "_api", side_effect=api)):
            p.start()
            self.addCleanup(p.stop)
        return calls

    def test_github_overview_watch_and_merge(self):
        import threading
        from unittest import mock
        from . import ci, gh_monitor
        run_ok = {"id": 11, "run_number": 381, "name": "android", "status": "completed", "conclusion": "success",
                  "head_branch": "main", "display_title": "Merge #30", "html_url": "https://x/11",
                  "run_started_at": "2026-10-13T20:00:00Z", "updated_at": "2026-10-13T20:04:30Z"}
        run_going = dict(run_ok, id=12, run_number=382, status="in_progress", conclusion=None)
        run_failed = dict(run_going, status="completed", conclusion="failure")
        calls = self.fake_github([
            ("/repos/o/r/actions/runs?head_sha=", {"workflow_runs": [{"id": 12, "name": "android", "status": "completed",
                                                                       "conclusion": "success", "created_at": "x"}]}),
            ("/repos/o/r/actions/runs?per_page", {"workflow_runs": [run_going, run_ok]}),
            ("/repos/o/r/actions/runs/12", [run_going, run_failed]),
            ("/repos/o/r/pulls?state=open", [[{"number": 7, "title": "Phone", "head": {"ref": "f", "sha": "abc"},
                                               "base": {"ref": "main"}, "html_url": "u"}]]),
            ("/repos/o/r/pulls/7/merge", {"merged": True, "sha": "0b36bbf5"}),
            ("/repos/o/r/pulls/7", {"head": {"sha": "abc"}}),
        ])
        text, data = gh_monitor.overview("o/r")
        self.assertIn("#381 android ✓", text)
        self.assertIn("#7 Phone (f → main) · ✓ passing", text)
        self.assertEqual(gh_monitor.resolve_run("o/r", "pr 7"), 12)
        self.assertEqual(gh_monitor.resolve_run("o/r", "#381"), 11)
        events = []
        svc = SimpleNamespace(broadcast=events.append)
        with mock.patch.object(gh_monitor, "POLL", 0), \
             mock.patch.object(ci, "failures", return_value=[{"run": "android", "job": "build", "url": "u",
                                                                "steps": ["Test"], "log": "AssertionError: 1 != 2"}]):
            self.assertTrue(gh_monitor.watch_in_background(svc, "s1", "o/r", 12))
            for t in threading.enumerate():
                if t.name == "musab-gh-watch":
                    t.join(10)
        end = [e for e in events if e.get("type") == "gh_watch" and e.get("state") == "end"][0]
        self.assertFalse(end["ok"])
        self.assertIn("AssertionError", end["text"])
        self.assertTrue(any(e.get("type") == "notice" and e.get("session") == "s1" for e in events))
        self.assertIn("Merged #7", gh_monitor.merge("o/r", "7", "squash"))
        self.assertIn(("PUT", "/repos/o/r/pulls/7/merge", {"merge_method": "squash"}), calls)
        self.assertEqual(gh_monitor.full_name("", "https://github.com/Musabgpt/NewAl-Cloud.git"), "Musabgpt/NewAl-Cloud")
        with self.assertRaises(gh_monitor.GHError):
            gh_monitor.full_name("", "not a repo")

    def test_fleet_and_github_tools_are_offered(self):
        for name in ("background_agent", "github_monitor"):
            self.assertIn(name, tools.REGISTRY)
            self.assertIn(name, tools.default_set(root=self.root))
        from . import permissions
        self.assertIn("watch", permissions.GITHUB_LOOKS)
        self.assertNotIn("merge", permissions.GITHUB_LOOKS)

    # ---------------------------------------------------------------- the v10 prompt
    def test_prompt_is_focused_and_stable(self):
        from . import agent_policy, claude_parity, prompts, tool_search
        core = prompts.system("bash")
        self.assertLess(len(core), 9000)
        self.assertLess(core.index("You are MusabAI"), 80)                      # identity first
        self.assertNotIn("phone", core)
        small = prompts.system("bash", local=True, steps=25, phone=True)
        self.assertLess(len(small), 2200)
        self.assertNotIn("background_agent", small)                             # only what a small model has
        a = self.agent()
        first = a.request_messages()[0]["content"]
        self.assertNotIn("Working style (like Cursor", first)                    # the rules live in the core now
        tool_search.activate(a, "export observability events", 4)
        self.assertEqual(a.request_messages()[0]["content"], first)              # loading a tool keeps the prefix
        content, _ = a._user_content("fix it", [])
        a.session.add({"role": "user", "content": content})
        second, _ = a._user_content("and add a test", [])
        self.assertEqual(second, "and add a test")                               # no note glued to every message
        self.assertIn("observability_export", claude_parity.deferred_note(a))
        self.assertTrue(agent_policy.stale_builtin("MusabAI behavior profile v9\nold"))


if __name__ == "__main__":
    unittest.main()
