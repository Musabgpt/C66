import io
import json
from pathlib import Path
import shlex
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import uuid
from unittest import mock

from . import mcp_bundles, runtime_manager, termux_bridge_server


TOKEN = "test-token-" + "a" * 48


class TermuxBridgeServerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.server = termux_bridge_server.make_server("127.0.0.1", 0, TOKEN, self.temp.name)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]

    def tearDown(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(2)

    def request(self, path, body=None, token=TOKEN, base=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"X-MusabAI-Token": token}
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request((base or self.base) + path, data=data, headers=headers)
        with urllib.request.urlopen(req, timeout=3) as response:
            return json.load(response)

    def test_auth_health_and_environment(self):
        with self.assertRaises(urllib.error.HTTPError) as denied:
            self.request("/health", token="wrong")
        self.assertEqual(denied.exception.code, 401)
        health = self.request("/health")
        self.assertTrue(health["ok"])
        self.assertEqual(health["bridge"], "musabai-termux")
        env = self.request("/environment?commands=python,bash,definitely_missing_command")
        self.assertTrue(env["commands"]["python"]["available"])
        self.assertTrue(env["commands"]["bash"]["available"])
        self.assertFalse(env["commands"]["definitely_missing_command"]["available"])

    def test_exec_returns_structured_json(self):
        out = self.request("/exec", {"command": "printf bridge-ok"})
        self.assertEqual(out["status"], "completed")
        self.assertEqual(out["exit_code"], 0)
        self.assertEqual(out["stdout"], "bridge-ok")
        self.assertTrue(out["command_success"])

    def runtime_bridge(self):
        # Keep HTTP, authentication, health checks and shell execution real.
        from contextlib import ExitStack
        stack = ExitStack()
        stack.enter_context(mock.patch.object(runtime_manager, "_bridge_origin", return_value=self.base))
        stack.enter_context(mock.patch.object(runtime_manager, "_bridge_token", return_value=TOKEN))
        stack.enter_context(mock.patch.object(runtime_manager, "_termux_record", return_value={"status": "connected"}))
        return stack

    def test_runtime_nonzero_exit_is_a_command_result_not_bridge_failure(self):
        with self.runtime_bridge():
            for code in (0, 1, 2, 127):
                with self.subTest(code=code):
                    out = runtime_manager.execute(
                        "printf output; printf diagnostic >&2; exit %d" % code, runtime_manager.TERMUX)
                    self.assertTrue(out["ok"])
                    self.assertEqual(out["status"], "completed")
                    self.assertEqual(out["exit_code"], code)
                    self.assertEqual(out["command_success"], code == 0)
                    self.assertEqual(out["stdout"], "output")
                    self.assertEqual(out["stderr"], "diagnostic")

    def test_timeout_remains_a_structured_command_result(self):
        with self.runtime_bridge(), mock.patch.object(
            termux_bridge_server.subprocess, "run",
            side_effect=subprocess.TimeoutExpired("test", 75, output=b"partial", stderr=b"diagnostic")
        ):
            out = runtime_manager.execute("sleep 90", runtime_manager.TERMUX)
        self.assertTrue(out["ok"])
        self.assertEqual(out["status"], "timeout")
        self.assertIsNone(out["exit_code"])
        self.assertFalse(out["command_success"])
        self.assertEqual(out["stdout"], "partial")
        self.assertEqual(out["stderr"], "diagnostic")

    def test_actual_bridge_errors_still_raise(self):
        with self.runtime_bridge():
            with self.assertRaisesRegex(runtime_manager.BridgeError, "non-empty command"):
                runtime_manager._bridge_request("POST", "/exec", {"command": ""})
            with mock.patch.object(runtime_manager, "_bridge_token", return_value="wrong"):
                with self.assertRaisesRegex(runtime_manager.BridgeError, "unauthorized"):
                    runtime_manager._bridge_request("GET", "/health")
            with mock.patch.object(termux_bridge_server.subprocess, "run", side_effect=OSError("spawn failed")):
                with self.assertRaises(runtime_manager.BridgeError):
                    runtime_manager._bridge_request("POST", "/exec", {"command": "true"})

    def test_missing_entry_reaches_installer_over_real_http_and_shell(self):
        item = mcp_bundles._item("memory")
        entry = Path(self.temp.name) / "managed entry.js"
        # Simulate only the npm installer, without network access in regression tests.
        # Both file probes and process lifecycle use the actual bridge and shell.
        real_start = runtime_manager.process_start

        def install(command, **kwargs):
            self.assertIn("npm install --prefix", command)
            self.assertIn(item["termux_npm_package"], command)
            return real_start("touch %s" % shlex.quote(str(entry)), **kwargs)

        with self.runtime_bridge(), \
             mock.patch.object(runtime_manager, "termux_home", return_value=self.temp.name), \
             mock.patch.object(mcp_bundles, "_termux_npm_entry", return_value=str(entry)), \
             mock.patch.object(runtime_manager, "process_start", side_effect=install) as start:
            self.assertFalse(entry.exists())
            mcp_bundles._ensure_termux_npm(item)
            self.assertTrue(entry.is_file())
            mcp_bundles._ensure_termux_npm(item)
            start.assert_called_once()

    def test_process_request_exchanges_json_rpc_over_stdio_channel(self):
        process_id = str(uuid.uuid4())
        state = self.server.bridge_state
        paths = state._paths(process_id)
        paths["meta"].write_text(json.dumps({
            "id": process_id,
            "pid": 4242,
            "proc_start": "1",
            "started_at": 1,
            "mode": "stdio",
        }), encoding="utf-8")

        class FakeProc:
            def __init__(self):
                self.stdin = io.BytesIO()
                self.stdout = io.BytesIO(
                    b'{"jsonrpc":"2.0","id":9,"result":{"tools":[]}}\n'
                )
            def poll(self):
                return None

        proc = FakeProc()
        state._procs[process_id] = proc
        state._stdio_locks[process_id] = threading.Lock()
        with mock.patch.object(
            termux_bridge_server.select, "select",
            return_value=([proc.stdout], [], [])
        ):
            out = self.request("/process/request", {
                "id": process_id,
                "message": {"jsonrpc": "2.0", "id": 9, "method": "tools/list", "params": {}},
                "timeout": 3,
            })
        self.assertEqual(out["response"]["id"], 9)
        self.assertEqual(out["response"]["result"]["tools"], [])
        sent = proc.stdin.getvalue().decode("utf-8")
        self.assertIn('"method":"tools/list"', sent)

    def test_process_survives_bridge_restart_and_can_be_stopped(self):
        started = self.request("/process/start", {"command": "echo started; sleep 30"})
        self.assertEqual(started["status"], "running")
        process_id = started["id"]
        deadline = time.time() + 2
        while time.time() < deadline:
            if "started" in self.request("/process/status?id=" + process_id)["logs"]:
                break
            time.sleep(0.05)

        original = self.server
        original.shutdown()
        original.server_close()
        self.thread.join(2)

        replacement = termux_bridge_server.make_server("127.0.0.1", 0, TOKEN, self.temp.name)
        thread = threading.Thread(target=replacement.serve_forever, daemon=True)
        thread.start()
        base = "http://127.0.0.1:%d" % replacement.server_address[1]
        try:
            status = self.request("/process/status?id=" + process_id, base=base)
            self.assertEqual(status["status"], "running")
            stopped = self.request("/process/stop", {"id": process_id}, base=base)
            self.assertEqual(stopped["status"], "stopped")
            after = self.request("/process/status?id=" + process_id, base=base)
            self.assertEqual(after["status"], "stopped")
        finally:
            replacement.shutdown()
            replacement.server_close()
            thread.join(2)
            for proc in original.bridge_state._procs.values():
                try:
                    proc.wait(timeout=2)
                except Exception:
                    pass
            self.server = None


if __name__ == "__main__":
    unittest.main()
