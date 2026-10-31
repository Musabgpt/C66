"""The floating assistant's events endpoint (Service.wait_events, GET /api/sessions/<id>/events) and the local time
note (clock.py)."""
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.request
from unittest import mock

from . import clock, service as servicemod, session as sessmod, settings


class _Busy:
    def __init__(self):
        self.alive = True

    def is_alive(self):
        return self.alive


class WaitEventsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for name, value in (("HOME", self.temp.name), ("SESSIONS", os.path.join(self.temp.name, "sessions"))):
            p = mock.patch.object(settings, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.svc = servicemod.Service()
        self.s = self.svc.create(self.temp.name, model="fake/remote")
        self.turn = _Busy()
        self.svc.threads[self.s.id] = self.turn

    def add(self, kind, **kw):
        ev = dict(kw, type=kind, session=self.s.id, t=time.time())
        self.s.add_event(ev)
        self.svc.broadcast(ev)

    def test_peek_gives_the_cursor_and_model(self):
        self.add("turn_start")
        r = self.svc.wait_events(self.s.id, -1, wait=5)
        self.assertEqual((r["events"], r["next"], r["model"]), ([], 1, "fake/remote"))

    def test_waits_for_the_next_event_and_returns_only_it(self):
        self.add("turn_start")
        threading.Timer(0.3, lambda: self.add("tool_start", name="phone")).start()
        t0 = time.monotonic()
        r = self.svc.wait_events(self.s.id, 1, wait=10)
        self.assertLess(time.monotonic() - t0, 3)
        self.assertEqual([e["type"] for e in r["events"]], ["tool_start"])
        self.assertEqual((r["next"], r["busy"]), (2, True))

    def test_returns_when_the_turn_ends_without_an_event(self):
        threading.Timer(0.3, lambda: setattr(self.turn, "alive", False)).start()
        t0 = time.monotonic()
        r = self.svc.wait_events(self.s.id, 0, wait=10)
        self.assertLess(time.monotonic() - t0, 3)
        self.assertEqual((r["events"], r["busy"]), ([], False))

    def test_quiet_turn_times_out(self):
        t0 = time.monotonic()
        r = self.svc.wait_events(self.s.id, 0, wait=0.4)
        self.assertGreaterEqual(time.monotonic() - t0, 0.35)
        self.assertEqual((r["events"], r["busy"]), ([], True))

    def test_cursor_survives_dropped_old_events(self):
        for i in range(4001):                      # the session drops its oldest 1000 past 4000
            self.s.events.append({"type": "x", "i": i})
            self.s.event_total = i + 1
        self.add("turn_end", answer="تم")
        self.assertEqual(len(self.s.events), 3002)
        r = self.svc.wait_events(self.s.id, 4001, wait=1)
        self.assertEqual([e["type"] for e in r["events"]], ["turn_end"])
        self.assertEqual(r["next"], 4002)

    def test_send_returns_the_cursor_and_a_failed_turn_is_kept_in_the_thread(self):
        self.svc.threads.pop(self.s.id)
        agent = mock.Mock()
        agent.session = self.s
        agent.run.side_effect = RuntimeError("model down")
        with mock.patch.object(self.svc, "agent", return_value=agent):
            r = self.svc.send(self.s.id, "مرحبا")
            self.assertTrue(r["started"])
            self.svc.threads[self.s.id].join(5)
        out = self.svc.wait_events(self.s.id, r["cursor"], wait=1)
        kinds = [e["type"] for e in out["events"]]
        self.assertEqual(kinds, ["error", "turn_end"])
        self.assertIn("model down", out["events"][-1]["error"])


class EventsRouteTests(unittest.TestCase):
    def test_route_answers_with_new_events(self):
        from . import server
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        svc = servicemod.Service()
        patches = [mock.patch.object(settings, "HOME", temp.name),
                   mock.patch.object(settings, "SESSIONS", os.path.join(temp.name, "sessions")),
                   mock.patch.object(server.Handler, "service", svc, create=True),
                   mock.patch.object(server.Handler, "key", "k" * 20, create=True)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        httpd = server.Server(("127.0.0.1", 0), server.Handler)      # without serve()'s background helpers
        httpd.daemon_threads = True
        self.addCleanup(httpd.server_close)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.shutdown)
        s = svc.create(temp.name)
        s.add_event({"type": "turn_end", "answer": "تم", "session": s.id})
        url = "http://127.0.0.1:%d/api/sessions/%s/events?after=0&wait=1" % (httpd.server_address[1], s.id)
        req = urllib.request.Request(url, headers={"X-NewAl-Key": "k" * 20})
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read())
        self.assertEqual([e["answer"] for e in body["events"]], ["تم"])
        self.assertEqual((body["next"], body["busy"]), (1, False))


class ClockTests(unittest.TestCase):
    def test_note_has_weekday_time_and_offset(self):
        with mock.patch.dict(os.environ, {"TZ": "Asia/Amman"}):
            note = clock.note(0)
        self.assertTrue(note.startswith("Local time: "))
        self.assertIn("Asia/Amman", note)
        self.assertRegex(note, r"UTC[+-]\d\d:\d\d")

    @unittest.skipUnless(hasattr(time, "tzset"), "needs time.tzset")
    def test_offset_follows_tz(self):
        with mock.patch.dict(os.environ, {"TZ": "Asia/Amman"}):
            time.tzset()
            try:
                self.assertIn("(UTC+03:00, Asia/Amman)", clock.note(1798761600))
            finally:
                os.environ.pop("TZ", None)
        time.tzset()


if __name__ == "__main__":
    unittest.main()
