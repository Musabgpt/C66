import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from . import project_rag


class ProjectRAGTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "project"
        self.root.mkdir()
        self.cache = Path(self.tmp.name) / "rag-cache"
        self.env = patch.dict(os.environ, {"NEWAL_RAG_HOME": str(self.cache)})
        self.env.start()
        self.addCleanup(self.env.stop)

    def store(self):
        store = project_rag.ProjectRAG(str(self.root))
        self.addCleanup(store.close)
        return store

    def test_indexes_and_returns_file_line_evidence(self):
        (self.root / "agent.py").write_text(
            "def route_task(task):\n"
            "    if task == 'document':\n"
            "        return 'document_engine_selector'\n",
            encoding="utf-8",
        )
        store = self.store()
        status = store.refresh()
        self.assertEqual(status["files"], 1)
        rows = store.search("document route task", refresh=False)
        self.assertTrue(rows)
        self.assertEqual(rows[0]["path"], "agent.py")
        self.assertGreaterEqual(rows[0]["start_line"], 1)
        self.assertIn("document_engine_selector", rows[0]["snippet"])

    def test_refresh_is_incremental_and_removes_deleted_files(self):
        path = self.root / "notes.md"
        path.write_text("alpha beta gamma", encoding="utf-8")
        store = self.store()
        first = store.refresh()
        second = store.refresh()
        self.assertEqual(first["changed"], 1)
        self.assertEqual(second["changed"], 0)
        path.write_text("alpha delta epsilon", encoding="utf-8")
        third = store.refresh()
        self.assertEqual(third["changed"], 1)
        self.assertTrue(store.search("epsilon", refresh=False))
        path.unlink()
        fourth = store.refresh()
        self.assertEqual(fourth["removed"], 1)
        self.assertEqual(store.search("epsilon", refresh=False), [])

    def test_ignores_build_node_modules_binary_and_large_files(self):
        (self.root / "build").mkdir()
        (self.root / "build" / "ignored.py").write_text("forbidden_token", encoding="utf-8")
        (self.root / "node_modules").mkdir()
        (self.root / "node_modules" / "ignored.js").write_text("another_forbidden", encoding="utf-8")
        (self.root / "image.png").write_bytes(b"\x00\x01\x02")
        (self.root / "good.kt").write_text("class MusabRouter", encoding="utf-8")
        store = self.store()
        status = store.refresh()
        self.assertEqual(status["files"], 1)
        self.assertTrue(store.search("MusabRouter", refresh=False))
        self.assertFalse(store.search("forbidden_token", refresh=False))

    def test_arabic_terms_are_searchable(self):
        (self.root / "README.md").write_text("ذاكرة دائمة للمشروع مع فهرسة محلية", encoding="utf-8")
        store = self.store()
        store.refresh()
        rows = store.search("ذاكرة المشروع", refresh=False)
        self.assertTrue(rows)
        self.assertEqual(rows[0]["path"], "README.md")

    def test_index_lives_outside_project(self):
        (self.root / "x.py").write_text("print('hello')", encoding="utf-8")
        store = self.store()
        store.refresh()
        self.assertTrue(self.cache.exists())
        self.assertFalse(any(p.suffix == ".sqlite3" for p in self.root.rglob("*")))


if __name__ == "__main__":
    unittest.main()
