"""ClawHub cursor paging, privacy and backoff contracts."""
import json
from unittest import TestCase
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from . import clawhub_browse as catalog


class ClawHubBrowseTests(TestCase):
    def setUp(self):
        catalog._pages.clear()
        catalog._cursors.clear()
        catalog._retry_at = 0

    def test_follow_cursor_even_after_empty_page_and_cache_results(self):
        page1 = json.dumps({"items": [], "nextCursor": "encoded & cursor"})
        page2 = json.dumps({"items": [{"ownerHandle": "clock", "slug": "time"}], "nextCursor": None})
        with patch.object(catalog.tools, "_get", side_effect=(page1, page2)) as get:
            body, meta = catalog.browse(None)
            self.assertTrue(meta["has_more"])
            self.assertEqual(catalog.browse(None)[0], body)
            next_page = json.loads(body)["next_page"]
            self.assertNotIn("encoded", body)
            body, meta = catalog.browse(None, next_page)
            self.assertEqual(json.loads(body), {"catalog_ids": ["clock/time"], "next_page": None})
            self.assertFalse(meta["has_more"])
            self.assertEqual(get.call_count, 2)
            self.assertEqual(parse_qs(urlsplit(get.call_args.args[0]).query)["cursor"], ["encoded & cursor"])

    def test_unknown_tokens_never_reach_registry(self):
        with patch.object(catalog.tools, "_get") as get:
            for token in ("private text", "a" * 24, 42):
                with self.assertRaises(catalog.tools.ToolError):
                    catalog.browse(None, token)
            get.assert_not_called()

    def test_malformed_identifiers_are_skipped_without_losing_next_page(self):
        rows = [None, {}, {"ownerHandle": "a", "slug": "one\nother instructions"},
                {"ownerHandle": "name with spaces", "slug": "two"}, {"ownerHandle": "a", "slug": "x" * 101},
                {"ownerHandle": "valid-owner", "slug": "valid_slug"}]
        with patch.object(catalog.tools, "_get", return_value=json.dumps({"items": rows, "nextCursor": "more"})):
            body, meta = catalog.browse(None)
        self.assertEqual(json.loads(body)["catalog_ids"], ["valid-owner/valid_slug"])
        self.assertEqual(meta, {"count": 1, "has_more": True})
        self.assertIsNotNone(json.loads(body)["next_page"])

    def test_rate_limit_applies_to_subsequent_requests(self):
        error = HTTPError("url", 429, "rate limited", {"Retry-After": "30"}, None)
        with patch.object(catalog.tools, "_get", side_effect=error) as get:
            for _ in range(2):
                with self.assertRaises(catalog.tools.ToolError):
                    catalog.browse(None)
            self.assertEqual(get.call_count, 1)
        catalog._retry_at = 0
