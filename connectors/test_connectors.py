"""Offline contract and regression tests; not a substitute for provider/device authorization tests."""
import base64
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("musab_broker", ROOT / "broker.py")
broker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(broker)


class OAuthTest(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {p + suffix: "test-only" for p in ("GITHUB", "GITLAB", "GOOGLE", "NOTION", "FIGMA", "NOTION_MCP", "NETLIFY_MCP", "MIRO_MCP", "HF_MCP", "GITLAB_MCP") for suffix in ("_CLIENT_ID", "_CLIENT_SECRET")})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.b = broker.Broker("https://connectors.example.invalid")
        self.verifier = "A" * 64

    def start(self, provider="github"):
        return self.b.start(provider, broker.challenge(self.verifier))

    def test_tls_and_proof_required(self):
        for url in ("http://localhost", "https://example.invalid/path", "https://example.invalid?x=1"):
            with self.assertRaises(ValueError): broker.Broker(url)
        for proof in ("", "abc", "a" * 44):
            with self.assertRaises(broker.OAuthError): self.b.start("github", proof)

    def test_all_provider_requests_have_state_and_exact_callback(self):
        for provider in broker.PROVIDERS:
            with self.subTest(provider=provider):
                started = self.start(provider)
                params = parse_qs(urlsplit(started["authorization_url"]).query)
                self.assertEqual(params["state"], [started["id"]])
                self.assertEqual(params["redirect_uri"], [self.b.url + "/oauth/callback/" + provider])
                self.assertNotIn("client_secret", params)
                if provider != "notion": self.assertEqual(params["code_challenge_method"], ["S256"])

    def test_state_provider_match_and_denial(self):
        sid = self.start()["id"]
        with self.assertRaises(broker.OAuthError): self.b.callback("gitlab", {"state": sid, "code": "x"})
        with self.assertRaises(broker.OAuthError): self.b.callback("github", {"state": sid, "error": "access_denied"})
        result = self.b.poll(sid, self.verifier)
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("tokens", result)

    def test_tokens_only_after_verified_one_use_handoff(self):
        sid = self.start()["id"]
        with self.assertRaises(broker.OAuthError): self.b.poll(sid, "B" * 64)
        self.assertEqual(self.b.poll(sid, self.verifier), {"status": "pending"})
        with patch.object(broker, "exchange", return_value={"access_token": "private-test-token"}) as exchange:
            self.b.callback("github", {"state": sid, "code": "test-code"})
            self.assertIn("code_verifier", exchange.call_args.args[1])
        with self.assertRaises(broker.OAuthError): self.b.poll(sid, "B" * 64)
        self.assertEqual(self.b.poll(sid, self.verifier)["tokens"]["access_token"], "private-test-token")
        with self.assertRaises(broker.OAuthError): self.b.poll(sid, self.verifier)
        with self.assertRaises(broker.OAuthError): self.b.callback("github", {"state": sid, "code": "test-code"})

    def test_duplicate_callback_never_exchanges_twice(self):
        sid = self.start()["id"]
        entered, release = threading.Event(), threading.Event()
        def exchange(*args):
            entered.set(); release.wait(2); return {"access_token": "test"}
        with patch.object(broker, "exchange", side_effect=exchange) as mock:
            thread = threading.Thread(target=self.b.callback, args=("github", {"state": sid, "code": "x"}))
            thread.start(); self.assertTrue(entered.wait(1))
            with self.assertRaises(broker.OAuthError): self.b.callback("github", {"state": sid, "code": "x"})
            release.set(); thread.join(2)
            self.assertEqual(mock.call_count, 1)

    def test_expiry_and_unconfigured_provider(self):
        sid = self.start()["id"]
        self.b.pending[sid]["expires"] = time.time() - 1
        with self.assertRaises(broker.OAuthError): self.b.poll(sid, self.verifier)
        with patch.dict(os.environ, {"GOOGLE_CLIENT_SECRET": ""}):
            with self.assertRaises(broker.OAuthError): self.start("gmail")


class RuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from newal_code import connectors, tools, permissions
        cls.c, cls.t, cls.p = connectors, tools, permissions

    def test_registry_preserves_original_tools_and_permissions(self):
        for name in ("read", "write", "bash", "skill", "memory_recall", "self_evolve"):
            self.assertIn(name, self.t.REGISTRY)
        for name, (_, method, _) in self.c.OPERATIONS.items():
            kind = self.t.REGISTRY[name].kind
            if method != "GET" and name != "notion_search":
                self.assertNotEqual(kind, "read", name)
                decision = self.p.decide("read-only", name, kind, {"command": "touch output.txt"}, "/tmp", {})
                self.assertEqual(decision.action, self.p.DENY, name)
        decision = self.p.decide("full-auto", "termux_exec", "exec", {"command": "rm -rf /"}, "/tmp", {})
        self.assertEqual(decision.action, self.p.DENY)

    def test_only_connected_tools_are_exposed(self):
        self.c._cache = (0, [])
        with patch.object(self.c.phone, "available", return_value=True), patch.object(self.c, "status", return_value={"connectors": [{"id": "gmail", "status": "connected"}, {"id": "github", "status": "error"}]}):
            names = self.c.names()
            self.assertIn("gmail_send", names)
            self.assertNotIn("github_push", names)

    def test_official_mcp_tools_preserve_schema_and_use_native_vault(self):
        self.c._cache = (0, [])
        self.c._mcp_cache.clear()
        schema = {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"], "additionalProperties": False}
        account = {"id": "notionmcp", "status": "connected", "generation": "test-grant", "tested_at": 1}
        catalog = {"data": {"tools": [{"name": "notion-search", "description": "Search pages", "inputSchema": schema}]}}
        with patch.object(self.c.phone, "available", return_value=True), patch.object(self.c, "status", return_value={"connectors": [account]}), patch.object(self.c, "native", return_value=catalog) as native:
            names = self.c.names()
            self.assertEqual(len(names), 1)
            tool = self.t.REGISTRY[names[0]]
            self.assertEqual(tool.schema()["function"]["parameters"], schema)
            self.assertEqual(tool.kind, "connector_write")
            native.return_value = {"data": {"content": [{"type": "text", "text": "found"}]}}
            result, meta = tool.fn(None, query="ملاحظات")
            native.assert_called_with("mcp_call", provider="notionmcp", name="notion-search", arguments={"query": "ملاحظات"})
            self.assertIn("found", result)
            native.return_value = {"data": {"isError": True, "content": [{"type": "text", "text": "failed"}]}}
            with self.assertRaises(self.t.ToolError): tool.fn(None, query="missing")
            self.c._cache = (0, [])
            with patch.object(self.c, "status", return_value={"connectors": []}):
                self.assertEqual(self.c.names(), [])

    def test_unavailable_mcp_service_does_not_hide_github(self):
        self.c._cache = (0, [])
        self.c._mcp_cache.clear()
        accounts = [{"id": "github", "status": "connected"}, {"id": "netlify", "status": "connected"}]
        with patch.object(self.c.phone, "available", return_value=True), patch.object(self.c, "status", return_value={"connectors": accounts}), patch.object(self.c, "native", side_effect=self.t.ToolError("unavailable")):
            self.assertIn("github_push", self.c.names())

    def test_mime_email_preserves_arabic_and_rejects_header_injection(self):
        with patch.object(self.c, "request", return_value={"id": "sent"}) as req:
            self.t.REGISTRY["gmail_send"].fn(None, to="user@example.invalid", subject="اختبار", text="مرحبا مصعب")
            raw = req.call_args.args[3]["raw"]
            from email.parser import BytesParser
            from email.policy import default
            message = BytesParser(policy=default).parsebytes(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
            self.assertEqual(str(message["Subject"]), "اختبار")
            self.assertEqual(message.get_content().strip(), "مرحبا مصعب")
            with self.assertRaises(ValueError): self.t.REGISTRY["gmail_send"].fn(None, to="user@example.invalid\nBcc: victim@example.invalid", subject="test", text="hello")

    def test_github_publish_is_one_commit_and_non_force(self):
        responses = [{"object": {"sha": "parent"}}, {"tree": {"sha": "old-tree"}}, {"sha": "new-tree"}, {"sha": "new-commit"}, {"object": {"sha": "new-commit"}}]
        with patch.object(self.c, "request", side_effect=responses) as req:
            text, meta = self.t.REGISTRY["github_push"].fn(None, owner="o", repo="r", branch="main", message="Add", files={"a.py": "print(1)", "b.py": "print(2)"})
            self.assertEqual(req.call_count, 5)
            self.assertFalse(req.call_args.args[3]["force"])
            self.assertEqual(meta["commit"], "new-commit")

    def test_workspace_needs_no_git_or_connector(self):
        class Handler:
            def _json(self, data, status=200): self.result, self.status = data, status
        handler = Handler()
        from unittest.mock import Mock
        handler.service = Mock()
        handler.service.create.return_value.id = "session"
        handler.service.create.return_value.meta.return_value = {"root": "independent"}
        with tempfile.TemporaryDirectory() as home, patch.object(self.c.settings, "HOME", home), patch.object(self.c, "native", side_effect=AssertionError("must not require a connector")):
            self.assertTrue(self.c.route(handler, "POST", "/api/workspaces", {}))
            self.assertEqual(handler.status, 200)
            self.assertTrue(Path(handler.result["root"]).is_dir())
            self.assertFalse(handler.result["remote_worker"])

    def test_agent_can_invoke_tools_connected_after_session_started(self):
        from newal_code.agent import Agent
        from newal_code.session import Session
        with tempfile.TemporaryDirectory() as root, patch.object(self.c, "names", return_value=[]):
            s = Session(root, mode="full-auto")
            a = Agent(s)
            a.schemas()
            self.assertNotIn("gmail_read", s.tool_names)
            with patch.object(self.c, "names", return_value=["gmail_read"]):
                schemas = a.schemas()
                self.assertIn("gmail_read", [d["function"]["name"] for d in schemas])
                self.assertIn("gmail_read", s.tool_names)
            a.schemas()
            self.assertNotIn("gmail_read", s.tool_names)


    def test_activepieces_callback_forwards_only_short_lived_oauth_code_to_android(self):
        class Handler:
            def _query(self):
                return {"code": "one-time-code", "state": "bound-state", "iss": "https://automation.example"}
            def _text(self, value, ctype):
                self.value, self.ctype = value, ctype
            def _json(self, value, status=200):
                self.json, self.status = value, status
        handler = Handler()
        with patch.object(self.c, "native", return_value={"ok": True, "status": "connected", "tools": 12}) as native:
            self.assertTrue(self.c.route(handler, "GET", "/api/mcp-oauth/callback"))
        native.assert_called_once_with("oauth_complete", provider="activepieces", code="one-time-code",
                                      state="bound-state", iss="https://automation.example", error="")
        self.assertIn("Activepieces connected", handler.value)
        self.assertNotIn("one-time-code", handler.value)
        self.assertIn("text/html", handler.ctype)

        failed = Handler()
        with patch.object(self.c, "native", side_effect=self.t.ToolError("declined")):
            self.assertTrue(self.c.route(failed, "GET", "/api/mcp-oauth/callback"))
        self.assertIn("authorization failed", failed.value)
        self.assertNotIn("declined", failed.value)


class ReadinessTest(unittest.TestCase):
    def test_health_requires_key_and_never_runs_expensive_state_probes(self):
        import urllib.request
        import urllib.error
        from newal_code import server, settings, hardware
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as home, patch.object(settings, "HOME", home), patch.object(server, "Service", return_value=Mock()):
            httpd, _ = server.serve(0)
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            try:
                with patch.object(hardware, "summary", side_effect=AssertionError("must not probe hardware")), patch.object(server, "_shells", side_effect=AssertionError("must not discover shells")):
                    request = urllib.request.Request(httpd.base_url + "api/health", headers={"X-NewAl-Key": httpd.key})
                    with urllib.request.urlopen(request, timeout=1) as response:
                        self.assertEqual(json.load(response), {"ok": True})
                    with self.assertRaises(urllib.error.HTTPError) as denied:
                        urllib.request.urlopen(httpd.base_url + "api/health", timeout=1)
                    self.assertEqual(denied.exception.code, 401)
            finally:
                httpd.shutdown()
                httpd.server_close()


if __name__ == "__main__": unittest.main()
