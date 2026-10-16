import threading
import unittest
from unittest import mock
from . import managed_linux as linux, mcp_bundles as bundles, runtime_manager as rm, tools


class ManagedLinuxTests(unittest.TestCase):
    def test_android_uses_linux_node_and_never_android_sharp_path(self):
        with mock.patch.object(rm, 'termux_home', return_value='/termux/home'):
            spec = linux.spec(bundles._item('android'))
        self.assertEqual(spec['command'], 'proot-distro')
        self.assertIn(linux.NODE, spec['args'])
        self.assertTrue(any('/opt/musabai/android/' in arg for arg in spec['args']))
        self.assertNotIn('npx', spec['args'])

    def test_missing_packages_are_installed_before_entry_verification(self):
        probes = [{'status':'completed','exit_code':code} for code in [1,1,1,0]]
        with mock.patch.object(rm,'execute',side_effect=probes), \
             mock.patch.object(rm,'process_start',return_value={'id':'setup'}) as start, \
             mock.patch.object(bundles,'_wait_termux_process'):
            linux.ensure(bundles._item('docling'))
        commands = [call.args[0] for call in start.call_args_list]
        self.assertEqual(commands[0], 'pkg install -y proot-distro')
        self.assertIn('debian:bookworm --name musabai-mcp', commands[1])
        self.assertTrue(any('python3 -m venv' in command for command in commands))
        self.assertTrue(any('docling-mcp[local]==3.3.0' in command for command in commands))
        self.assertFalse(any('uvx' in command for command in commands))

    def test_failed_verification_does_not_succeed(self):
        with mock.patch.object(rm,'execute',return_value={'status':'completed','exit_code':1}), \
             mock.patch.object(rm,'process_start',return_value={'id':'setup'}), \
             mock.patch.object(bundles,'_wait_termux_process'):
            with self.assertRaisesRegex(tools.ToolError,'verification failed'):
                linux.ensure(bundles._item('android'))

    def test_existing_working_environment_does_not_reinstall(self):
        with mock.patch.object(rm,'execute',return_value={'status':'completed','exit_code':0}), \
             mock.patch.object(rm,'process_start') as start:
            linux.ensure(bundles._item('browser-use'))
        start.assert_not_called()

    def test_native_failure_routes_to_linux_setup_not_uvx(self):
        state={'runtime':rm.TERMUX,'missing':[], 'unknown':[], 'stdio':True}
        with mock.patch.object(bundles,'_runtime_requirements',return_value=state), \
             mock.patch.object(linux,'ensure',side_effect=tools.ToolError('apt repository unreachable')), \
             mock.patch.object(bundles.mcp_config,'StdioServer') as server:
            with self.assertRaisesRegex(tools.ToolError,'apt repository unreachable'):
                bundles._test(bundles._item('docling'), '/tmp/project')
        server.assert_not_called()
