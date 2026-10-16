import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from . import search_router, settings


class SearchRouterTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = mock.patch.object(settings, "HOME", self.temp.name)
        self.home.start()
        self.addCleanup(self.home.stop)

    def test_provider_urls_allow_https_or_loopback_http_only(self):
        self.assertEqual(
            search_router._base("http://127.0.0.1:8888/", "SearXNG"),
            "http://127.0.0.1:8888",
        )
        self.assertEqual(
            search_router._base("https://search.example/path/", "SearXNG"),
            "https://search.example/path",
        )
        for value in (
            "http://search.example",
            "https://user:pass@search.example",
            "https://search.example?q=x",
            "file:///tmp/search",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                search_router._base(value, "SearXNG")

    def test_searxng_search_uses_json_api_and_bounds_results(self):
        payload = {
            "results": [
                {"title": "One", "url": "https://one.example", "content": "first", "engine": "x"},
                {"title": "Bad", "url": "javascript:alert(1)", "content": "skip"},
                {"title": "Two", "url": "https://two.example", "content": "second", "engine": "y"},
            ]
        }
        with mock.patch.object(search_router, "_open_json", return_value=payload) as req:
            rows = search_router._searx("https://search.example", "hello world", 2)
        self.assertEqual([x["title"] for x in rows], ["One", "Two"])
        self.assertIn("/search?", req.call_args.args[0])
        self.assertIn("format=json", req.call_args.args[0])
        self.assertIn("q=hello+world", req.call_args.args[0])

    def test_crawl4ai_extracts_markdown_without_inventing_content(self):
        payload = {"results": [{"url": "https://example.com", "markdown": {"fit_markdown": "# Hello\nWorld"}}]}
        with mock.patch.object(search_router, "_open_json", return_value=payload),              mock.patch.object(search_router, "_crawl_token", return_value="token"):
            result = search_router._crawl("https://crawl.example", "https://example.com")
        self.assertEqual(result["markdown"], "# Hello\nWorld")

    def test_search_router_deep_reads_top_results_only(self):
        search_rows = [
            {"title": "A", "url": "https://a.example", "snippet": "", "engine": "", "score": 1},
            {"title": "B", "url": "https://b.example", "snippet": "", "engine": "", "score": 0.9},
            {"title": "C", "url": "https://c.example", "snippet": "", "engine": "", "score": 0.8},
        ]
        fake_ctx = SimpleNamespace()
        with mock.patch.object(search_router, "_config", return_value={
                "searxng": "https://search.example", "crawl4ai": "https://crawl.example"}),              mock.patch.object(search_router, "_searx", return_value=search_rows),              mock.patch.object(search_router, "_crawl", side_effect=lambda base, url: {
                 "url": url, "markdown": "read " + url
             }) as crawl:
            tool = search_router.tools.REGISTRY["search_router"]
            text, meta = tool.fn(fake_ctx, query="topic", limit=3, deep=True, deep_limit=2)
        data = json.loads(text)
        self.assertEqual(len(data["search"]), 3)
        self.assertEqual(len(data["deep"]), 2)
        self.assertEqual(crawl.call_count, 2)
        self.assertEqual(meta["deep_reads"], 2)

    def test_save_keeps_token_out_of_config_file(self):
        class Handler:
            def _json(self, data, status=200):
                self.response = (data, status)
        handler = Handler()
        with mock.patch.object(search_router, "_set_crawl_token") as save_secret:
            self.assertTrue(search_router.route(handler, "POST", "/api/search-layer/save", {
                "searxng": "https://search.example",
                "crawl4ai": "https://crawl.example",
                "crawl4ai_token": "private-secret-token",
            }))
        self.assertEqual(handler.response[1], 200)
        save_secret.assert_called_once_with("private-secret-token")
        raw = search_router._path().read_text(encoding="utf-8")
        self.assertNotIn("private-secret-token", raw)
        self.assertEqual(json.loads(raw)["crawl4ai"], "https://crawl.example")


if __name__ == "__main__":
    unittest.main()
