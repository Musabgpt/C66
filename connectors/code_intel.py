"""Code intelligence for the agent, the way Cursor uses its editor: problems (lints) after edits and on demand, a
file's outline, where a symbol is defined and used, and showing a file in the user's VS Code (code-server).

Everything is local and bounded. Extra linters (ruff, pyflakes, eslint, tsc, shellcheck) are used when the project or
Termux has them; without them the built-in checks (syntax, undefined names, node --check, JSON) still run.
"""
import ast
import os
import re
import shutil
import subprocess

from . import tools

NAMES = ["diagnostics", "code_outline", "find_symbol", "vscode_open"]
MAX_FILES = 4000
MAX_HITS = 80
TIMEOUT = 25
SOURCE_EXT = {".py", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".java", ".kt", ".go", ".rs", ".c", ".h", ".cpp",
              ".hpp", ".cs", ".rb", ".php", ".swift", ".dart", ".sh", ".lua", ".html", ".css", ".vue", ".svelte"}


def _run(args, cwd, timeout=TIMEOUT):
    try:
        p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout, errors="replace")
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except (OSError, subprocess.SubprocessError):
        return None, ""


def _local_bin(root, name):
    p = os.path.join(root or "", "node_modules", ".bin", name)
    return p if os.path.isfile(p) else shutil.which(name)


def extra_problems(path, root=None, deep=False):
    """Problems from real linters the phone or project has (empty when none is installed). After every edit (deep
    False) Python relies on the built-in check alone, which already reports syntax errors and undefined names."""
    root = root or os.path.dirname(path)
    ext = os.path.splitext(path)[1].lower()
    out = []
    if ext == ".py" and deep:
        if shutil.which("ruff"):
            code, text = _run(["ruff", "check", "--quiet", "--output-format", "concise", "--select", "E9,F", path], root)
            out += [l for l in text.splitlines() if l.strip() and ":" in l][:20] if code else []
        elif shutil.which("pyflakes"):
            code, text = _run(["pyflakes", path], root)
            out += [l for l in text.splitlines() if l.strip()][:20] if code else []
    elif ext in (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"):
        eslint = _local_bin(root, "eslint")
        if eslint and any(os.path.exists(os.path.join(root, f)) for f in
                          ("eslint.config.js", "eslint.config.mjs", ".eslintrc", ".eslintrc.js", ".eslintrc.json")):
            code, text = _run([eslint, "--format", "unix", path], root, timeout=40)
            out += [l for l in text.splitlines() if l.strip() and ":" in l and "problem" not in l][:20] if code else []
    elif ext in (".sh", ".bash"):
        code, text = _run(["bash", "-n", path], root)
        if code:
            out += [l for l in text.splitlines() if l.strip()][:5]
        elif shutil.which("shellcheck"):
            code, text = _run(["shellcheck", "-f", "gcc", "-S", "error", path], root)
            out += [l for l in text.splitlines() if l.strip()][:20] if code else []
    return out


def _project_files(root, exts=None):
    count = 0
    for folder, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in tools.IGNORED_DIRS and not d.startswith(".")]
        for f in files:
            if exts is None or os.path.splitext(f)[1].lower() in exts:
                count += 1
                if count > MAX_FILES:
                    return
                yield os.path.join(folder, f)


def _rel(ctx, p):
    return os.path.relpath(p, ctx.root)


# ------------------------------------------------------------------ outline

_JS_DEF = re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:function\*?\s+([A-Za-z_$][\w$]*)|"
                     r"class\s+([A-Za-z_$][\w$]*)|(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?"
                     r"(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>|interface\s+([A-Za-z_$][\w$]*)|type\s+([A-Za-z_$][\w$]*)\s*=)")
_GENERIC_DEF = re.compile(r"^\s*(?:pub\s+|public\s+|private\s+|protected\s+|static\s+|final\s+|abstract\s+|async\s+|"
                          r"export\s+|override\s+|suspend\s+|inline\s+)*"
                          r"(?:fn|func|fun|def|class|struct|enum|trait|interface|object|impl|function)\s+"
                          r"([A-Za-z_][\w]*)")


def outline(path, text):
    """[(line, kind, name)] of a file's definitions."""
    ext = os.path.splitext(path)[1].lower()
    out = []
    if ext == ".py":
        try:
            tree = ast.parse(text)
        except SyntaxError:
            tree = None
        if tree is not None:
            def walk(nodes, prefix=""):
                for n in nodes:
                    if isinstance(n, ast.ClassDef):
                        out.append((n.lineno, "class", prefix + n.name))
                        walk(n.body, prefix + n.name + ".")
                    elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        out.append((n.lineno, "def", prefix + n.name))
            walk(tree.body)
            return out
    rx = _JS_DEF if ext in (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".vue", ".svelte") else _GENERIC_DEF
    for i, line in enumerate(text.splitlines(), 1):
        m = rx.match(line)
        if m:
            name = next(g for g in m.groups() if g)
            kind = "class" if re.search(r"\b(class|struct|interface|enum|trait|object|type)\b", line[:m.start(0) + 40]) \
                else "def"
            out.append((i, kind, name))
    return out


def install():
    @tools.tool("diagnostics", "Problems (lints and errors) in files, like an editor's Problems panel: syntax errors, "
                "undefined names, and the project's linters when installed (ruff/pyflakes, eslint, shellcheck). "
                "Without paths: the files changed in this thread. Run it after editing, before saying work is done.",
                {"paths": {"type": "array", "items": {"type": "string"}, "description": "files to check"}}, [], "read")
    def t_diagnostics(ctx, paths=None):
        from . import diagnostics as builtin
        if not paths:
            try:
                paths = [c.get("path") for c in ctx.session.changes() if c.get("path")]
            except Exception:  # noqa: BLE001 - a session without change tracking
                paths = []
            paths = paths or sorted(_rel(ctx, p) for p in getattr(ctx, "changed", ()) or ())
        if not paths:
            return "No changed files to check; pass paths.", {"files": 0, "problems": 0}
        report, total = [], 0
        for raw in list(paths)[:40]:
            p = tools.resolve(ctx, raw)
            if not os.path.isfile(p):
                report.append("%s: no such file" % raw)
                continue
            text = tools.read_text(p)
            found = list(builtin.check(p, text)) + extra_problems(p, ctx.root, deep=True)
            total += len(found)
            report.append("%s: %s" % (_rel(ctx, p), "no problems" if not found else "\n  " + "\n  ".join(found)))
        return "\n".join(report), {"files": len(report), "problems": total}

    @tools.tool("code_outline", "A file's outline: its classes and functions with line numbers (the editor's Outline "
                "view). Read the outline of a big file first, then read only the part you need.",
                {"path": tools._s("file path")}, ["path"], "read")
    def t_outline(ctx, path):
        p = tools.resolve(ctx, path)
        if not os.path.isfile(p):
            raise tools.ToolError("no such file: %s" % path)
        items = outline(p, tools.read_text(p))
        if not items:
            return "%s: no definitions found" % _rel(ctx, p), {"path": _rel(ctx, p), "count": 0}
        lines = ["%5d  %-5s %s" % (ln, kind, name) for ln, kind, name in items[:400]]
        return "%s\n%s" % (_rel(ctx, p), "\n".join(lines)), {"path": _rel(ctx, p), "count": len(items)}

    @tools.tool("find_symbol", "Where a function, class or variable is defined and where it is used across the project "
                "(go to definition / find all references). Use it before renaming or changing a signature.",
                {"name": tools._s("exact identifier"), "path": tools._s("optional folder to search in")}, ["name"], "read")
    def t_find_symbol(ctx, name, path=""):
        name = str(name or "").strip()
        if not re.fullmatch(r"[A-Za-z_$][\w$]*", name):
            raise tools.ToolError("give a single identifier, e.g. parse_config")
        base = tools.resolve(ctx, path) if path else ctx.root
        word = re.compile(r"(?<![\w$])" + re.escape(name) + r"(?![\w$])")
        defs, refs = [], []
        for f in _project_files(base, SOURCE_EXT):
            try:
                with open(f, encoding="utf-8", errors="replace") as fh:
                    text = fh.read(400000)
            except OSError:
                continue
            if name not in text:
                continue
            defined = {ln for ln, _, n in outline(f, text) if n.split(".")[-1] == name}
            for i, line in enumerate(text.splitlines(), 1):
                if word.search(line):
                    entry = "%s:%d: %s" % (_rel(ctx, f), i, line.strip()[:160])
                    (defs if i in defined else refs).append(entry)
            if len(refs) > MAX_HITS * 3:
                break
        if not defs and not refs:
            return "%s: not found in the project" % name, {"definitions": 0, "references": 0}
        out = ["Definitions:"] + (defs or ["  (none found; it may come from a library)"])
        out += ["References (%d):" % len(refs)] + refs[:MAX_HITS]
        if len(refs) > MAX_HITS:
            out.append("... %d more" % (len(refs) - MAX_HITS))
        return "\n".join(out), {"definitions": len(defs), "references": len(refs)}

    @tools.tool("vscode_open", "Show a file (at a line) in the user's VS Code (code-server in Termux), so they see what "
                "you are working on. Works when VS Code runs on this phone.",
                {"path": tools._s("file path"), "line": tools._i("line to show")}, ["path"], "read")
    def t_vscode_open(ctx, path, line=0):
        p = tools.resolve(ctx, path)
        if not os.path.exists(p):
            raise tools.ToolError("no such file: %s" % path)
        exe = shutil.which("code-server")
        if not exe:
            return ("VS Code is not on this side of the phone (it runs in Termux); the user can open %s there."
                    % _rel(ctx, p)), {"opened": False}
        target = "%s:%d" % (p, int(line)) if line else p
        code, text = _run([exe, "--reuse-window", "--goto", target], ctx.root, timeout=15)
        if code != 0:
            return "VS Code did not open it (is it running? the VS Code button starts it): %s" % text[:300], \
                {"opened": False}
        return "Shown in VS Code: %s" % _rel(ctx, p), {"opened": True, "path": _rel(ctx, p)}
