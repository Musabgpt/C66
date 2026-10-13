"""Cursor-style @-mentions beyond files: @git-diff (uncommitted changes), @git-log (recent commits), @folder:path
(the folder's file list) and @problems (errors in the files changed in this thread). Each is expanded into the
message as context the agent reads immediately."""
import os
import re
import subprocess

MAX = 12000


def _git(root, *args):
    try:
        p = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=20, errors="replace")
        return p.stdout if p.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _folder(root, rel):
    base = os.path.realpath(os.path.join(root, rel))
    top = os.path.realpath(root)
    if base != top and not base.startswith(top + os.sep) or not os.path.isdir(base):
        return "(no such folder: %s)" % rel
    out = []
    for folder, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in ("node_modules", "__pycache__", "build",
                                                                                 "dist", "venv", ".venv"))
        for f in sorted(files):
            out.append(os.path.relpath(os.path.join(folder, f), top).replace(os.sep, "/"))
            if len(out) >= 300:
                return "\n".join(out) + "\n..."
    return "\n".join(out) or "(empty)"


def _problems(session):
    from . import diagnostics, tools
    lines = []
    try:
        changes = session.changes()
    except Exception:  # noqa: BLE001
        changes = []
    for c in changes:
        full = os.path.join(session.root, c.get("path", ""))
        if os.path.isfile(full):
            for p in diagnostics.check(full, tools.read_text(full)):
                lines.append("%s: %s" % (c.get("path"), p))
    return "\n".join(lines) or "(no problems in the changed files)"


def expand(session, text):
    """The message with its @-mentions' content appended (unchanged when it has none)."""
    if "@" not in (text or "") or not session or not getattr(session, "root", ""):
        return text
    root = session.root
    blocks = []
    if re.search(r"(?<![\w/])@git-diff\b", text):
        diff = _git(root, "diff", "HEAD") or _git(root, "diff")
        blocks.append("<git-diff>\n%s\n</git-diff>" % (diff[:MAX] or "(no uncommitted changes)"))
    if re.search(r"(?<![\w/])@git-log\b", text):
        blocks.append("<git-log>\n%s\n</git-log>" % (_git(root, "log", "--oneline", "-20")[:MAX] or "(no commits)"))
    if re.search(r"(?<![\w/])@problems\b", text):
        blocks.append("<problems>\n%s\n</problems>" % _problems(session)[:MAX])
    for rel in re.findall(r"(?<![\w/])@folder:([\w./-]+)", text)[:3]:
        blocks.append('<folder path="%s">\n%s\n</folder>' % (rel, _folder(root, rel)[:MAX]))
    if not blocks:
        return text
    return text + "\n\n" + "\n\n".join(blocks)
