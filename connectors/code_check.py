"""code_check: the language servers VS Code uses, without VS Code. pyright for Python and typescript-language-server
for JavaScript/TypeScript run in Termux (or on this machine when they are installed here) and tell the agent:

* errors: the real errors in files, the way an editor underlines them (the files changed this turn by default);
* definition: where a name is defined;
* references: everywhere a name is used.

One request per call (lsp_once.py starts the server, asks, stops it). The first use installs the servers in Termux in
the background (npm: pyright, typescript, typescript-language-server); until then the tool says so and the agent
carries on. Before the agent says it is done, the certain errors in the files it changed hold it back (gate).
"""
import base64
import json
import os
import shutil
import subprocess
import sys
import time

from . import tools

NAMES = ["code_check"]
SERVERS = {
    "python": ["pyright-langserver", "--stdio"],
    "typescript": ["typescript-language-server", "--stdio"],
}
KIND = {".py": "python", ".pyi": "python", ".js": "typescript", ".mjs": "typescript", ".cjs": "typescript",
        ".jsx": "typescript", ".ts": "typescript", ".mts": "typescript", ".cts": "typescript", ".tsx": "typescript"}
INSTALL = ("command -v node >/dev/null 2>&1 || (yes | pkg install -y nodejs-lts || yes | pkg install -y nodejs); "
           "npm install -g --no-fund --no-audit pyright typescript typescript-language-server")
# missing packages in the checker's own environment are not errors of the code
NOT_CODE = ("reportMissingImports", "reportMissingModuleSource", "2307", "7016")
# what holds a turn back: never a style or typing opinion
CERTAIN = ("", "reportUndefinedVariable", "reportCallIssue", "reportRedeclaration", "reportSelfClsParameterName",
           "1005", "1003", "1109", "1128", "1161", "2304", "2552", "2554", "2555")
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".next", ".gradle"}
_install = {"id": None, "started": 0.0}


def _client_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "lsp_once.py")


def _termux():
    try:
        from . import runtime_manager
        return runtime_manager._phone_available() and runtime_manager._termux_record().get("status") == "connected"
    except Exception:  # noqa: BLE001
        return False


def where(kind):
    """'local' when this machine has the server, 'termux' through the bridge, or ''."""
    if shutil.which(SERVERS[kind][0]):
        return "local"
    if _termux():
        return "termux"
    return ""


def _payload(args):
    return base64.b64encode(json.dumps(args).encode("utf-8")).decode("ascii")


def _run(args, place, timeout=60):
    if place == "local":
        try:
            p = subprocess.run([sys.executable, _client_path(), _payload(args)], capture_output=True, text=True,
                               timeout=timeout, errors="replace")
            return json.loads(p.stdout or "{}")
        except subprocess.TimeoutExpired:
            return {"error": "the language server took too long"}
        except ValueError:
            return {"error": "no answer from the language server"}
    from . import runtime_manager
    with open(_client_path(), encoding="utf-8") as f:
        source = f.read()
    # Termux: lsp_once.py is written to ~/.cache/musab-lsp (renamed into place, so a check never reads a half-written
    # file) and run there with keep, so the language server stays up for the next check
    command = ('D="$HOME/.cache/musab-lsp"; mkdir -p "$D" && cat > "$D/lsp_once.py.$$" <<\'MUSAB_LSP_EOF\'\n%s\n'
               'MUSAB_LSP_EOF\nmv -f "$D/lsp_once.py.$$" "$D/lsp_once.py" && python3 "$D/lsp_once.py" %s') % (
        source, _payload(dict(args, keep=True)))
    data = runtime_manager.execute(command)
    try:
        return json.loads(data.get("stdout") or "{}")
    except ValueError:
        return {"error": (data.get("stderr") or data.get("stdout") or "no answer from Termux")[-400:]}


def install_status(place, found=None):
    """'' when nothing is being installed, else a line about it (starting the install the first time). found: what
    lsp_once reported (where it looked), so a server installed somewhere unusual can be told apart from none."""
    seen = ""
    if found and found.get("looked"):
        seen = " (node: %s; looked in %s)" % (found.get("node") or "not found", ", ".join(found["looked"][:4]))
    if place == "termux":
        from . import runtime_manager
        if _install["id"]:
            try:
                st = runtime_manager.process_status(_install["id"])
            except tools.ToolError:
                st = {}
            if st.get("status") == "running":
                return "The language servers are being installed in Termux (started %d s ago); this takes a few " \
                       "minutes the first time. Don't install them yourself; carry on and check by running the " \
                       "code." % (time.time() - _install["started"])
            _install["id"] = None
            if st.get("exit_code") not in (0, None):
                return "Installing the language servers in Termux failed (%s). Run in Termux: %s" % (
                    str(st.get("stdout") or st.get("stderr") or "")[-300:], INSTALL)
            return "The language servers were installed but are not found%s. Run in Termux: %s" % (seen, INSTALL)
        data = runtime_manager.process_start(INSTALL)
        _install.update(id=data.get("id"), started=time.time())
        return "Installing the language servers in Termux now (pyright, typescript-language-server), once; this " \
               "takes a few minutes. Don't install them yourself; carry on and check by running the code, and call " \
               "code_check again later."
    return "No language server on this machine: install them once with npm install -g pyright typescript " \
           "typescript-language-server (or connect Termux on the phone, where code_check installs them itself)."


def _rel(path, root):
    try:
        rel = os.path.relpath(path, root)
        return path if rel.startswith("..") else rel
    except ValueError:
        return path


def _project_files(root, kind, limit=300):
    out = []
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for f in files:
            if KIND.get(os.path.splitext(f)[1].lower()) == kind:
                out.append(os.path.join(base, f))
                if len(out) >= limit:
                    return out
    return out


def check(root, files, place=None):
    """{kind: result} for the files, grouped by language server."""
    groups = {}
    for f in files:
        kind = KIND.get(os.path.splitext(f)[1].lower())
        if kind:
            groups.setdefault(kind, []).append(f)
    out = {}
    for kind, fs in groups.items():
        at = place or where(kind)
        if not at:
            out[kind] = {"missing": SERVERS[kind][0]}
            continue
        out[kind] = _run({"server": SERVERS[kind], "root": root, "files": fs[:40], "action": "errors",
                          "timeout": 45}, at)
        out[kind]["place"] = at
    return out


def problems(results, root, certain=False):
    """(lines, counts) from check()'s results."""
    lines, errors, warnings, missing_pkgs = [], 0, 0, 0
    for res in results.values():
        for path, rows in (res.get("diagnostics") or {}).items():
            for d in rows:
                code = str(d.get("code") or "")
                if code in NOT_CODE:
                    missing_pkgs += 1
                    continue
                if d["severity"] not in ("error", "warning"):
                    continue
                if certain and (d["severity"] != "error" or code not in CERTAIN):
                    continue
                if d["severity"] == "error":
                    errors += 1
                else:
                    warnings += 1
                lines.append("%s:%d:%d: %s: %s%s" % (_rel(path, root), d["line"], d["column"], d["severity"],
                                                     d["message"].splitlines()[0][:300],
                                                     " (%s)" % code if code else ""))
    return lines, {"errors": errors, "warnings": warnings, "missing_packages": missing_pkgs}


def _changed(ctx):
    paths = set(getattr(ctx, "changed", ()) or ())
    try:
        s = ctx.session
        for c in s.changes(s.turn) or []:
            paths.add(os.path.join(ctx.root, c["path"]) if not os.path.isabs(c["path"]) else c["path"])
    except Exception:  # noqa: BLE001
        pass
    return sorted(p for p in paths if os.path.isfile(p) and os.path.splitext(p)[1].lower() in KIND)


def gate(ctx, limit=12):
    """Certain errors in this turn's changed files, when a server is already installed (never installs, never
    waits for an install): the lines the finishing check adds."""
    files = _changed(ctx)
    if not files:
        return []
    kinds = {KIND[os.path.splitext(f)[1].lower()] for f in files}
    if not any(where(k) for k in kinds):
        return []
    try:
        lines, _ = problems({k: v for k, v in check(ctx.root, files).items() if "missing" not in v}, ctx.root,
                            certain=True)
    except Exception:  # noqa: BLE001 - a check never breaks the turn
        return []
    return lines[:limit]


def install():
    @tools.tool("code_check",
                "Check code with the language servers VS Code uses (pyright for Python, typescript-language-server "
                "for JavaScript/TypeScript), in Termux. action=errors (default): the real errors and warnings in "
                "paths, or in the files changed this turn when paths is empty: run it after editing code and fix "
                "what it reports. action=definition: where the name at path:line is defined. action=references: "
                "everywhere it is used (give symbol, the name on that line).",
                {"action": tools._s("errors (default), definition or references"),
                 "paths": {"type": "array", "items": {"type": "string"},
                           "description": "files to check (errors); empty: the files changed this turn"},
                 "path": tools._s("definition/references: the file"),
                 "line": tools._i("definition/references: the line (1-based)"),
                 "symbol": tools._s("definition/references: the name on that line")}, [], "read")
    def t_code_check(ctx, action="errors", paths=None, path="", line=0, symbol=""):
        action = (action or "errors").strip().lower()
        if action == "errors":
            files = [tools.resolve(ctx, p) for p in (paths or [])] or _changed(ctx)
            files = [f for f in files if os.path.isfile(f)]
            if not files:
                return "Nothing to check: no code files given or changed this turn."
            results = check(ctx.root, files)
            notes = []
            for kind, res in results.items():
                if res.get("missing"):
                    notes.append(install_status(where(kind) or ("termux" if _termux() else "local"), res))
                elif res.get("error"):
                    notes.append("%s: %s" % (SERVERS[kind][0], res["error"]))
                elif res.get("complete") is False:
                    notes.append("%s did not finish checking in time, so some results may be missing: run code_check "
                                 "again." % SERVERS[kind][0])
            lines, n = problems(results, ctx.root)
            checked = sum(len(r.get("diagnostics") or {}) for r in results.values())
            head = ("%d error(s), %d warning(s) in %d file(s)" % (n["errors"], n["warnings"], checked) if checked
                    else "Not checked")
            if n["missing_packages"]:
                head += "; %d import(s) not installed where the checker runs (not counted)" % n["missing_packages"]
            text = "\n".join([head + ("." if lines else (": no problems." if checked else "."))] + lines[:80] +
                             [x for x in notes if x])
            return text, {"errors": n["errors"], "warnings": n["warnings"], "files": checked}
        if action not in ("definition", "references"):
            raise tools.ToolError("action: errors, definition or references")
        if not path or not line:
            raise tools.ToolError("%s needs path and line (and symbol: the name on that line)" % action)
        full = tools.resolve(ctx, path)
        kind = KIND.get(os.path.splitext(full)[1].lower())
        if not kind or not os.path.isfile(full):
            raise tools.ToolError("not a Python or JavaScript/TypeScript file: %s" % path)
        with open(full, encoding="utf-8", errors="replace") as f:
            rows = f.read().splitlines()
        if not 1 <= int(line) <= len(rows):
            raise tools.ToolError("%s has %d lines" % (path, len(rows)))
        text_line = rows[int(line) - 1]
        col = text_line.find(symbol) if symbol else len(text_line) - len(text_line.lstrip())
        if col < 0:
            raise tools.ToolError("%r is not on line %d: %s" % (symbol, int(line), text_line.strip()[:120]))
        at = where(kind)
        if not at:
            return install_status("termux" if _termux() else "local")
        files = [full] + [f for f in _project_files(ctx.root, kind) if f != full]
        res = _run({"server": SERVERS[kind], "root": ctx.root, "files": files if action == "references" else [full],
                    "action": action, "line": int(line), "character": col, "timeout": 50}, at)
        if res.get("missing"):
            return install_status(at)
        if res.get("error"):
            raise tools.ToolError(res["error"])
        locs = res.get("locations") or []
        if not locs:
            return "No %s found for %s." % (action, symbol or "that name")
        out = []
        for loc in locs[:60]:
            snippet = ""
            try:
                with open(loc["path"], encoding="utf-8", errors="replace") as f:
                    snippet = f.read().splitlines()[loc["line"] - 1].strip()[:160]
            except (OSError, IndexError):
                pass
            out.append("%s:%d: %s" % (_rel(loc["path"], ctx.root), loc["line"], snippet))
        return "\n".join(out), {"count": len(locs)}
