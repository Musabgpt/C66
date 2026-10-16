"""Offline regression tests for durable, project-scoped evidence memory."""
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch


if __package__ == "newal_code":
    from . import autonomy
else:
    spec = importlib.util.spec_from_file_location("autonomy_under_test", Path(__file__).with_name("autonomy.py"))
    autonomy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(autonomy)


class MemoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, {"NEWAL_MEMORY_HOME": self.tmp.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.root = os.path.join(self.tmp.name, "project")
        self.store = autonomy.MemoryStore(self.root)
        self.addCleanup(lambda: self.store.close())

    def test_unrelated_and_stopword_queries_never_inject_other_lessons(self):
        self.store.learn("Gradle", "Use the offline build option", "exit 0")
        self.assertEqual(self.store.recall("unicorn migration"), [])
        self.assertEqual(self.store.recall("the and for"), [])
        self.assertEqual(self.store.recall("في من على"), [])
        self.assertEqual(self.store.recall("offline", 0), [])

    def test_arabic_normalization_and_topic_relevance(self):
        self.store.learn("أخطاء المَلفات", "تحقق من المسار قبل القراءة", "read corrected")
        self.assertEqual(len(self.store.recall("اخطاء ملفات")), 1)
        self.store.learn("Tests", "Check Gradle when tests fail")
        expected = self.store.learn("Gradle", "Set the project directory")
        self.assertIsInstance(expected, int)
        self.assertEqual(self.store.recall("gradle")[0]["id"], expected)

    def test_index_finds_old_relevant_memory_outside_popularity_window(self):
        expected = self.store.learn("uniquebuild", "Use the wrapper", "exit 0")
        for index in range(260):
            self.store.learn("other" + str(index), "Unrelated package " + str(index))
        self.assertIsInstance(expected, int)
        self.assertEqual([row["id"] for row in self.store.recall("uniquebuild")], [expected])

    def test_normalized_duplicates_have_one_stable_id(self):
        first = self.store.learn("Gradle", "Use   ./gradlew check.", "exit 0")
        second = self.store.learn("GRADLE", "use ./gradlew check.", "exit 0")
        self.assertIsInstance(first, int)
        self.assertEqual(first, second)
        self.assertEqual(len(self.store.recall()), 1)

    def test_credentials_are_redacted_before_persistence(self):
        secrets = ["json-secret-123", "basic-credential-456", "url-secret-789",
                   "userinfo-secret", "github_pat_" + "A" * 30,
                   "sk-proj-" + "B" * 30, "hf_" + "C" * 30]
        evidence = json.dumps({"nested": {"client_secret": secrets[0]},
                               "Authorization": "Basic " + secrets[1],
                               "url": "https://user:" + secrets[3] + "@example.invalid/?access_token=" + secrets[2],
                               "output": " ".join(secrets[4:])})
        self.store.learn("Credentials", "Never retain provider secrets", evidence)
        self.store.episode("failure", secrets[4], evidence, evidence, evidence, evidence)
        text = "\n".join(self.store.db.iterdump())
        for secret in secrets:
            with self.subTest(secret=secret):
                self.assertNotIn(secret, text)
        self.assertIn("[redacted]", text)

    def test_project_preference_survives_restart_and_disables_capture(self):
        self.assertTrue(hasattr(self.store, "set_enabled"), "Memory must expose a persisted enabled preference")
        self.store.learn("Gradle", "Use wrapper")
        self.store.set_enabled(False)
        self.store.close()
        self.store = autonomy.MemoryStore(self.root)
        self.assertFalse(self.store.enabled)
        self.assertEqual(self.store.recall("Gradle"), [])
        self.assertIsNone(self.store.learn("Ignored", "Disabled memory"))
        self.store.episode("failure", task="Ignored", error="failure")
        self.assertEqual(self.store.overview()["counts"], {"lessons": 1, "episodes": 0})
        other = autonomy.MemoryStore(self.root + "-other")
        try:
            self.assertTrue(other.enabled)
        finally:
            other.close()
        with self.assertRaises(ValueError):
            self.store.set_enabled("false")

    def test_review_forget_and_clear_affect_only_memory_records(self):
        self.assertTrue(hasattr(self.store, "overview"), "Review API must expose counts and lesson identifiers")
        lesson = self.store.learn("Gradle", "Use wrapper", "exit 0")
        self.store.episode("failure", task="Build", error="missing wrapper")
        overview = self.store.overview("gradle")
        self.assertEqual(overview["counts"], {"lessons": 1, "episodes": 1})
        self.assertEqual(overview["lessons"][0]["id"], lesson)
        self.assertEqual(len(overview["recent_failures"]), 1)
        self.assertTrue(self.store.forget(lesson))
        self.assertFalse(self.store.forget(lesson))
        self.assertEqual(self.store.recall("gradle"), [])
        self.store.clear()
        self.assertEqual(self.store.overview()["counts"], {"lessons": 0, "episodes": 0})
        self.assertTrue(os.path.isfile(self.store.path))

    def test_clear_or_disable_from_another_connection_invalidates_pending_evidence(self):
        controls = autonomy.MemoryStore(self.root)
        self.addCleanup(controls.close)
        for action in ("clear", "disable"):
            with self.subTest(action=action):
                self.store.record_tool("Build", "bash", {"command": "bad"},
                                       {"output": "Error: missing executable", "meta": {"exit": 1}}, False, "s")
                if action == "clear":
                    controls.clear()
                else:
                    controls.set_enabled(False)
                    controls.set_enabled(True)
                learned = self.store.record_tool("Build", "bash", {"command": "fixed"},
                                                 {"meta": {"exit": 0}}, True, "s")
                self.assertIsNone(learned)
                self.assertEqual(self.store.recall(), [])

    def test_tool_failure_alone_is_not_a_lesson_and_corrected_success_is(self):
        self.assertTrue(hasattr(self.store, "record_tool"), "Tool observations must require corrected failure evidence")
        self.store.record_tool("Build", "bash", {"command": "gradle check"},
                               {"output": "Error: gradle not found", "meta": {"exit": 127}}, False, "s")
        self.assertEqual(self.store.recall(), [])
        self.assertEqual(len(self.store.recent_failures()), 1)
        learned = self.store.record_tool("Build", "bash", {"command": "./gradlew check"},
                                        {"output": "arbitrary-private-success-output", "meta": {"exit": 0}}, True, "s")
        self.assertIsInstance(learned, int)
        lesson = self.store.recall("gradlew")[0]
        self.assertIn("./gradlew check", lesson["lesson"])
        self.assertIn("127", lesson["evidence"])
        self.assertIn("0", lesson["evidence"])
        self.assertNotIn("arbitrary-private-success-output", "\n".join(self.store.db.iterdump()))

    def test_tool_learning_requires_same_task_session_tool_and_changed_arguments(self):
        self.assertTrue(hasattr(self.store, "record_tool"))
        failure = {"output": "Error: missing file", "meta": {"exit": 1}}
        success = {"output": "ok", "meta": {"exit": 0}}
        self.store.record_tool("Build", "bash", {"command": "bad"}, failure, False, "s")
        for task, name, args, sid in [("Other", "bash", {"command": "fixed"}, "s"),
                                      ("Build", "read", {"path": "fixed"}, "s"),
                                      ("Build", "bash", {"command": "fixed"}, "other"),
                                      ("Build", "bash", {"command": "bad"}, "s")]:
            self.assertIsNone(self.store.record_tool(task, name, args, success, True, sid))
        self.assertEqual(self.store.recall(), [])

    def test_nonzero_exit_overrides_success_flag_and_unknown_results_are_ignored(self):
        self.assertTrue(hasattr(self.store, "record_tool"))
        self.store.record_tool("Build", "bash", {"command": "bad"}, {"meta": {"exit": 1}}, False, "s")
        self.assertIsNone(self.store.record_tool("Build", "bash", {"command": "still-bad"},
                                              {"meta": {"exit": 2}}, True, "s"))
        self.store.record_tool("Build", "bash", {"command": "unknown"}, {}, None, "s")
        self.assertEqual(self.store.recall(), [])
        self.assertEqual(len(self.store.recent_failures()), 2)

    def test_parallel_calls_in_one_step_do_not_create_a_correction_lesson(self):
        self.store.record_tool("Read files", "read", {"path": "missing.py"},
                               {"error": "No such file", "step": 1}, False, "s")
        self.assertIsNone(self.store.record_tool("Read files", "read", {"path": "other.py"},
                                                {"output": "unrelated file", "step": 1}, True, "s"))
        self.assertEqual(self.store.recall(), [])
        self.assertIsInstance(self.store.record_tool("Read files", "read", {"path": "src/missing.py"},
                                                    {"output": "found the file", "step": 2}, True, "s"), int)

    def test_structured_failure_flags_override_a_successful_dispatch(self):
        for flag in ({"ok": False}, {"isError": True}, {"error": "Missing file"}):
            with self.subTest(flag=flag):
                self.store.record_tool("Build", "bash", {"command": "bad"},
                                       {"meta": {"exit": 1}}, False, "s")
                self.assertIsNone(self.store.record_tool("Build", "bash", {"command": "still-bad"},
                                                        {"meta": dict(flag, exit=0)}, True, "s"))
        self.assertEqual(self.store.recall(), [])

    def test_existing_database_is_migrated_without_losing_lessons(self):
        root = self.root + "-legacy"
        digest = hashlib.sha256(root.encode()).hexdigest()[:16]
        path = os.path.join(self.tmp.name, digest + ".sqlite3")
        db = sqlite3.connect(path)
        db.execute("CREATE TABLE learnings(id INTEGER PRIMARY KEY, ts REAL NOT NULL, topic TEXT NOT NULL, lesson TEXT NOT NULL, evidence TEXT, hits INTEGER NOT NULL DEFAULT 0, last_used REAL)")
        db.execute("INSERT INTO learnings VALUES(9,1,'Gradle','Use wrapper','exit 0',2,1)")
        db.commit()
        db.close()
        legacy = autonomy.MemoryStore(root)
        try:
            recalled = legacy.recall("gradle")
            self.assertIn("id", recalled[0])
            self.assertEqual(recalled[0]["id"], 9)
            self.assertEqual(legacy.learn("GRADLE", "use wrapper"), 9)
        finally:
            legacy.close()

    def test_capture_is_bounded_and_success_without_failure_is_not_persisted(self):
        self.assertTrue(hasattr(self.store, "record_tool"))
        self.store.record_tool("Build", "bash", {"command": "true"}, {"output": "private" * 10000}, True, "s")
        self.assertEqual(self.store.overview()["counts"], {"lessons": 0, "episodes": 0})
        self.store.record_tool("Build", "bash", {"command": "false"},
                               {"output": "Error: missing executable\n" + "unrelated-data" * 10000}, False, "s")
        failure = self.store.recent_failures()[0]
        self.assertLess(len(failure["evidence"]), 2000)
        self.assertNotIn("unrelated-data", "\n".join(self.store.db.iterdump()))


if __name__ == "__main__":
    unittest.main()
