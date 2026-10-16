import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from . import mcp_config, mcp_registry, settings


class McpRegistryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = self.temp.name
        self.home = mock.patch.object(settings, "HOME", os.path.join(self.root, "private"))
        self.home.start()
        self.addCleanup(self.home.stop)

    def test_search_uses_official_registry_and_sanitizes_output(self):
        payload = {
            "servers": [{
                "server": {
                    "name": "io.example/demo",
                    "title": "Demo",
                    "description": "safe",
                    "version": "1.2.3",
                    "repository": {"url": "https://github.com/example/demo"},
                    "remotes": [{"type": "streamable-http", "url": "https://demo.example/mcp"}],
                    "packages": [{"registryType": "npm", "identifier": "demo"}],
                }
            }],
            "metadata": {"nextCursor": "opaque"},
        }
        with mock.patch.object(mcp_registry, "_read_json", return_value=payload) as request:
            result = mcp_registry.search("demo", 5)
        self.assertEqual(result["servers"][0]["name"], "io.example/demo")
        self.assertEqual(result["servers"][0]["remote_count"], 1)
        self.assertEqual(result["next_cursor"], "opaque")
        self.assertIn("/v0.1/servers?", request.call_args.args[0])
        self.assertIn("version=latest", request.call_args.args[0])

    def test_only_literal_headerless_https_streamable_http_is_auto_installable(self):
        base = {
            "name": "io.example/demo",
            "title": "Demo",
            "version": "1",
            "packages": [{"registryType": "npm", "identifier": "demo", "transport": {"type": "stdio"}}],
        }
        good = dict(base, remotes=[{"type": "streamable-http", "url": "https://demo.example/mcp"}])
        with mock.patch.object(mcp_registry, "detail", return_value=good):
            info = mcp_registry.inspect("io.example/demo")
        self.assertTrue(info["installable"])
        self.assertTrue(any("never executed" in x for x in info["reasons"]))

        for remote in (
            {"type": "sse", "url": "https://demo.example/sse"},
            {"type": "streamable-http", "url": "http://demo.example/mcp"},
            {"type": "streamable-http", "url": "https://{tenant}.example/mcp"},
            {"type": "streamable-http", "url": "https://demo.example/mcp",
             "headers": [{"name": "X-Key", "isRequired": True, "isSecret": True}]},
        ):
            with self.subTest(remote=remote), mock.patch.object(
                    mcp_registry, "detail", return_value=dict(base, remotes=[remote])):
                self.assertFalse(mcp_registry.inspect("io.example/demo")["installable"])

    def test_install_handshakes_before_persisting(self):
        info = {
            "installable": True,
            "remote": "https://demo.example/mcp",
            "version": "1",
            "reasons": [],
        }
        fake = mock.Mock()
        fake.tools = [{"name": "read"}]
        with mock.patch.object(mcp_registry, "inspect", return_value=info),              mock.patch.object(mcp_config, "HttpServer", return_value=fake):
            result = mcp_registry.install(self.root, "io.example/demo", "demo")
        fake.start.assert_called_once()
        fake.stop.assert_called_once()
        self.assertEqual(result["tools"], 1)
        saved = mcp_config.read(self.root)["demo"]
        self.assertEqual(saved["registry"], "io.example/demo")
        self.assertEqual(saved["spec"]["url"], "https://demo.example/mcp")

    def test_failed_handshake_does_not_persist(self):
        info = {"installable": True, "remote": "https://demo.example/mcp", "version": "1", "reasons": []}
        fake = mock.Mock()
        fake.start.side_effect = RuntimeError("handshake failed")
        with mock.patch.object(mcp_registry, "inspect", return_value=info),              mock.patch.object(mcp_config, "HttpServer", return_value=fake):
            with self.assertRaises(RuntimeError):
                mcp_registry.install(self.root, "io.example/demo", "demo")
        self.assertEqual(mcp_config.read(self.root), {})

    def test_route_requires_project_for_install_but_not_search(self):
        handler = SimpleNamespace(service=SimpleNamespace(get=lambda sid: SimpleNamespace(root=self.root)))
        handler._json = lambda data, status=200: setattr(self, "response", (data, status))
        handler._query = lambda: {"q": "demo"}
        with mock.patch.object(mcp_registry, "search", return_value={"servers": [], "count": 0, "next_cursor": None}):
            self.assertTrue(mcp_registry.route(handler, "GET", "/api/mcp-registry"))
            self.assertEqual(self.response[1], 200)
        mcp_registry.route(handler, "POST", "/api/mcp-registry/install", {"name": "io.example/demo"})
        self.assertEqual(self.response[1], 400)


if __name__ == "__main__":
    unittest.main()
