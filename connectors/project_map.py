"""Codebase index (Cursor-style): a compact map of the project — folders, files and each file's main definitions —
put at the start of a thread so the agent knows where things are without exploring first. Cached per file by
modification time, so later threads build it in milliseconds."""
import hashlib
import json
import os

from . import settings, tools

MAX_FILES = 400
MAX_CHARS = 7000
MAX_DEFS = 8
CODE_EXT = {".py", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".java", ".kt", ".go", ".rs", ".c", ".h", ".cpp",
            ".hpp", ".cs", ".rb", ".php", ".swift", ".dart", ".sh", ".lua", ".vue", ".svelte"}


def _cache_path(root):
    key = hashlib.sha1(os.path.abspath(root).encode("utf-8")).hexdigest()[:16]
    return os.path.join(settings.HOME, "index", key + ".json")


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save(path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except OSError:
        pass


def files(root):
    out = []
    for folder, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in tools.IGNORED_DIRS and not d.startswith("."))
        for n in sorted(names):
            if n.startswith("."):
                continue
            out.append(os.path.join(folder, n))
            if len(out) >= MAX_FILES:
                return out
    return out


def build(root):
    """[(relative path, [definition names])] for the project, using and refreshing the cache."""
    from . import code_intel
    cache_file = _cache_path(root)
    cache = _load(cache_file)
    fresh, out = {}, []
    for p in files(root):
        rel = os.path.relpath(p, root).replace(os.sep, "/")
        ext = os.path.splitext(p)[1].lower()
        defs = []
        if ext in CODE_EXT:
            try:
                st = os.stat(p)
            except OSError:
                continue
            stamp = "%d:%d" % (int(st.st_mtime), st.st_size)
            hit = cache.get(rel)
            if hit and hit.get("s") == stamp:
                defs = hit.get("d", [])
            elif st.st_size <= 400000:
                try:
                    with open(p, encoding="utf-8", errors="replace") as f:
                        text = f.read()
                    defs = [n for _, _, n in code_intel.outline(p, text) if "." not in n][:MAX_DEFS]
                except OSError:
                    defs = []
            fresh[rel] = {"s": stamp, "d": defs}
        out.append((rel, defs))
    if fresh != {k: v for k, v in cache.items() if k in fresh} or len(fresh) != len(cache):
        _save(cache_file, fresh)
    return out


def block(root):
    """The map as text for the thread's context ("" for an empty project)."""
    if not root or not os.path.isdir(root):
        return ""
    try:
        entries = build(root)
    except Exception:  # noqa: BLE001 - the map is a help, never a failure
        return ""
    if not entries:
        return ""
    lines, size = [], 0
    for rel, defs in entries:
        line = rel + (": " + ", ".join(defs) if defs else "")
        if size + len(line) > MAX_CHARS:
            lines.append("... (%d more files; use glob or find_symbol)" % (len(entries) - len(lines)))
            break
        lines.append(line)
        size += len(line) + 1
    return "Project map (files and their main definitions; read a file before editing it):\n" + "\n".join(lines)
