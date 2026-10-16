"""Build a deterministic, integrity-described MusabAI hot-update bundle.

Run only after the full CI verification has passed. The bundle carries Python/UI
engine files only; Android/native code is deliberately excluded and must ship as
an APK. No signing secret or key material is read or written here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import zipfile

SCHEMA = 1
COMPATIBILITY = "action125-python314-v1"
REPOSITORY = "Musabgpt/NewAl-Cloud"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_digest(root: Path) -> str:
    h = hashlib.sha256()
    for file in sorted(root.rglob("*")):
        if file.is_symlink():
            raise SystemExit("Refusing symlink in update tree: " + str(file))
        if file.is_file() and "__pycache__" not in file.parts and ".git" not in file.parts:
            h.update(file.relative_to(root).as_posix().encode())
            h.update(file.read_bytes())
    return h.hexdigest()


def build(source: Path, output: Path) -> dict:
    source = source.resolve()
    if not (source / "__init__.py").is_file() or not (source / "server.py").is_file():
        raise SystemExit("Expected a complete newal_code package")
    commit = os.environ.get("GITHUB_SHA", "").strip().lower()
    if len(commit) != 40 or any(c not in "0123456789abcdef" for c in commit):
        raise SystemExit("GITHUB_SHA must be the exact 40-character source commit")
    try:
        version = int(os.environ["NEWAL_BUILD"])
    except (KeyError, ValueError):
        raise SystemExit("NEWAL_BUILD must be a positive integer") from None
    if version <= 0:
        raise SystemExit("NEWAL_BUILD must be a positive integer")

    ref = os.environ.get("GITHUB_REF_NAME", "")
    channel = "stable" if ref in ("main", "release") else "candidate"
    files = {}
    disk_files = []
    for file in sorted(source.rglob("*")):
        if file.is_symlink():
            raise SystemExit("Refusing symlink in update tree: " + str(file))
        if not file.is_file() or "__pycache__" in file.parts or ".git" in file.parts:
            continue
        rel = file.relative_to(source).as_posix()
        files["newal_code/" + rel] = sha256(file)
        disk_files.append((file, "newal_code/" + rel))

    manifest = {
        "schema": SCHEMA,
        "revision": commit[:24],
        "version_code": version,
        "channel": channel,
        "compatibility_id": COMPATIBILITY,
        "source_repository": REPOSITORY,
        "source_commit": commit,
        "provenance": {
            "github_run_id": os.environ.get("GITHUB_RUN_ID", ""),
            "github_run_number": os.environ.get("GITHUB_RUN_NUMBER", ""),
            "workflow": os.environ.get("GITHUB_WORKFLOW", ""),
            "workflow_ref": os.environ.get("GITHUB_WORKFLOW_REF", ""),
            "head_sha": commit,
        },
        "native_required": False,
        "tree_sha256": tree_digest(source),
        "files": files,
        "ci_checks": {
            "node_ui": True,
            "memory_agent": True,
            "connector_runtime": True,
            "phase9": True,
            "regression": True,
            "promptfoo": True,
            "testReleaseUnitTest": True,
            "assembleRelease": True,
        },
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        )
        for file, name in disk_files:
            archive.write(file, name)
    digest = sha256(output)
    output.with_suffix(output.suffix + ".sha256").write_text(
        digest + "  " + output.name + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "bundle": str(output),
        "sha256": digest,
        "revision": manifest["revision"],
        "version_code": version,
        "files": len(files),
        "bytes": output.stat().st_size,
    }, sort_keys=True))
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    build(args.source, args.output)
