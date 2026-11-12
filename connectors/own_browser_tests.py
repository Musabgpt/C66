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
        self.answers["open"] = {"accepted": "Accept all"}
        text, meta = own_browser.run("open", url="shop.example")
        self.assertEqual(self.calls[0], ("web", {"op": "open", "url": "shop.example"}))
        self.assertEqual(self.ops(), ["open", "read"])
        self.assertTrue(text.startswith("(The cookie notice was accepted: Accept all.)\nShop — https://shop.example/\n\nWelcome to the shop."))
        for line in ("  [1] field: Search flowers", "  [2] button: Search", "  [3] link: About us",
                     "  [4] field: Email = a@b.c", "(More below: op=scroll.)"):
            self.assertIn(line, text)
        self.assertEqual(text.count("cookie notice"), 1)
        self.assertEqual(meta, {"url": "https://shop.example/", "title": "Shop", "own_browser": True})

    def test_a_page_is_listed_with_what_the_model_needs_to_fill_it(self):
        page = {"text": "§" * 9000, "text_from": 6000, "text_total": 20000, "more_items": 37, "dialog": "Sign in",
                "dialog_text": "Sign in to continue", "items": [
                    {"i": 1, "kind": "field", "name": "Email", "type": "email", "required": True, "dialog": True},
                    {"i": 2, "kind": "select", "name": "Country", "value": "Syria", "options": ["Syria", "Türkiye"], "more_options": 190},
                    {"i": 3, "kind": "checkbox", "name": "I agree", "value": "checked"},
                    {"i": 4, "kind": "button", "name": "Send", "disabled": True},
                    {"i": 5, "kind": "field", "name": "Password", "type": "password", "value": "(hidden)"}]}
        text = own_browser._listing(page)
        for line in ("A dialog is open: Sign in. Its things are listed first", "Sign in to continue",
                     "(Text: characters 6000 to 10500 of 20000.", "  [1] field (email, required): Email",
                     "  [2] select: Country = Syria  {Syria | Türkiye …+190}", "  [3] checkbox: I agree (checked)",
                     "  [4] button (disabled): Send", "  [5] field (password): Password = (hidden)",
                     "(37 more links are not listed"):
            self.assertIn(line, text)
        self.assertEqual(text.count("§"), own_browser.PAGE_TEXT)

    def test_every_step_answers_with_the_page_after_it(self):
        self.answers["read"] = {"page": PAGE}
        self.answers["click"] = {"element": "Search", "moved": True}
        text, _ = own_browser.run("click", target="[2]")
        self.assertEqual(self.calls[0][1], {"op": "click", "index": 2, "text": ""})
        self.assertEqual(self.ops(), ["click", "read"])                            # no extract needed after it
        self.assertIn("Clicked Search. The page changed:\nShop — https://shop.example/", text)
        self.assertIn("[1] field: Search flowers", text)
        self.answers["click"] = {"element": "About us", "moved": False, "covered": "Newsletter"}
        text, _ = own_browser.run("click", target="About us")
        self.assertEqual(self.calls[-2][1], {"op": "click", "index": 0, "text": "About us"})
        self.assertIn("The page did not go elsewhere", text)
        self.assertIn("Something covers that place: Newsletter", text)
        self.calls.clear()
        self.answers["type"] = {"element": "Search flowers", "value": "roses"}
        text, _ = own_browser.run("type", target="1", text="roses")
        self.assertEqual(self.calls, [("web", {"op": "type", "value": "roses", "submit": False, "index": 1, "text": ""})])
        self.assertEqual(text, "Typed into Search flowers. It now holds: roses")   # a field alone: nothing to read
        self.answers["type"] = {"element": "Search flowers", "value": "roses", "moved": True}
        text, _ = own_browser.run("type", target="1", text="roses", submit=True)
        self.assertIn("Sent with Enter. The page changed:\nShop — ", text)

    def test_a_whole_form_is_filled_in_one_step(self):
        self.answers["read"] = {"page": PAGE}
        self.answers["fill"] = {"failed": 0, "fields": [{"target": "Full name", "ok": True, "name": "Full name", "value": "Musab"},
                                                        {"target": "2", "ok": True, "name": "Email", "value": "m@x.co"}]}
        self.answers["click"] = {"element": "Send", "moved": True}
        text, _ = own_browser.run("fill", fields={"Full name": "Musab", "2": "m@x.co"}, target="Send")
        self.assertEqual(self.calls[0][1], {"op": "fill", "fields": [{"target": "Full name", "value": "Musab"}, {"target": "2", "value": "m@x.co"}]})
        self.assertEqual(self.ops(), ["fill", "click", "read"])
        self.assertIn("Filled 2 of 2 fields:\n  Full name = Musab\n  Email = m@x.co\nThen clicked Send. The page changed:", text)
        self.calls.clear()
        self.answers["fill"] = {"failed": 1, "fields": [{"target": "Full name", "ok": True, "name": "Full name", "value": "Musab"},
                                                        {"target": "Phone", "ok": False, "error": "nothing on the page matches"}]}
        text, _ = own_browser.run("fill", fields='{"Full name": "Musab", "Phone": "055"}', target="Send")
        self.assertEqual(self.ops(), ["fill"])                                     # a form with a hole is not sent
        self.assertIn("Phone: NOT filled (nothing on the page matches)", text)
        self.assertIn("Not sent: fill what is missing first", text)
        with self.assertRaises(tools.ToolError):
            own_browser.run("fill", fields={})

    def test_fields_are_understood_however_they_are_written(self):
        want = [{"target": "Name", "value": "Musab"}, {"target": "Agree", "value": "yes"}]
        for written in ({"Name": "Musab", "Agree": True}, [{"target": "Name", "value": "Musab"}, {"name": "Agree", "value": "yes"}],
                        [{"Name": "Musab"}, ["Agree", "yes"]], '{"Name": "Musab", "Agree": "yes"}', "Name=Musab; Agree=yes",
                        "Name: Musab\nAgree: yes"):
            self.assertEqual(own_browser.fields_of(written), want, written)
        self.assertEqual(own_browser.fields_of(None), [])
        self.assertEqual(own_browser.fields_of({"": "x", "Age": 31, "Note": None}), [{"target": "Age", "value": "31"}, {"target": "Note", "value": ""}])

    def test_the_other_steps_and_what_is_refused(self):
        self.answers["read"] = {"page": PAGE}
        self.assertIn("Went back.\nShop — ", own_browser.run("back")[0])
        self.assertIn("Scrolled down one screen.", own_browser.run("scroll")[0])
        self.assertEqual(self.calls[-2][1], {"op": "scroll", "to": "down"})
        own_browser.run("scroll", text="bottom")
        self.assertEqual(self.calls[-2][1], {"op": "scroll", "to": "bottom"})
        self.assertIn("Pressed Enter.", own_browser.run("press", key="Enter")[0])
        own_browser.run("press", key="esc")
        self.assertEqual(self.calls[-2][1], {"op": "key", "key": "Escape"})
        self.answers["find"] = {"count": 2, "places": ["... price 95 SAR ...", "... price 180 SAR ..."]}
        text, _ = own_browser.run("find", text="price")
        self.assertIn("'price' is on the page 2 time(s)", text)
        self.assertIn("price 95 SAR", text)
        self.answers["find"] = {"count": 0, "places": []}
        self.assertIn("is not on this page", own_browser.run("find", text="visa")[0])
        self.answers["wait"] = {"there": True}
        self.assertIn("It is there now.", own_browser.run("wait", text="Results", seconds=99)[0])
        self.assertEqual(self.calls[-2][1], {"op": "wait", "text": "Results", "seconds": 20})
        self.answers["dismiss"] = {"did": 'closed it with "Close"'}
        self.assertIn('Closed it with "Close".\nShop — ', own_browser.run("dismiss")[0])
        for op, args in (("open", {}), ("click", {}), ("type", {"text": "x"}), ("press", {"key": "F5"}), ("find", {}),
                         ("upload", {"path": "a.pdf"}), ("fly", {})):
            with self.assertRaises(tools.ToolError):
                own_browser.run(op, **args)
        self.answers["open"] = phone.PhoneError("this address does not open in the assistant's browser")
        with self.assertRaises(tools.ToolError) as refused:
            own_browser.run("open", url="http://127.0.0.1:8795/")
        self.assertIn("does not open", str(refused.exception))                    # the app's own words reach the model
        self.answers["open"] = {"note": "the page is still loading"}
        self.answers["read"] = {"page": {}}
        self.assertIn("Note: the page is still loading", own_browser.run("open", url="slow.example")[0])

    def test_only_a_file_of_the_conversation_is_given_to_a_page(self):
        import os, tempfile
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as elsewhere:
            cv = os.path.join(root, "cv.pdf")
            secret = os.path.join(elsewhere, "secret.txt")
            for path in (cv, secret):
                with open(path, "w") as f:
                    f.write("x")
            ctx = SimpleNamespace(root=root, session=SimpleNamespace(dirs=[]))
            self.answers["upload"] = {"element": "CV", "value": "cv.pdf"}
            text, _ = own_browser.run("upload", target="CV", path="cv.pdf", ctx=ctx)
            self.assertEqual(self.calls[-1][1], {"op": "upload", "path": cv, "index": 0, "text": "CV"})
            self.assertIn("Gave the file cv.pdf to CV.", text)
            for bad in (secret, "missing.pdf", "../" + os.path.basename(elsewhere) + "/secret.txt"):
                with self.assertRaises(tools.ToolError):
                    own_browser.run("upload", target="CV", path=bad, ctx=ctx)
            self.assertEqual(self.ops().count("upload"), 1)

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

    def test_where_it_is_takes_no_picture(self):
        self.assertEqual(own_browser.where(), {"url": "https://shop.example/", "title": "Shop", "loading": False})
        self.assertEqual(self.ops(), ["state"])
        self.answers["state"] = phone.PhoneError("no answer")
        self.assertEqual(own_browser.where(), {})

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
            text, _ = AR.t_browser(None, "fill", fields={"Name": "Musab", "Email": "m@x.co"})
            self.assertEqual([c[0][2] for c in termux.call_args_list[-2:]],
                             [{"op": "type", "target": "Name", "text": "Musab"}, {"op": "type", "target": "Email", "text": "m@x.co"}])
            self.assertIn("Name = Musab", text)
            with self.assertRaises(tools.ToolError):
                AR.t_browser(None, "dismiss")
        self.assertIn("fields", tools.REGISTRY["browser"].params)


if __name__ == "__main__":
    unittest.main()
