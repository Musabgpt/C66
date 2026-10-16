import json
import unittest
from types import SimpleNamespace

from . import browser_router


class BrowserRouterTest(unittest.TestCase):
    def test_api_wins_for_connected_service(self):
        result = browser_router.select(
            {"github_list_repos", "mcp__playwright__browser_navigate"},
            "https://github.com/Musabgpt/NewAl-Cloud",
        )
        self.assertEqual(result["route"], "api")
        self.assertEqual(result["label"], "GitHub API")

    def test_matching_service_mcp_wins_before_browser_backends(self):
        result = browser_router.select(
            {
                "mcp__shopify__products_list",
                "mcp__playwright__browser_navigate",
                "mcp__browser-use__browser_exec",
            },
            "https://store.example",
            service="shopify",
        )
        self.assertEqual(result["route"], "mcp")
        self.assertIn("mcp__shopify__products_list", result["tools"])

    def test_default_browser_priority_matches_architecture(self):
        names = {
            "mcp__open-browser-use__js",
            "mcp__browser-use__browser_exec",
            "mcp__playwright__browser_navigate",
        }
        self.assertEqual(browser_router.select(names)["route"], "playwright")
        self.assertEqual(browser_router.select(names, complex_ui=True)["route"], "browser-use")
        self.assertEqual(browser_router.select(names, needs_session=True)["route"], "open-browser-use")

    def test_android_is_honest_fallback(self):
        result = browser_router.select({"phone"})
        self.assertEqual(result["route"], "android-session")
        self.assertFalse(result["availability"]["playwright"])
        self.assertTrue(result["availability"]["android_session"])

    def test_unavailable_is_not_faked(self):
        result = browser_router.select({"read", "bash"})
        self.assertEqual(result["route"], "unavailable")
        self.assertEqual(result["tools"], [])

    def test_route_requires_session_and_returns_live_state(self):
        class Handler:
            def __init__(self):
                self.service = SimpleNamespace(get=lambda sid: SimpleNamespace(
                    tool_names=["mcp__playwright__browser_navigate"]
                ))
            def _query(self):
                return {"session": "abc123"}
            def _json(self, data, status=200):
                self.response = (data, status)

        handler = Handler()
        self.assertTrue(browser_router.route(handler, "GET", "/api/browser-router"))
        self.assertEqual(handler.response[1], 200)
        self.assertEqual(handler.response[0]["route"], "playwright")

        handler._query = lambda: {}
        browser_router.route(handler, "GET", "/api/browser-router")
        self.assertEqual(handler.response[1], 400)


if __name__ == "__main__":
    unittest.main()
