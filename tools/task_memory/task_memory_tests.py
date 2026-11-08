"""The task memory's builder: the same search terms as the phone (TaskMemoryText.java), the converters read their
datasets right, and the shipped library holds every source sorted into categories.
Run: python3 -m unittest discover -s tools/task_memory -p '*_tests.py'"""
import gzip
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import a11y_textproto as A  # noqa: E402
import convert_mind2web as M  # noqa: E402
import curated  # noqa: E402
import knowledge as K  # noqa: E402
import textnorm as T  # noqa: E402

ASSET = os.path.join(HERE, "..", "..", "android-lite", "app", "src", "main", "assets", "task_memory", "library.db.gz")

SCREEN = '''windows {
  bounds_in_screen {
    right: 1080
    bottom: 2400
  }
  layer: 2
  window_type: TYPE_APPLICATION
  tree {
    nodes {
      bounds_in_screen {
        right: 1080
        bottom: 2400
      }
      class_name: "android.widget.FrameLayout"
    }
    nodes {
      bounds_in_screen {
        left: 800
        top: 100
        right: 1000
        bottom: 200
      }
      class_name: "android.widget.Button"
      is_clickable: true
      depth: 1
    }
    nodes {
      bounds_in_screen {
        left: 820
        top: 120
        right: 980
        bottom: 180
      }
      class_name: "android.widget.TextView"
      text: "\\330\\247\\331\\206\\330\\264\\330\\261"
      depth: 2
    }
  }
}
'''


class TextTests(unittest.TestCase):
    def test_terms_match_the_phone(self):
        # TaskMemoryTextTest.java expects exactly these
        self.assertEqual(T.query("افتح فيسبوك وانشر منشور يقول مرحبا يا جماعة"),
                         [["افتح"], ["فيسبوك", "يسبوك"], ["وانشر", "انشر"], ["منشور"], ["يقول"], ["مرحبا"],
                          ["جماعه", "جماع"]])
        self.assertEqual(T.query("Searching for posts, photos & classes!"),
                         [["search"], ["post"], ["photo"], ["class"]])
        self.assertEqual(T.fold("الإعدادات"), "الاعدادات")


class ConverterTests(unittest.TestCase):
    def test_a_tap_becomes_what_it_hit(self):
        nodes = A.parse(SCREEN)
        self.assertEqual(len(nodes), 3)
        label, rid, kind = A.target(nodes, 900, 150)
        self.assertEqual(label, "انشر")            # octal-escaped UTF-8 decoded, the words inside the button
        self.assertEqual(kind, "item")

    def test_mind2web_actions(self):
        m = M.REPR.match("[combobox]  Reservation type -> SELECT: Pickup")
        self.assertEqual((m.group("role"), m.group("label").strip(), m.group("op"), m.group("value")),
                         ("combobox", "Reservation type", "SELECT", "Pickup"))
        self.assertEqual(M.site_url("sports.yahoo"), "https://sports.yahoo.com")
        self.assertEqual(M.site_url("united"), "https://www.united.com")

    def test_curated_procedures_are_well_formed(self):
        keys = set()
        cats = {c[0] for c in K.CATEGORIES}
        for c in curated.CURATED:
            self.assertNotIn(c["key"], keys)
            keys.add(c["key"])
            self.assertIn(c["cat"], cats, c["key"])
            self.assertTrue(c["title_ar"] and c["goal"] and c["steps"], c["key"])
            text = json.dumps(c["steps"], ensure_ascii=False)
            for i, _ in enumerate(c.get("slots") or []):
                self.assertIn("{%d}" % (i + 1), text, c["key"])
        self.assertIn("cur.facebook.post", keys)


@unittest.skipUnless(os.path.isfile(ASSET), "the shipped library is built by build_db.py")
class LibraryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        path = os.path.join(cls.tmp, "library.db")
        with gzip.open(ASSET, "rb") as f, open(path, "wb") as out:
            shutil.copyfileobj(f, out)
        cls.db = sqlite3.connect(path)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        shutil.rmtree(cls.tmp)

    def test_every_source_is_in_it(self):
        n = dict(self.db.execute("SELECT src, COUNT(*) FROM procs GROUP BY src"))
        self.assertEqual(n["curated"], len(curated.CURATED))
        self.assertGreater(n["androidcontrol"], 15000)
        self.assertGreater(n["mind2web"], 1000)
        self.assertGreater(n["insta150k"], 140000)
        execs = self.db.execute("SELECT COUNT(*) FROM procs WHERE exec = 1").fetchone()[0]
        self.assertGreater(execs, 3000)

    def test_sorted_into_categories(self):
        cats = dict(self.db.execute("SELECT id, n FROM categories"))
        self.assertEqual(set(cats), {c[0] for c in K.CATEGORIES})
        total = sum(cats.values())
        self.assertEqual(total, self.db.execute("SELECT COUNT(*) FROM procs").fetchone()[0])
        self.assertGreater(cats["social"], 0)
        self.assertGreater(cats["shopping"], 1000)

    def test_the_index_finds_a_procedure(self):
        ids = [r[0] for r in self.db.execute("SELECT docid FROM procs_fts WHERE procs_fts MATCH ?", ("منشور",))]
        keys = [r[0] for r in self.db.execute("SELECT key FROM procs WHERE id IN (%s)" % ",".join(map(str, ids)))]
        self.assertIn("cur.facebook.post", keys)
        steps = json.loads(self.db.execute("SELECT steps FROM procs WHERE key = 'cur.facebook.post'").fetchone()[0])
        self.assertEqual(steps["steps"][0]["pkg"], "com.facebook.katana")
        lex = dict(self.db.execute("SELECT ar, en FROM lexicon"))
        self.assertIn("post", lex["انشر"].split())


if __name__ == "__main__":
    unittest.main()
