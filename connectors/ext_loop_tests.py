"""The Termux loop on the phone: bash runs in Termux in the project folder, Termux commands start there, local pages
open in Chrome, and a GitHub token connected in the app reaches gh and git in Termux."""
import os
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

from . import tools  # noqa: F401 - registers every tool first, as the engine does

PROJECT = "/storage/emulated/0/M80"


class LoopTests(unittest.TestCase):
    def ctx(self, root=PROJECT):
        return SimpleNamespace(root=root, cwd=root, cancel=threading.Event(), emit=lambda ev: None,
                               session=SimpleNamespace(turn=1, root=root))

    def on_phone(self):
        from . import lean, termux_loop
        for patch in (mock.patch.object(lean, "active", lambda: True),
                      mock.patch.object(termux_loop, "in_termux", lambda: False),
                      mock.patch.object(termux_loop, "termux_ready", lambda: True)):
            patch.start()
            self.addCleanup(patch.stop)

    def test_commands_start_in_the_project_when_termux_sees_it(self):
        from . import termux_loop
        self.assertEqual(termux_loop.in_project(self.ctx(), "node server.js"),
                         "cd /storage/emulated/0/M80 2>/dev/null; node server.js")
        self.assertEqual(termux_loop.in_project(self.ctx("/data/user/0/app/files/p"), "ls"), "ls")
        self.assertEqual(termux_loop.in_project(self.ctx(), "ls", runtime="REMOTE"), "ls")
        self.assertEqual(termux_loop.in_project(self.ctx("/storage/emulated/0/my app"), "ls"),
                         "cd '/storage/emulated/0/my app' 2>/dev/null; ls")

    def test_runtime_exec_and_process_start_run_in_the_project(self):
        from . import runtime_manager
        with mock.patch.object(runtime_manager, "execute",
                               lambda cmd, runtime=None: {"exit_code": 0, "stdout": cmd, "runtime": "TERMUX"}):
            out, _ = tools.REGISTRY["runtime_exec"].fn(self.ctx(), command="npm test")
        self.assertIn("cd /storage/emulated/0/M80 2>/dev/null; npm test", out)
        with mock.patch.object(runtime_manager, "process_start",
                               lambda cmd: {"id": "p1", "status": "running", "command": cmd}):
            out, _ = tools.REGISTRY["runtime_process_start"].fn(self.ctx(), command="node server.js")
        self.assertIn("cd /storage/emulated/0/M80 2>/dev/null; node server.js", out)

    def test_bash_runs_in_termux_on_the_phone(self):
        from . import runtime_manager
        self.on_phone()
        sent = []
        with mock.patch.object(runtime_manager, "execute",
                               lambda cmd: sent.append(cmd) or {"exit_code": 0, "stdout": "hi\n", "stderr": ""}):
            out, meta = tools.REGISTRY["bash"].fn(self.ctx(), command="curl -s 127.0.0.1:3000", timeout=30)
        self.assertEqual(sent, ["cd /storage/emulated/0/M80 2>/dev/null; curl -s 127.0.0.1:3000"])
        self.assertEqual(out, "exit 0\nhi")
        self.assertEqual(meta["runtime"], "termux")

    def test_a_longer_command_is_followed_to_its_end_and_background_starts_a_process(self):
        from . import runtime_manager
        self.on_phone()
        states = iter([{"status": "running", "logs": "added 1\n"},
                       {"status": "completed", "exit_code": 0, "logs": "added 1\nadded 2\n"}])
        with mock.patch.object(runtime_manager, "process_start", lambda cmd: {"id": "p9"}), \
                mock.patch.object(runtime_manager, "process_status", lambda pid: next(states)), \
                mock.patch("time.sleep", lambda s: None):
            out, meta = tools.REGISTRY["bash"].fn(self.ctx(), command="npm install")
        self.assertEqual(out, "exit 0\nadded 1\nadded 2")
        with mock.patch.object(runtime_manager, "process_start", lambda cmd: {"id": "p10", "command": cmd}):
            out, meta = tools.REGISTRY["bash"].fn(self.ctx(), command="node server.js", background=True)
        self.assertIn("process p10", out)
        self.assertEqual(meta["process"], "p10")

    def test_bash_stays_in_the_app_off_the_phone_or_outside_shared_storage(self):
        from . import termux_loop
        self.assertFalse(termux_loop.routes(self.ctx()))                          # not on a phone
        self.on_phone()
        self.assertTrue(termux_loop.routes(self.ctx()))
        self.assertFalse(termux_loop.routes(self.ctx(tempfile.mkdtemp())))        # Termux cannot see it

    def test_busybox_auto_routes_local_file_work_without_termux(self):
        from . import termux_loop
        self.on_phone()
        with tempfile.TemporaryDirectory() as folder:
            binary = os.path.join(folder, "libbusybox.so")
            with open(binary, "wb") as out:
                out.write(b"mock BusyBox executable")
            with mock.patch.dict(os.environ, {"NEWAL_BUSYBOX": binary, "NEWAL_BUSYBOX_AUTO": "1"}), \
                 mock.patch.object(termux_loop.os, "access", return_value=True):
                # These commands need no Termux and the user does not write 'busybox'.
                for command in ("grep -n hello src/app.py", "find . -name '*.py'",
                                "ls -la", "awk '{print $1}' input.txt",
                                "grep foo file.txt | head -5", "mkdir build"):
                    with self.subTest(command=command):
                        self.assertTrue(termux_loop.local_busybox_command(command))
                        self.assertFalse(termux_loop.routes(self.ctx(), command))
                # Existing Termux build flow must never be hijacked or blindly retried.
                for command in ("npm test", "node server.js", "gh repo clone owner/repo",
                                "grep foo file | npm test", "echo x; rm -rf /",
                                "echo $(node --version)", "grep foo\nnode app.js"):
                    with self.subTest(command=command):
                        self.assertFalse(termux_loop.local_busybox_command(command))
                        self.assertTrue(termux_loop.routes(self.ctx(), command))
                with mock.patch.object(tools, "run_command", return_value=(0, "found\\n")):
                    output, meta = tools.REGISTRY["bash"].fn(self.ctx(), command="grep hello file")
                self.assertEqual(meta["runtime"], "android-busybox")
                self.assertEqual(meta["exit"], 0)
                self.assertIn("found", output)

    def test_busybox_auto_off_or_missing_binary_preserves_termux_routing(self):
        from . import termux_loop
        self.on_phone()
        with tempfile.TemporaryDirectory() as folder:
            binary = os.path.join(folder, "busybox")
            with mock.patch.dict(os.environ, {"NEWAL_BUSYBOX": binary, "NEWAL_BUSYBOX_AUTO": "1"}):
                self.assertFalse(termux_loop.local_busybox_command("grep word file"))
                self.assertTrue(termux_loop.routes(self.ctx(), "grep word file"))
            with open(binary, "wb") as out:
                out.write(b"mock")
            with mock.patch.dict(os.environ, {"NEWAL_BUSYBOX": binary, "NEWAL_BUSYBOX_AUTO": "0"}):
                self.assertFalse(termux_loop.local_busybox_command("grep word file"))
                self.assertTrue(termux_loop.routes(self.ctx(), "grep word file"))

    def test_a_local_page_opens_in_chrome_and_else_in_the_default_browser(self):
        from . import phone, termux_loop
        calls = []

        def fake(action, timeout=90, **args):
            calls.append((action, dict(args)))
            if args.get("package") == termux_loop.CHROME and fail_chrome[0]:
                raise phone.PhoneError("no app on this phone does that (android.intent.action.VIEW)")
            return {"ok": True, "text": "opened"}
        fail_chrome = [False]
        ctx = self.ctx()
        ctx.agent = SimpleNamespace()
        with mock.patch.object(phone, "call", fake), mock.patch.object(phone, "available", lambda: True):
            tools.REGISTRY["phone"].fn(ctx, action="open_url", url="http://127.0.0.1:3000")
            tools.REGISTRY["phone"].fn(ctx, action="open_url", url="https://www.youtube.com/results?search_query=x")
            fail_chrome[0] = True
            tools.REGISTRY["phone"].fn(ctx, action="open_url", url="http://localhost:8080/")
        self.assertEqual(calls[0], ("open_url", {"url": "http://127.0.0.1:3000", "package": termux_loop.CHROME}))
        self.assertEqual(calls[1], ("open_url", {"url": "https://www.youtube.com/results?search_query=x",
                                                  "package": termux_loop.CHROME}))
        self.assertEqual(calls[-1], ("open_url", {"url": "http://localhost:8080/"}))      # no Chrome: the default

    def test_private_project_still_routes_development_to_connected_termux(self):
        from . import termux_loop
        self.on_phone()
        private = self.ctx("/data/user/0/dev.newal/files/project")
        self.assertFalse(termux_loop.routes(private))
        for command in ("pkg install git", "npm test", "python3 --version", "gh repo list",
                        "curl -I https://example.com"):
            with self.subTest(command=command):
                self.assertTrue(termux_loop.routes(private, command))
        self.assertFalse(termux_loop.routes(private, "ls -la"))
        self.assertEqual(termux_loop.in_project(private, "npm test"), "npm test")

    def test_fast_chrome_ui_sequence_requires_visible_confirmation(self):
        from . import phone, phone_fast
        self.assertIn("phone_sequence", tools.REGISTRY)
        calls = []

        def fake(action, timeout=90, **kwargs):
            calls.append((action, kwargs))
            return {"ok": True, "screen": "App: Chrome (com.android.chrome)\\n"
                    "[1] Sent successfully · tap" if action == "tap" else
                    "App: Chrome (com.android.chrome)\\n[1] Email · field"}
        steps = [{"action": "open_url", "url": "https://example.com/form"},
                 {"action": "tap", "text": "Email"},
                 {"action": "type", "text": "someone@example.com"},
                 {"action": "tap", "text": "Send"}]
        with mock.patch.object(phone, "call", fake), mock.patch.object(phone_fast, "read_screen",
                                                                        lambda: "App: Chrome\\nSent successfully"):
            result, meta = tools.REGISTRY["phone_sequence"].fn(
                self.ctx(), steps=steps, expected_text="Sent successfully")
        self.assertTrue(meta["verified"])
        self.assertEqual(calls[0][1]["package"], "com.android.chrome")
        self.assertTrue(calls[2][1]["private"])
        self.assertNotIn("someone@example.com", result)

    def test_fast_phone_sequence_reports_unverified_not_sent(self):
        from . import phone, phone_fast
        with mock.patch.object(phone, "call", lambda *a, **kw: {"ok": True,
                "screen": "App: Chrome (com.android.chrome)\\n[1] Submit · tap"}), \
             mock.patch.object(phone_fast, "read_screen",
                               lambda: "App: Chrome (com.android.chrome)\\n[1] Submit · tap"), \
             mock.patch.object(phone_fast.time, "sleep", lambda s: None):
            result, meta = tools.REGISTRY["phone_sequence"].fn(
                self.ctx(), steps=[{"action": "tap", "text": "Submit"}],
                expected_text="Your request was received")
        self.assertFalse(meta["done"])
        self.assertFalse(meta["verified"])
        self.assertIn("not found", result)

    def test_github_token_kind_and_handover_to_termux(self):
        from . import github, runtime_manager, termux_loop
        self.assertEqual(termux_loop.token_kind("github_pat_" + "a" * 60), "fine-grained")
        self.assertEqual(termux_loop.token_kind("ghp_" + "a" * 36), "classic")
        self.assertIn("read:org", github.TOKEN_PAGE)
        sent = []
        with mock.patch.object(termux_loop, "termux_ready", lambda: True), \
                mock.patch.object(termux_loop, "in_termux", lambda: False), \
                mock.patch.object(runtime_manager, "execute",
                                  lambda cmd: sent.append(cmd) or {"stdout": "synced:1\n", "exit_code": 0}):
            self.assertTrue(termux_loop.github_sync("ghp_" + "b" * 36))
            self.assertFalse(termux_loop.github_sync("not a token; rm -rf ~"))
        self.assertEqual(len(sent), 1)
        self.assertIn("T=ghp_" + "b" * 36, sent[0])
        self.assertIn("gh auth login -h github.com --with-token", sent[0])
        self.assertIn("credential.https://github.com.helper store", sent[0])


if __name__ == "__main__":
    unittest.main()
