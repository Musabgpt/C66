"""One request to a language server, or to one kept running between requests: what code_check runs, on this machine or
inside Termux (sent through the Termux bridge, so it needs only Python's standard library).

    python3 lsp_once.py <base64 JSON>
    {"server": ["pyright-langserver", "--stdio"], "root": "/abs/project", "files": ["/abs/a.py"],
     "action": "errors" | "definition" | "references", "line": 12, "character": 4, "timeout": 40, "keep": false}

Prints one JSON object: {"diagnostics": {path: [...]}, "complete": bool}, or {"locations": [...]}, or
{"missing": "<command>"} when the server is not installed, or {"error": "..."}.

keep (in Termux, on the phone): the server stays up between calls. The first call starts a process of its own, which
listens on ~/.cache/musab-lsp/<server>-<protocol>.sock and answers the calls that follow. It stops after IDLE seconds
without a call (MUSAB_LSP_IDLE sets that); the next call starts it again. Between calls it learns what changed on disk
(project files are compared by size and time and sent as watched-file notices), so every answer is for the files as
they are now.
"""
import base64
import json
import os
import queue
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request

LANGUAGE = {".py": "python", ".pyi": "python", ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
            ".jsx": "javascriptreact", ".ts": "typescript", ".mts": "typescript", ".cts": "typescript",
            ".tsx": "typescriptreact"}
QUIET = 1.5                                                        # seconds without a new diagnostic: complete
IDLE = int(os.environ.get("MUSAB_LSP_IDLE") or 180)                # seconds a kept server waits for a call
PROTOCOL = 1                                                       # changes with the calls the socket understands
KEEP_DIR = os.path.join(os.path.expanduser("~"), ".cache", "musab-lsp")
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".next", ".gradle", ".tox",
             ".mypy_cache"}
SCAN_LIMIT = 20000
PACKAGES = {"pyright-langserver": "pyright", "typescript-language-server": "typescript-language-server"}


def uri(path):
    return "file://" + urllib.parse.quote(os.path.abspath(path))


def path_of(u):
    return os.path.abspath(urllib.request.url2pathname(urllib.parse.urlparse(u).path))


def scan(root):
    """({path: (mtime, size)} of the project's code files, complete): complete is False past SCAN_LIMIT files."""
    seen = {}
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for f in files:
            if os.path.splitext(f)[1].lower() not in LANGUAGE:
                continue
            path = os.path.join(base, f)
            try:
                st = os.stat(path)
            except OSError:
                continue
            seen[path] = (st.st_mtime_ns, st.st_size)
            if len(seen) >= SCAN_LIMIT:
                return seen, False
    return seen, True


class Server:
    """A language server run as a process, spoken to over stdin/stdout (JSON-RPC with Content-Length framing)."""

    def __init__(self, cmd, root):
        self.root = root
        self.proc = subprocess.Popen(cmd, cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL)
        self.inbox = queue.Queue()     # requests the server makes of us: answered by wait() and drain()
        self.replies = {}              # request id -> the server's answer
        self.published = {}            # path -> (seq, version, diagnostics) of its latest publishDiagnostics
        self.seq = 0                   # how many publishes so far
        self.published_at = 0.0
        self.dead = False
        self.next_id = 0
        self.texts = {}                # path -> the text the server has open
        self.versions = {}             # path -> the version the server has
        self.snapshot = None           # project files on disk at the last call
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        out = self.proc.stdout
        while True:
            length = 0
            while True:
                line = out.readline()
                if not line:
                    self.dead = True
                    return
                line = line.strip()
                if not line:
                    break
                if line.lower().startswith(b"content-length:"):
                    length = int(line.split(b":", 1)[1])
            try:
                msg = json.loads(out.read(length).decode("utf-8", "replace"))
            except ValueError:
                continue
            if msg.get("method") == "textDocument/publishDiagnostics":
                params = msg.get("params") or {}
                self.seq += 1
                self.published[path_of(params.get("uri", ""))] = (self.seq, params.get("version"),
                                                                 params.get("diagnostics") or [])
                self.published_at = time.time()
            elif "method" in msg and "id" in msg:
                self.inbox.put(msg)
            elif "id" in msg:
                self.replies[msg["id"]] = msg

    def send(self, msg):
        data = json.dumps(dict(msg, jsonrpc="2.0")).encode("utf-8")
        self.proc.stdin.write(b"Content-Length: %d\r\n\r\n" % len(data) + data)
        self.proc.stdin.flush()

    def request(self, method, params):
        self.next_id += 1
        self.send({"id": self.next_id, "method": method, "params": params})
        return self.next_id

    def notify(self, method, params):
        self.send({"method": method, "params": params})

    def answer(self, msg):
        """The server's own requests get harmless answers, so it never waits for us."""
        result = None
        if msg.get("method") == "workspace/configuration":
            result = [None for _ in (msg.get("params") or {}).get("items") or []]
        elif msg.get("method") == "workspace/workspaceFolders":
            result = []
        self.send({"id": msg["id"], "result": result})

    def drain(self):
        while True:
            try:
                msg = self.inbox.get_nowait()
            except queue.Empty:
                return
            self.answer(msg)

    def wait(self, until, deadline):
        """Answers the server's requests until until() is true (True), or the deadline passes first (False)."""
        while True:
            self.drain()
            if until():
                return True
            if self.dead:
                raise RuntimeError("the language server stopped")
            if time.time() >= deadline:
                return False
            time.sleep(0.05)

    def fresh(self, path, mark):
        """Its latest answer came after mark and is for the text sent last."""
        got = self.published.get(path)
        return bool(got) and got[0] > mark and (got[1] is None or got[1] == self.versions.get(path))

    def _send_text(self, path, text):
        if path not in self.texts:
            self.versions[path] = 1
            self.notify("textDocument/didOpen", {"textDocument": {
                "uri": uri(path), "languageId": LANGUAGE.get(os.path.splitext(path)[1].lower(), "plaintext"),
                "version": 1, "text": text}})
        else:
            self.versions[path] += 1
            self.notify("textDocument/didChange", {"textDocument": {"uri": uri(path), "version": self.versions[path]},
                                                   "contentChanges": [{"text": text}]})
        self.texts[path] = text

    def sync(self, files, watch, force):
        """Sends what changed since the last call and returns the files asked for whose answers must be fresh. With
        force (errors), when anything changed every file asked for is sent again: an answer can depend on another file
        that changed, and the server answers again only for a text it is sent."""
        docs, moved = set(), []
        if watch:
            now, complete = scan(self.root)
            if self.snapshot is not None:
                for path, sig in now.items():
                    old = self.snapshot.get(path)
                    if old != sig:
                        if path in self.texts:
                            docs.add(path)
                        else:
                            moved.append({"uri": uri(path), "type": 1 if old is None else 2})
                if complete:
                    for path in self.snapshot:
                        if path not in now:
                            if path in self.texts:
                                self.notify("textDocument/didClose", {"textDocument": {"uri": uri(path)}})
                                self.texts.pop(path, None)
                                self.versions.pop(path, None)
                            moved.append({"uri": uri(path), "type": 3})
            self.snapshot = now
        if moved:
            self.notify("workspace/didChangeWatchedFiles", {"changes": moved})
        sent = set()
        for path in set(files) | docs:
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    text = f.read()
            except OSError:
                continue
            if self.texts.get(path) != text:
                self._send_text(path, text)
                sent.add(path)
        if force and (sent or moved or any(path not in self.published for path in files)):
            for path in files:
                if path not in sent and path in self.texts:
                    self._send_text(path, self.texts[path])
                    sent.add(path)
        return {path for path in sent if path in files}

    def close(self):
        try:
            rid = self.request("shutdown", None)
            self.wait(lambda: rid in self.replies, time.time() + 3)
            self.notify("exit", None)
        except Exception:  # noqa: BLE001 - it is stopping anyway
            pass
        try:
            self.proc.wait(timeout=3)
        except Exception:  # noqa: BLE001
            self.proc.kill()
        self.reader.join(1)
        for pipe in (self.proc.stdin, self.proc.stdout):
            try:
                pipe.close()
            except Exception:  # noqa: BLE001
                pass


def _npm_roots(ask_npm=False):
    """Where npm puts global packages: next to node, Termux's prefix, a user prefix; npm's own answer (a slow start on
    a phone) only when those have nothing."""
    roots = []
    if ask_npm:
        try:
            out = subprocess.run(["npm", "root", "-g"], capture_output=True, text=True, timeout=25).stdout.strip()
            if out:
                roots.append(out)
        except Exception:  # noqa: BLE001 - no npm
            pass
    node = shutil.which("node")
    if node:
        roots.append(os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(node))), "lib", "node_modules"))
    prefix = os.environ.get("PREFIX") or "/data/data/com.termux/files/usr"
    roots += [os.path.join(prefix, "lib", "node_modules"), os.path.expanduser("~/.npm-global/lib/node_modules"),
              "/usr/local/lib/node_modules", "/usr/lib/node_modules"]
    seen, out = set(), []
    for r in roots:
        if r and r not in seen:
            seen.add(r)
            out.append(r)
    return out


def _script(roots, pkg, name):
    for root in roots:
        try:
            with open(os.path.join(root, pkg, "package.json"), encoding="utf-8") as f:
                bins = json.load(f).get("bin")
        except (OSError, ValueError):
            continue
        rel = bins.get(name) if isinstance(bins, dict) else bins
        script = os.path.join(root, pkg, rel or "")
        if rel and os.path.isfile(script):
            return script
    return ""


def resolve(cmd):
    """The command that starts the server. An npm package runs as `node <its script>`: a script's
    `#!/usr/bin/env node` line fails where there is no /usr/bin/env (Termux without termux-exec), and the package
    is found even when npm's bin folder is not on PATH. Else the command on PATH. ([], places looked) when missing."""
    pkg = PACKAGES.get(cmd[0])
    node = shutil.which("node")
    roots = _npm_roots() if pkg else []
    if pkg and node:
        script = _script(roots, pkg, cmd[0])
        if not script and not shutil.which(cmd[0]):
            roots = _npm_roots(ask_npm=True)
            script = _script(roots, pkg, cmd[0])
        if script:
            return [node, script] + cmd[1:], roots
    exe = shutil.which(cmd[0])
    if exe:
        return [exe] + cmd[1:], roots
    return [], roots


def initialize(s, root, deadline):
    rid = s.request("initialize", {
        "processId": os.getpid(), "rootUri": uri(root), "rootPath": root,
        "workspaceFolders": [{"uri": uri(root), "name": os.path.basename(root) or "project"}],
        "capabilities": {"textDocument": {"publishDiagnostics": {"relatedInformation": False},
                                          "definition": {}, "references": {}},
                         "workspace": {"configuration": True, "workspaceFolders": True}},
        "initializationOptions": {}})
    if not s.wait(lambda: rid in s.replies, deadline):
        return False
    s.replies.pop(rid, None)
    s.notify("initialized", {})
    return True


def rows(entry):
    out = []
    for d in entry[2] if entry else []:
        start = (d.get("range") or {}).get("start") or {}
        out.append({"line": start.get("line", 0) + 1, "column": start.get("character", 0) + 1,
                    "severity": {1: "error", 2: "warning", 3: "info", 4: "hint"}.get(d.get("severity"), "error"),
                    "message": str(d.get("message") or "").strip(),
                    "code": str(d.get("code") or (d.get("data") or {}).get("rule", "") or ""),
                    "source": d.get("source") or ""})
    return out


def ask(s, args, deadline, watch):
    """The answer to one call on an initialized server: the errors of the files, or the definition or the references
    of the name at a place."""
    files = [os.path.abspath(f) for f in args.get("files") or []]
    action = args.get("action", "errors")
    s.drain()
    mark = s.seq
    need = s.sync(files, watch, force=action == "errors")
    if action == "errors":
        ok = s.wait(lambda: all(s.fresh(p, mark) for p in need) and time.time() - s.published_at > QUIET, deadline)
        return {"diagnostics": {p: rows(s.published.get(p)) for p in files},
                "complete": ok and all(p in s.published for p in files)}
    method = "textDocument/definition" if action == "definition" else "textDocument/references"
    params = {"textDocument": {"uri": uri(files[0])},
              "position": {"line": int(args.get("line", 1)) - 1, "character": int(args.get("character", 0))}}
    if method.endswith("references"):
        params["context"] = {"includeDeclaration": False}
    rid = s.request(method, params)
    if not s.wait(lambda: rid in s.replies, deadline):
        return {"error": "the language server did not answer in time"}
    msg = s.replies.pop(rid)
    if msg.get("error"):
        return {"error": str((msg.get("error") or {}).get("message") or "the language server refused")[:300]}
    result = msg.get("result") or []
    if isinstance(result, dict):
        result = [result]
    locations = []
    for loc in result:
        rng = loc.get("range") or loc.get("targetSelectionRange") or {}
        locations.append({"path": path_of(loc.get("uri") or loc.get("targetUri") or ""),
                          "line": ((rng.get("start") or {}).get("line") or 0) + 1})
    return {"locations": locations}


def run(args):
    cmd, looked = resolve(args["server"])
    if not cmd:
        return {"missing": args["server"][0], "node": shutil.which("node") or "", "looked": looked,
                "path": os.environ.get("PATH", "")}
    if args.get("keep"):
        return keep(args)
    root = os.path.abspath(args.get("root") or ".")
    deadline = time.time() + float(args.get("timeout") or 40)
    s = Server(cmd, root)
    try:
        if not initialize(s, root, deadline):
            return {"error": "the language server did not start in time"}
        return ask(s, args, deadline, watch=False)
    except RuntimeError as exc:
        return {"error": str(exc)}
    finally:
        s.close()


# ------------------------------------------------------------------ kept between calls (on the phone)

class Keeper:
    """The language server kept between calls: one at a time, for the project of the last call (another project or
    another server starts it again)."""

    def __init__(self):
        self.key = None
        self.s = None

    def close(self):
        if self.s is not None:
            self.s.close()
        self.s, self.key = None, None

    def answer(self, args):
        cmd, looked = resolve(args["server"])
        if not cmd:
            return {"missing": args["server"][0], "node": shutil.which("node") or "", "looked": looked,
                    "path": os.environ.get("PATH", "")}
        root = os.path.abspath(args.get("root") or ".")
        key = (tuple(cmd), root)
        last = ""
        for _ in range(2):
            deadline = time.time() + float(args.get("timeout") or 40)
            try:
                if self.s is None or self.key != key or self.s.proc.poll() is not None or len(self.s.texts) > 400:
                    self.close()
                    s = Server(cmd, root)
                    if not initialize(s, root, deadline):
                        s.close()
                        return {"error": "the language server did not start in time"}
                    self.s, self.key = s, key
                return ask(self.s, args, deadline, watch=True)
            except (RuntimeError, OSError) as exc:
                self.close()                      # it stopped under us: start it again, once
                last = str(exc)[:200]
        return {"error": "the language server stopped: %s" % last}


def alive(path):
    try:
        _connect(path).close()
        return True
    except OSError:
        return False


def serve(path, idle):
    """The kept server: answers the calls on its socket, one at a time, and stops after idle seconds without one."""
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        srv.bind(path)
    except OSError:
        if alive(path):                           # another server keeps this socket
            srv.close()
            return
        os.remove(path)                           # a leftover file of a server that has stopped
        srv.bind(path)
    srv.listen(4)
    srv.settimeout(1.0)
    keeper = Keeper()
    last = time.time()
    try:
        while time.time() - last < idle:
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                continue
            try:
                conn.settimeout(120)
                res = keeper.answer(json.loads(_line(conn).decode("utf-8", "replace").strip() or "{}"))
            except Exception as exc:  # noqa: BLE001 - always one JSON answer
                res = {"error": str(exc)[:500]}
            try:
                conn.sendall(json.dumps(res, ensure_ascii=False).encode("utf-8") + b"\n")
            except OSError:
                pass
            finally:
                conn.close()
            last = time.time()
    finally:
        keeper.close()
        srv.close()
        try:
            os.remove(path)
        except OSError:
            pass


def _socket(server):
    name = "%s-%d.sock" % (os.path.basename(server[0]), PROTOCOL)
    path = os.path.join(KEEP_DIR, name)
    if len(path.encode("utf-8")) > 100:           # a unix socket path has a short limit
        path = os.path.join(tempfile.gettempdir(), "musab-lsp-" + name)
    return path


def _connect(path):
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.settimeout(2)
    try:
        c.connect(path)
    except OSError:
        c.close()
        raise
    return c


def _start(path):
    """A kept server in a process of its own: it outlives this call (its own session, its output in a log file)."""
    os.makedirs(KEEP_DIR, exist_ok=True)
    with open(os.path.join(KEEP_DIR, "daemon.log"), "ab") as log:
        subprocess.Popen([sys.executable, os.path.abspath(__file__), "--serve", path, str(IDLE)],
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True, close_fds=True)


def _connect_or_start(path):
    try:
        return _connect(path)
    except OSError:
        pass
    _start(path)
    until = time.time() + 15
    while time.time() < until:
        time.sleep(0.2)
        try:
            return _connect(path)
        except OSError:
            pass
    return None


def _line(c):
    buf = b""
    while not buf.endswith(b"\n"):
        chunk = c.recv(65536)
        if not chunk:
            break
        buf += chunk
    return buf


def keep(args):
    """The answer of the kept server (started when none runs). A server that stopped just before the call is started
    again, once."""
    path = _socket(args["server"])
    req = {k: v for k, v in args.items() if k != "keep"}
    data = json.dumps(req, ensure_ascii=False).encode("utf-8") + b"\n"
    for _ in range(2):
        c = _connect_or_start(path)
        if c is None:
            return {"error": "the language server kept in Termux did not start (log: %s)"
                    % os.path.join(KEEP_DIR, "daemon.log")}
        try:
            c.settimeout(float(args.get("timeout") or 40) + 30)
            c.sendall(data)
            line = _line(c)
        except socket.timeout:
            return {"error": "the language server kept in Termux did not answer in time"}
        except OSError:
            line = b""
        finally:
            c.close()
        if line:
            try:
                return json.loads(line.decode("utf-8", "replace"))
            except ValueError:
                return {"error": "the language server kept in Termux sent no answer"}
    return {"error": "the language server kept in Termux stopped before it answered"}


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--serve":
        serve(sys.argv[2], int(sys.argv[3]))
    else:
        try:
            result = run(json.loads(base64.b64decode(sys.argv[1]).decode("utf-8")))
        except Exception as exc:  # noqa: BLE001 - always one JSON answer
            result = {"error": str(exc)[:500]}
        sys.stdout.write(json.dumps(result, ensure_ascii=False))
