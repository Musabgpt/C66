"""code_check: language servers without VS Code. The real pyright runs where it is installed (skipped elsewhere);
the Termux route, the first-time install and the finishing gate are checked with the bridge stood in for."""
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from . import tools  # noqa: F401 - registers every tool first, as the engine does

HAS_PYRIGHT = bool(shutil.which("pyright-langserver"))
BROKEN = "def add(a, b):\n    return a + b\n\nprint(add(1))\nundefined_name\nimport not_installed_pkg\n"


class CodeCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.realpath(self.tmp.name)

    def write(self, rel, text):
        p = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        return p

    def ctx(self, changed=()):
        return SimpleNamespace(root=self.root, cwd=self.root, changed=set(changed), cancel=None,
                               session=SimpleNamespace(turn=1, changes=lambda turn: [], root=self.root, dirs=[]))

    def test_offered_with_the_tools(self):
        self.assertIn("code_check", tools.default_set({}, self.root))

    def test_problems_leave_out_missing_packages_and_the_gate_keeps_only_certain_errors(self):
        from . import code_check
        results = {"python": {"diagnostics": {os.path.join(self.root, "a.py"): [
            {"line": 4, "column": 7, "severity": "error", "message": "Argument missing", "code": "reportCallIssue"},
            {"line": 6, "column": 8, "severity": "error", "message": "Import could not be resolved",
             "code": "reportMissingImports"},
            {"line": 5, "column": 1, "severity": "warning", "message": "Expression value is unused",
             "code": "reportUnusedExpression"},
            {"line": 7, "column": 1, "severity": "error", "message": "Type mismatch", "code": "reportArgumentType"}]}}}
        lines, n = code_check.problems(results, self.root)
        self.assertEqual(n, {"errors": 2, "warnings": 1, "missing_packages": 1})
        self.assertEqual(lines[0], "a.py:4:7: error: Argument missing (reportCallIssue)")
        certain, _ = code_check.problems(results, self.root, certain=True)
        self.assertEqual(certain, ["a.py:4:7: error: Argument missing (reportCallIssue)"])

    def test_an_npm_package_runs_through_node_even_off_path(self):
        """Termux: npm's bin folder may be off PATH and #!/usr/bin/env fails there, so the package's own script runs
        with node; a server found nowhere says where it looked."""
        from . import lsp_once
        pkg = os.path.join(self.root, "prefix", "lib", "node_modules", "pyright")
        self.write(os.path.join(pkg, "package.json"),
                   '{"name": "pyright", "bin": {"pyright-langserver": "langserver.index.js"}}')
        self.write(os.path.join(pkg, "langserver.index.js"), "// server\n")
        which = {"node": "/opt/node/bin/node"}
        with mock.patch.object(lsp_once.shutil, "which", lambda n: which.get(n)), \
                mock.patch.object(lsp_once.subprocess, "run", side_effect=OSError("no npm")), \
                mock.patch.dict(os.environ, {"PREFIX": os.path.join(self.root, "prefix")}):
            cmd, _ = lsp_once.resolve(["pyright-langserver", "--stdio"])
            self.assertEqual(cmd, ["/opt/node/bin/node", os.path.join(pkg, "langserver.index.js"), "--stdio"])
            missing = lsp_once.run({"server": ["typescript-language-server", "--stdio"], "files": []})
        self.assertEqual(missing["missing"], "typescript-language-server")
        self.assertIn(os.path.join(self.root, "prefix", "lib", "node_modules"), missing["looked"])

    def test_first_use_in_termux_installs_the_servers_once(self):
        from . import code_check, runtime_manager
        self.write("a.py", "x = 1\n")
        started = []
        with mock.patch.object(code_check, "where", lambda kind: ""), \
                mock.patch.object(code_check, "_termux", lambda: True), \
                mock.patch.object(runtime_manager, "process_start",
                                  lambda cmd: started.append(cmd) or {"id": "p1", "status": "running"}), \
                mock.patch.object(runtime_manager, "process_status", lambda pid: {"status": "running"}), \
                mock.patch.dict(code_check._install, {"id": None, "started": 0.0}):
            out, _ = tools.REGISTRY["code_check"].fn(self.ctx(), paths=["a.py"])
            self.assertIn("Installing the language servers in Termux", out)
            out2, _ = tools.REGISTRY["code_check"].fn(self.ctx(), paths=["a.py"])
            self.assertIn("being installed", out2)
        self.assertEqual(len(started), 1)
        self.assertIn("npm install -g", started[0])

    @unittest.skipUnless(HAS_PYRIGHT, "pyright is not installed here")
    def test_real_errors_definition_and_references(self):
        self.write("a.py", BROKEN)
        self.write("b.py", "from a import add\nprint(add(2, 3))\n")
        fn = tools.REGISTRY["code_check"].fn
        out, meta = fn(self.ctx(), paths=["a.py", "b.py"])
        self.assertIn("a.py:4:7: error: Argument missing for parameter \"b\"", out)
        self.assertIn("\"undefined_name\" is not defined", out)
        self.assertIn("not installed where the checker runs", out)
        self.assertEqual(meta["errors"], 2)
        out, _ = fn(self.ctx(), action="definition", path="b.py", line=2, symbol="add")
        self.assertTrue(out.startswith("a.py:1: def add(a, b):"), out)
        out, _ = fn(self.ctx(), action="references", path="a.py", line=1, symbol="add")
        self.assertIn("b.py:2: print(add(2, 3))", out)

    @unittest.skipUnless(HAS_PYRIGHT, "pyright is not installed here")
    def test_through_the_termux_bridge(self):
        """The client goes to Termux inside the command (a heredoc), with no file to copy first."""
        from . import code_check, runtime_manager

        def bridge(command):
            env = dict(os.environ, MUSAB_LSP_IDLE="3")
            p = subprocess.run(["bash", "-lc", command], capture_output=True, text=True, timeout=90, env=env)
            return {"stdout": p.stdout, "stderr": p.stderr, "exit_code": p.returncode}
        path = self.write("a.py", BROKEN)
        with mock.patch.object(runtime_manager, "execute", bridge):
            res = code_check.check(self.root, [path], place="termux")
        rows = res["python"]["diagnostics"][path]
        self.assertIn("reportCallIssue", [r["code"] for r in rows])

    @unittest.skipUnless(HAS_PYRIGHT, "pyright is not installed here")
    def test_the_gate_holds_a_turn_back_only_for_certain_errors(self):
        from . import code_check
        bad = self.write("a.py", BROKEN)
        lines = code_check.gate(self.ctx([bad]))
        self.assertTrue(any("reportCallIssue" in x for x in lines))
        self.assertFalse(any("reportMissingImports" in x or "warning" in x for x in lines))
        good = self.write("c.py", "def add(a, b):\n    return a + b\n\nprint(add(1, 2))\n")
        self.assertEqual(code_check.gate(self.ctx([good])), [])


# A stand-in for pyright, so the kept server runs wherever Python does. It answers the way pyright does: a text sent
# again is answered again; a watched-file notice is answered for the open files; "NEEDS" is an error only while some
# other .py file on disk says BAD (it is read when answering, like an unopened import).
FAKE_SERVER = r'''
import json
import os
import sys
from urllib.parse import unquote, urlparse

LOG = os.environ.get("FAKE_LOG")
if LOG:
    with open(LOG, "a", encoding="utf-8") as f:
        f.write("start\n")
docs = {}
ROW = {"range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 3}}, "severity": 1}


def out(msg):
    data = json.dumps(dict(msg, jsonrpc="2.0")).encode("utf-8")
    sys.stdout.buffer.write(b"Content-Length: %d\r\n\r\n" % len(data) + data)
    sys.stdout.buffer.flush()


def where(u):
    return unquote(urlparse(u).path)


def publish(path):
    version, text = docs[path]
    rows = []
    if "BAD" in text:
        rows.append(dict(ROW, message="bad code", code="reportCallIssue"))
    if "NEEDS" in text:
        for base, _, files in os.walk(os.getcwd()):
            for f in files:
                other = os.path.join(base, f)
                if other != path and f.endswith(".py"):
                    with open(other, encoding="utf-8") as h:
                        if "BAD" in h.read():
                            rows.append(dict(ROW, message="depends on a file that says BAD", code="reportCallIssue"))
    out({"method": "textDocument/publishDiagnostics",
         "params": {"uri": "file://" + path, "version": version, "diagnostics": rows}})


buf = sys.stdin.buffer
while True:
    length = 0
    while True:
        line = buf.readline()
        if not line:
            sys.exit(0)
        line = line.strip()
        if not line:
            break
        if line.lower().startswith(b"content-length:"):
            length = int(line.split(b":", 1)[1])
    msg = json.loads(buf.read(length).decode("utf-8"))
    method, params = msg.get("method"), msg.get("params") or {}
    if method == "initialize":
        out({"id": msg["id"], "result": {"capabilities": {}}})
    elif method == "textDocument/didOpen":
        d = params["textDocument"]
        docs[where(d["uri"])] = (d["version"], d["text"])
        publish(where(d["uri"]))
    elif method == "textDocument/didChange":
        d = params["textDocument"]
        docs[where(d["uri"])] = (d["version"], params["contentChanges"][-1]["text"])
        publish(where(d["uri"]))
    elif method == "textDocument/didClose":
        docs.pop(where(params["textDocument"]["uri"]), None)
    elif method == "workspace/didChangeWatchedFiles":
        for path in list(docs):
            publish(path)
    elif method == "textDocument/definition":
        u = params["textDocument"]["uri"]
        out({"id": msg["id"], "result": [dict(range=ROW["range"], uri=u)]})
    elif method == "shutdown":
        out({"id": msg["id"], "result": None})
    elif method == "exit":
        sys.exit(0)
'''


def _put(root, rel, text):
    with open(os.path.join(root, rel), "w", encoding="utf-8") as f:
        f.write(text)
    return os.path.join(root, rel)


def serve_in_thread(lsp_once, path, idle):
    """The kept server on its socket, in a thread of this process, once it answers."""
    threading.Thread(target=lsp_once.serve, args=(path, idle), daemon=True).start()
    until = time.time() + 5
    while time.time() < until:
        try:
            lsp_once._connect(path).close()
            return
        except OSError:
            time.sleep(0.05)
    raise AssertionError("the kept server did not start")


class KeptServerTests(unittest.TestCase):
    """The language server kept between calls (lsp_once keep), against FAKE_SERVER."""

    def setUp(self):
        from . import lsp_once
        self.L = lsp_once
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.realpath(self.tmp.name)
        stub = tempfile.TemporaryDirectory()      # outside the project: the stand-in's own text says BAD
        self.addCleanup(stub.cleanup)
        self.fake = _put(stub.name, "server_stub.py", FAKE_SERVER)
        self.log = os.path.join(self.root, "starts.log")
        self.server = ["pyright-langserver", "--stdio"]
        for p in (mock.patch.object(lsp_once, "KEEP_DIR", os.path.join(self.root, "keep")),
                  mock.patch.object(lsp_once, "QUIET", 0.2),
                  mock.patch.object(lsp_once, "resolve", lambda cmd: ([sys.executable, self.fake, "--stdio"], [])),
                  mock.patch.dict(os.environ, {"FAKE_LOG": self.log})):
            p.start()
            self.addCleanup(p.stop)
        os.makedirs(lsp_once.KEEP_DIR)
        self.path = lsp_once._socket(self.server)

    def daemon(self):
        serve_in_thread(self.L, self.path, 3)

    def check(self, *files, **more):
        args = dict({"server": self.server, "root": self.root, "files": [os.path.join(self.root, f) for f in files],
                     "action": "errors", "timeout": 20}, **more)
        return self.L.keep(args)

    def messages(self, res, rel):
        return [d["message"] for d in res["diagnostics"][os.path.join(self.root, rel)]]

    def starts(self):
        try:
            with open(self.log, encoding="utf-8") as f:
                return f.read().count("start")
        except OSError:
            return 0

    def test_the_kept_server_starts_once_and_answers_for_the_text_now(self):
        _put(self.root, "a.py", "x = BAD\n")
        self.daemon()
        self.assertEqual(self.messages(self.check("a.py"), "a.py"), ["bad code"])
        _put(self.root, "a.py", "x = 1\n")
        res = self.check("a.py")
        self.assertEqual(self.messages(res, "a.py"), [])          # the fixed text, not the old answer
        self.assertTrue(res["complete"])
        self.assertEqual(self.starts(), 1)

    def test_a_file_changed_on_disk_that_is_not_open_reaches_the_answer(self):
        _put(self.root, "a.py", "NEEDS\n")
        _put(self.root, "b.py", "ok = 1\n")
        self.daemon()
        self.assertEqual(self.messages(self.check("a.py"), "a.py"), [])
        _put(self.root, "b.py", "BAD\n")      # a.py is unchanged; b.py, which is not open, changed on disk
        self.assertEqual(self.messages(self.check("a.py"), "a.py"), ["depends on a file that says BAD"])

    def test_a_check_with_nothing_changed_answers_at_once(self):
        _put(self.root, "a.py", "x = 1\n")
        self.daemon()
        self.check("a.py")
        started = time.time()
        res = self.check("a.py")
        self.assertLess(time.time() - started, 1.0)
        self.assertTrue(res["complete"])

    def test_definition_through_the_kept_server(self):
        _put(self.root, "a.py", "x = 1\n")
        self.daemon()
        res = self.check("a.py", action="definition", line=1, character=0)
        self.assertEqual(res["locations"], [{"path": os.path.join(self.root, "a.py"), "line": 1}])

    def test_a_leftover_socket_file_is_replaced_and_an_idle_server_stops(self):
        open(self.path, "w").close()
        t = threading.Thread(target=self.L.serve, args=(self.path, 1), daemon=True)
        t.start()
        t.join(10)
        self.assertFalse(t.is_alive())
        self.assertFalse(os.path.exists(self.path))

    def test_a_one_shot_check_still_works(self):
        _put(self.root, "a.py", "x = BAD\n")
        res = self.L.run({"server": self.server, "root": self.root, "files": [os.path.join(self.root, "a.py")],
                          "action": "errors", "timeout": 20})
        self.assertEqual(self.messages(res, "a.py"), ["bad code"])
        self.assertTrue(res["complete"])

    def test_the_termux_route_writes_the_client_and_keeps_the_server(self):
        from . import code_check, runtime_manager
        name = "musab-fake-langserver"          # its own name: the test never reaches an installed pyright
        bin_dir = os.path.join(self.root, "bin")
        os.makedirs(bin_dir)
        shim = os.path.join(bin_dir, name)
        with open(shim, "w", encoding="utf-8") as f:
            f.write('#!/bin/sh\nexec "%s" "%s" "$@"\n' % (sys.executable, self.fake))
        os.chmod(shim, 0o755)
        env = dict(os.environ, HOME=os.path.join(self.root, "home"), MUSAB_LSP_IDLE="3",
                   PATH=bin_dir + os.pathsep + os.environ.get("PATH", ""))

        def bridge(command):
            p = subprocess.run(["bash", "-c", command], capture_output=True, text=True, timeout=90, env=env)
            return {"stdout": p.stdout, "stderr": p.stderr, "exit_code": p.returncode}
        path = _put(self.root, "a.py", "x = BAD\n")
        with mock.patch.dict(code_check.SERVERS, {"python": [name, "--stdio"]}), \
                mock.patch.object(runtime_manager, "execute", bridge):
            res = code_check.check(self.root, [path], place="termux")
        self.assertEqual([r["message"] for r in res["python"]["diagnostics"][path]], ["bad code"])
        self.assertTrue(os.path.isfile(os.path.join(self.root, "home", ".cache", "musab-lsp", "lsp_once.py")))


@unittest.skipUnless(HAS_PYRIGHT, "pyright is not installed here")
class RealKeptServerTests(unittest.TestCase):
    def test_the_real_server_is_kept_and_answers_each_edit(self):
        from . import lsp_once
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = os.path.realpath(tmp.name)
        server = ["pyright-langserver", "--stdio"]
        with mock.patch.object(lsp_once, "KEEP_DIR", os.path.join(root, "keep")), \
                mock.patch.object(lsp_once, "QUIET", 0.5):
            os.makedirs(lsp_once.KEEP_DIR)
            serve_in_thread(lsp_once, lsp_once._socket(server), 8)

            def check(path, **more):
                args = dict({"server": server, "root": root, "files": [path], "action": "errors",
                             "timeout": 40}, **more)
                return lsp_once.keep(args)
            a = _put(root, "a.py", BROKEN)
            codes = [d["code"] for d in check(a)["diagnostics"][a]]
            self.assertIn("reportCallIssue", codes)
            self.assertIn("reportUndefinedVariable", codes)
            _put(root, "a.py", "def add(a, b):\n    return a + b\n\nprint(add(1, 2))\n")
            self.assertEqual(check(a)["diagnostics"][a], [])          # the fix shows, nothing from the old answer
            c = _put(root, "c.py", "from d import twice\nprint(twice(1))\n")
            d = _put(root, "d.py", "def twice(x):\n    return 2 * x\n")
            self.assertEqual(check(c)["diagnostics"][c], [])
            _put(root, "d.py", "def twice(x, y):\n    return 2 * x\n")   # d.py is not open: the server learns of it
            self.assertIn("reportCallIssue", [x["code"] for x in check(c)["diagnostics"][c]])
            res = check(c, action="definition", line=1, character=14)  # 'twice' on the import line
            self.assertEqual(res["locations"], [{"path": d, "line": 1}])


if __name__ == "__main__":
    unittest.main()
