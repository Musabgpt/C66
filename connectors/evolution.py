"""Verified self-evolution and staged self-update manager for MusabAI.

The running engine is never edited in place. Candidates live in immutable revision
directories, verification is executed from the packaged/stable engine when available,
and activation is an atomic pointer switch. Android's native CandidateSelection
validates the same digest before a Python revision is placed on PYTHONPATH.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import zipfile

from . import settings, tools

NAMES = [
    "self_evolve",
    "self_evolve_verify",
    "self_evolve_activate",
    "self_update_status",
    "self_update_stage",
    "self_update_verify",
    "memory_learn",
]

UPDATE_SCHEMA = 1
UPDATE_COMPATIBILITY = "action125-python314-v1"
UPDATE_REPOSITORY = "Musabgpt/NewAl-Cloud"
UPDATE_CHANNELS = {"stable", "candidate"}
MAX_UPDATE_BYTES = 64 * 1024 * 1024
_ALLOWED_DOWNLOAD_HOSTS = {
    "github.com",
    "api.github.com",
    "objects.githubusercontent.com",
    "raw.githubusercontent.com",
    "release-assets.githubusercontent.com",
}
HOT_FILES = {
    "agent_prompt.md",
    "ui/app.js",
    "ui/connectors.js",
    "ui/connectors.css",
    "ui/workspace.js",
    "ui/mcp_ui.js",
    "ui/index.html",
    "ui/style.css",
    "ui/i18n.js",
    "ui/markdown.js",
    "ui/icon.svg",
    "ui/favicon.ico",
}
TRUSTED_TESTS = (
    "tool_protocol_tests",
    "document_tests", "evolution_tests", "addon_tests", "memory_tests", "autonomy_tests",
    "prompt_tests", "workbench_tests", "mcp_config_tests", "mcp_bundles_tests",
    "mcp_registry_tests", "browser_router_tests", "search_router_tests",
    "document_engine_tests", "project_rag_tests", "orchestrator_tests", "execution_tests",
    "runtime_manager_tests", "termux_bridge_tests", "git_workspace_tests",
    "observability_tests", "task_state_tests", "task_supervisor_tests",
    "provider_pool_tests", "free_provider_adapters_tests", "provider_keys_tests",
    "automation_tests", "auto_update_tests", "managed_linux_tests",
)


def home():
    p = Path(settings.HOME) / "evolution"
    p.mkdir(parents=True, exist_ok=True)
    return p


def save(path, data):
    """Durable atomic JSON replacement; a crash cannot leave a half-written pointer."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp-" + secrets.token_hex(4))
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")
    try:
        with open(tmp, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        try:
            fd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            pass
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def _read_json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def digest(path):
    if path.is_symlink():
        raise tools.ToolError("Engine candidates cannot contain symbolic links")
    h = hashlib.sha256()
    for f in sorted(path.rglob("*")):
        if f.is_symlink():
            raise tools.ToolError("Engine candidates cannot contain symbolic links")
        if f.is_file() and "__pycache__" not in f.parts and ".git" not in f.parts:
            h.update(f.relative_to(path).as_posix().encode())
            h.update(f.read_bytes())
    return h.hexdigest()


def _hashes(path):
    result = {}
    for f in sorted(path.rglob("*")):
        if f.is_symlink():
            raise tools.ToolError("Engine candidates cannot contain symbolic links")
        if f.is_file() and "__pycache__" not in f.parts and ".git" not in f.parts:
            result[f.relative_to(path).as_posix()] = _sha256_file(f)
    return result


def candidate(cid):
    if not isinstance(cid, str) or len(cid) != 24 or any(c not in "0123456789abcdef" for c in cid):
        raise tools.ToolError("Invalid candidate ID")
    p = home() / "candidates" / cid
    if p.is_symlink() or not p.is_dir() or p.resolve().parent != (home() / "candidates").resolve():
        raise tools.ToolError("Candidate does not exist")
    for child in p.iterdir():
        if child.name not in ("newal_code", "test-home") or child.is_symlink() or not child.is_dir():
            raise tools.ToolError("Keep candidate code inside newal_code and check outputs inside test-home")
    return p


def _record(cid):
    record = _read_json(home() / (cid + ".json"))
    if record.get("id") != cid:
        raise tools.ToolError("Candidate record is missing or invalid")
    return record


def _packaged_build():
    return os.environ.get("NEWAL_PACKAGED_BUILD", "host")


def _compatibility():
    return os.environ.get("NEWAL_UPDATE_COMPAT", UPDATE_COMPATIBILITY)


def _int_version(value):
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def _active_marker():
    return _read_json(home() / "active.json")


def _running_revision():
    value = os.environ.get("NEWAL_ACTIVE_REVISION", "")
    return value if len(value) == 24 and all(c in "0123456789abcdef" for c in value) else ""


def _current_version_code():
    running = _running_revision()
    if running:
        record = _read_json(home() / (running + ".json"))
        if record.get("kind") == "update":
            return _int_version(record.get("version_code"))
    return _int_version(_packaged_build())


def _set_state(state, **extra):
    allowed = {
        "up_to_date", "update_available", "downloading", "staged", "verifying",
        "ready_to_activate", "activating", "active", "rollback_available",
        "failed_verification", "failed_activation", "rolled_back",
    }
    if state not in allowed:
        raise ValueError("invalid update state")
    data = {"state": state, "updated_at": time.time()}
    data.update(extra)
    save(home() / "update-state.json", data)
    return data


def _trusted_root():
    packaged = os.environ.get("NEWAL_PACKAGED_ENGINE")
    if packaged:
        root = Path(packaged) / "newal_code"
        if root.is_dir():
            return root
    return Path(__file__).resolve().parent


def _trusted_commands(p, record):
    trusted = _trusted_root()
    tests = tuple(record.get("trusted_tests") or TRUSTED_TESTS)
    runner = """import importlib.util, pathlib, sys, unittest
base = pathlib.Path(%r)
names = %r
suite = unittest.TestSuite()
for name in names:
    file = base / (name + '.py')
    if not file.is_file():
        raise SystemExit('trusted test missing: ' + name)
    spec = importlib.util.spec_from_file_location('newal_code._baseline_' + name, file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(module))
result = unittest.TextTestRunner(verbosity=1).run(suite)
sys.exit(0 if result.wasSuccessful() and result.testsRun else 1)
""" % (str(trusted), tests)
    startup = """import json, threading, urllib.request
from newal_code import server
httpd, _ = server.serve(0, open_browser=False, host='127.0.0.1')
thread = threading.Thread(target=httpd.serve_forever, daemon=True); thread.start()
port = httpd.server_address[1]
headers = {'X-NewAl-Key': httpd.key}
for path in ('/api/health', '/api/evolution'):
    req = urllib.request.Request('http://127.0.0.1:%d%s' % (port, path), headers=headers)
    with urllib.request.urlopen(req, timeout=5) as response:
        assert response.status == 200
        json.loads(response.read().decode('utf-8'))
httpd.shutdown(); httpd.server_close()
print('runtime startup and critical API probe passed')
"""
    return [
        [sys.executable, "-m", "compileall", "-q", str(p / "newal_code")],
        [sys.executable, "-c",
         "from newal_code import documents,evolution,server,agent,runtime_manager,mcp_bundles,memory_api; "
         "assert documents.NAMES and evolution.NAMES"],
        [sys.executable, "-c", runner],
        [sys.executable, "-c", startup],
    ]


def _run_verification(p, record):
    before = digest(p / "newal_code")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(p) + os.pathsep + str(Path(__file__).resolve().parent.parent)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["NEWAL_DISABLE_AUTOMATION"] = "1"
    env["NEWAL_CODE_HOME"] = str(p / "test-home")
    env["NEWAL_MEMORY_HOME"] = str(p / "test-home" / "memory")
    results = []
    for command in _trusted_commands(p, record) + list(record.get("checks") or []):
        try:
            proc = subprocess.run(
                command, cwd=p, env=env, shell=isinstance(command, str),
                capture_output=True, text=True, timeout=180,
            )
            results.append({
                "command": command,
                "exit": proc.returncode,
                "output": (proc.stdout + proc.stderr)[-12000:],
            })
        except subprocess.TimeoutExpired:
            results.append({"command": command, "exit": 124, "output": "Verification timed out"})
        if results[-1]["exit"]:
            break
    after = digest(p / "newal_code")
    return all(x["exit"] == 0 for x in results) and before == after, results, after


@tools.tool(
    "self_evolve",
    "Prepare an isolated copy of this Python engine for improvement. Edit its files using normal tools, then call self_evolve_verify. Does not modify the running engine or Android APK.",
    {
        "goal": tools._s("specific improvement goal"),
        "checks": {
            "type": "array", "items": {"type": "string"},
            "description": "additional test commands for verification",
        },
    },
    ["goal"], "exec",
)
def prepare(ctx, goal, checks=None):
    cid = secrets.token_hex(12)
    p = home() / "candidates" / cid
    p.mkdir(parents=True)
    source = Path(__file__).resolve().parent
    shutil.copytree(source, p / "newal_code", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    if ctx.session is not None and str(p) not in ctx.session.dirs:
        ctx.session.dirs.append(str(p))
    record = {
        "id": cid,
        "kind": "local_evolution",
        "goal": str(goal)[:2000],
        "created": time.time(),
        "status": "prepared",
        "source_digest": digest(p / "newal_code"),
        "checks": checks or [],
        "packaged_build": _packaged_build(),
        "compatibility_id": _compatibility(),
        "base_version_code": _current_version_code(),
    }
    save(home() / (cid + ".json"), record)
    return (
        json.dumps(dict(
            record, path=str(p),
            instruction="Edit newal_code in this candidate, then run self_evolve_verify. Call self_evolve_activate after verification to schedule activation when idle.",
        ), ensure_ascii=False),
        {"candidate": cid, "path": str(p)},
    )


@tools.tool(
    "self_evolve_verify",
    "Verify an edited self-improvement candidate with syntax, isolated imports, runtime startup and packaged regression tests. Test code comes from the stable packaged engine. Activation requires an unchanged verified digest.",
    {"candidate": tools._s("candidate ID")},
    ["candidate"], "exec",
)
def verify(ctx, candidate):
    p = globals()["candidate"](candidate)
    file = home() / (candidate + ".json")
    record = _record(candidate)
    if record.get("kind") == "update":
        raise tools.ToolError("Use self_update_verify for a staged update bundle")
    before = digest(p / "newal_code")
    if before == record["source_digest"]:
        raise tools.ToolError("No engine changes yet; edit the candidate before verification")
    ok, results, after = _run_verification(p, record)
    record.update(
        status="verified" if ok else "failed",
        verified_digest=after, results=results, verified_at=time.time(),
    )
    save(file, record)
    return json.dumps(record, ensure_ascii=False), record


def _safe_member(name):
    path = PurePosixPath(name)
    if not name or path.is_absolute() or ".." in path.parts or "\\" in name:
        raise tools.ToolError("Unsafe update archive path")
    return path


def _zip_is_link(info):
    return ((info.external_attr >> 16) & 0o170000) == 0o120000


def _manifest_from_archive(archive):
    try:
        raw = archive.read("manifest.json")
    except KeyError as exc:
        raise tools.ToolError("Update bundle is missing manifest.json") from exc
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise tools.ToolError("Update manifest is invalid JSON") from exc
    if not isinstance(manifest, dict):
        raise tools.ToolError("Update manifest must be an object")
    return manifest


def _validate_manifest(manifest, allow_downgrade):
    revision = manifest.get("revision")
    commit = manifest.get("source_commit")
    tree = manifest.get("tree_sha256")
    if manifest.get("schema") != UPDATE_SCHEMA:
        raise tools.ToolError("Unsupported update manifest schema")
    if not isinstance(revision, str) or len(revision) != 24 or any(c not in "0123456789abcdef" for c in revision):
        raise tools.ToolError("Update revision must be 24 lowercase hex characters")
    if manifest.get("source_repository") != UPDATE_REPOSITORY:
        raise tools.ToolError("Update provenance repository is not trusted")
    if not isinstance(commit, str) or len(commit) != 40 or any(c not in "0123456789abcdef" for c in commit):
        raise tools.ToolError("Update provenance commit is invalid")
    if manifest.get("channel") not in UPDATE_CHANNELS:
        raise tools.ToolError("Update channel is not allowed")
    if manifest.get("compatibility_id") != _compatibility():
        raise tools.ToolError("This update needs a different APK/native compatibility level")
    if manifest.get("native_required"):
        raise tools.ToolError("This candidate changes native-only files and requires an APK update")
    if not isinstance(tree, str) or len(tree) != 64 or any(c not in "0123456789abcdef" for c in tree):
        raise tools.ToolError("Update tree hash is invalid")
    version = manifest.get("version_code")
    if not isinstance(version, int) or isinstance(version, bool) or version <= 0:
        raise tools.ToolError("Update version_code must be a positive integer")
    current = _current_version_code()
    if version <= current and not (allow_downgrade and manifest.get("downgrade_policy") == "explicit"):
        raise tools.ToolError("Refusing an older or equal update without explicit downgrade policy")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise tools.ToolError("Update manifest has no file integrity map")
    for name, sha in files.items():
        p = _safe_member(str(name))
        if not str(p).startswith("newal_code/"):
            raise tools.ToolError("Native/outside-engine file requires an APK update")
        if not isinstance(sha, str) or len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
            raise tools.ToolError("Update file hash is invalid: " + str(name))


def _changed_files(new_root):
    current_root = Path(__file__).resolve().parent
    new = _hashes(new_root)
    old = _hashes(current_root)
    return sorted(
        [name for name in set(new) | set(old) if new.get(name) != old.get(name)],
        key=str.casefold,
    )


class SelfUpdateManager:
    """Single staged-update path shared by API, tools and Android activation."""

    def stage(self, source, expected_sha256, allow_downgrade=False):
        expected_sha256 = str(expected_sha256 or "").lower()
        if len(expected_sha256) != 64 or any(c not in "0123456789abcdef" for c in expected_sha256):
            raise tools.ToolError("A lowercase SHA-256 for the update archive is required")
        local = None
        temporary = None
        try:
            parsed = urllib.parse.urlsplit(str(source))
            if parsed.scheme:
                if parsed.scheme != "https" or parsed.hostname not in _ALLOWED_DOWNLOAD_HOSTS:
                    raise tools.ToolError("Updates may be downloaded only from the approved GitHub channel")
                _set_state("downloading", source=parsed.hostname)
                downloads = home() / "downloads"
                downloads.mkdir(parents=True, exist_ok=True)
                temporary = downloads / ("update-" + secrets.token_hex(8) + ".zip")
                request = urllib.request.Request(source, headers={"User-Agent": "MusabAI-SelfUpdate/1"})
                total = 0
                with urllib.request.urlopen(request, timeout=30) as response, open(temporary, "wb") as out:
                    final_url = urllib.parse.urlsplit(response.geturl())
                    if final_url.scheme != "https" or final_url.hostname not in _ALLOWED_DOWNLOAD_HOSTS:
                        raise tools.ToolError("Update download redirected outside the approved GitHub channel")
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > MAX_UPDATE_BYTES:
                            raise tools.ToolError("Update bundle exceeds the size limit")
                        out.write(chunk)
                local = temporary
            else:
                local = Path(source).expanduser().resolve()
                if not local.is_file():
                    raise tools.ToolError("Update bundle does not exist")
                if local.stat().st_size > MAX_UPDATE_BYTES:
                    raise tools.ToolError("Update bundle exceeds the size limit")

            actual_archive_sha = _sha256_file(local)
            if actual_archive_sha != expected_sha256:
                _set_state("failed_verification", error="archive hash mismatch")
                raise tools.ToolError("Update archive SHA-256 mismatch")

            with zipfile.ZipFile(local) as archive:
                manifest = _manifest_from_archive(archive)
                _validate_manifest(manifest, allow_downgrade)
                _set_state(
                    "update_available",
                    candidate=manifest["revision"],
                    version_code=manifest["version_code"],
                    channel=manifest["channel"],
                )
                names = []
                total_uncompressed = 0
                for info in archive.infolist():
                    path = _safe_member(info.filename)
                    if info.is_dir():
                        continue
                    if _zip_is_link(info):
                        raise tools.ToolError("Update archive cannot contain symbolic links")
                    if str(path) != "manifest.json" and not str(path).startswith("newal_code/"):
                        raise tools.ToolError("Native/outside-engine file requires an APK update")
                    total_uncompressed += info.file_size
                    if total_uncompressed > MAX_UPDATE_BYTES:
                        raise tools.ToolError("Expanded update bundle exceeds the size limit")
                    names.append(str(path))
                actual_files = sorted(x for x in names if x != "manifest.json")
                expected_files = sorted(manifest["files"])
                if actual_files != expected_files:
                    raise tools.ToolError("Update archive contents do not match the manifest")

                revisions = home() / "candidates"
                revisions.mkdir(parents=True, exist_ok=True)
                final = revisions / manifest["revision"]
                if final.exists():
                    existing = _read_json(home() / (manifest["revision"] + ".json"))
                    if (existing.get("archive_sha256") == actual_archive_sha
                            and existing.get("source_commit") == manifest.get("source_commit")):
                        return existing
                    raise tools.ToolError("A different candidate already uses this revision ID")
                stage = revisions / (".stage-" + manifest["revision"] + "-" + secrets.token_hex(4))
                stage.mkdir(parents=True)
                try:
                    engine = stage / "newal_code"
                    engine.mkdir()
                    for info in archive.infolist():
                        path = _safe_member(info.filename)
                        if info.is_dir() or str(path) == "manifest.json":
                            continue
                        rel = PurePosixPath(*path.parts[1:])
                        out = engine.joinpath(*rel.parts)
                        out.parent.mkdir(parents=True, exist_ok=True)
                        with archive.open(info) as src, open(out, "wb") as dst:
                            shutil.copyfileobj(src, dst)
                    actual_hashes = {"newal_code/" + name: sha for name, sha in _hashes(engine).items()}
                    if actual_hashes != manifest["files"]:
                        raise tools.ToolError("Staged file hash mismatch")
                    tree = digest(engine)
                    if tree != manifest["tree_sha256"]:
                        raise tools.ToolError("Staged tree hash mismatch")
                    changed = _changed_files(engine)
                    hot = bool(changed) and all(name in HOT_FILES for name in changed)
                    record = {
                        "id": manifest["revision"],
                        "kind": "update",
                        "goal": "Update %s from %s" % (manifest["version_code"], manifest["source_commit"][:12]),
                        "created": time.time(),
                        "status": "staged",
                        "state": "staged",
                        "version_code": manifest["version_code"],
                        "channel": manifest["channel"],
                        "compatibility_id": manifest["compatibility_id"],
                        "source_repository": manifest["source_repository"],
                        "source_commit": manifest["source_commit"],
                        "provenance": manifest.get("provenance") or {},
                        "archive_sha256": actual_archive_sha,
                        "source_digest": tree,
                        "tree_sha256": tree,
                        "files": manifest["files"],
                        "changed_files": changed,
                        "hot_reloadable": hot,
                        "restart_required": not hot,
                        "ci_checks": manifest.get("ci_checks") or {},
                        "downgrade_policy": manifest.get("downgrade_policy") or "",
                        "downgrade_allowed": bool(
                            allow_downgrade and manifest.get("downgrade_policy") == "explicit"
                        ),
                    }
                    os.replace(stage, final)
                    save(home() / (manifest["revision"] + ".json"), record)
                finally:
                    if stage.exists():
                        shutil.rmtree(stage, ignore_errors=True)

            _set_state(
                "staged", candidate=record["id"], version_code=record["version_code"],
                restart_required=record["restart_required"],
            )
            return record
        except tools.ToolError as exc:
            _set_state("failed_verification", error=str(exc))
            raise
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            _set_state("failed_verification", error=str(exc))
            raise tools.ToolError("Could not stage update: " + str(exc)) from exc
        finally:
            if temporary is not None:
                try:
                    temporary.unlink()
                except OSError:
                    pass

    def verify(self, cid):
        p = candidate(cid)
        record = _record(cid)
        if record.get("kind") != "update":
            raise tools.ToolError("Candidate is not a staged update")
        if record.get("status") not in ("staged", "failed_verification", "ready_to_activate"):
            raise tools.ToolError("Update is not in a verifiable state")
        if digest(p / "newal_code") != record.get("tree_sha256"):
            record.update(status="failed_verification", state="failed_verification")
            save(home() / (cid + ".json"), record)
            _set_state("failed_verification", candidate=cid, error="staged tree changed")
            raise tools.ToolError("Staged update changed after download")
        _set_state("verifying", candidate=cid, version_code=record.get("version_code"))
        record["state"] = "verifying"
        save(home() / (cid + ".json"), record)
        ok, results, after = _run_verification(p, record)
        if after != record.get("tree_sha256"):
            ok = False
            results.append({
                "command": "integrity-after-tests", "exit": 1,
                "output": "candidate changed during verification",
            })
        record.update(
            status="ready_to_activate" if ok else "failed_verification",
            state="ready_to_activate" if ok else "failed_verification",
            verified_digest=after, results=results, verified_at=time.time(),
        )
        save(home() / (cid + ".json"), record)
        _set_state(
            record["state"], candidate=cid, version_code=record.get("version_code"),
            error="" if ok else (results[-1]["output"][-1000:] if results else "verification failed"),
        )
        return record


_MANAGER = SelfUpdateManager()


@tools.tool(
    "self_update_stage",
    "Stage a MusabAI update ZIP without touching the running revision. A SHA-256 is mandatory. HTTPS downloads are limited to the approved GitHub channel. Native-only files are rejected and require an APK update.",
    {
        "source": tools._s("local ZIP path or approved GitHub HTTPS URL"),
        "sha256": tools._s("expected lowercase SHA-256 of the ZIP"),
        "allow_downgrade": {
            "type": "boolean",
            "description": "explicitly opt in only when the bundle also declares downgrade_policy=explicit",
        },
    },
    ["source", "sha256"], "exec",
)
def stage_update(ctx, source, sha256, allow_downgrade=False):
    record = _MANAGER.stage(source, sha256, bool(allow_downgrade))
    return json.dumps(record, ensure_ascii=False), {
        "candidate": record["id"], "state": record["state"],
    }


@tools.tool(
    "self_update_verify",
    "Verify a staged update using the stable packaged verification layer: syntax/imports, runtime startup/critical API probe and packaged Memory/Agent/MCP/Runtime regressions. It does not activate the update.",
    {"candidate": tools._s("staged update revision ID")},
    ["candidate"], "exec",
)
def verify_update(ctx, candidate):
    record = _MANAGER.verify(candidate)
    return json.dumps(record, ensure_ascii=False), {
        "candidate": candidate, "state": record["state"],
    }


@tools.tool(
    "memory_learn",
    "Remember a useful user preference or verified lesson with supporting evidence, for later tasks.",
    {
        "topic": tools._s("short topic"),
        "lesson": tools._s("preference or lesson"),
        "evidence": tools._s("why this lesson is supported"),
    },
    ["topic", "lesson", "evidence"], "edit",
)
def learn(ctx, topic, lesson, evidence):
    from .autonomy import memory_for
    store = memory_for(ctx.root)
    try:
        learned = store.learn(topic, lesson, evidence)
    finally:
        store.close()
    if learned is None:
        return "Memory is disabled or the lesson is empty; nothing saved.", {"memory": False}
    return "Remembered lesson: " + str(topic), {"memory": True, "id": learned}


def _selection_snapshot(marker):
    if not marker:
        return {}
    keep = (
        "id", "path", "kind", "packaged_build", "compatibility_id", "version_code",
        "verified_digest", "state", "health_confirmed", "activated", "healthy_at",
        "downgrade_allowed",
    )
    return {key: marker[key] for key in keep if key in marker}


def _selection_valid(marker):
    cid = marker.get("id")
    if not cid:
        return False
    try:
        p = candidate(cid)
        record = _record(cid)
    except Exception:
        return False
    verified = record.get("status") in ("verified", "ready_to_activate")
    if not verified or digest(p / "newal_code") != record.get("verified_digest"):
        return False
    if record.get("kind") == "update":
        return (
            marker.get("compatibility_id") == _compatibility()
            and record.get("compatibility_id") == _compatibility()
        )
    return (
        marker.get("packaged_build") == _packaged_build()
        and record.get("packaged_build") == _packaged_build()
    )


def activate(cid):
    p = candidate(cid)
    record = _record(cid)
    if record.get("kind") == "update":
        if record.get("status") != "ready_to_activate":
            raise tools.ToolError("Update must pass verification before activation")
        if record.get("compatibility_id") != _compatibility():
            raise tools.ToolError("Update is not compatible with this APK")
        if (_int_version(record.get("version_code")) <= _current_version_code()
                and not record.get("downgrade_allowed")):
            raise tools.ToolError("Refusing activation because this is not newer than the running revision")
    elif record.get("status") != "verified":
        raise tools.ToolError("Candidate must pass verification again after its last edit")

    if digest(p / "newal_code") != record.get("verified_digest"):
        raise tools.ToolError("Candidate must pass verification again after its last edit")
    if record.get("kind") != "update" and record.get("packaged_build") != _packaged_build():
        raise tools.ToolError("This candidate belongs to a different app build; prepare and verify a new one")

    previous_marker = _active_marker()
    previous = (
        _selection_snapshot(previous_marker)
        if previous_marker.get("state", "active") == "active"
        and previous_marker.get("health_confirmed", True)
        and _selection_valid(previous_marker)
        else {}
    )
    restart = bool(record.get("restart_required", True))
    marker = {
        "id": cid,
        "path": str(p),
        "kind": record.get("kind", "local_evolution"),
        "verified_digest": record["verified_digest"],
        "activated": time.time(),
        "previous": previous,
        "state": "activating" if restart else "active",
        "health_confirmed": not restart,
    }
    if record.get("kind") == "update":
        marker.update(
            compatibility_id=record["compatibility_id"],
            version_code=record["version_code"],
            downgrade_allowed=bool(record.get("downgrade_allowed")),
        )
    else:
        marker["packaged_build"] = record["packaged_build"]

    save(home() / "active.json", marker)
    if restart:
        _set_state("activating", candidate=cid, version_code=record.get("version_code"))
        return {
            "ok": True,
            "state": "activating",
            "restart_required": True,
            "text": "Verified revision selected. Restart the engine; native startup health will confirm it or roll back automatically.",
        }

    os.environ["NEWAL_ACTIVE_REVISION"] = cid
    _set_state(
        "active", candidate=cid, version_code=record.get("version_code"),
        rollback_available=True,
    )
    return {
        "ok": True,
        "state": "active",
        "hot_reloaded": True,
        "restart_required": False,
        "text": "Verified hot-reload resources are active.",
    }


@tools.tool("self_evolve_activate", "Schedule automatic activation of an unchanged verified improvement after active tasks finish. Native startup health confirms it or rolls back. Never mark untested code verified.",
            {"candidate": tools._s("verified candidate revision ID")}, ["candidate"], "exec")
def activate_verified(ctx, candidate):
    from . import auto_update
    if getattr(getattr(ctx, "session", None), "mode", "") == "read-only":
        raise tools.ToolError("Read-only mode cannot activate an improvement")
    result = auto_update.request_activation(candidate)
    return json.dumps(result), result


@tools.tool("self_update_status", "Read automatic GitHub update state, verification failures, active revision and rollback status.", {}, [], "read")
def update_status(ctx):
    from . import auto_update
    return json.dumps(dict(status(), automatic=auto_update.status()), ensure_ascii=False), {"ok": True}


def rollback():
    marker = _active_marker()
    active_id = marker.get("id")
    previous = marker.get("previous") if isinstance(marker.get("previous"), dict) else {}
    restored = ""
    if previous and _selection_valid(previous):
        save(home() / "active.json", previous)
        restored = previous.get("id", "")
    else:
        try:
            (home() / "active.json").unlink()
        except FileNotFoundError:
            pass

    running = _running_revision()
    active_record = _read_json(home() / (str(active_id) + ".json")) if active_id else {}
    hot_only = bool(active_record) and not active_record.get("restart_required", True)
    if hot_only and running == active_id:
        if restored:
            os.environ["NEWAL_ACTIVE_REVISION"] = restored
        else:
            os.environ.pop("NEWAL_ACTIVE_REVISION", None)
    restart_required = bool(running and not hot_only)
    _set_state(
        "rolled_back", failed_revision=active_id or "", restored_revision=restored,
        restart_required=restart_required,
    )
    from . import auto_update
    auto_update.cancel_revision(active_id)
    return {
        "ok": True,
        "state": "rolled_back",
        "restart_required": restart_required,
        "restored_revision": restored,
        "text": "Rolled back to the last verified revision." if restored
                else "Rolled back to the packaged engine.",
    }


def dynamic_file(relative):
    """Return a verified active hot resource, else None; never overlays Python/native files."""
    if relative not in HOT_FILES:
        return None
    marker = _active_marker()
    cid = marker.get("id")
    if (
        marker.get("state") != "active"
        or marker.get("health_confirmed") is not True
        or not cid
        or _running_revision() != cid
    ):
        return None
    record = _read_json(home() / (cid + ".json"))
    if record.get("kind") != "update" or record.get("status") != "ready_to_activate":
        return None
    try:
        root = candidate(cid) / "newal_code"
        path = root.joinpath(*PurePosixPath(relative).parts)
        expected = (record.get("files") or {}).get("newal_code/" + relative)
        if not expected or not path.is_file() or path.is_symlink():
            return None
        if _sha256_file(path) != expected:
            return None
        return path
    except Exception:
        return None


def status():
    records = []
    for file in sorted(home().glob("*.json")):
        if file.name in ("active.json", "update-state.json", "last-failure.json"):
            continue
        try:
            data = json.loads(file.read_text())
            if isinstance(data, dict) and data.get("id"):
                records.append(data)
        except (ValueError, OSError):
            continue
    records = sorted(records, key=lambda x: x.get("created", 0), reverse=True)[:30]
    active = _active_marker()
    running = _running_revision()
    state = _read_json(home() / "update-state.json") or {"state": "up_to_date"}
    failure = _read_json(home() / "last-failure.json")
    if failure.get("state") == "rolled_back" and failure.get("failed_revision"):
        state = dict(failure)
    elif active:
        if active.get("state") == "active" and active.get("health_confirmed") is True:
            if running == active.get("id"):
                state = {
                    "state": "active",
                    "candidate": active.get("id"),
                    "version_code": active.get("version_code"),
                    "rollback_available": True,
                }
            else:
                state = {
                    "state": "failed_activation",
                    "candidate": active.get("id"),
                    "error": "Saved active revision is not the revision running now",
                }
        elif active.get("state") == "activating":
            state = {
                "state": "activating",
                "candidate": active.get("id"),
                "version_code": active.get("version_code"),
            }

    updates = [x for x in records if x.get("kind") == "update"]
    candidate_record = next((
        x for x in updates
        if x.get("id") != active.get("id")
        and x.get("status") in ("staged", "ready_to_activate", "failed_verification")
    ), None)
    current = {
        "packaged_build": _packaged_build(),
        "compatibility_id": _compatibility(),
        "revision": running or "packaged",
        "version_code": _current_version_code(),
    }
    from . import auto_update
    return {
        "automatic": auto_update.status(),
        "candidates": records,
        "active": active,
        "update": state,
        "current": current,
        "candidate": {
            "revision": candidate_record.get("id"),
            "version_code": candidate_record.get("version_code"),
            "state": candidate_record.get("state"),
            "channel": candidate_record.get("channel"),
        } if candidate_record else {},
        "rollback_available": bool(active),
    }


def route(handler, method, path, body=None):
    if not path.startswith("/api/evolution"):
        return False
    try:
        if method == "GET" and path == "/api/evolution":
            handler._json(status())
        elif method == "GET" and path == "/api/evolution/automatic":
            from . import auto_update
            handler._json(auto_update.status())
        elif method == "POST" and path == "/api/evolution/automatic":
            from . import auto_update
            handler._json(auto_update.configure((body or {}).get('enabled')))
        elif method == "POST" and path == "/api/evolution/activate":
            handler._json(activate((body or {}).get("candidate", "")))
        elif method == "POST" and path == "/api/evolution/rollback":
            handler._json(rollback())
        elif method == "POST" and path == "/api/evolution/stage":
            data = body or {}
            handler._json(_MANAGER.stage(
                data.get("source", ""), data.get("sha256", ""),
                bool(data.get("allow_downgrade")),
            ))
        elif method == "POST" and path == "/api/evolution/verify-update":
            handler._json(_MANAGER.verify((body or {}).get("candidate", "")))
        else:
            handler._json({"error": "Not found"}, 404)
    except Exception as exc:
        handler._json({"error": str(exc)}, 400)
    return True
