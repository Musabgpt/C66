"""Hermetic tests for the automation tools (fake server, no container, no network)."""

import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from . import automation_runtime as AR, tools


class _Fake(BaseHTTPRequestHandler):
    seen = []

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
        self._send(200, {"ok": True, "version": 1}) if self.path == "/health" else self._send(404, {})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(n) or b"{}")
        _Fake.seen.append((self.path, body))
        if self.path == "/phone_agent":
            return self._send(200, {"ok": True, "success": True, "reason": "done", "steps": 4})
        if self.path == "/browser_agent":
            return self._send(200, {"ok": True, "result": "found", "success": True, "steps": 2})
        if self.path == "/browser":
            return self._send(200, {"ok": True, "text": "page text"} if body.get("op") == "extract"
                              else {"ok": True, "url": "https://x"})
        if self.path == "/u2":
            if body.get("op") == "fail":
                return self._send(200, {"ok": False, "error": "no device"})
            return self._send(200, {"ok": True, "output": "ok"})
        self._send(404, {})


class AutomationToolsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _Fake.seen = []
        cls.srv = HTTPServer(("127.0.0.1", 0), _Fake)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        os.environ["MUSAB_AUTOMATION_ORIGIN"] = "http://127.0.0.1:%d" % cls.srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        os.environ.pop("MUSAB_AUTOMATION_ORIGIN", None)
        cls.srv.shutdown()

    def setUp(self):
        self.ctx = type("Ctx", (), {})()

    def test_all_tools_registered(self):
        for name in AR.NAMES:
            self.assertIn(name, tools.REGISTRY, name)

    def test_phone_agent_sends_goal(self):
        text, meta = AR.t_phone_agent(self.ctx, "open whatsapp", 9)
        self.assertEqual(meta["steps"], 4)
        self.assertTrue(any(p == "/phone_agent" and b["goal"] == "open whatsapp" and b["max_steps"] == 9
                            for p, b in _Fake.seen))

    def test_browser_ops(self):
        AR.t_browser(self.ctx, "open", url="example.com")
        _, meta = AR.t_browser(self.ctx, "extract")
        self.assertEqual(meta["text"], "page text")
        ops = [b["op"] for p, b in _Fake.seen if p == "/browser"]
        self.assertIn("open", ops)
        self.assertIn("extract", ops)

    def test_browser_agent(self):
        text, meta = AR.t_browser_agent(self.ctx, "find the price", 3)
        self.assertEqual(meta["result"], "found")

    def test_adb_and_power(self):
        AR.t_adb_connect(self.ctx, "connect", address="1.2.3.4:5555")
        AR.t_phone_power(self.ctx, "click_text", text="Post")
        u2 = [b for p, b in _Fake.seen if p == "/u2"]
        self.assertTrue(any(b["op"] == "adb_connect" and b["address"] == "1.2.3.4:5555" for b in u2))
        self.assertTrue(any(b["op"] == "click_text" and b["text"] == "Post" for b in u2))

    def test_error_becomes_toolerror(self):
        with self.assertRaises(tools.ToolError):
            AR.t_phone_power(self.ctx, "fail")


if __name__ == "__main__":
    unittest.main()
