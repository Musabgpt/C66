"""Durable scheduled tasks with real agent actions, optional read-only and no duplicate triggers."""
import datetime as dt
import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace
from unittest import mock

from . import scheduled_tasks as st, settings, tools


class ScheduledTaskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        p = mock.patch.object(settings, "HOME", self.temp.name)
        p.start()
        self.addCleanup(p.stop)

    def test_once_create_and_delivery_persists(self):
        row = st.create("Medicine reminder", "Time to take your medicine", delay_minutes=5)
        self.assertEqual(st.listing()[0]["id"], row["id"])
        with mock.patch.object(st, "_notify") as notify:
            self.assertTrue(st.run_one(None, now=row["next_run"] + 2))
        notify.assert_called_once_with("Medicine reminder", "Time to take your medicine")
        self.assertEqual(st.listing()[0]["status"], "done")
        self.assertFalse(st.run_one(None, now=row["next_run"] + 30))
        self.assertTrue(st.listing()[0]["last_run"])

    def test_recurrence_skips_missed_runs_after_restart(self):
        row = st.create("Hourly", "Check", delay_minutes=5, repeat_minutes=60)
        with mock.patch.object(st, "_notify"):
            self.assertTrue(st.run_one(None, now=row["next_run"] + 3 * 3600 + 2))
        updated = st.listing()[0]
        self.assertEqual(updated["status"], "active")
        self.assertGreater(updated["next_run"], row["next_run"] + 3 * 3600)
        self.assertFalse(st.run_one(None, now=row["next_run"] + 30))

    def test_pause_resume_delete_run_now(self):
        row = st.create("Weekly", "Summary", repeat_minutes=10080)
        self.assertEqual(st.manage(row["id"], "pause")["status"], "paused")
        self.assertIsNone(st._claim(now=row["next_run"] + 2))
        self.assertEqual(st.manage(row["id"], "resume")["status"], "active")
        self.assertLess(st.manage(row["id"], "run")["next_run"], time.time() + 30)
        self.assertTrue(st.manage(row["id"], "delete")["ok"])
        self.assertEqual(st.listing(), [])
        with self.assertRaisesRegex(ValueError, "not found"):
            st.manage(row["id"], "pause")

    def test_validate_time_timezone_and_frequency(self):
        for kwargs in ({"when": "2026-11-20T09:00:00"}, {"delay_minutes": 0},
                       {"repeat_minutes": 5}, {"kind": "watch", "delay_minutes": 5},
                       {"when": "1990-01-01T00:00:00+00:00"},
                       {"permission_mode": "bypass-security"}):
            with self.subTest(kwargs=kwargs):
                with mock.patch.dict(os.environ), self.assertRaises(ValueError):
                    os.environ.pop("TZ", None)          # no phone zone passed: a time without offset is refused
                    st.create("Test", "Test", **kwargs)
        when = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=2)).isoformat()
        item = st.create("At fixed UTC time", "Reminder", when=when)
        self.assertAlmostEqual(item["next_run"],
                               dt.datetime.fromisoformat(when).timestamp(), delta=2)

    def test_changes_ask_the_app_to_set_its_alarm(self):
        posts = []

        class Phone(BaseHTTPRequestHandler):
            def do_POST(self):
                posts.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                self.send_response(200)
                self.send_header("Content-Length", "11")
                self.end_headers()
                self.wfile.write(b'{"ok":true}')

            def log_message(self, *a):
                pass

        server = HTTPServer(("127.0.0.1", 0), Phone)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with mock.patch.dict(os.environ, {"NEWAL_PHONE_KEY": "k", "NEWAL_PHONE_PORT": str(server.server_address[1])}):
            row = st.create("Water", "Drink water", delay_minutes=5)
            st.manage(row["id"], "pause")
            deadline = time.time() + 5
            while len(posts) < 2 and time.time() < deadline:
                time.sleep(0.05)
        self.assertEqual(posts, [{"action": "task_alarm"}] * 2)

    @unittest.skipUnless(hasattr(time, "tzset"), "needs time.tzset")
    def test_time_without_offset_is_the_phones_local_time(self):
        with mock.patch.dict(os.environ, {"TZ": "Asia/Amman"}):
            time.tzset()
            try:
                local = dt.datetime.now() + dt.timedelta(days=1)
                item = st.create("Local", "Reminder", when=local.strftime("%Y-%m-%dT%H:%M:00"))
                expected = dt.datetime(local.year, local.month, local.day, local.hour, local.minute,
                                       tzinfo=dt.timezone(dt.timedelta(hours=3))).timestamp()
                self.assertAlmostEqual(item["next_run"], expected, delta=1)
            finally:
                os.environ.pop("TZ", None)
        time.tzset()

    def test_user_task_cap_and_no_command_injection(self):
        with self.assertRaises(ValueError):
            st.create("", "test", delay_minutes=1)
        with self.assertRaises(ValueError):
            st.create("x", "x" * 5000, delay_minutes=1)
        with self.assertRaises(ValueError):
            st.manage("foo' OR 1=1 --", "delete")
        for n in range(100):
            st.create("item" + str(n), "test", delay_minutes=2)
        with self.assertRaisesRegex(ValueError, "Maximum"):
            st.create("101", "test", delay_minutes=1)

    def test_agent_kind_runs_in_full_auto_and_can_set_phone_alarm(self):
        row = st.create("Alarm", "شغّل منبه الساعة 7 على هاتفي", delay_minutes=3, kind="agent")
        session = SimpleNamespace(id="full-auto-session-1")
        agent = SimpleNamespace(run=mock.Mock(return_value="Alarm configured on the phone."))
        service = SimpleNamespace(create=mock.Mock(return_value=session),
                                  agent=mock.Mock(return_value=agent))
        with mock.patch.object(st, "_notify") as notify:
            self.assertTrue(st.run_one(service, now=row["next_run"] + 1))
        self.assertEqual(service.create.call_args.kwargs["mode"], "full-auto")
        self.assertEqual(row["permission_mode"], "full-auto")
        agent.run.assert_called_once()
        self.assertIn("شغّل منبه", agent.run.call_args.args[0])
        self.assertIn("phone tool for alarms", agent.run.call_args.args[0])
        self.assertNotIn("Read-only research and inspection only", agent.run.call_args.args[0])
        notify.assert_called_once()
        self.assertEqual(st.listing()[0]["session_id"], "full-auto-session-1")
        self.assertEqual(st.listing()[0]["status"], "done")

    def test_explicit_read_only_remains_available_for_individual_task(self):
        row = st.create("Optional read only", "Examine status", kind="agent",
                        delay_minutes=2, permission_mode="read-only")
        agent = SimpleNamespace(run=mock.Mock(return_value="Checked"))
        svc = SimpleNamespace(create=mock.Mock(return_value=SimpleNamespace(id="readonly")),
                              agent=mock.Mock(return_value=agent))
        with mock.patch.object(st, "_notify"):
            st.run_one(svc, now=row["next_run"] + 1)
        self.assertEqual(svc.create.call_args.kwargs["mode"], "read-only")
        self.assertIn("explicitly configured as read-only", agent.run.call_args.args[0])

    def test_legacy_jobs_are_migrated_to_full_execution(self):
        path = st.db_path()
        with sqlite3.connect(path) as db:
            db.execute("""CREATE TABLE scheduled_tasks (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, prompt TEXT NOT NULL,
                kind TEXT NOT NULL, created REAL NOT NULL, next_run REAL NOT NULL,
                repeat_seconds INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'active',
                last_run REAL, last_result TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0,
                session_id TEXT NOT NULL DEFAULT '', root TEXT NOT NULL DEFAULT ''
            )""")
            db.execute("INSERT INTO scheduled_tasks (id,title,prompt,kind,created,next_run) "
                       "VALUES (?,?,?,?,?,?)",
                       ("abcdef0123456789", "Existing alarm", "Open the clock app", "agent",
                        time.time(), time.time() + 500))
        rec = st.listing()[0]
        self.assertEqual(rec["title"], "Existing alarm")
        self.assertEqual(rec["permission_mode"], "full-auto")
        agent = SimpleNamespace(run=mock.Mock(return_value="Alarm done"))
        svc = SimpleNamespace(create=mock.Mock(return_value=SimpleNamespace(id="restored")),
                              agent=mock.Mock(return_value=agent))
        with mock.patch.object(st, "_notify"):
            st.run_one(svc, now=rec["next_run"] + 1)
        self.assertEqual(svc.create.call_args.kwargs["mode"], "full-auto")

    def test_watch_suppresses_no_change_notification(self):
        row = st.create("Watch GitHub", "Tell me if CI fails", delay_minutes=2,
                        repeat_minutes=60, kind="watch")
        agent = SimpleNamespace(run=mock.Mock(return_value="NO_CHANGE"))
        service = SimpleNamespace(create=mock.Mock(return_value=SimpleNamespace(id="s2")),
                                  agent=mock.Mock(return_value=agent))
        with mock.patch.object(st, "_notify") as notify:
            self.assertTrue(st.run_one(service, now=row["next_run"] + 1))
        notify.assert_not_called()
        self.assertEqual(st.listing()[0]["status"], "active")

    def test_watch_triggers_actions_and_completes_only_once(self):
        row = st.create("CI repair watch", "When CI fails, inspect and repair", kind="watch",
                        repeat_minutes=60, delay_minutes=3)
        agent = SimpleNamespace(run=mock.Mock(return_value="CI fixed and tests passed."))
        svc = SimpleNamespace(create=mock.Mock(return_value=SimpleNamespace(id="agentwatch")),
                              agent=mock.Mock(return_value=agent))
        with mock.patch.object(st, "_notify") as notify:
            self.assertTrue(st.run_one(svc, now=row["next_run"] + 1))
        self.assertEqual(svc.create.call_args.kwargs["mode"], "full-auto")
        self.assertIn("perform only the requested actions", agent.run.call_args.args[0])
        notify.assert_called_once()
        self.assertEqual(st.listing()[0]["status"], "done")
        self.assertFalse(st.run_one(svc, now=row["next_run"] + 6 * 3600))

    def test_recover_running_after_service_restart(self):
        row = st.create("Interrupted", "Check", delay_minutes=2)
        with st._connect() as db:
            db.execute("UPDATE scheduled_tasks SET status='running' WHERE id=?", (row["id"],))
        st.recover_incomplete()
        rec = st.listing()[0]
        self.assertEqual(rec["status"], "active")
        self.assertLess(rec["next_run"], time.time() + 20)

    def test_failed_notification_never_marks_reminder_done(self):
        row = st.create("Retry", "remind", delay_minutes=1)
        with mock.patch.object(st, "_notify", side_effect=OSError("notification denied")):
            self.assertTrue(st.run_one(None, now=row["next_run"] + 1))
        rec = st.listing()[0]
        self.assertEqual(rec["status"], "active")
        self.assertIn("notification denied", rec["last_result"])
        self.assertEqual(rec["attempts"], 1)

    def test_api_routes_are_bounded_and_return_errors(self):
        class Handler:
            response = None
            def _json(self, body, status=200):
                self.response = body, status
        h = Handler()
        self.assertFalse(st.route(h, "GET", "/api/unrelated"))
        self.assertTrue(st.route(h, "POST", "/api/scheduled-tasks",
                                 {"title": "Via UI", "prompt": "Check", "delay_minutes": 2}))
        obj = h.response[0]["task"]
        self.assertEqual(h.response[1], 200)
        self.assertEqual(obj["permission_mode"], "full-auto")
        self.assertTrue(st.route(h, "GET", "/api/scheduled-tasks"))
        self.assertEqual(h.response[0]["tasks"][0]["id"], obj["id"])
        self.assertTrue(st.route(h, "POST", "/api/scheduled-tasks/bad/delete", {}))
        self.assertEqual(h.response[1], 404)

    def test_agent_tool_creates_task_in_connected_project(self):
        ctx = SimpleNamespace(root="/storage/emulated/0/my-project",
                              session=SimpleNamespace(mode="auto-edit"))
        self.assertIn("scheduled_task_create", tools.REGISTRY)
        _, item = tools.REGISTRY["scheduled_task_create"].fn(
            ctx, title="Check project", prompt="Inspect tests", delay_minutes=5)
        self.assertEqual(item["root"], ctx.root)
        self.assertEqual(item["permission_mode"], "full-auto")
        self.assertEqual(st.listing()[0]["title"], "Check project")
        with self.assertRaises(tools.ToolError):
            tools.REGISTRY["scheduled_task_create"].fn(
                SimpleNamespace(root=ctx.root, session=SimpleNamespace(mode="read-only")),
                title="Forbidden", prompt="No", delay_minutes=3)


if __name__ == "__main__":
    unittest.main()
