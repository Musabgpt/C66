"""User-added remote MCP servers: private project configuration and verified discovery."""
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import tempfile
import threading
import time
from urllib.parse import urlsplit
from . import settings, mcp

LOCK = threading.RLock()
LEGACY_PROTOCOLS = {'2024-11-05', '2025-03-26', '2025-06-18', '2025-11-25'}
PATHS = {'/api/mcp-servers', '/api/mcp-servers/save', '/api/mcp-servers/test', '/api/mcp-servers/remove'}


def path_for(root):
    key = hashlib.sha256(os.path.realpath(root).encode()).hexdigest()
    return Path(settings.HOME) / 'private-mcp' / (key + '.json')


def read(root):
    try:
        data = json.loads(path_for(root).read_text())
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}


def configs(root):
    if not root:
        return {}
    with LOCK:
        return {name: dict(value['spec'], _musab_managed=True) for name, value in read(root).items()}


def save(root, data):
    path = path_for(root)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.mcp-')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def validate(name, url, token=''):
    if not isinstance(name, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,23}', name) or '__' in name:
        raise ValueError('Use a server name such as my-server (24 characters maximum)')
    if not isinstance(url, str) or len(url) > 2000:
        raise ValueError('Enter the MCP server URL')
    u = urlsplit(url)
    loopback = u.hostname in {'localhost', '127.0.0.1', '::1'}
    if not u.hostname or u.username or u.password or u.fragment or u.query or (
            u.scheme != 'https' and not (u.scheme == 'http' and loopback)):
        raise ValueError('Use HTTPS, or HTTP on localhost; put credentials in the token field, not the URL')
    if not isinstance(token, str) or len(token) > 8192 or any(c.isspace() for c in token):
        raise ValueError('Invalid access token')
    return {'url': url, 'headers': {'Authorization': 'Bearer ' + token} if token else {}}


class MCPHTTPError(RuntimeError):
    pass


class HttpServer(mcp.HttpServer):
    def request(self, method, params, timeout=120):
        if method == 'tools/call':
            params = dict(params, name=getattr(self, 'aliases', {}).get(params.get('name'), params.get('name')))
        return super().request(method, params, timeout)

    def _post(self, msg, timeout):
        # Stream does not follow redirects: credentials stay on the specified endpoint.
        from . import providers
        headers = dict(self.spec.get('headers') or {})
        headers.update({'MCP-Protocol-Version': getattr(self, 'protocol', mcp.PROTOCOL),
                        'Accept': 'application/json, text/event-stream'})
        if self.session:
            headers['Mcp-Session-Id'] = self.session
        stream = None
        deadline = time.monotonic() + min(timeout, 30)
        try:
            stream = providers.Stream(self.spec['url'], msg, headers, timeout=min(timeout, 30))
            response = stream.resp
            if response.status >= 300:
                reasons = {401:'Authentication required: configure this service access token or sign in',
                           403:'Access denied: check account permission and service subscription',
                           404:'MCP endpoint not found', 429:'Service rate limit reached; retry later'}
                reason = reasons.get(response.status, 'Remote service error' if response.status >= 500 else 'Redirect or HTTP request rejected')
                raise MCPHTTPError('MCP HTTP %s: %s' % (response.status, reason))
            self.session = response.getheader('Mcp-Session-Id') or self.session
            if 'id' not in msg:
                return {}
            if 'text/event-stream' in (response.getheader('Content-Type') or ''):
                size, chunks, result = 0, [], None
                while time.monotonic() < deadline:
                    raw = response.readline(65537)
                    size += len(raw)
                    if len(raw) > 65536 or size > 2 * 1024 * 1024:
                        raise RuntimeError('MCP response exceeds the size limit')
                    if not raw:
                        break
                    line = raw.decode('utf-8').rstrip('\r\n')
                    if line.startswith('data:'):
                        chunks.append(line[5:].lstrip())
                    elif not line and chunks:
                        item = json.loads('\n'.join(chunks)); chunks = []
                        if item.get('id') == msg['id']:
                            result = item
                            break
            else:
                raw = response.read(2 * 1024 * 1024 + 1)
                if len(raw) > 2 * 1024 * 1024:
                    raise RuntimeError('MCP response exceeds the size limit')
                result = json.loads(raw)
        except MCPHTTPError:
            raise
        except (TimeoutError, OSError) as exc:
            raise RuntimeError('MCP transport failure (%s); check network/TLS reachability' % type(exc).__name__) from None
        except (ValueError, UnicodeError):
            raise RuntimeError('MCP endpoint returned invalid JSON or event-stream data') from None
        finally:
            if stream is not None:
                stream.close()
        if 'id' in msg and (not isinstance(result, dict) or result.get('id') != msg['id']):
            raise RuntimeError('MCP returned an invalid response ID')
        return result

    def start(self, timeout=30):
        deadline = time.monotonic() + timeout
        result = self.request('initialize', {'protocolVersion': mcp.PROTOCOL, 'capabilities': {},
                                            'clientInfo': {'name': 'MusabAI', 'version': '1'}}, timeout)
        self.protocol = result.get('protocolVersion')
        if self.protocol not in LEGACY_PROTOCOLS | {mcp.PROTOCOL}:
            raise RuntimeError('MCP server selected an unsupported protocol version')
        self._post({'jsonrpc': '2.0', 'method': 'notifications/initialized'}, timeout)
        self.tools = []
        self.aliases = {}
        cursor, seen, names = None, set(), set()
        for _ in range(20):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError('MCP discovery timed out')
            result = self.request('tools/list', {'cursor': cursor} if cursor else {}, remaining)
            batch = result.get('tools')
            if not isinstance(batch, list):
                raise RuntimeError('MCP did not return a tool catalog')
            prepared = []
            for tool in batch:
                name = tool.get('name', '') if isinstance(tool, dict) else ''
                schema = tool.get('inputSchema') if isinstance(tool, dict) else None
                if not isinstance(name, str) or not 1 <= len(name) <= 256 or name in names or not isinstance(schema, dict) or schema.get('type') != 'object':
                    raise RuntimeError('MCP returned an invalid tool definition')
                names.add(name)
                budget = 64 - len('mcp__' + self.name + '__')
                alias = name
                if len(name) > budget or not re.fullmatch(r'[A-Za-z0-9_-]+', name):
                    alias = re.sub(r'[^A-Za-z0-9_-]', '_', name)[:budget-11] + '_' + hashlib.sha256(name.encode()).hexdigest()[:10]
                if alias in self.aliases:
                    raise RuntimeError('MCP tool names collide')
                self.aliases[alias] = name
                prepared.append(dict(tool, name=alias))
            self.tools.extend(prepared)
            if len(self.tools) > 512:
                raise RuntimeError('MCP tool catalog exceeds 512 tools')
            cursor = result.get('nextCursor')
            if not cursor:
                if not self.tools:
                    raise RuntimeError('MCP server has no usable tools')
                return self
            if not isinstance(cursor, str) or cursor in seen:
                raise RuntimeError('MCP returned an invalid pagination cursor')
            seen.add(cursor)
        raise RuntimeError('MCP tool pagination did not finish')



class StdioServer(mcp.StdioServer):
    """Managed stdio server that runs inside Termux on Android.

    Local desktop/test hosts keep using the upstream subprocess transport. On
    Android, the command is started once through runtime_manager's durable
    localhost process lifecycle and JSON-RPC is exchanged through the
    authenticated /process/request bridge endpoint.
    """

    def __init__(self, name, spec, cwd):
        super().__init__(name, spec, cwd)
        self._termux_process_id = ""
        self._termux = False
        self.aliases = {}

    def _prepare_tools(self):
        self.aliases = {}
        prepared, names = [], set()
        budget = 64 - len('mcp__' + self.name + '__')
        for tool in self.tools:
            name = tool.get('name', '') if isinstance(tool, dict) else ''
            schema = tool.get('inputSchema') if isinstance(tool, dict) else None
            if not isinstance(name, str) or not name or name in names or not isinstance(schema, dict):
                raise RuntimeError('MCP returned an invalid tool definition')
            names.add(name)
            alias = name
            if len(name) > budget or not re.fullmatch(r'[A-Za-z0-9_-]+', name):
                alias = re.sub(r'[^A-Za-z0-9_-]', '_', name)[:max(1, budget-11)] + '_' + hashlib.sha256(name.encode()).hexdigest()[:10]
            if alias in self.aliases:
                raise RuntimeError('MCP tool names collide')
            self.aliases[alias] = name
            prepared.append(dict(tool, name=alias))
        if not prepared or len(prepared) > 512:
            raise RuntimeError('MCP server has an invalid tool catalog')
        self.tools = prepared

    def _termux_command(self):
        command = str(self.spec.get('command') or '')
        args = [str(x) for x in self.spec.get('args') or []]
        if not command:
            raise RuntimeError('MCP stdio command is missing')
        env = self.spec.get('env') or {}
        for key in env:
            if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key):
                raise RuntimeError('MCP environment contains an invalid variable name')
        argv = ' '.join(shlex.quote(x) for x in [command] + args)
        termux_cwd = str(self.spec.get('termux_cwd') or '').strip()
        if termux_cwd:
            if not termux_cwd.startswith('/') or '\x00' in termux_cwd:
                raise RuntimeError('Invalid Termux MCP working directory')
            return 'cd %s && %s' % (shlex.quote(termux_cwd), argv)
        return argv

    def _termux_message(self, message, timeout):
        from . import runtime_manager, tools
        if not self._termux_process_id:
            raise RuntimeError('MCP stdio process is not started')
        try:
            data = runtime_manager.process_request(self._termux_process_id, message, timeout)
        except tools.ToolError as exc:
            detail = ''
            try:
                state = runtime_manager.process_status(self._termux_process_id)
                logs = str(state.get('logs') or '').strip()
                if logs:
                    detail = ' | stderr: ' + logs[-6000:]
            except Exception:
                pass
            raise RuntimeError(str(exc) + detail) from exc
        response = data.get('response')
        if message.get('id') is None:
            return {}
        if not isinstance(response, dict) or response.get('id') != message.get('id'):
            raise RuntimeError('MCP returned an invalid response ID')
        if 'error' in response:
            error = response.get('error')
            if isinstance(error, dict):
                error = error.get('message') or error
            raise RuntimeError(str(error))
        result = response.get('result')
        return result if isinstance(result, dict) else {}

    def request(self, method, params, timeout=120):
        if method == 'tools/call':
            params = dict(params, name=self.aliases.get(params.get('name'), params.get('name')))
        if not self._termux:
            return super().request(method, params, timeout)
        self._id += 1
        return self._termux_message({
            'jsonrpc': '2.0',
            'id': self._id,
            'method': method,
            'params': params,
        }, timeout)

    def _notify(self, method, params=None, timeout=30):
        if not self._termux:
            self._send({'jsonrpc': '2.0', 'method': method, 'params': params or {}})
            return
        self._termux_message({
            'jsonrpc': '2.0',
            'method': method,
            'params': params or {},
        }, timeout)

    def start(self, timeout=60):
        from . import runtime_manager
        state = runtime_manager.requirements([self.spec.get('command')])
        if state['unknown']:
            raise RuntimeError(state['reason'])
        if state['missing']:
            raise RuntimeError('Missing requirements: ' + ', '.join(state['missing']))
        if state['runtime'] != runtime_manager.TERMUX:
            super().start(timeout)
            self._prepare_tools()
            return self
        if not state.get('stdio'):
            raise RuntimeError('Termux localhost bridge does not support MCP stdio')
        self._termux = True
        started = runtime_manager.process_start(
            self._termux_command(),
            stdio=True,
            env={str(k): str(v) for k, v in (self.spec.get('env') or {}).items()},
        )
        if started.get('status') != 'running' or not started.get('id'):
            raise RuntimeError('MCP stdio process did not start')
        self._termux_process_id = started['id']
        try:
            result = self.request('initialize', {
                'protocolVersion': mcp.PROTOCOL,
                'capabilities': {},
                'clientInfo': {'name': 'MusabAI', 'version': '1'},
            }, timeout)
            protocol = result.get('protocolVersion')
            if protocol not in LEGACY_PROTOCOLS | {mcp.PROTOCOL}:
                raise RuntimeError('MCP server selected an unsupported protocol version')
            self._notify('notifications/initialized', timeout=timeout)
            self.tools = self._list_tools()
            self._prepare_tools()
            return self
        except Exception:
            self.stop()
            raise

    def alive(self):
        if not self._termux:
            return super().alive()
        if not self._termux_process_id:
            return False
        from . import runtime_manager
        try:
            state = runtime_manager.process_status(self._termux_process_id)
        except Exception:
            return False
        if state.get('status') == 'running' and state.get('attached', True):
            return True
        if state.get('status') == 'running' and not state.get('attached', True):
            try:
                runtime_manager.process_stop(self._termux_process_id)
            except Exception:
                pass
        return False

    def stop(self):
        if not self._termux:
            return super().stop()
        process_id, self._termux_process_id = self._termux_process_id, ''
        if not process_id:
            return
        from . import runtime_manager
        try:
            runtime_manager.process_stop(process_id)
        except Exception:
            pass


def refresh_agent(agent):
    path = path_for(agent.session.root)
    try:
        revision = path.stat().st_mtime_ns
    except FileNotFoundError:
        revision = 0
    if getattr(agent, '_custom_mcp_revision', None) != revision:
        agent.mcp.stop_all()
        agent._schemas = None
        agent.session.tool_names = [n for n in agent.session.tool_names if not n.startswith('mcp__')]
        agent._custom_mcp_revision = revision


def route(handler, method, path, body=None):
    if path not in PATHS:
        return False
    try:
        data = handler._query() if method == 'GET' else (body or {})
        sid = data.get('session', '')
        if not isinstance(sid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', sid):
            raise ValueError('Open a project conversation first')
        session = handler.service.get(sid)
        root = session.root
        if method == 'GET' and path == '/api/mcp-servers':
            with LOCK:
                servers = read(root)
            handler._json({'servers': [dict(
                name=name,
                url=value['spec'].get('url') or ' '.join(
                    [str(value['spec'].get('command') or '')] +
                    [str(x) for x in value['spec'].get('args') or []]
                ).strip(),
                transport='http' if value['spec'].get('url') else 'stdio',
                bundle=value.get('bundle', ''),
                registry=value.get('registry', ''), registry_version=value.get('registry_version', ''),
                authenticated=bool(value['spec'].get('headers', {}).get('Authorization')),
                tools=value['tools'], tested_at=value['tested_at'])
                for name, value in servers.items()]})
            return True
        if method != 'POST' or path == '/api/mcp-servers':
            handler._json({'error': 'Method not allowed'}, 405)
            return True
        name = data.get('name')
        with LOCK:
            existing = read(root).get(name) if isinstance(name, str) else None
        if path == '/api/mcp-servers/remove':
            with LOCK:
                servers = read(root)
                if name not in servers:
                    raise ValueError('Unknown saved MCP server')
                del servers[name]
                save(root, servers)
            handler._json({'ok': True})
            return True
        if path == '/api/mcp-servers/test':
            if not existing:
                raise ValueError('Unknown saved MCP server')
            spec = existing['spec']
        else:
            spec = validate(name, data.get('url'), data.get('token', ''))
        server = HttpServer(name, spec, root) if spec.get('url') else StdioServer(name, spec, root)
        try:
            server.start()
            count = len(server.tools)
        finally:
            server.stop()
        with LOCK:
            servers = read(root)
            if path.endswith('/test') and name not in servers:
                raise ValueError('Server was removed during the test')
            record = {'spec': spec, 'tools': count, 'tested_at': int(time.time())}
            if existing:
                for marker in ('bundle', 'registry', 'registry_version'):
                    if existing.get(marker):
                        record[marker] = existing[marker]
            servers[name] = record
            save(root, servers)
        handler._json({'ok': True, 'tools': count})
    except (ValueError, KeyError, OSError, RuntimeError, TypeError) as exc:
        message = str(exc)
        secret = (body or {}).get('token', '')
        if secret:
            message = message.replace(secret, '[redacted]')
        handler._json({'error': 'MCP connection failed: ' + message[-6000:] + '. No unverified configuration was saved.'}, 400)
    return True
