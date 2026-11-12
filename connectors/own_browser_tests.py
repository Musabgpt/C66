"""Hermetic tests for the browser tool on the app's own browser (no Android: the phone is a stand-in)."""

import base64
import unittest
from unittest import mock

from . import automation_runtime as AR, own_browser, phone, tools

PAGE = {"url": "https://shop.example/", "title": "Shop", "text": "Welcome to the shop.\nSearch our flowers.",
        "y": 0, "view": 700, "height": 2400,
        "items": [{"i": 1, "kind": "field", "name": "Search flowers"},
                  {"i": 2, "kind": "button", "name": "Search"},
                  {"i": 3, "kind": "link", "name": "About us", "href": "https://shop.example/about"},
                  {"i": 4, "kind": "field", "name": "Email", "value": "a@b.c"}]}


class OwnBrowserTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.answers = {}
        own_browser._STATE["supported"] = None
        self.addCleanup(own_browser._STATE.__setitem__, "supported", None)

        def call(action, timeout=90, **args):
            self.calls.append((action, args))
            answer = self.answers.get(args.get("op"))
            if isinstance(answer, Exception):
                raise answer
            base = {"ok": True, "url": PAGE["url"], "title": PAGE["title"], "loading": False}
            return dict(base, **(answer or {}))
        for name, value in (("call", call), ("available", lambda: True)):
            patch = mock.patch.object(phone, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def ops(self):
        return [args.get("op") for _, args in self.calls]

    def test_an_app_with_its_own_browser_is_used_and_an_older_one_is_not(self):
        self.assertTrue(own_browser.available())
        self.assertTrue(own_browser.available())
        self.assertEqual(self.ops(), ["state"])                                   # asked once
        own_browser._STATE["supported"] = None
        self.answers["state"] = phone.PhoneError('unknown action "web"')
        self.assertFalse(own_browser.available())
        self.assertFalse(own_browser._STATE["supported"])                          # an older app: remembered
        own_browser._STATE["supported"] = None
        self.answers["state"] = phone.PhoneError("the phone app does not answer")
        self.assertFalse(own_browser.available())
        self.assertIsNone(own_browser._STATE["supported"])                         # asked again next time
        with mock.patch.object(phone, "available", lambda: False):
            self.assertFalse(own_browser.available())                              # not on a phone at all

    def test_open_reads_the_page_and_numbers_what_can_be_used(self):
        self.answers["read"] = {"page": PAGE}
        text, meta = own_browser.run("open", url="shop.example")
        self.assertEqual(self.calls[0], ("web", {"op": "open", "url": "shop.example"}))
        self.assertEqual(self.ops(), ["open", "read"])
        self.assertTrue(text.startswith("Shop — https://shop.example/\n\nWelcome to the shop."))
        for line in ("  [1] field: Search flowers", "  [2] button: Search", "  [3] link: About us",
                     "  [4] field: Email = a@b.c", "(More below: op=scroll, then extract again.)"):
            self.assertIn(line, text)
        self.assertEqual(meta, {"url": "https://shop.example/", "title": "Shop", "own_browser": True})

    def test_a_target_is_a_number_or_a_name(self):
        self.answers["click"] = {"element": "Search", "moved": True}
        text, _ = own_browser.run("click", target="[2]")
        self.assertEqual(self.calls[-1][1], {"op": "click", "index": 2, "text": ""})
        self.assertIn("Clicked Search. The page changed:", text)
        own_browser.run("click", target="About us")
        self.assertEqual(self.calls[-1][1], {"op": "click", "index": 0, "text": "About us"})
        self.answers["type"] = {"value": "roses"}
        text, _ = own_browser.run("type", target="1", text="roses", submit=True)
        self.assertEqual(self.calls[-1][1], {"op": "type", "value": "roses", "submit": True, "index": 1, "text": ""})
        self.assertIn("The field now holds: roses", text)
        self.assertIn("extract to read what came back", text)

    def test_the_other_steps_and_what_is_refused(self):
        self.assertIn("Went back.", own_browser.run("back")[0])
        self.assertIn("Scrolled down", own_browser.run("scroll")[0])
        self.assertIn("Pressed Enter.", own_browser.run("press", key="Enter")[0])
        self.assertEqual(self.ops(), ["back", "scroll", "key"])
        for op, args in (("open", {}), ("click", {}), ("type", {"text": "x"}), ("press", {"key": "Tab"}), ("fly", {})):
            with self.assertRaises(tools.ToolError):
                own_browser.run(op, **args)
        self.answers["open"] = phone.PhoneError("this address does not open in the assistant's browser")
        with self.assertRaises(tools.ToolError) as refused:
            own_browser.run("open", url="http://127.0.0.1:8795/")
        self.assertIn("does not open", str(refused.exception))                    # the app's own words reach the model
        self.answers["open"] = {"note": "the page is still loading after 25 s"}
        self.answers["read"] = {"page": {}}
        self.assertIn("Note: the page is still loading", own_browser.run("open", url="slow.example")[0])

    def test_the_live_picture(self):
        self.answers["shot"] = {"jpeg": base64.b64encode(b"\xff\xd8jpeg").decode(), "loading": True}
        picture, where = own_browser.shot()
        self.assertEqual(picture, b"\xff\xd8jpeg")
        self.assertEqual(where, {"url": "https://shop.example/", "title": "Shop", "loading": True})
        self.assertEqual(self.calls[-1][1], {"op": "shot", "width": 360})
        self.answers["shot"] = {}                                                  # no page open: no picture
        self.assertEqual(own_browser.shot()[0], b"")
        self.answers["shot"] = phone.PhoneError("no answer")
        self.assertEqual(own_browser.shot(), (b"", {}))

    def test_the_browser_tool_uses_it_and_needs_no_termux(self):
        self.answers["read"] = {"page": PAGE}
        with mock.patch.object(AR, "_ensure_and_call", side_effect=AssertionError("Termux was asked")):
            text, meta = AR.t_browser(None, "open", url="https://shop.example/")
        self.assertIn("[2] button: Search", text)
        self.assertTrue(meta["own_browser"])
        own_browser._STATE["supported"] = False                                    # an older app: Chromium in Termux
        with mock.patch.object(AR, "_ensure_and_call", return_value={"ok": True, "url": "u", "title": "T"}) as termux:
            AR.t_browser(None, "scroll")
        self.assertEqual(termux.call_args[0][1:3], ("/browser", {"op": "press", "url": "", "target": "", "text": "",
                                                                 "submit": False, "key": "PageDown"}))


if __name__ == "__main__":
    unittest.main()
