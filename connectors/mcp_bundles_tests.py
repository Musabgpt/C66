import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from . import mcp_bundles, mcp_config, runtime_manager, settings, tools


class McpBundlesTest(unittest.TestCase):
    def test_catalog_batches_all_runtime_requirements_once(self):
        ready={'runtime':runtime_manager.TERMUX,'missing':[],'unknown':[],'stdio':True,'reason':'verified'}
        with mock.patch.object(runtime_manager,'requirements',return_value=ready) as requirements:
            mcp_bundles.catalog(None)
        requirements.assert_called_once()

    def test_first_install_failure_is_not_reported_ready(self):
        runtime = {"runtime": runtime_manager.TERMUX, "unknown": [], "missing": [], "stdio": True}
        self.assertEqual(mcp_bundles._lifecycle_status(
            mcp_bundles._item("android"), runtime, False, False, "sharp android-arm64 unsupported"
        ), "health_failed")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = self.temp.name
        self.env = mock.patch.object(settings, "HOME", os.path.join(self.root, "private"))
        self.env.start()
        self.addCleanup(self.env.stop)
        mcp_bundles._RUNNING.clear()
        mcp_bundles._LAST_ERRORS.clear()

    def test_exact_requested_bundle_catalog_is_real_and_honest(self):
        ids = [item["id"] for item in mcp_bundles.BUNDLES]
        self.assertEqual(ids, ["playwright", "browser-use", "open-browser-use", "docling", "github", "filesystem", "android", "memory"])
        self.assertTrue(all(item["repository"].startswith("https://github.com/") for item in mcp_bundles.BUNDLES))

        def no_runtime(commands):
            return {
                "runtime": "LOCAL_HOST",
                "missing": list(commands),
                "unknown": [],
                "reason": "No command is installed on the test host.",
                "stdio": True,
            }

        with mock.patch.object(runtime_manager, "requirements", side_effect=no_runtime), \
             mock.patch.dict(os.environ, {}, clear=True):
            catalog = {item["id"]: item for item in mcp_bundles.catalog(self.root)}
        self.assertEqual(catalog["playwright"]["status"], "tool_missing")
        self.assertEqual(catalog["browser-use"]["status"], "tool_missing")
        self.assertEqual(catalog["open-browser-use"]["status"], "tool_missing")
        self.assertEqual(catalog["docling"]["status"], "tool_missing")
        self.assertEqual(catalog["filesystem"]["status"], "tool_missing")
        self.assertEqual(catalog["memory"]["status"], "tool_missing")
        self.assertEqual(catalog["android"]["status"], "tool_missing")
        self.assertEqual(catalog["github"]["status"], "permission_required")
        self.assertFalse(any(item["enabled"] for item in catalog.values()))

    def test_termux_npx_ready_is_not_reported_as_npx_missing(self):
        ready = {
            "runtime": runtime_manager.TERMUX,
            "missing": [],
            "unknown": [],
            "reason": "npx verified inside Termux",
            "stdio": True,
        }
        with mock.patch.object(runtime_manager, "requirements", return_value=ready):
            catalog = {item["id"]: item for item in mcp_bundles.catalog(self.root)}
        self.assertEqual(catalog["playwright"]["status"], "ready")
        self.assertEqual(catalog["playwright"]["missing"], [])
        self.assertEqual(catalog["playwright"]["runtime"], runtime_manager.TERMUX)
        self.assertTrue(catalog["playwright"]["available"])
        self.assertEqual(catalog["playwright"]["start_method"], "runtime_process_start(stdio=true)")
        self.assertEqual(catalog["playwright"]["stop_method"], "runtime_process_stop")
        self.assertEqual(catalog["playwright"]["health_check"], "live process status + MCP tools/list")

    def test_filesystem_and_memory_are_scoped_to_current_project(self):
        fs = mcp_bundles._spec(mcp_bundles._item("filesystem"), self.root)
        memory = mcp_bundles._spec(mcp_bundles._item("memory"), self.root)
        self.assertEqual(fs["args"][-1], os.path.realpath(self.root))
        memory_file = memory["env"]["MEMORY_FILE_PATH"]
        self.assertTrue(memory_file.startswith(os.path.realpath(self.root) + os.sep))
        self.assertTrue(memory_file.endswith(os.path.join(".newal", "mcp-memory.jsonl")))

    def test_memory_uses_project_scoped_file_inside_termux_home(self):
        ready = {
            "runtime": runtime_manager.TERMUX,
            "missing": [],
            "unknown": [],
            "reason": "npx verified inside Termux",
            "stdio": True,
        }
        with mock.patch.object(runtime_manager, "requirements", return_value=ready), \
             mock.patch.object(runtime_manager, "termux_home", return_value="/data/data/com.termux/files/home"):
            memory = mcp_bundles._spec(mcp_bundles._item("memory"), self.root)
        memory_file = memory["env"]["MEMORY_FILE_PATH"]
        self.assertTrue(memory_file.startswith("/data/data/com.termux/files/home/"))
        self.assertNotIn(os.path.realpath(self.root), memory_file)
        self.assertIn("musabai-mcp-memory-", memory_file)

    def test_filesystem_is_not_falsely_available_for_app_private_root_in_termux(self):
        ready = {
            "runtime": runtime_manager.TERMUX,
            "missing": [],
            "unknown": [],
            "reason": "npx verified inside Termux",
            "stdio": True,
        }
        with mock.patch.object(runtime_manager, "requirements", return_value=ready):
            catalog = {item["id"]: item for item in mcp_bundles.catalog(self.root)}
        self.assertEqual(catalog["filesystem"]["status"], "needs_setup")
        self.assertFalse(catalog["filesystem"]["available"])

    def test_phase2_browser_bundle_commands_are_pinned_and_explicit(self):
        playwright = mcp_bundles._spec(mcp_bundles._item("playwright"), self.root)
        browser_use = mcp_bundles._spec(mcp_bundles._item("browser-use"), self.root)
        obu = mcp_bundles._spec(mcp_bundles._item("open-browser-use"), self.root)
        self.assertEqual(playwright["command"], "npx")
        self.assertIn("@playwright/mcp@0.0.83", playwright["args"])
        self.assertIn("--isolated", playwright["args"])
        self.assertIn("--headless", playwright["args"])
        self.assertIn("--no-sandbox", playwright["args"])
        self.assertIn("/data/data/com.termux/files/usr/bin/chromium-browser", playwright["args"])
        self.assertEqual(playwright["env"]["PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD"], "1")
        self.assertEqual(playwright["env"]["PLAYWRIGHT_BROWSERS_PATH"], "0")
        self.assertEqual(browser_use, {
            "command": "uvx",
            "args": ["browser-use==0.13.5", "--cli-mcp"],
            "env": {},
        })
        self.assertEqual(obu, {
            "command": "obu",
            "args": ["mcp", "stdio"],
            "env": {},
        })

    def test_docling_bundle_is_pinned_to_reviewed_local_stdio_contract(self):
        docling = mcp_bundles._spec(mcp_bundles._item("docling"), self.root)
        self.assertEqual(docling["command"], "uvx")
        self.assertEqual(docling["args"], [
            "--from", "docling-mcp[local]==3.3.0", "docling-mcp-server", "--transport", "stdio"
        ])
        self.assertEqual(docling["env"]["DOCLING_MCP_CONVERSION_MODE"], "local")
        self.assertEqual(docling["env"]["DOCLING_MCP_KEEP_IMAGES"], "false")

    def test_github_token_is_only_in_child_process_environment(self):
        item = mcp_bundles._item("github")
        with mock.patch.dict(os.environ, {"GH_TOKEN": "private-test-token"}, clear=True):
            spec = mcp_bundles._spec(item, self.root)
        self.assertEqual(spec["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"], "private-test-token")
        public = mcp_bundles.catalog(self.root)
        self.assertNotIn("private-test-token", repr(public))

    def test_managed_termux_npm_bundle_uses_direct_node_entry(self):
        item = mcp_bundles._item("playwright")
        ready = {
            "runtime": runtime_manager.TERMUX,
            "missing": [],
            "unknown": [],
            "reason": "Termux bridge verified",
            "stdio": True,
        }
        home = "/data/data/com.termux/files/home"
        with mock.patch.object(runtime_manager, "requirements", return_value=ready), \
             mock.patch.object(runtime_manager, "termux_home", return_value=home):
            spec = mcp_bundles._spec(item, self.root)
        self.assertEqual(spec["command"], "node")
        self.assertIn("/.musabai/mcp/playwright/", spec["args"][0])
        self.assertTrue(spec["args"][0].endswith("node_modules/@playwright/mcp/cli.js"))
        self.assertNotIn("npx", spec["args"])
        self.assertEqual(spec["termux_cwd"], os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(spec["args"][0])))))
        self.assertIn("--isolated", spec["args"])
        self.assertIn("--executable-path", spec["args"])

    def test_managed_termux_npm_bundle_installs_once_and_verifies_entry(self):
        item = mcp_bundles._item("memory")
        ready = {
            "runtime": runtime_manager.TERMUX,
            "missing": [],
            "unknown": [],
            "reason": "Termux bridge verified",
            "stdio": True,
        }
        home = "/data/data/com.termux/files/home"
        probes = [
            {"exit_code": 1, "status": "completed"},
            {"exit_code": 0, "status": "completed"},
        ]
        with mock.patch.object(runtime_manager, "requirements", return_value=ready), \
             mock.patch.object(runtime_manager, "termux_home", return_value=home), \
             mock.patch.object(runtime_manager, "execute", side_effect=probes) as execute, \
             mock.patch.object(runtime_manager, "process_start", return_value={"id":"npm-install"}) as start, \
             mock.patch.object(mcp_bundles, "_wait_termux_process") as wait, \
             mock.patch.object(runtime_manager, "_clear_cache"):
            mcp_bundles._ensure_termux_npm(item)
        self.assertEqual(execute.call_count, 2)
        command = start.call_args.args[0]
        self.assertIn("npm install --prefix", command)
        self.assertIn("@modelcontextprotocol/server-memory@0.6.2", command)
        self.assertFalse(start.call_args.kwargs["stdio"])
        wait.assert_called_once_with("npm-install", timeout=600)

    def test_managed_install_rejects_incomplete_or_failed_entry_probes(self):
        failures = [
            {"status": "timeout", "exit_code": None, "stderr": "probe diagnostic"},
            {"status": "completed", "exit_code": None, "stderr": "probe diagnostic"},
            {"status": "completed", "exit_code": 2, "stderr": "probe diagnostic"},
        ]
        for failure in failures:
            with self.subTest(failure=failure), \
                 mock.patch.object(runtime_manager, "termux_home", return_value=self.root), \
                 mock.patch.object(runtime_manager, "execute", return_value=failure), \
                 mock.patch.object(runtime_manager, "process_start") as start:
                with self.assertRaisesRegex(RuntimeError, "entry probe failed.*probe diagnostic"):
                    mcp_bundles._ensure_termux_npm(mcp_bundles._item("memory"))
                start.assert_not_called()

    def test_managed_install_requires_completed_successful_post_install_probe(self):
        for result in ({"status": "completed", "exit_code": 1},
                       {"status": "timeout", "exit_code": None},
                       {"status": "completed"}):
            with self.subTest(result=result), \
                 mock.patch.object(runtime_manager, "termux_home", return_value=self.root), \
                 mock.patch.object(runtime_manager, "execute", side_effect=[
                     {"status": "completed", "exit_code": 1}, result]), \
                 mock.patch.object(runtime_manager, "process_start", return_value={"id": "install"}), \
                 mock.patch.object(mcp_bundles, "_wait_termux_process"):
                with self.assertRaisesRegex(RuntimeError, "entry verification failed"):
                    mcp_bundles._ensure_termux_npm(mcp_bundles._item("memory"))

    def test_setup_requires_explicit_zero_exit_and_preserves_install_diagnostics(self):
        for code in (None, 1, 127):
            with self.subTest(code=code), mock.patch.object(runtime_manager, "process_status", return_value={
                "status": "completed", "exit_code": code, "logs": "npm failure diagnostic"
            }):
                with self.assertRaisesRegex(RuntimeError, "npm failure diagnostic"):
                    mcp_bundles._wait_termux_process("install")

    def test_playwright_termux_setup_installs_system_chromium_before_handshake(self):
        item = mcp_bundles._item("playwright")
        ready = {
            "runtime": runtime_manager.TERMUX,
            "missing": [],
            "unknown": [],
            "reason": "Termux bridge verified",
            "stdio": True,
        }
        chromium_missing = dict(ready, missing=["chromium-browser"])
        chromium_ready = dict(ready, missing=[])
        statuses = [
            {"status": "completed", "exit_code": 0, "logs": ""},
            {"status": "completed", "exit_code": 0, "logs": ""},
        ]
        with mock.patch.object(mcp_bundles, "_runtime_requirements", return_value=ready), \
             mock.patch.object(mcp_bundles, "_ensure_termux_npm"), \
             mock.patch.object(runtime_manager, "requirements", side_effect=[chromium_missing, chromium_ready]), \
             mock.patch.object(runtime_manager, "process_start", side_effect=[{"id":"repo"}, {"id":"chromium"}]) as start, \
             mock.patch.object(runtime_manager, "process_status", side_effect=statuses), \
             mock.patch.object(runtime_manager, "_clear_cache"):
            mcp_bundles._ensure_termux_setup(item)
        self.assertEqual(start.call_args_list[0].args[0], "pkg install -y x11-repo")
        self.assertEqual(start.call_args_list[1].args[0], "pkg install -y chromium")

    def test_supported_termux_stdio_bundles_get_long_first_handshake_window(self):
        ready = {
            "runtime": runtime_manager.TERMUX,
            "missing": [],
            "unknown": [],
            "reason": "Termux bridge verified",
            "stdio": True,
        }
        # Filesystem is intentionally excluded here because app-private Android
        # project roots are not accessible from Termux until a shared path is configured.
        for bundle_id in ("playwright", "browser-use", "open-browser-use", "docling", "github", "android", "memory"):
            seen = {}
            item = mcp_bundles._item(bundle_id)
            class FakeServer:
                def __init__(self, name, spec, cwd):
                    self.tools = [{"name":"tool","inputSchema":{"type":"object"}}]
                def start(self, timeout=60):
                    seen["timeout"] = timeout
                    return self
                def stop(self):
                    pass
            with self.subTest(bundle=bundle_id), \
                 mock.patch.object(mcp_bundles, "_ensure_termux_setup"), \
                 mock.patch.object(mcp_bundles, "_runtime_requirements", return_value=ready), \
                 mock.patch.object(runtime_manager, "termux_home", return_value="/data/data/com.termux/files/home"), \
                 mock.patch.object(mcp_config, "StdioServer", FakeServer):
                self.assertEqual(mcp_bundles._test(item, self.root), 1)
                self.assertEqual(seen["timeout"], 240)

    def test_enable_persists_only_after_successful_real_handshake(self):
        handler = SimpleNamespace(service=SimpleNamespace(get=lambda sid: SimpleNamespace(root=self.root)))
        handler._json = lambda data, status=200: setattr(self, "response", (data, status))
        handler._query = lambda: {"session": "session1"}

        with mock.patch.object(mcp_bundles, "_test", return_value=17):
            self.assertTrue(mcp_bundles.route(
                handler, "POST", "/api/mcp-bundles/enable",
                {"session": "session1", "id": "playwright"},
            ))
        self.assertEqual(self.response, ({"ok": True, "tools": 17, "enabled": True, "running": False}, 200))
        saved = mcp_config.read(self.root)
        self.assertEqual(saved["playwright"]["bundle"], "playwright")
        self.assertEqual(saved["playwright"]["tools"], 17)
        catalog = {item["id"]: item for item in mcp_bundles.catalog(self.root)}
        self.assertEqual(catalog["playwright"]["status"], "server_stopped")
        self.assertTrue(catalog["playwright"]["installed"])
        self.assertFalse(catalog["playwright"]["connected"])

        mcp_bundles.route(handler, "POST", "/api/mcp-bundles/disable",
                          {"session": "session1", "id": "playwright"})
        self.assertEqual(self.response, ({"ok": True, "enabled": False, "running": False}, 200))
        self.assertNotIn("playwright", mcp_config.read(self.root))

    def test_start_stop_and_reconnect_control_the_real_managed_process(self):
        ready = {
            "runtime": runtime_manager.TERMUX,
            "missing": [],
            "unknown": [],
            "reason": "Termux bridge verified",
            "stdio": True,
        }

        class FakeServer:
            starts = 0

            def __init__(self, name, spec, cwd):
                self.name = name
                self.spec = spec
                self.cwd = cwd
                self._termux = True
                self._termux_process_id = ""
                self.tools = []

            def start(self, timeout=45):
                type(self).starts += 1
                self._termux_process_id = "mcp-%d" % type(self).starts
                self.tools = [{"name": "tool", "inputSchema": {"type": "object"}}]
                return self

            def alive(self):
                return bool(self._termux_process_id)

            def _list_tools(self):
                return list(self.tools) or [{"name": "tool", "inputSchema": {"type": "object"}}]

            def stop(self):
                self._termux_process_id = ""

        item = mcp_bundles._item("playwright")
        with mock.patch.object(mcp_bundles, "_test", return_value=1):
            mcp_bundles._save_enabled(item, self.root, 1)

        with mock.patch.object(runtime_manager, "requirements", return_value=ready), \
             mock.patch.object(runtime_manager, "termux_home", return_value="/data/data/com.termux/files/home"), \
             mock.patch.object(mcp_config, "StdioServer", FakeServer), \
             mock.patch.object(runtime_manager, "process_stop", return_value={"ok": True}) as process_stop:
            started = mcp_bundles.perform(self.root, "playwright", "start")
            self.assertTrue(started["running"])
            catalog = {row["id"]: row for row in mcp_bundles.catalog(self.root)}
            self.assertEqual(catalog["playwright"]["status"], "server_running")
            self.assertTrue(catalog["playwright"]["connected"])
            self.assertTrue(catalog["playwright"]["running"])
            self.assertTrue(catalog["playwright"]["process_id"].startswith("mcp-"))

            stopped = mcp_bundles.perform(self.root, "playwright", "stop")
            self.assertFalse(stopped["running"])
            process_stop.assert_called_once()
            catalog = {row["id"]: row for row in mcp_bundles.catalog(self.root)}
            self.assertEqual(catalog["playwright"]["status"], "server_stopped")
            self.assertFalse(catalog["playwright"]["connected"])

            reconnected = mcp_bundles.perform(self.root, "playwright", "reconnect")
            self.assertTrue(reconnected["running"])
            catalog = {row["id"]: row for row in mcp_bundles.catalog(self.root)}
            self.assertEqual(catalog["playwright"]["status"], "server_running")

    def test_running_process_can_be_reattached_from_persisted_runtime_state(self):
        ready = {
            "runtime": runtime_manager.TERMUX,
            "missing": [],
            "unknown": [],
            "reason": "Termux bridge verified",
            "stdio": True,
        }

        class AttachedServer:
            def __init__(self, name, spec, cwd):
                self._termux = False
                self._termux_process_id = ""
                self.tools = []

            def alive(self):
                return self._termux and self._termux_process_id == "persisted-1"

        item = mcp_bundles._item("playwright")
        mcp_bundles._save_enabled(item, self.root, 2)
        mcp_bundles._save_runtime_state(self.root, {
            "playwright": {"process_id": "persisted-1", "started_at": 1}
        })
        mcp_bundles._RUNNING.clear()
        with mock.patch.object(runtime_manager, "requirements", return_value=ready), \
             mock.patch.object(runtime_manager, "termux_home", return_value="/data/data/com.termux/files/home"), \
             mock.patch.object(mcp_config, "StdioServer", AttachedServer):
            catalog = {row["id"]: row for row in mcp_bundles.catalog(self.root)}
        self.assertEqual(catalog["playwright"]["status"], "server_running")
        self.assertEqual(catalog["playwright"]["process_id"], "persisted-1")

    def test_failed_handshake_never_claims_enabled(self):
        handler = SimpleNamespace(service=SimpleNamespace(get=lambda sid: SimpleNamespace(root=self.root)))
        handler._json = lambda data, status=200: setattr(self, "response", (data, status))
        with mock.patch.object(mcp_bundles, "_test", side_effect=RuntimeError("handshake failed")):
            mcp_bundles.route(handler, "POST", "/api/mcp-bundles/enable",
                              {"session": "session1", "id": "memory"})
        self.assertEqual(self.response[1], 400)
        self.assertNotIn("memory", mcp_config.read(self.root))

    def test_termux_bridge_tool_error_is_returned_as_json_not_dropped_connection(self):
        handler = SimpleNamespace(service=SimpleNamespace(get=lambda sid: SimpleNamespace(root=self.root)))
        handler._json = lambda data, status=200: setattr(self, "response", (data, status))
        with mock.patch.object(mcp_bundles, "_test", side_effect=tools.ToolError("stdio request timed out")):
            self.assertTrue(mcp_bundles.route(
                handler, "POST", "/api/mcp-bundles/enable",
                {"session": "session1", "id": "playwright"},
            ))
        self.assertEqual(self.response[1], 400)
        self.assertIn("stdio request timed out", self.response[0]["error"])
        self.assertNotIn("playwright", mcp_config.read(self.root))


if __name__ == "__main__":
    unittest.main()

class ConcurrentMcpTests(unittest.TestCase):
    setUp = McpBundlesTest.setUp
    def test_stop_interrupts_wait_for_another_lifecycle_request(self):
        import threading
        from . import providers
        key = mcp_bundles._key(self.root, 'memory')
        lock = threading.Lock()
        lock.acquire()
        mcp_bundles._OPERATION_LOCKS[key] = lock
        cancel = threading.Event(); cancel.set()
        try:
            with mcp_bundles.cancel_scope(cancel), mock.patch.object(mcp_bundles, '_perform') as perform:
                with self.assertRaises(providers.Cancelled):
                    mcp_bundles.perform(self.root, 'memory', 'start')
            perform.assert_not_called()
        finally:
            lock.release()

    def test_simultaneous_start_creates_only_one_process(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor
        key = mcp_bundles._key(self.root, 'memory')
        entered, release = threading.Event(), threading.Event()
        starts = []
        class Server:
            tools = [{}]
            def __init__(self, *a): pass
            def start(self, **kw):
                starts.append(1); entered.set(); release.wait(2)
        runtime = {'runtime': 'LOCAL_HOST', 'unknown': [], 'missing': [], 'stdio': True}
        with mock.patch.object(mcp_bundles, '_installed_record', return_value={'tools': 1}), \
             mock.patch.object(mcp_bundles, '_running_server', side_effect=lambda *a: mcp_bundles._RUNNING.get(key)), \
             mock.patch.object(mcp_bundles, '_runtime_requirements', return_value=runtime), \
             mock.patch.object(mcp_bundles, '_spec', return_value={}), \
             mock.patch.object(mcp_bundles, '_remember_process'), \
             mock.patch.object(mcp_config, 'StdioServer', Server), ThreadPoolExecutor(2) as pool:
            first = pool.submit(mcp_bundles.perform, self.root, 'memory', 'start')
            self.assertTrue(entered.wait(1))
            second = pool.submit(mcp_bundles.perform, self.root, 'memory', 'start')
            threading.Event().wait(.05)
            release.set()
            self.assertTrue(first.result()['running']); self.assertTrue(second.result()['running'])
        self.assertEqual(len(starts), 1)
