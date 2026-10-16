"""Real HTTP MCP discovery/call, private persistence and live agent refresh."""
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch
from . import mcp_config, mcp, settings, connectors, runtime_manager, tools
from .agent import Agent
from .session import Session


class McpConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = self.temp.name
        self.env = patch.object(settings, 'HOME', os.path.join(self.root, 'private'))
        self.env.start(); self.addCleanup(self.env.stop)
        self.requests = []
        requests = self.requests

        class Server(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append((body, dict(self.headers)))
                if self.path == '/denied':
                    self.send_response(401); self.end_headers(); return
                if self.path == '/unauthorized':
                    self.send_response(401); self.end_headers(); self.wfile.write(b'private-fixture-token'); return
                if self.path == '/redirect':
                    self.send_response(302); self.send_header('Location', '/mcp'); self.end_headers(); return
                if body['method'] == 'notifications/initialized':
                    self.send_response(202); self.end_headers(); return
                if body['method'] == 'initialize':
                    result = {'protocolVersion': mcp.PROTOCOL, 'capabilities': {'tools': {}}, 'serverInfo': {'name':'fixture','version':'1'}}
                elif body['method'] == 'tools/list':
                    cursor = body.get('params', {}).get('cursor')
                    result = {'tools': [{'name':'second' if cursor else 'first', 'inputSchema': {'type':'object','properties':{}}}]}
                    if self.path == '/long':
                        result['tools'][0]['name'] = 'namespace.' + 'long' * 20 + ('second' if cursor else 'first')
                    if not cursor: result['nextCursor'] = 'page2'
                else: result = {'content':[{'type':'text','text':'real MCP reply'}]}
                self.send_response(200)
                self.send_header('Mcp-Session-Id','fixture-session')
                self.send_header('Content-Type','text/event-stream' if self.path == '/sse' else 'application/json')
                self.end_headers()
                data = json.dumps({'jsonrpc':'2.0','id':body['id'],'result':result}, indent=2)
                if self.path == '/sse': data = '\n'.join('data: ' + line for line in data.splitlines()) + '\n\n'
                self.wfile.write(data.encode())

        self.http = ThreadingHTTPServer(('127.0.0.1', 0), Server)
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.addCleanup(self.http.server_close); self.addCleanup(self.http.shutdown)
        self.url = 'http://127.0.0.1:%d/mcp' % self.http.server_port
        self.handler = SimpleNamespace(service=SimpleNamespace(get=lambda sid: SimpleNamespace(root=self.root)))
        self.handler._json = lambda data, status=200: setattr(self, 'response', (data, status))
        self.handler._query = lambda: {'session':'s'}

    def save(self, url=None):
        mcp_config.route(self.handler, 'POST', '/api/mcp-servers/save', {'session':'s','name':'fixture','url':url or self.url,'token':'private-fixture-token'})

    def test_registry_bearer_token_uses_real_handshake_and_private_storage(self):
        from . import mcp_registry
        info = {'installable': True, 'remote': self.url, 'version': '1', 'reasons': []}
        with patch.object(mcp_registry, 'inspect', return_value=info):
            result = mcp_registry.install(self.root, 'io.example/authenticated', 'secured', 'private-fixture-token')
        self.assertEqual(result['tools'], 2)
        self.assertTrue(all(headers.get('Authorization') == 'Bearer private-fixture-token' for _, headers in self.requests))
        self.assertNotIn('private-fixture-token', json.dumps(result))
        self.assertEqual(os.stat(mcp_config.path_for(self.root)).st_mode & 0o777, 0o600)

    def test_authentication_error_is_actionable_without_echoing_credentials(self):
        self.save(self.url.replace('/mcp','/unauthorized'))
        self.assertEqual(self.response[1], 400)
        self.assertIn('HTTP 401', self.response[0]['error'])
        self.assertIn('Authentication required', self.response[0]['error'])
        self.assertNotIn('private-fixture-token', self.response[0]['error'])
        self.assertEqual(mcp_config.read(self.root), {})

    def test_real_discovery_call_and_private_persistence(self):
        self.save()
        self.assertEqual(self.response, ({'ok':True,'tools':2}, 200))
        path = mcp_config.path_for(self.root)
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        self.assertEqual(mcp_config.configs(self.root + '-other'), {})
        mcp_config.route(self.handler, 'GET', '/api/mcp-servers')
        self.assertNotIn('private-fixture-token', json.dumps(self.response))
        manager = mcp.Manager(self.root)
        self.addCleanup(manager.stop_all)
        names = [t['function']['name'] for t in manager.schemas()]
        self.assertEqual(names, ['mcp__fixture__first', 'mcp__fixture__second'])
        self.assertEqual(manager.call(names[0], {}), 'real MCP reply')
        self.assertTrue(all(h.get('Authorization') == 'Bearer private-fixture-token' for _, h in self.requests))

    def test_failure_is_not_saved_or_claimed_connected(self):
        self.save(self.url.replace('/mcp','/denied'))
        self.assertEqual(self.response[1], 400)
        self.assertFalse(mcp_config.path_for(self.root).exists())

    def test_long_remote_tool_names_use_model_safe_aliases(self):
        self.save(self.url.replace('/mcp','/long'))
        manager = mcp.Manager(self.root)
        self.addCleanup(manager.stop_all)
        tool = manager.schemas()[0]['function']['name']
        self.assertLessEqual(len(tool),64)
        self.assertEqual(manager.call(tool,{}),'real MCP reply')
        self.assertTrue(self.requests[-1][0]['params']['name'].startswith('namespace.'))

    def test_sse_catalog_and_session_header(self):
        self.save(self.url.replace('/mcp','/sse'))
        self.assertEqual(self.response, ({'ok':True,'tools':2},200))
        self.assertEqual(self.requests[-1][1].get('Mcp-Session-Id'),'fixture-session')

    def test_redirect_does_not_forward_token_or_replace_working_config(self):
        self.save()
        before = mcp_config.path_for(self.root).read_text()
        count = len(self.requests)
        self.save(self.url.replace('/mcp','/redirect'))
        self.assertEqual(self.response[1],400)
        self.assertEqual(len(self.requests),count+1)
        self.assertEqual(mcp_config.path_for(self.root).read_text(),before)

    def test_added_and_removed_tools_refresh_existing_agent(self):
        a = Agent(Session(self.root), client=SimpleNamespace(on_device=False, local=False, spec={}))
        self.addCleanup(a.memory.close); self.addCleanup(a.mcp.stop_all)
        with patch.object(connectors, 'names', return_value=[]):
            self.assertFalse(any(d['function']['name'].startswith('mcp__fixture__') for d in a.schemas()))
            self.save()
            self.assertTrue(any(d['function']['name']=='mcp__fixture__first' for d in a.schemas()))
            mcp_config.route(self.handler, 'POST', '/api/mcp-servers/remove', {'session':'s','name':'fixture'})
            self.assertFalse(any(d['function']['name'].startswith('mcp__fixture__') for d in a.schemas()))

    def test_managed_stdio_uses_termux_runtime_lifecycle(self):
        calls = []
        def request(process_id, message, timeout=120):
            calls.append((process_id, message, timeout))
            if message.get('id') is None:
                return {'ok': True, 'id': process_id, 'notification': True}
            method = message.get('method')
            if method == 'initialize':
                result = {
                    'protocolVersion': mcp.PROTOCOL,
                    'capabilities': {'tools': {}},
                    'serverInfo': {'name': 'fixture', 'version': '1'},
                }
            elif method == 'tools/list':
                result = {'tools': [{'name': 'echo', 'inputSchema': {'type': 'object', 'properties': {}}}]}
            else:
                result = {'content': [{'type': 'text', 'text': 'termux reply'}]}
            return {'ok': True, 'id': process_id, 'response': {
                'jsonrpc': '2.0', 'id': message['id'], 'result': result
            }}

        spec = {
            'command': 'npx',
            'args': ['-y', '@modelcontextprotocol/server-memory'],
            'env': {'MCP_TEST_TOKEN': 'private-termux-secret'},
        }
        with patch.object(runtime_manager, 'requirements', return_value={
            'runtime': runtime_manager.TERMUX,
            'missing': [],
            'unknown': [],
            'reason': 'verified in Termux',
            'bridge': True,
            'stdio': True,
        }), patch.object(runtime_manager, 'process_start', return_value={
            'ok': True, 'id': 'mcp-1', 'pid': 101, 'status': 'running', 'mode': 'stdio', 'attached': True
        }) as start, patch.object(runtime_manager, 'process_request', side_effect=request), \
             patch.object(runtime_manager, 'process_status', return_value={
                 'ok': True, 'id': 'mcp-1', 'pid': 101, 'status': 'running', 'mode': 'stdio', 'attached': True
             }), patch.object(runtime_manager, 'process_stop') as stop:
            server = mcp_config.StdioServer('memory', spec, self.root).start()
            self.assertTrue(server.alive())
            self.assertEqual(server.tools[0]['name'], 'echo')
            result = server.request('tools/call', {'name': 'echo', 'arguments': {}})
            self.assertEqual(result['content'][0]['text'], 'termux reply')
            server.stop()

        start.assert_called_once()
        self.assertTrue(start.call_args.kwargs['stdio'])
        self.assertIn('npx', start.call_args.args[0])
        self.assertNotIn('private-termux-secret', start.call_args.args[0])
        self.assertEqual(start.call_args.kwargs['env']['MCP_TEST_TOKEN'], 'private-termux-secret')
        self.assertTrue(any(msg.get('method') == 'initialize' for _, msg, _ in calls))
        self.assertTrue(any(msg.get('method') == 'tools/list' for _, msg, _ in calls))
        stop.assert_called_once_with('mcp-1')

    def test_termux_handshake_error_includes_process_stderr(self):
        spec = {'command':'npx','args':['-y','fixture'],'env':{}}
        with patch.object(runtime_manager, 'requirements', return_value={
            'runtime': runtime_manager.TERMUX,
            'missing': [],
            'unknown': [],
            'reason': 'verified in Termux',
            'bridge': True,
            'stdio': True,
        }), patch.object(runtime_manager, 'process_start', return_value={
            'ok': True, 'id': 'mcp-timeout', 'pid': 101, 'status': 'running', 'mode': 'stdio', 'attached': True
        }), patch.object(runtime_manager, 'process_request', side_effect=tools.ToolError('stdio request timed out')), \
             patch.object(runtime_manager, 'process_status', return_value={
                 'ok': True, 'id': 'mcp-timeout', 'pid': 101, 'status': 'running', 'mode':'stdio',
                 'attached': True, 'logs': 'npm ERR! diagnostic fixture'
             }), patch.object(runtime_manager, 'process_stop'):
            server = mcp_config.StdioServer('fixture', spec, self.root)
            with self.assertRaisesRegex(RuntimeError, 'npm ERR! diagnostic fixture'):
                server.start(timeout=1)

    def test_input_rejects_unsafe_urls_and_missing_session(self):
        for url in ['http://example.com/mcp','https://user:secret@example.com/mcp','https://example.com/mcp?token=secret']:
            with self.assertRaises(ValueError): mcp_config.validate('safe',url)
        with self.assertRaises(ValueError): mcp_config.validate('bad__name',self.url)
        mcp_config.route(self.handler,'POST','/api/mcp-servers/save',{'name':'test','url':self.url})
        self.assertEqual(self.response[1],400)
