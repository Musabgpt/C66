"""Cursor-style review of the agent's changes: per-hunk Keep / Undo, and restoring the project to how it was before
any message (checkpoints). Works on the session's saved originals (Checkpoints), so nothing extra is recorded."""
import difflib
import hashlib
import os


def _read(path):
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as f:
        return f.read()


def _lines(data):
    return (data or b"").decode("utf-8", "replace").splitlines(True)


def _sig(data):
    return hashlib.sha1(data or b"").hexdigest()[:16]


def _target(s, rel):
    root = os.path.realpath(s.root)
    full = os.path.realpath(os.path.join(s.root, rel or ""))
    if full != root and not full.startswith(root + os.sep):
        raise ValueError("outside the project")
    return full


def _original(s, full):
    orig = s.checkpoints.originals()
    for p, data in orig.items():
        if os.path.realpath(p) == full:
            return True, data
    return False, None


def _blocks(before, now):
    sm = difflib.SequenceMatcher(None, before, now, autojunk=False)
    return [op for op in sm.get_opcodes() if op[0] != "equal"]


def hunks(s, rel):
    """{path, sig, hunks: [{i, old_start, new_start, removed: [...], added: [...]}]} for one changed file."""
    full = _target(s, rel)
    known, data = _original(s, full)
    if not known:
        return {"path": rel, "sig": "", "hunks": [], "error": "no saved original (not changed in this thread)"}
    now_bytes = _read(full)
    before, now = _lines(data), _lines(now_bytes)
    out = []
    for i, (_, a1, a2, b1, b2) in enumerate(_blocks(before, now)):
        out.append({"i": i, "old_start": a1 + 1, "new_start": b1 + 1,
                    "removed": [l.rstrip("\n") for l in before[a1:a2]][:400],
                    "added": [l.rstrip("\n") for l in now[b1:b2]][:400]})
    return {"path": rel, "sig": _sig(now_bytes), "hunks": out, "created": data is None,
            "deleted": now_bytes is None}


def revert_hunk(s, rel, index, sig=""):
    """Puts one hunk back to the original; refuses when the file changed since the hunks were listed."""
    full = _target(s, rel)
    known, data = _original(s, full)
    if not known:
        raise ValueError("no saved original for %s" % rel)
    now_bytes = _read(full)
    if sig and sig != _sig(now_bytes):
        raise ValueError("the file changed since the review was opened; reopen it")
    before, now = _lines(data), _lines(now_bytes)
    blocks = _blocks(before, now)
    index = int(index)
    if not 0 <= index < len(blocks):
        raise ValueError("no such change")
    _, a1, a2, b1, b2 = blocks[index]
    result = now[:b1] + before[a1:a2] + now[b2:]
    text = "".join(result)
    if data is None and not text:
        if os.path.isfile(full):
            os.remove(full)
    else:
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(text.encode("utf-8"))
    return hunks(s, rel)


def restore(s, turn):
    """The project as it was before message `turn`: undoes that turn and every later one. Returns the files."""
    turn = int(turn)
    files = set()
    for t in sorted((t for t in list(s.checkpoints.turns) if t >= turn), reverse=True):
        files.update(s.checkpoints.undo(t))
    return sorted(os.path.relpath(f, s.root) for f in files)


def route(s, action, b):
    """(handled, body) for /api/sessions/<id>/<action> review actions."""
    try:
        if action == "hunks":
            return True, hunks(s, b.get("path", ""))
        if action == "revert_hunk":
            return True, revert_hunk(s, b.get("path", ""), b.get("hunk", -1), b.get("sig", ""))
        if action == "restore":
            return True, {"ok": True, "reverted": restore(s, b.get("turn", 0))}
    except (ValueError, OSError) as e:
        return True, {"error": str(e)}
    return False, None
