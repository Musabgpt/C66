"""Hermetic tests for the automation tools and their runtime (fake server, fake Termux, no network)."""

import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from . import automation_runtime as AR, tools

TOKEN = "test-token"


class _Fake(BaseHTTPRequestHandler):
    seen = []
    digest = "d-current"

    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, {"ok": True, "version": 2, "digest": _Fake.digest})
        self._send(404, {})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(n) or b"{}")
        _Fake.seen.append((self.path, body, self.headers.get("Authorization", "")))
        if self.headers.get("Authorization") != "Bearer " + TOKEN:
            return self._send(401, {"ok": False, "error": "unauthorized"})
        if self.path == "/ping":
            return self._send(200, {"ok": True})
        if self.path == "/phone_agent":
            if "fail" in body.get("goal", ""):
                return self._send(200, {"ok": True, "success": False, "reason": "could not find the app", "steps": 6})
            return self._send(200, {"ok": True, "success": True, "reason": "note saved", "steps": 4})
        if self.path == "/browser_agent":
            return self._send(200, {"ok": True, "result": "42 dollars", "success": True, "steps": 2,
                                    "urls": ["https://x/results"]})
        if self.path == "/browser":
            op = body.get("op")
            if op == "extract":
                return self._send(200, {"ok": True, "url": "https://x/", "title": "Shop", "text": "Welcome",
                                        "elements": ['field "Search" #q', 'button "Go"']})
            if op == "type":
                return self._send(200, {"ok": True, "url": "https://x/", "title": "Shop", "value": body.get("text")})
            return self._send(200, {"ok": True, "url": "https://x/", "title": "Shop"})
        if self.path == "/u2":
            if body.get("op") == "fail":
                return self._send(200, {"ok": False, "error": "no device"})
            if body.get("op") == "dump":
                return self._send(200, {"ok": True, "xml": "<hierarchy/>"})
            return self._send(200, {"ok": True, "output": "ok"})
        self._send(404, {})


class AutomationToolsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), _Fake)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.origin = "http://127.0.0.1:%d" % cls.srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        _Fake.seen = []
        _Fake.digest = "d-current"
        self.env = {k: os.environ.get(k) for k in ("MUSAB_AUTOMATION_ORIGIN", "MUSAB_AUTOMATION_TOKEN",
                                                   "MUSAB_ENGINE_BASE")}
        os.environ["MUSAB_AUTOMATION_ORIGIN"] = self.origin
        os.environ["MUSAB_AUTOMATION_TOKEN"] = TOKEN
        AR._STATE.update(token="", engine="", ready_digest="")
        self.ctx = type("Ctx", (), {"cancel": None, "emit": lambda self, e: None})()

    def tearDown(self):
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        AR._STATE.update(token="", engine="", ready_digest="")

    # ------------------------------------------------------------ the tools

    def test_tools_are_registered_with_the_engine_s_permission_kinds(self):
        kinds = {name: tools.REGISTRY[name].kind for name in AR.NAMES}
        self.assertEqual(kinds, {"phone_agent": "phone", "phone_power": "phone", "adb_connect": "phone",
                                 "browser": "net", "browser_agent": "net"})

    def test_every_call_carries_the_token(self):
        AR.t_browser(self.ctx, "open", url="example.com")
        self.assertEqual(_Fake.seen[-1][2], "Bearer " + TOKEN)

    def test_phone_agent_reports_done_or_not_done(self):
        text, meta = AR.t_phone_agent(self.ctx, "write a note and save it", 9)
        self.assertEqual(text, "Done (4 steps): note saved")
        self.assertEqual(_Fake.seen[-1][1], {"goal": "write a note and save it", "max_steps": 9})
        text, meta = AR.t_phone_agent(self.ctx, "fail to open the app")
        self.assertTrue(text.startswith("Not completed (6 steps): could not find the app"))
        self.assertIn("Look at the result yourself", text)
        with self.assertRaises(tools.ToolError):
            AR.t_phone_agent(self.ctx, "  ")

    def test_browser_extract_shows_the_text_and_what_can_be_used(self):
        text, meta = AR.t_browser(self.ctx, "extract")
        self.assertEqual(text.split("\n")[0], "Shop — https://x/")
        self.assertIn("Welcome", text)
        self.assertIn('field "Search" #q', text)
        self.assertNotIn("text", meta)                      # the page's text is the answer, not metadata

    def test_browser_type_and_submit(self):
        text, meta = AR.t_browser(self.ctx, "type", target="Search", text="shoes", submit=True)
        self.assertIn("The field now holds: shoes", text)
        sent = _Fake.seen[-1][1]
        self.assertEqual((sent["op"], sent["target"], sent["text"], sent["submit"]), ("type", "Search", "shoes", True))

    def test_browser_agent(self):
        text, meta = AR.t_browser_agent(self.ctx, "find the price", 3)
        self.assertEqual(text, "Done (2 steps): 42 dollars")
        self.assertEqual(meta["urls"], ["https://x/results"])

    def test_adb_and_power(self):
        AR.t_adb_connect(self.ctx, "connect", address="1.2.3.4:5555")
        self.assertEqual(_Fake.seen[-1][1], {"op": "adb_connect", "address": "1.2.3.4:5555", "code": ""})
        text, _ = AR.t_phone_power(self.ctx, "dump")
        self.assertEqual(text, "<hierarchy/>")
        with self.assertRaises(tools.ToolError):
            AR.t_adb_connect(self.ctx, "reboot")
        with self.assertRaises(tools.ToolError) as failed:
            AR.t_phone_power(self.ctx, "fail")
        self.assertEqual(str(failed.exception), "no device")

    def test_a_server_that_does_not_answer_is_a_clear_error(self):
        os.environ["MUSAB_AUTOMATION_ORIGIN"] = "http://127.0.0.1:1"
        AR._STATE["ready_digest"] = "something"
        with self.assertRaises(tools.ToolError) as failed:
            AR.t_browser(self.ctx, "extract")
        self.assertIn("does not answer", str(failed.exception))
        self.assertEqual(AR._STATE["ready_digest"], "")     # it is looked for again next time

    # ------------------------------------------------------------ the runtime

    def test_the_token_is_the_same_after_an_engine_restart(self):
        from . import server
        os.environ.pop("MUSAB_AUTOMATION_TOKEN")
        saved = server.server_key
        try:
            server.server_key = lambda: "engine-key-1"
            first = AR._token()
            AR._STATE["token"] = ""                          # a new engine process
            self.assertEqual(AR._token(), first)
            AR._STATE["token"] = ""
            server.server_key = lambda: "engine-key-2"       # another install
            self.assertNotEqual(AR._token(), first)
            self.assertNotIn("engine-key", first)            # the engine's key itself never leaves
        finally:
            server.server_key = saved

    def test_a_server_is_fresh_only_with_this_app_s_source_and_token(self):
        saved = AR.source_digest
        AR.source_digest = lambda: "d-current"
        try:
            self.assertTrue(AR._fresh())
            _Fake.digest = "d-older-app"
            self.assertFalse(AR._fresh())
            _Fake.digest = "d-current"
            os.environ["MUSAB_AUTOMATION_TOKEN"] = "another-engine"
            AR._STATE["token"] = ""
            self.assertFalse(AR._fresh())
        finally:
            AR.source_digest = saved

    def test_ensure_does_nothing_for_a_server_started_by_hand(self):
        from . import runtime_manager
        saved = runtime_manager.process_start
        runtime_manager.process_start = lambda *a, **k: self.fail("Termux must not be touched")
        try:
            AR.ensure(self.ctx)
        finally:
            runtime_manager.process_start = saved


class AutomationEnsureTests(unittest.TestCase):
    """ensure() against a faked Termux: what it installs, stops, writes and starts."""

    def setUp(self):
        from . import managed_linux, mcp_bundles, phone, runtime_manager, server
        self.mods = (runtime_manager, mcp_bundles, phone, server)
        self.saved = (runtime_manager.execute, runtime_manager.process_start, mcp_bundles._wait_termux_process,
                      phone.config, server.server_key, AR.health, AR._authorized, AR.call, AR.source_digest)
        self.env = {k: os.environ.get(k) for k in ("MUSAB_AUTOMATION_ORIGIN", "MUSAB_AUTOMATION_TOKEN",
                                                   "MUSAB_ENGINE_BASE")}
        os.environ.pop("MUSAB_AUTOMATION_ORIGIN", None)
        os.environ.pop("MUSAB_AUTOMATION_TOKEN", None)
        os.environ["MUSAB_ENGINE_BASE"] = "http://127.0.0.1:8795"
        AR._STATE.update(token="", engine="", ready_digest="")
        self.started, self.probed, self.calls, self.said = [], [], [], []
        self.server = "down"                 # down | stale | fresh
        self.installed = True
        test = self

        def execute(command, runtime=None):
            test.probed.append(command)
            if "pkill" in command:
                test.server = "down"
                return {"status": "completed", "exit_code": 0}
            ok = test.installed or "import mobilerun" not in command
            return {"status": "completed", "exit_code": 0 if ok else 1}

        def process_start(command, stdio=False, env=None):
            test.started.append(command)
            if "pip install" in command:
                test.installed = True
            if "exec ./venv/bin/python automation_server.py" in command:
                test.server = "fresh"
            return {"id": "p%d" % len(test.started)}

        def call(path, body=None, timeout=600):
            test.calls.append(path)
            if path == "/shutdown":
                test.server = "down"
            return {"ok": True}

        runtime_manager.execute = execute
        runtime_manager.process_start = process_start
        mcp_bundles._wait_termux_process = lambda process_id, timeout=420: {"status": "completed", "exit_code": 0}
        phone.config = lambda: ("http://127.0.0.1:8796", "phone-key")
        server.server_key = lambda: "engine-key"
        AR.source_digest = lambda: "d-current"
        AR.health = lambda timeout=4: (None if test.server == "down" else
                                       {"ok": True, "digest": "d-current" if test.server == "fresh" else "d-older"})
        AR._authorized = lambda timeout=5: test.server == "fresh"
        AR.call = call
        self.ctx = type("Ctx", (), {"cancel": None, "emit": lambda s, e: test.said.append(e["text"])})()

    def tearDown(self):
        runtime_manager, mcp_bundles, phone, server = self.mods
        (runtime_manager.execute, runtime_manager.process_start, mcp_bundles._wait_termux_process,
         phone.config, server.server_key, AR.health, AR._authorized, AR.call, AR.source_digest) = self.saved
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        AR._STATE.update(token="", engine="", ready_digest="")

    def start_command(self):
        return [c for c in self.started if "exec ./venv/bin/python automation_server.py" in c][-1]

    def test_installed_and_down_it_writes_the_server_and_starts_it(self):
        AR.ensure(self.ctx)
        self.assertFalse(any("apt-get" in c or "pip install" in c for c in self.started))    # nothing reinstalled
        self.assertTrue(any("base64 -d > " + AR.SERVER_PATH in c for c in self.started))
        cmd = self.start_command()
        self.assertIn("MUSAB_AUTOMATION_TOKEN=" + AR._token(), cmd)
        self.assertIn("MUSAB_LLM_BASE=http://127.0.0.1:8795/v1/free", cmd)
        self.assertIn("MUSAB_PHONE_KEY=phone-key", cmd)
        self.assertNotIn("engine-key ", AR._token())
        self.assertEqual(AR._STATE["ready_digest"], "d-current")
        self.assertIn("Starting the automation server...", self.said)

    def test_a_running_current_server_is_left_alone(self):
        self.server = "fresh"
        AR.ensure(self.ctx)
        self.assertEqual(self.started, [])
        AR.ensure(self.ctx)                                   # and the next call does not even probe Termux
        self.assertEqual(self.probed, [])

    def test_a_server_left_by_an_older_app_is_stopped_and_replaced(self):
        self.server = "stale"
        AR.ensure(self.ctx)
        self.assertIn("/shutdown", self.calls)
        self.assertEqual(self.server, "fresh")
        self.assertEqual(len([c for c in self.started if "automation_server.py" in c and "exec" in c]), 1)

    def test_a_stale_server_that_ignores_shutdown_is_killed(self):
        self.server = "stale"
        AR.call = lambda path, body=None, timeout=600: (_ for _ in ()).throw(tools.ToolError("401"))
        AR.ensure(self.ctx)
        self.assertTrue(any("pkill -f automation_server.py" in c for c in self.probed))
        self.assertEqual(self.server, "fresh")

    def test_first_use_installs_and_says_what_it_is_doing(self):
        self.installed = False
        AR.ensure(self.ctx)
        self.assertTrue(any("apt-get install" in c and "chromium" in c for c in self.started))
        pip = [c for c in self.started if "pip install" in c and "mobilerun" in c][0]
        for package in ("uiautomator2", "pydoll-python", "browser-use==0.13.5"):
            self.assertIn(package, pip)
        self.assertTrue(any("Installing Mobilerun" in line for line in self.said))
        self.assertEqual(self.server, "fresh")

    def test_stop_cancels_before_anything_is_started(self):
        from . import providers
        cancel = threading.Event()
        cancel.set()
        self.ctx.cancel = cancel
        with self.assertRaises(providers.Cancelled):
            AR.ensure(self.ctx)
        self.assertEqual(self.started, [])


if __name__ == "__main__":
    unittest.main()
