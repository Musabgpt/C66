from pathlib import Path
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch

from . import git_workspace


class GitWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.email", "musabai@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.name", "MusabAI Tests"], check=True)

    def ctx(self):
        return types.SimpleNamespace(session=types.SimpleNamespace(root=str(self.root)))

    def test_git_helper_reads_status(self):
        (self.root / "a.txt").write_text("hello", encoding="utf-8")
        result = git_workspace._git(self.root, ["status", "--porcelain=v1"])
        self.assertTrue(result["ok"])
        self.assertIn("a.txt", result["stdout"])

    def test_paths_reject_escape(self):
        with self.assertRaises(Exception):
            git_workspace._paths(["../outside"])

    def test_local_commit_does_not_push_or_change_remote(self):
        (self.root / "a.txt").write_text("hello", encoding="utf-8")
        git_workspace._git(self.root, ["add", "--", "a.txt"])
        result = git_workspace._git(self.root, ["commit", "-m", "test commit"])
        self.assertTrue(result["ok"])
        remotes = git_workspace._git(self.root, ["remote"])
        self.assertEqual(remotes["stdout"].strip(), "")

    def test_missing_git_fails_honestly(self):
        with patch("shutil.which", return_value=None):
            with self.assertRaises(Exception):
                git_workspace._git(self.root, ["status"])


if __name__ == "__main__":
    unittest.main()
