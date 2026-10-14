"""One request to a language server, then exit: what code_check runs, on this machine or inside Termux (sent through
the Termux bridge, so it needs only Python's standard library).

    python3 lsp_once.py <base64 JSON>
    {"server": ["pyright-langserver", "--stdio"], "root": "/abs/project", "files": ["/abs/a.py"],
     "action": "errors" | "definition" | "references", "line": 12, "character": 4, "timeout": 40}

Prints one JSON object: {"diagnostics": {path: [...]}} or {"locations": [...]}, or {"missing": "<command>"} when the
server is not installed, or {"error": "..."}.
"""
import base64
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request

LANGUAGE = {".py": "python", ".pyi": "python", ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
            ".jsx": "javascriptreact", ".ts": "typescript", ".mts": "typescript", ".cts": "typescript",
            ".tsx": "typescriptreact"}


def uri(path):
    return "file://" + urllib.parse.quote(os.path.abspath(path))


def path_of(u):
    return urllib.request.url2pathname(urllib.parse.urlparse(u).path)


class Server:
    def __init__(self, cmd, root):
        self.proc = subprocess.Popen(cmd, cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL)
        self.inbox = queue.Queue()
        self.next_id = 0
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        out = self.proc.stdout
        while True:
            length = 0
            while True:
                line = out.readline()
                if not line:
                    self.inbox.put(None)
                    return
                line = line.strip()
                if not line:
                    break
                if line.lower().startswith(b"content-length:"):
                    length = int(line.split(b":", 1)[1])
            body = out.read(length)
            try:
                self.inbox.put(json.loads(body.decode("utf-8", "replace")))
            except ValueError:
                continue

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
        """Requests the server makes of the client get a harmless answer, so it never waits for us."""
        method = msg.get("method", "")
        if method == "workspace/configuration":
            result = [None for _ in (msg.get("params") or {}).get("items") or []]
        elif method == "workspace/workspaceFolders":
            result = []
        else:
            result = None
        self.send({"id": msg["id"], "result": result})

    def wait(self, until, deadline, on_note=None):
        """Messages until until(msg) is true or the deadline passes; returns that message or None."""
        while True:
            left = deadline - time.time()
            if left <= 0:
                return None
            try:
                msg = self.inbox.get(timeout=min(left, 0.5))
            except queue.Empty:
                if until(None):
                    return None
                continue
            if msg is None:
                raise RuntimeError("the language server stopped")
            if "method" in msg and "id" in msg:
                self.answer(msg)
                continue
            if "method" in msg and on_note:
                on_note(msg)
            if until(msg):
                return msg

    def close(self):
        try:
            self.request("shutdown", None)
            self.notify("exit", None)
        except Exception:  # noqa: BLE001
            pass
        try:
            self.proc.wait(timeout=3)
        except Exception:  # noqa: BLE001
            self.proc.kill()


def run(args):
    cmd = args["server"]
    if not shutil.which(cmd[0]):
        return {"missing": cmd[0]}
    root = os.path.abspath(args.get("root") or ".")
    files = [os.path.abspath(f) for f in args.get("files") or []]
    deadline = time.time() + float(args.get("timeout") or 40)
    s = Server(cmd, root)
    try:
        init = s.request("initialize", {
            "processId": os.getpid(), "rootUri": uri(root), "rootPath": root,
            "workspaceFolders": [{"uri": uri(root), "name": os.path.basename(root) or "project"}],
            "capabilities": {"textDocument": {"publishDiagnostics": {"relatedInformation": False},
                                              "definition": {}, "references": {},
                                              "synchronization": {"didSave": True}},
                             "workspace": {"configuration": True, "workspaceFolders": True}},
            "initializationOptions": {}})
        if s.wait(lambda m: m is not None and m.get("id") == init, deadline) is None:
            return {"error": "the language server did not start in time"}
        s.notify("initialized", {})
        for f in files:
            with open(f, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            s.notify("textDocument/didOpen", {"textDocument": {
                "uri": uri(f), "languageId": LANGUAGE.get(os.path.splitext(f)[1].lower(), "plaintext"),
                "version": 1, "text": text}})
        if args.get("action", "errors") == "errors":
            got, last = {}, [time.time()]

            def note(msg):
                if msg.get("method") == "textDocument/publishDiagnostics":
                    p = msg.get("params") or {}
                    got[os.path.abspath(path_of(p.get("uri", "")))] = p.get("diagnostics") or []
                    last[0] = time.time()

            # every file answered, and nothing new for a moment (servers send a quick first pass, then the full one)
            s.wait(lambda m: all(f in got for f in files) and time.time() - last[0] > 1.5, deadline, note)
            out = {}
            for f in files:
                rows = []
                for d in got.get(f, []):
                    start = (d.get("range") or {}).get("start") or {}
                    rows.append({"line": start.get("line", 0) + 1, "column": start.get("character", 0) + 1,
                                 "severity": {1: "error", 2: "warning", 3: "info", 4: "hint"}.get(d.get("severity"),
                                                                                              "error"),
                                 "message": str(d.get("message") or "").strip(),
                                 "code": str(d.get("code") or (d.get("data") or {}).get("rule", "") or ""),
                                 "source": d.get("source") or ""})
                out[f] = rows
            return {"diagnostics": out, "complete": all(f in got for f in files)}
        method = "textDocument/definition" if args["action"] == "definition" else "textDocument/references"
        params = {"textDocument": {"uri": uri(files[0])},
                  "position": {"line": int(args.get("line", 1)) - 1, "character": int(args.get("character", 0))}}
        if method.endswith("references"):
            params["context"] = {"includeDeclaration": False}
        rid = s.request(method, params)
        msg = s.wait(lambda m: m is not None and m.get("id") == rid, deadline)
        if msg is None:
            return {"error": "the language server did not answer in time"}
        result = msg.get("result") or []
        if isinstance(result, dict):
            result = [result]
        locations = []
        for loc in result:
            u = loc.get("uri") or loc.get("targetUri") or ""
            rng = loc.get("range") or loc.get("targetSelectionRange") or {}
            locations.append({"path": path_of(u), "line": ((rng.get("start") or {}).get("line") or 0) + 1})
        return {"locations": locations}
    finally:
        s.close()


if __name__ == "__main__":
    try:
        result = run(json.loads(base64.b64decode(sys.argv[1]).decode("utf-8")))
    except Exception as exc:  # noqa: BLE001 - always one JSON answer
        result = {"error": str(exc)[:500]}
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
