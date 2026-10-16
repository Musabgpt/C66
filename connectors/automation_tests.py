"""Autonomous provisioning uses real registry operations and truthful verification."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

from . import tools, settings, extensions, plugins, mcp_bundles, mcp_config


class AutomationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = self.temp.name
        self.patch = mock.patch.object(settings, 'HOME', self.root + '/private')
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.ctx = SimpleNamespace(root=self.root, cancel=threading.Event(), session=SimpleNamespace(
            mode='full-auto', dirs=[], tool_names=[], root=self.root), emit=lambda ev: None)

    def invoke(self, tool_name, **args):
        self.assertTrue(tool_name in tools.REGISTRY, 'agent cannot provision ' + tool_name + ' yet')
        return tools.REGISTRY[tool_name].fn(self.ctx, **args)

    def test_dependency_install_runs_pkg_then_verifies_both_uv_commands(self):
        from . import runtime_manager as rm
        with mock.patch.object(rm, 'execute', side_effect=[{'status':'completed','exit_code':127},
                {'status':'completed','exit_code':0,'stdout':'uv 0.12\nuvx 0.12'}]), \
             mock.patch.object(rm, 'process_start', return_value={'id':'install-uv'}) as start, \
             mock.patch.object(mcp_bundles, '_wait_termux_process', return_value={'exit_code':0}):
            _, result = self.invoke('dependency_install', name='uvx')
        self.assertTrue(result['ok'])
        self.assertEqual(start.call_args.args[0], 'pkg install -y uv')

    def test_dependency_install_does_not_claim_success_after_failed_verification(self):
        from . import runtime_manager as rm
        with mock.patch.object(rm, 'execute', return_value={'status':'completed','exit_code':127,'stderr':'uvx missing'}), \
             mock.patch.object(rm, 'process_start', return_value={'id':'install-uv'}), \
             mock.patch.object(mcp_bundles, '_wait_termux_process', return_value={'exit_code':0}):
            with self.assertRaisesRegex(tools.ToolError, 'uvx missing'):
                self.invoke('dependency_install', name='uv')

    def test_android_bash_version_probe_uses_termux(self):
        from . import runtime_manager as rm
        with mock.patch.object(rm, '_phone_available', return_value=True), \
             mock.patch.object(rm, 'execute', return_value={'status':'completed','exit_code':0,'stdout':'uv 0.12','runtime':'TERMUX'}) as execute:
            text, meta = tools.t_bash(self.ctx, 'uvx --version 2>&1; uv --version 2>&1')
        execute.assert_called_once()
        self.assertEqual(meta['runtime'], 'TERMUX')
        self.assertIn('uv 0.12', text)

    def test_linux_install_failure_prevents_mcp_configuration(self):
        from . import managed_linux, runtime_manager as rm
        ready = {'runtime':rm.TERMUX, 'missing':['uvx'], 'unknown':[], 'stdio':True, 'reason':'connected'}
        with mock.patch.object(mcp_bundles, '_runtime_requirements', return_value=ready), \
             mock.patch.object(managed_linux, 'ensure', side_effect=tools.ToolError('pkg download failed')), \
             mock.patch.object(mcp_config, 'StdioServer') as server:
            with self.assertRaisesRegex(tools.ToolError, 'pkg download failed'):
                self.invoke('capability_ensure', bundle='docling')
        server.assert_not_called()
        self.assertNotIn('docling', mcp_config.read(self.root))

    def test_new_skill_is_discovered_without_restarting_session(self):
        text, meta = self.invoke('skill_create', name='browser-check', description='Verify a browser task',
            instructions='Read the actual page title before reporting success.', evidence='Validated with browser tools')
        self.assertTrue(meta['ok'])
        self.assertIn('browser-check', extensions.skills(self.root))
        self.assertIn('actual page title', extensions.skills(self.root)['browser-check']['body'])
        with self.assertRaises(tools.ToolError):
            self.invoke('skill_create', name='../escape', description='bad', instructions='bad', evidence='bad')
        self.assertFalse((Path(self.root).parent / 'escape').exists())

    def test_first_skill_becomes_callable_in_existing_agent_session(self):
        from . import agent, session, automation
        s = session.Session(self.root)
        a = agent.Agent(s, client=SimpleNamespace(spec={}, local=False, on_device=False))
        self.addCleanup(a.memory.close)
        a.schemas()
        self.assertNotIn('skill', s.tool_names)
        ctx = agent.ToolContext(a)
        automation.create_skill(ctx, 'verified-loop', 'Use a verified loop', 'Inspect, repair, test.', 'Passing project test')
        self.assertIn('skill', {d['function']['name'] for d in a.schemas()})

    def test_saved_conversation_receives_new_dependency_tools_after_upgrade(self):
        from . import agent, session
        s = session.Session(self.root)
        s.tool_names = ['read', 'bash']  # a persisted pre-upgrade schema
        a = agent.Agent(s, client=SimpleNamespace(spec={}, local=False, on_device=False))
        self.addCleanup(a.memory.close)
        names = {d['function']['name'] for d in a.schemas()}
        self.assertIn('dependency_install', names)
        self.assertIn('capability_ensure', names)

    def test_bundled_plugin_is_installed_into_project_without_git_or_shell(self):
        self.invoke('plugin_install', name='data')
        self.assertTrue(any(p['name'] == 'data' for p in plugins.listing(self.root)))
        self.invoke('plugin_install', name='data')  # idempotent provisioning
        with self.assertRaises(tools.ToolError):
            self.invoke('plugin_install', name='https://unreviewed.example/code')

    def test_mcp_ensure_verifies_before_persisting_and_starts_process(self):
        ready = {'runtime': 'LOCAL_HOST', 'missing': [], 'unknown': [], 'stdio': True, 'reason': 'host'}
        class Server:
            tools = [{'name': 'read_graph'}]
            def __init__(self, *args): pass
            def start(self, **kwargs): return self
            def stop(self): pass
            def alive(self): return True
            def _list_tools(self): return self.tools
        with mock.patch.object(mcp_bundles, '_runtime_requirements', return_value=ready), \
             mock.patch.object(mcp_config, 'StdioServer', Server):
            _, meta = self.invoke('capability_ensure', bundle='memory')
        self.assertTrue(meta['ok'])
        self.assertGreater(mcp_config.read(self.root)['memory']['tools'], 0)
        mcp_bundles._RUNNING.clear()

    def test_mcp_failure_cannot_be_saved_as_installed(self):
        with mock.patch.object(mcp_bundles, '_test', side_effect=RuntimeError('initialize failed')):
            with self.assertRaisesRegex(tools.ToolError, 'initialize failed'):
                self.invoke('capability_ensure', bundle='memory')
        self.assertNotIn('memory', mcp_config.read(self.root))

    def test_cancelled_preparation_cannot_start_or_install_anything(self):
        self.ctx.cancel.set()
        with mock.patch.object(mcp_bundles, 'perform') as perform:
            with self.assertRaises(Exception):
                self.invoke('capability_ensure', bundle='memory')
            perform.assert_not_called()

    def test_read_only_mode_cannot_install_capabilities(self):
        self.ctx.session.mode='read-only'
        with self.assertRaises(tools.ToolError):
            self.invoke('plugin_install', name='data')
        self.assertEqual(plugins.listing(self.root), [])

class AutomaticDependencyTests(unittest.TestCase):
    def test_connected_termux_prepares_uv_without_model_request(self):
        from . import automation as a, runtime_manager as rm
        setup = a.DependencyPreparation()
        service = SimpleNamespace(lock=threading.RLock(), updating='')
        with mock.patch.object(rm, '_termux_record', return_value={'status':'connected'}), \
             mock.patch.object(a, 'ensure_dependency', return_value={'ok':True,'exit_code':0,'stdout':'uv 1\nuvx 1'}) as install:
            setup.tick(service, threading.Event())
            setup.tick(service, threading.Event())
        install.assert_called_once()
        self.assertEqual(setup.status()['state'], 'ready')
        self.assertFalse(service.dependency_setup_busy)

    def test_failure_visible_and_retries_bounded(self):
        from . import automation as a, runtime_manager as rm
        setup = a.DependencyPreparation()
        service = SimpleNamespace(lock=threading.RLock(), updating='')
        with mock.patch.object(rm, '_termux_record', return_value={'status':'connected'}), \
             mock.patch.object(a, 'ensure_dependency', side_effect=tools.ToolError('pkg: repository unavailable')) as install:
            for now in [0, 1, 61, 62, 182, 1000]:
                setup.tick(service, threading.Event(), now=now)
        self.assertEqual(install.call_count, 3)
        self.assertEqual(setup.status()['state'], 'failed')
        self.assertIn('repository unavailable', setup.status()['error'])
        self.assertFalse(service.dependency_setup_busy)

    def test_disconnect_invalidates_ready_and_reconnect_rechecks(self):
        from . import automation as a, runtime_manager as rm
        setup = a.DependencyPreparation()
        service = SimpleNamespace(lock=threading.RLock(), updating='')
        with mock.patch.object(rm, '_termux_record', side_effect=[{'status':'connected'}, {'status':'disconnected'}, {'status':'connected'}]), \
             mock.patch.object(a, 'ensure_dependency', return_value={'ok':True,'exit_code':0}) as install:
            setup.tick(service, threading.Event())
            setup.tick(service, threading.Event())
            self.assertEqual(setup.status()['state'], 'waiting_termux')
            setup.tick(service, threading.Event())
        self.assertEqual(install.call_count, 2)

    def test_update_activation_defers_dependency_install(self):
        from . import automation as a, runtime_manager as rm
        setup = a.DependencyPreparation()
        service = SimpleNamespace(lock=threading.RLock(), updating='candidate')
        with mock.patch.object(rm, '_termux_record', return_value={'status':'connected'}), \
             mock.patch.object(a, 'ensure_dependency') as install:
            setup.tick(service, threading.Event())
        install.assert_not_called()
