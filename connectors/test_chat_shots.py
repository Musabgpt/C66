"""The pictures in the chat: the copy kept with a session, the one address they are served from, and what is refused.

Run in CI: python3 -m unittest discover -s connectors -p test_chat_shots.py -v
"""
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("session_images", ROOT / "session_images.py")
shots = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shots)

JPEG = b"\xff\xd8\xff\xe0" + b"screen" * 200
PNG = b"\x89PNG\r\n\x1a\n" + b"pixels" * 50


class ChatPicturesTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.store = os.path.join(self.dir, "shots")
        self.shot = os.path.join(self.dir, "musabai-screen-1-small.jpg")
        with open(self.shot, "wb") as f:
            f.write(JPEG)

    def keep(self, path=None, sid="sess-1"):
        return shots.keep(sid, path or self.shot, self.store)

    def test_keeps_a_copy_under_the_session_and_reads_it_back_after_the_original_is_gone(self):
        ref = self.keep()
        self.assertEqual(ref["sid"], "sess-1")
        self.assertRegex(ref["name"], r"^[0-9a-f]{16}\.jpg$")
        os.remove(self.shot)          # the phone clears its screenshot cache when the next screenshot is taken
        self.assertEqual(shots.read("/api/shot/sess-1/" + ref["name"], self.store), (JPEG, "image/jpeg"))

    def test_each_picture_gets_its_own_name(self):
        self.assertEqual(len({self.keep()["name"] for _ in range(5)}), 5)

    def test_png_and_jpeg_extensions_are_normalised_and_served_with_their_own_type(self):
        path = os.path.join(self.dir, "chart.PNG")
        with open(path, "wb") as f:
            f.write(PNG)
        ref = self.keep(path)
        self.assertTrue(ref["name"].endswith(".png"))
        self.assertEqual(shots.read("/api/shot/sess-1/" + ref["name"], self.store), (PNG, "image/png"))
        alt = os.path.join(self.dir, "shot.jpeg")
        with open(alt, "wb") as f:
            f.write(JPEG)
        self.assertTrue(self.keep(alt)["name"].endswith(".jpg"))

    def test_only_pictures_we_show_are_kept(self):
        for name, data in (("page.html", b"<script>x</script>"), ("drawing.svg", b"<svg/>"), ("notes.txt", b"hi"),
                           ("empty.jpg", b"")):
            with self.subTest(name=name):
                path = os.path.join(self.dir, name)
                with open(path, "wb") as f:
                    f.write(data)
                self.assertIsNone(self.keep(path))
        self.assertIsNone(shots.keep("sess-1", os.path.join(self.dir, "missing.png"), self.store))

    def test_a_picture_over_the_size_limit_is_not_kept(self):
        with patch.object(shots, "MAX_BYTES", 10):
            self.assertIsNone(self.keep())

    def test_session_ids_must_be_plain_words(self):
        for sid in ("", "../x", "a/b", "a b", "x" * 81):
            with self.subTest(sid=sid):
                self.assertIsNone(shots.keep(sid, self.shot, self.store))

    def test_only_kept_names_are_served(self):
        ref = self.keep()
        url = "/api/shot/sess-1/" + ref["name"]
        self.assertIsNotNone(shots.read(url, self.store))
        self.assertIsNotNone(shots.read(url + "?t=1", self.store))          # a query string is ignored
        for bad in (
            "/api/shot/../sess-1/" + ref["name"],
            "/api/shot/sess-1/../../" + ref["name"],
            "/api/shot/sess-1/passwd.jpg",                                  # not a name we made
            "/api/shot/sess-1/" + ref["name"].replace(".jpg", ".svg"),      # never a vector image
            "/api/shot/sess-1/" + ref["name"].replace(".jpg", ".html"),     # never a page
            "/api/shot/nobody/" + ref["name"],                              # no such session folder
            "/api/file?path=x",
        ):
            with self.subTest(bad=bad):
                self.assertIsNone(shots.read(bad, self.store))

    def test_a_stored_picture_over_the_size_limit_is_not_served(self):
        ref = self.keep()
        with patch.object(shots, "MAX_BYTES", 10):
            self.assertIsNone(shots.read("/api/shot/sess-1/" + ref["name"], self.store))

    def test_drop_removes_one_session_and_a_bad_id_removes_nothing(self):
        mine = self.keep()
        other = self.keep(sid="sess-2")
        shots.drop("sess-1", self.store)
        self.assertIsNone(shots.read("/api/shot/sess-1/" + mine["name"], self.store))
        self.assertIsNotNone(shots.read("/api/shot/sess-2/" + other["name"], self.store))
        shots.drop("../sess-2", self.store)
        self.assertIsNotNone(shots.read("/api/shot/sess-2/" + other["name"], self.store))


if __name__ == "__main__":
    unittest.main()
