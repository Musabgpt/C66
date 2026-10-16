import hashlib
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from . import evolution as e


class EvolutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.patch = patch.object(e.settings, "HOME", self.temp.name)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.env = patch.dict(
            e.os.environ,
            {
                "NEWAL_PACKAGED_BUILD": "100",
                "NEWAL_UPDATE_COMPAT": e.UPDATE_COMPATIBILITY,
            },
            clear=False,
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        e.os.environ.pop("NEWAL_ACTIVE_REVISION", None)
        self.ctx = SimpleNamespace(root=self.temp.name, session=SimpleNamespace(dirs=[]))

    def prepared(self):
        _, meta = e.prepare(self.ctx, "improve document formatting")
        return meta["candidate"], Path(meta["path"])

    def test_prepare_does_not_replace_running_engine(self):
        cid, p = self.prepared()
        self.assertTrue((p / "newal_code/server.py").exists())
        self.assertIn(str(p), self.ctx.session.dirs)
        self.assertFalse((e.home() / "active.json").exists())
        with self.assertRaises(e.tools.ToolError):
            e.activate(cid)

    def test_unchanged_candidate_cannot_claim_improvement(self):
        cid, _ = self.prepared()
        with self.assertRaises(e.tools.ToolError):
            e.verify(self.ctx, cid)

    def test_failed_checks_prevent_activation(self):
        cid, p = self.prepared()
        (p / "newal_code/new_test.py").write_text("x = 1\n")
        results = [{"command": ["python"], "exit": 1, "output": "failed"}]
        with patch.object(e, "_run_verification", return_value=(False, results, e.digest(p / "newal_code"))):
            _, record = e.verify(self.ctx, cid)
        self.assertEqual(record["status"], "failed")
        with self.assertRaises(e.tools.ToolError):
            e.activate(cid)

    def test_verified_candidate_can_activate_and_rollback_but_last_minute_edit_invalidates_it(self):
        cid, p = self.prepared()
        file = p / "newal_code/new_test.py"
        file.write_text("x = 1\n")
        good = e.digest(p / "newal_code")
        with patch.object(e, "_run_verification", return_value=(True, [], good)):
            e.verify(self.ctx, cid)
        selected = e.activate(cid)
        self.assertTrue(selected["ok"])
        self.assertEqual(selected["state"], "activating")
        self.assertEqual(e.status()["active"]["id"], cid)
        self.assertTrue(e.rollback()["ok"])
        self.assertFalse((e.home() / "active.json").exists())
        file.write_text("x = 2\n")
        with self.assertRaises(e.tools.ToolError):
            e.activate(cid)

    def test_preparation_records_packaged_build_for_safe_updates(self):
        with patch.dict(e.os.environ, {"NEWAL_PACKAGED_BUILD": "217"}):
            cid, _ = self.prepared()
        record = json.loads((e.home() / (cid + ".json")).read_text())
        self.assertEqual(record.get("packaged_build"), "217")
        self.assertEqual(record.get("compatibility_id"), e.UPDATE_COMPATIBILITY)

    def test_original_rollback_selects_packaged_engine_when_no_previous_revision(self):
        e.save(e.home() / "active.json", {
            "id": "old", "state": "active", "health_confirmed": True, "previous": {},
        })
        self.assertTrue(e.rollback()["ok"])
        self.assertFalse((e.home() / "active.json").exists())

    def test_verify_uses_stable_packaged_regression_source(self):
        cid, p = self.prepared()
        (p / "newal_code/new_test.py").write_text("x = 1\n")
        record = e._record(cid)
        commands = e._trusted_commands(p, record)
        suite = [
            command for command in commands
            if isinstance(command, list) and "-c" in command and "loadTestsFromModule" in command[-1]
        ]
        self.assertEqual(len(suite), 1)
        for name in (
            "memory_tests", "mcp_bundles_tests", "runtime_manager_tests",
            "termux_bridge_tests", "task_supervisor_tests", "provider_pool_tests",
        ):
            self.assertIn(name, suite[0][-1])
        self.assertTrue(any(
            "runtime startup and critical API probe passed" in command[-1]
            for command in commands if isinstance(command, list) and "-c" in command
        ))

    def test_symlink_candidate_cannot_be_activated(self):
        cid, p = self.prepared()
        outside = Path(self.temp.name) / "outside.py"
        outside.write_text("x = 1\n")
        (p / "newal_code/alias.py").symlink_to(outside)
        with self.assertRaises(e.tools.ToolError):
            e.digest(p / "newal_code")

    def test_unchecked_import_sibling_is_rejected(self):
        cid, p = self.prepared()
        (p / "sitecustomize.py").write_text('raise RuntimeError("unchecked")\n')
        with self.assertRaises(e.tools.ToolError):
            e.candidate(cid)

    def test_candidate_from_an_older_apk_cannot_activate(self):
        cid, p = self.prepared()
        (p / "newal_code/probe.py").write_text("x = 1\n")
        good = e.digest(p / "newal_code")
        with patch.object(e, "_run_verification", return_value=(True, [], good)):
            e.verify(self.ctx, cid)
        with patch.dict(e.os.environ, {"NEWAL_PACKAGED_BUILD": "101"}):
            with self.assertRaises(e.tools.ToolError):
                e.activate(cid)

    def _bundle(self, version=101, revision="1" * 24, extra=None, downgrade_policy=None):
        root = Path(self.temp.name) / ("bundle-" + revision)
        engine = root / "engine"
        engine.mkdir(parents=True)
        (engine / "__init__.py").write_text("# candidate\n")
        (engine / "server.py").write_text("VALUE = 1\n")
        files = {}
        for file in sorted(engine.rglob("*")):
            if file.is_file():
                files["newal_code/" + file.relative_to(engine).as_posix()] = hashlib.sha256(
                    file.read_bytes()
                ).hexdigest()
        manifest = {
            "schema": 1,
            "revision": revision,
            "version_code": version,
            "channel": "candidate",
            "compatibility_id": e.UPDATE_COMPATIBILITY,
            "source_repository": e.UPDATE_REPOSITORY,
            "source_commit": "a" * 40,
            "provenance": {"github_run_id": "123"},
            "native_required": False,
            "tree_sha256": e.digest(engine),
            "files": files,
            "ci_checks": {
                "node_ui": True, "memory_agent": True,
                "connector_runtime": True, "gradle": True,
            },
        }
        if downgrade_policy:
            manifest["downgrade_policy"] = downgrade_policy
        archive = root / "update.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("manifest.json", json.dumps(manifest))
            for file in sorted(engine.rglob("*")):
                if file.is_file():
                    z.write(file, "newal_code/" + file.relative_to(engine).as_posix())
            for name, content in extra or []:
                z.writestr(name, content)
        return archive, hashlib.sha256(archive.read_bytes()).hexdigest(), manifest

    def test_hash_mismatch_rejects_bundle_and_does_not_change_active_revision(self):
        e.save(e.home() / "active.json", {
            "id": "legacy", "state": "active", "health_confirmed": True,
        })
        before = (e.home() / "active.json").read_bytes()
        archive, _, _ = self._bundle()
        with self.assertRaisesRegex(e.tools.ToolError, "SHA-256 mismatch"):
            e._MANAGER.stage(str(archive), "0" * 64)
        self.assertEqual((e.home() / "active.json").read_bytes(), before)
        self.assertEqual(
            e._read_json(e.home() / "update-state.json")["state"], "failed_verification"
        )

    def test_corrupted_staged_update_is_rejected_before_activation(self):
        archive, sha, manifest = self._bundle()
        record = e._MANAGER.stage(str(archive), sha)
        target = e.candidate(record["id"]) / "newal_code/server.py"
        target.write_text("tampered = True\n")
        with self.assertRaisesRegex(e.tools.ToolError, "changed after download"):
            e._MANAGER.verify(record["id"])
        self.assertFalse((e.home() / "active.json").exists())
        self.assertEqual(
            e._read_json(e.home() / (manifest["revision"] + ".json"))["status"],
            "failed_verification",
        )

    def test_update_verification_failure_never_changes_active_pointer(self):
        archive, sha, _ = self._bundle()
        record = e._MANAGER.stage(str(archive), sha)
        e.save(e.home() / "active.json", {
            "id": "previous", "state": "active", "health_confirmed": True,
        })
        before = (e.home() / "active.json").read_bytes()
        with patch.object(
            e, "_run_verification",
            return_value=(
                False,
                [{"exit": 1, "output": "contract failed"}],
                record["tree_sha256"],
            ),
        ):
            result = e._MANAGER.verify(record["id"])
        self.assertEqual(result["status"], "failed_verification")
        self.assertEqual((e.home() / "active.json").read_bytes(), before)

    def test_older_candidate_is_refused_without_two_part_downgrade_policy(self):
        archive, sha, _ = self._bundle(version=99)
        with self.assertRaisesRegex(e.tools.ToolError, "older or equal"):
            e._MANAGER.stage(str(archive), sha)
        archive2, sha2, _ = self._bundle(
            version=99, revision="2" * 24, downgrade_policy="explicit"
        )
        with self.assertRaisesRegex(e.tools.ToolError, "older or equal"):
            e._MANAGER.stage(str(archive2), sha2, allow_downgrade=False)

    def test_explicit_downgrade_policy_requires_bundle_and_caller_opt_in(self):
        archive, sha, _ = self._bundle(
            version=99, revision="3" * 24, downgrade_policy="explicit"
        )
        record = e._MANAGER.stage(str(archive), sha, allow_downgrade=True)
        self.assertEqual(record["version_code"], 99)
        self.assertTrue(record["downgrade_allowed"])

    def test_native_only_archive_entry_is_rejected_as_apk_update(self):
        archive, _, manifest = self._bundle(
            extra=[("android-lite/app/native.so", b"x")]
        )
        sha = hashlib.sha256(archive.read_bytes()).hexdigest()
        with self.assertRaisesRegex(e.tools.ToolError, "APK update"):
            e._MANAGER.stage(str(archive), sha)
        self.assertFalse(
            (e.home() / "candidates" / manifest["revision"]).exists()
        )

    def test_manifest_native_required_is_rejected_as_apk_update(self):
        archive, _, manifest = self._bundle(revision="4" * 24)
        root = archive.parent
        manifest["native_required"] = True
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("manifest.json", json.dumps(manifest))
            engine = root / "engine"
            for file in sorted(engine.rglob("*")):
                if file.is_file():
                    z.write(
                        file, "newal_code/" + file.relative_to(engine).as_posix()
                    )
        sha = hashlib.sha256(archive.read_bytes()).hexdigest()
        with self.assertRaisesRegex(e.tools.ToolError, "APK update"):
            e._MANAGER.stage(str(archive), sha)

    def test_interrupted_atomic_save_keeps_previous_active_pointer(self):
        path = e.home() / "active.json"
        old = {"id": "a" * 24, "state": "active", "health_confirmed": True}
        e.save(path, old)
        with patch.object(e.os, "replace", side_effect=OSError("power loss")):
            with self.assertRaises(OSError):
                e.save(path, {"id": "b" * 24, "state": "activating"})
        self.assertEqual(e._read_json(path), old)
        self.assertEqual(list(path.parent.glob("active.json.tmp-*")), [])

    def test_saved_active_state_does_not_claim_success_when_revision_is_not_running(self):
        cid = "5" * 24
        p = e.home() / "candidates" / cid / "newal_code"
        p.mkdir(parents=True)
        (p / "__init__.py").write_text("")
        (p / "server.py").write_text("")
        d = e.digest(p)
        e.save(e.home() / (cid + ".json"), {
            "id": cid, "kind": "update", "status": "ready_to_activate",
            "verified_digest": d, "compatibility_id": e.UPDATE_COMPATIBILITY,
            "version_code": 101, "files": {},
        })
        e.save(e.home() / "active.json", {
            "id": cid, "path": str(p.parent), "kind": "update",
            "state": "active", "health_confirmed": True,
            "compatibility_id": e.UPDATE_COMPATIBILITY, "version_code": 101,
            "verified_digest": d,
        })
        e.os.environ.pop("NEWAL_ACTIVE_REVISION", None)
        self.assertEqual(e.status()["update"]["state"], "failed_activation")

    def test_rollback_restores_last_verified_revision_not_arbitrary_path(self):
        old_id, new_id = "6" * 24, "7" * 24
        old_dir = e.home() / "candidates" / old_id / "newal_code"
        new_dir = e.home() / "candidates" / new_id / "newal_code"
        for directory, value in ((old_dir, "old"), (new_dir, "new")):
            directory.mkdir(parents=True)
            (directory / "__init__.py").write_text("")
            (directory / "server.py").write_text("VALUE=%r\n" % value)
        old_digest, new_digest = e.digest(old_dir), e.digest(new_dir)
        for cid, directory, digest, version in (
            (old_id, old_dir, old_digest, 101),
            (new_id, new_dir, new_digest, 102),
        ):
            e.save(e.home() / (cid + ".json"), {
                "id": cid, "kind": "update", "status": "ready_to_activate",
                "verified_digest": digest,
                "compatibility_id": e.UPDATE_COMPATIBILITY,
                "version_code": version, "files": {},
            })
        previous = {
            "id": old_id, "path": str(old_dir.parent), "kind": "update",
            "state": "active", "health_confirmed": True,
            "compatibility_id": e.UPDATE_COMPATIBILITY, "version_code": 101,
            "verified_digest": old_digest,
        }
        e.save(e.home() / "active.json", {
            "id": new_id, "path": str(new_dir.parent), "kind": "update",
            "state": "active", "health_confirmed": True,
            "compatibility_id": e.UPDATE_COMPATIBILITY, "version_code": 102,
            "verified_digest": new_digest, "previous": previous,
        })
        e.os.environ["NEWAL_ACTIVE_REVISION"] = new_id
        result = e.rollback()
        self.assertTrue(result["ok"])
        self.assertEqual(e._read_json(e.home() / "active.json")["id"], old_id)
        self.assertTrue(e._selection_valid(e._read_json(e.home() / "active.json")))

    def test_hot_reload_activation_never_overlays_python_or_native_files(self):
        cid = "8" * 24
        p = e.home() / "candidates" / cid / "newal_code"
        p.mkdir(parents=True)
        (p / "__init__.py").write_text("")
        (p / "server.py").write_text("")
        prompt = p / "agent_prompt.md"
        prompt.write_text("new prompt")
        d = e.digest(p)
        files = {
            "newal_code/__init__.py": hashlib.sha256(
                (p / "__init__.py").read_bytes()
            ).hexdigest(),
            "newal_code/server.py": hashlib.sha256(
                (p / "server.py").read_bytes()
            ).hexdigest(),
            "newal_code/agent_prompt.md": hashlib.sha256(
                prompt.read_bytes()
            ).hexdigest(),
        }
        e.save(e.home() / (cid + ".json"), {
            "id": cid, "kind": "update", "status": "ready_to_activate",
            "verified_digest": d, "compatibility_id": e.UPDATE_COMPATIBILITY,
            "version_code": 101, "restart_required": False, "files": files,
        })
        with patch.object(e, "_current_version_code", return_value=100):
            result = e.activate(cid)
        self.assertFalse(result["restart_required"])
        self.assertEqual(e.dynamic_file("agent_prompt.md"), prompt)
        self.assertIsNone(e.dynamic_file("server.py"))
        self.assertIsNone(e.dynamic_file("../android/native.so"))


if __name__ == "__main__":
    unittest.main()
