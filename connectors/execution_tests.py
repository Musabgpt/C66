import os
from pathlib import Path
import tempfile
import unittest

from . import execution


class ExecutionTests(unittest.TestCase):
    def test_plan_prefers_termux_for_phone_when_available(self):
        names = {"termux_exec", "sandbox_exec", "bash"}
        result = execution.plan(names, "Build and test this Android APK in Termux")
        self.assertEqual(result["routes"][0]["tool"], "termux_exec")
        self.assertIn("sandbox_exec", [r["tool"] for r in result["routes"]])

    def test_plan_never_invents_optional_backends(self):
        result = execution.plan({"sandbox_exec"}, "Run this in E2B with Appium")
        tools = [r["tool"] for r in result["routes"]]
        self.assertEqual(tools, ["sandbox_exec"])
        self.assertFalse(any("e2b" in t or "appium" in t for t in tools))

    def test_scratch_exec_writes_only_explicit_inputs_and_returns_outputs(self):
        result = execution.run_scratch(
            "cat src/in.txt > out.txt && printf '!done' >> out.txt",
            {"src/in.txt": "hello"},
            10,
        )
        self.assertTrue(result["ok"])
        paths = {row["path"] for row in result["files"]}
        self.assertIn("src/in.txt", paths)
        self.assertIn("out.txt", paths)
        self.assertEqual(result["isolation"], "ephemeral-workdir-not-container")

    def test_rejects_path_escape(self):
        with self.assertRaises(Exception):
            execution.run_scratch("true", {"../escape.txt": "no"}, 5)

    def test_timeout_is_bounded(self):
        result = execution.run_scratch("sleep 2", {}, 1)
        self.assertTrue(result["timed_out"])
        self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main()
