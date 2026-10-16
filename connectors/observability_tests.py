import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from . import observability


class _Response:
    status = 200
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False


class ObservabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "project"
        self.root.mkdir()
        self.obs = Path(self.tmp.name) / "obs"
        self.env = patch.dict(os.environ, {"NEWAL_OBSERVABILITY_HOME": str(self.obs)}, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_record_keeps_metadata_and_drops_payloads_and_secrets(self):
        observability.record(self.root, {
            "type": "tool_end", "session": "session-secret", "name": "github_put_file",
            "ok": True, "seconds": 1.25,
            "text": "Bearer TOP-SECRET", "args": {"token": "TOP-SECRET", "password": "pw"},
            "output": "private project content",
        })
        rows = observability._read(self.root, 10)
        self.assertEqual(len(rows), 1)
        raw = observability._path(self.root).read_text(encoding="utf-8")
        self.assertNotIn("TOP-SECRET", raw)
        self.assertNotIn("private project content", raw)
        self.assertNotIn("session-secret", raw)
        self.assertEqual(rows[0]["name"], "github_put_file")
        self.assertTrue(rows[0]["ok"])

    def test_status_never_returns_credentials(self):
        with patch.dict(os.environ, {
            "LANGFUSE_BASE_URL": "https://example.invalid",
            "LANGFUSE_PUBLIC_KEY": "pk-secret-value",
            "LANGFUSE_SECRET_KEY": "sk-secret-value",
        }, clear=False):
            data = observability.status(self.root)
        shown = json.dumps(data)
        self.assertTrue(data["configured_exporters"]["langfuse"])
        self.assertNotIn("pk-secret-value", shown)
        self.assertNotIn("sk-secret-value", shown)
        self.assertFalse(data["external_export_automatic"])

    def test_langfuse_export_uses_otlp_v4_and_metadata_only(self):
        observability.record(self.root, {
            "type": "tool_end", "session": "abc", "name": "bash", "ok": True,
            "text": "do not export me",
        })
        seen = {}
        def fake(req, timeout=0):
            seen["url"] = req.full_url
            seen["headers"] = dict(req.header_items())
            seen["body"] = req.data.decode("utf-8")
            return _Response()
        with patch.dict(os.environ, {
            "LANGFUSE_BASE_URL": "https://langfuse.example",
            "LANGFUSE_PUBLIC_KEY": "pk-test",
            "LANGFUSE_SECRET_KEY": "sk-test",
        }, clear=False), patch("urllib.request.urlopen", side_effect=fake):
            result = observability.export(self.root, "langfuse", 10)
        self.assertTrue(result["ok"])
        self.assertTrue(seen["url"].endswith("/api/public/otel/v1/traces"))
        self.assertIn("Authorization", seen["headers"])
        self.assertIn("X-langfuse-ingestion-version", seen["headers"])
        self.assertNotIn("do not export me", seen["body"])
        self.assertNotIn("pk-test", seen["body"])
        self.assertNotIn("sk-test", seen["body"])

    def test_plain_http_external_endpoint_is_rejected(self):
        with self.assertRaises(Exception):
            observability._safe_endpoint("http://example.com/v1/traces")
        self.assertEqual(observability._safe_endpoint("http://127.0.0.1:6006/v1/traces"),
                         "http://127.0.0.1:6006/v1/traces")


if __name__ == "__main__":
    unittest.main()
