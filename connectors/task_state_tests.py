import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from . import task_state


class TaskStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root_a = Path(self.tmp.name) / "a"
        self.root_b = Path(self.tmp.name) / "b"
        self.root_a.mkdir()
        self.root_b.mkdir()
        self.store = Path(self.tmp.name) / "task-state"
        self.env = patch.dict(os.environ, {"NEWAL_TASK_STATE_HOME": str(self.store)}, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_checkpoint_survives_reload_and_resumes_latest(self):
        row = task_state.checkpoint(
            self.root_a,
            objective="Build the APK",
            progress="Tests passed",
            next_step="Run Gradle",
            evidence="Action 269",
        )
        loaded = json.loads(task_state._path(self.root_a).read_text(encoding="utf-8"))
        self.assertIn(row["id"], loaded["tasks"])
        resumed = task_state.resume(self.root_a)
        self.assertEqual(resumed["id"], row["id"])
        self.assertEqual(resumed["checkpoint"]["next_step"], "Run Gradle")
        self.assertEqual(resumed["checkpoint"]["evidence"], "Action 269")

    def test_projects_are_isolated(self):
        a = task_state.checkpoint(self.root_a, objective="Project A", next_step="A")
        task_state.checkpoint(self.root_b, objective="Project B", next_step="B")
        self.assertEqual(task_state.resume(self.root_a)["id"], a["id"])
        self.assertEqual(task_state.resume(self.root_a)["objective"], "Project A")
        self.assertEqual(task_state.list_tasks(self.root_b)[0]["objective"], "Project B")

    def test_complete_is_not_returned_as_latest_active(self):
        first = task_state.checkpoint(self.root_a, objective="First")
        second = task_state.checkpoint(self.root_a, objective="Second")
        task_state.complete(self.root_a, second["id"], "verified")
        self.assertEqual(task_state.resume(self.root_a)["id"], first["id"])
        self.assertEqual(task_state.resume(self.root_a, second["id"])["status"], "complete")
        self.assertEqual(len(task_state.list_tasks(self.root_a)), 1)
        self.assertEqual(len(task_state.list_tasks(self.root_a, include_completed=True)), 2)

    def test_history_and_fields_are_bounded(self):
        row = task_state.checkpoint(self.root_a, objective="Bounded")
        for i in range(task_state.MAX_HISTORY + 8):
            row = task_state.checkpoint(
                self.root_a,
                task_id=row["id"],
                progress=("x" * (task_state.MAX_FIELD + 100)) + str(i),
                evidence="e" * (task_state.MAX_EVIDENCE + 100),
            )
        self.assertLessEqual(len(row["history"]), task_state.MAX_HISTORY)
        self.assertLessEqual(len(row["checkpoint"]["progress"]), task_state.MAX_FIELD)
        self.assertLessEqual(len(row["checkpoint"]["evidence"]), task_state.MAX_EVIDENCE)

    def test_invalid_task_id_is_rejected(self):
        with self.assertRaises(Exception):
            task_state.checkpoint(self.root_a, task_id="../escape", objective="bad")


if __name__ == "__main__":
    unittest.main()

class TaskStateRouteTests(unittest.TestCase):
    setUp = TaskStateTests.setUp
    def test_tasks_route_requires_session_and_isolates_conversations(self):
        from types import SimpleNamespace
        a = task_state.checkpoint(self.root_a, objective='First conversation', session_id='first')
        task_state.checkpoint(self.root_a, objective='Other conversation', session_id='second')
        results = []
        handler = SimpleNamespace(_query=lambda: {'session':'first'},
            service=SimpleNamespace(get=lambda sid: SimpleNamespace(root=self.root_a), threads={}, agents={}),
            _json=lambda data, status=200: results.append((data,status)))
        task_state.route(handler, 'GET', '/api/task-state')
        self.assertEqual([r['id'] for r in results[-1][0]['tasks']], [a['id']])
        self.assertFalse(results[-1][0]['running'])
        handler._query = lambda: {}
        task_state.route(handler, 'GET', '/api/task-state')
        self.assertEqual(results[-1][1], 400)
