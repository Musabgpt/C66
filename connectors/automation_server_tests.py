"""Hermetic tests for the automation server (no Android, no browser, no heavy libraries)."""

import json
import sys
import threading
import types
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer

from . import automation_server as A


class FakeDevice:
    def __init__(self, log, broken=False):
        self.log = log
        self.broken = broken
        self.serial = "127.0.0.1:5555"

    @property
    def info(self):
        self.log.append("info")
        if self.broken:
            raise RuntimeError("uiautomator2 server did not start")
        return {"sdkInt": 28, "displayWidth": 720, "displayHeight": 1280}

    def stop_uiautomator(self):
        self.log.append("stop")

    def dump_hierarchy(self):
        self.log.append("dump")
        return "<hierarchy/>"

    def press(self, key):
        self.log.append("press " + key)

    def app_current(self):
        raise RuntimeError("device went away")

    def __call__(self, **selector):
        log = self.log

        class Found:
            exists = selector.get("text") == "Post" or selector.get("textContains") == "Pos"

            def click(self):
                log.append("click %s" % selector)
        return Found()


class Uiautomator2Tests(unittest.TestCase):
    """uiautomator2 takes Android's accessibility away from the app while attached: every call
    detaches again and makes sure the app's bridge is back."""

    def setUp(self):
        self.log = []
        self.saved = (sys.modules.get("uiautomator2"), A.phone_call, A.PHONE_KEY, A.U2.SETTLE)
        self.broken_device = False
        fake = types.ModuleType("uiautomator2")
        fake.connect = lambda serial=None: (self.log.append("attach"), FakeDevice(self.log, self.broken_device))[1]
        sys.modules["uiautomator2"] = fake
        A.PHONE_KEY = "key"
        A.U2.SETTLE = 0
        # The app's state at each check, in order (the last one repeats). A call checks once before it
        # attaches, then once after each detach.
        self.bridge = []
        self.asked = []
        self.now = None

        def phone_call(action, **params):
            self.asked.append(action)
            if action == "a11y_state":      # a check starts here; the screen read after it sees the same state
                self.now = self.bridge.pop(0) if len(self.bridge) > 1 else self.bridge[0]
            answer = self.now
            if answer == "no-app":
                raise A.PhoneError("MusabAI's phone server is not reachable (refused)")
            if action == "a11y_state":
                if answer == "old-app":
                    raise A.PhoneError("unknown action a11y_state")
                return {"ok": True, "enabled": answer != "off", "connected": answer not in ("unbound", "off"),
                        "alive": answer in ("ok", "looks-alive")}
            if answer in ("ok", "old-app"):
                return {"ok": True, "state": {}}
            raise A.PhoneError("no window on the screen right now")
        A.phone_call = phone_call
        self.u2 = A.U2()

    def tearDown(self):
        module, A.phone_call, A.PHONE_KEY, A.U2.SETTLE = self.saved
        if module is None:
            sys.modules.pop("uiautomator2", None)
        else:
            sys.modules["uiautomator2"] = module

    def test_a_call_attaches_acts_detaches_and_finds_the_bridge_back(self):
        self.bridge = ["ok"]
        out = self.u2.run("dump", {})
        self.assertEqual(out, {"ok": True, "xml": "<hierarchy/>", "accessibility": "restored"})
        self.assertEqual(self.log, ["attach", "info", "dump", "stop"])
        self.assertEqual(self.asked, ["a11y_state", "screen_tree"] * 2)          # before the attach, after the detach

    def test_a_bridge_that_comes_back_dead_is_revived_by_another_detach_cycle(self):
        self.bridge = ["ok", "dead", "ok"]
        out = self.u2.run("press", {"key": "back"})
        self.assertEqual((out["accessibility"], out["repair_cycles"]), ("restored", 1))
        self.assertNotIn("note", out)
        self.assertEqual(self.log, ["attach", "info", "press back", "stop", "attach", "info", "stop"])

    def test_a_service_that_looks_connected_but_reads_nothing_is_not_back(self):
        self.bridge = ["ok", "looks-alive", "ok"]
        out = self.u2.run("dump", {})
        self.assertEqual((out["accessibility"], out["repair_cycles"]), ("restored", 1))
        self.assertEqual(self.asked[2:4], ["a11y_state", "screen_tree"])        # the screen is really read

    def test_when_it_never_comes_back_the_result_says_what_the_user_must_do(self):
        self.bridge = ["ok", "unbound"]
        out = self.u2.run("dump", {})
        self.assertEqual(out["accessibility"], "suspended")
        self.assertEqual(out["repair_cycles"], A.U2.REPAIRS)
        self.assertIn("switch it off and on", out["note"])
        self.assertEqual(self.log.count("attach"), 1 + A.U2.REPAIRS)
        self.assertEqual(self.log.count("stop"), 1 + A.U2.REPAIRS)             # never left attached

    def test_a_service_the_user_never_switched_on_is_left_alone(self):
        self.bridge = ["off"]
        out = self.u2.run("dump", {})
        self.assertEqual(out, {"ok": True, "xml": "<hierarchy/>"})               # nothing to restore, nothing to say
        self.assertEqual(self.log, ["attach", "info", "dump", "stop"])
        self.assertEqual(self.asked, ["a11y_state"])                             # it only looked, once

    def test_a_bridge_that_was_already_down_gets_one_try_and_an_honest_word(self):
        self.bridge = ["looks-alive"]                                            # a screen that is off reads the same
        out = self.u2.run("dump", {})
        self.assertEqual((out["accessibility"], out["repair_cycles"]), ("unavailable", 1))
        self.assertIn("screen may be off", out["note"])
        self.assertNotIn("did not come back", out["note"])                       # this call did not break it
        self.assertEqual(self.log.count("attach"), 2)
        self.bridge = ["dead", "ok"]                                             # left dead earlier: this call revives it
        out = self.u2.run("dump", {})
        self.assertEqual(out["accessibility"], "restored")
        self.assertNotIn("note", out)

    def test_a_failed_action_still_detaches_and_restores(self):
        self.bridge = ["ok"]
        with self.assertRaises(RuntimeError) as failed:
            self.u2.run("app_current", {})
        self.assertIn("device went away", str(failed.exception))
        self.assertNotIn("switch it off", str(failed.exception))
        self.assertEqual(self.log[-1], "stop")
        self.bridge = ["ok", "unbound"]
        with self.assertRaises(RuntimeError) as failed:
            self.u2.run("app_current", {})
        self.assertIn("switch it off and on", str(failed.exception))

    def test_an_attach_that_fails_does_not_stay_attached(self):
        self.bridge = ["ok"]
        self.broken_device = True
        with self.assertRaises(RuntimeError) as failed:
            self.u2.run("dump", {})
        self.assertIn("did not start", str(failed.exception))
        self.assertEqual(self.log, ["attach", "info", "stop"])

    def test_an_app_from_before_a11y_state_is_asked_for_the_screen(self):
        self.bridge = ["old-app"]
        out = self.u2.run("dump", {})
        self.assertEqual(out["accessibility"], "restored")
        self.assertEqual(self.asked, ["a11y_state", "screen_tree"] * 2)

    def test_without_a_phone_bridge_nothing_is_checked(self):
        A.PHONE_KEY = ""
        out = self.u2.run("dump", {})
        self.assertNotIn("accessibility", out)
        self.assertEqual(self.asked, [])
        A.PHONE_KEY = "key"
        self.bridge = ["no-app"]                                                 # the app is not running: ADB still works
        out = self.u2.run("dump", {})
        self.assertEqual(out, {"ok": True, "xml": "<hierarchy/>"})
        self.assertEqual(self.log.count("attach"), 2)

    def test_click_by_text_and_a_text_that_is_not_there(self):
        self.bridge = ["ok"]
        self.assertTrue(self.u2.run("click_text", {"text": "Post"})["ok"])
        self.assertTrue(self.u2.run("click_text", {"text": "Pos"})["ok"])         # part of the label
        out = self.u2.run("click_text", {"text": "Publish"})
        self.assertEqual((out["ok"], out["accessibility"]), (False, "restored"))
        self.assertIn("Publish", out["error"])

    def test_not_under_a_running_phone_agent(self):
        self.bridge = ["ok"]
        saved = A.U2BACKEND
        A.U2BACKEND = self.u2
        try:
            self.assertTrue(A._PHONE_TASK.acquire(blocking=False))
            try:
                out = A.dispatch("/u2", {"op": "dump"})
                self.assertFalse(out["ok"])
                self.assertIn("phone task", out["error"])
                self.assertEqual(self.log, [])
            finally:
                A._PHONE_TASK.release()
            self.assertEqual(A.dispatch("/u2", {"op": "dump"})["accessibility"], "restored")
        finally:
            A.U2BACKEND = saved


class PhoneCallTests(unittest.TestCase):
    """The app answers a failed action with HTTP 400 and says why in the body."""

    @classmethod
    def setUpClass(cls):
        class Phone(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
                if self.headers.get("X-NewAl-Key") != "k":
                    code, out = 401, {"ok": False, "error": "wrong key"}
                elif body.get("action") == "screen_tree":
                    code, out = 400, {"ok": False, "error": "ACCESSIBILITY_OFF: the service is off"}
                else:
                    code, out = 200, {"ok": True, "text": "fine"}
                data = json.dumps(out).encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        cls.srv = HTTPServer(("127.0.0.1", 0), Phone)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        self.saved = (A.PHONE_ORIGIN, A.PHONE_KEY)
        A.PHONE_ORIGIN = "http://127.0.0.1:%d" % self.srv.server_address[1]
        A.PHONE_KEY = "k"

    def tearDown(self):
        A.PHONE_ORIGIN, A.PHONE_KEY = self.saved

    def test_the_phone_s_own_words_reach_the_caller(self):
        self.assertEqual(A.phone_call("device")["text"], "fine")
        with self.assertRaises(A.PhoneError) as failed:
            A.phone_call("screen_tree")
        self.assertEqual(str(failed.exception), "ACCESSIBILITY_OFF: the service is off")
        A.PHONE_KEY = "wrong"
        with self.assertRaises(A.PhoneError) as failed:
            A.phone_call("device")
        self.assertEqual(str(failed.exception), "wrong key")

    def test_an_unreachable_phone_is_said_plainly(self):
        A.PHONE_ORIGIN = "http://127.0.0.1:1"
        with self.assertRaises(A.PhoneError) as failed:
            A.phone_call("device")
        self.assertIn("not reachable", str(failed.exception))


class ServerTests(unittest.TestCase):
    """The HTTP surface: a token for everything but /health, a body cap, clear errors."""

    @classmethod
    def setUpClass(cls):
        cls.saved = A.TOKEN
        A.TOKEN = "secret"
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), A.Handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = "http://127.0.0.1:%d" % cls.srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        A.TOKEN = cls.saved

    def post(self, path, body=None, token="secret", headers=None):
        h = {"Content-Type": "application/json"}
        if token is not None:
            h["Authorization"] = "Bearer " + token
        h.update(headers or {})
        req = urllib.request.Request(self.base + path, data=json.dumps(body or {}).encode(), headers=h, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_health_is_open_and_says_which_source_runs(self):
        with urllib.request.urlopen(self.base + "/health", timeout=10) as r:
            state = json.loads(r.read())
        self.assertEqual((state["ok"], state["version"], state["digest"]), (True, A.VERSION, A.source_digest()))
        self.assertEqual(len(state["digest"]), 16)

    def test_everything_else_needs_the_token(self):
        self.assertEqual(self.post("/ping", token=None)[0], 401)
        self.assertEqual(self.post("/ping", token="wrong")[0], 401)
        self.assertEqual(self.post("/shutdown", token="wrong")[0], 401)
        self.assertEqual(self.post("/ping"), (200, {"ok": True, "version": A.VERSION, "digest": A.source_digest()}))

    def test_bad_requests_are_refused_clearly(self):
        code, out = self.post("/phone_agent", {"goal": "  "})
        self.assertEqual((code, out["ok"]), (200, False))
        self.assertIn("goal", out["error"])
        code, out = self.post("/browser", {"op": "fly"})
        self.assertIn("unknown browser op", out["error"])
        self.assertIn("unknown path", self.post("/nowhere")[1]["error"])

    def test_a_server_without_a_token_authorizes_nobody_and_does_not_start(self):
        A.TOKEN = ""
        try:
            self.assertEqual(self.post("/ping", token="")[0], 401)
            with self.assertRaises(SystemExit):
                A.main()
        finally:
            A.TOKEN = "secret"


class TargetTests(unittest.TestCase):
    def test_what_counts_as_a_selector_and_what_as_a_name(self):
        for selector in ("#q", ".btn-primary", "input[name=q]", "button.primary", "//a[@id='x']", "div > a"):
            self.assertTrue(A._looks_like_selector(selector), selector)
        for name in ("Add to cart", "Search products", "Sign in", "About us", "example.com", "متابعة"):
            self.assertFalse(A._looks_like_selector(name), name)

    def test_attribute_selectors_are_quoted_safely(self):
        self.assertEqual(A._attr("placeholder", 'Say "hi"'), '[placeholder="Say \\"hi\\""]')

    def test_script_values(self):
        self.assertEqual(A._script_value({"result": {"result": {"value": "x"}}}), "x")
        self.assertEqual(A._script_value({"result": {"value": "y"}}), "y")
        self.assertEqual(A._script_value("z"), "z")
        self.assertEqual(A._script_value(None), "")


if __name__ == "__main__":
    unittest.main()
