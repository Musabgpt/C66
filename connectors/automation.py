"""Agent-accessible provisioning; installation is never substituted for verification."""
import json
import os
import time
from pathlib import Path
import re
import threading

from . import tools

NAMES = ['capability_catalog', 'capability_ensure', 'skill_create', 'plugin_install',
         'mcp_registry_search', 'mcp_registry_install', 'dependency_install']
_INSTALL_LOCK = threading.Lock()


class DependencyPreparation:
    """Engine-owned provisioning, independent of model availability and chat turns."""
    def __init__(self):
        self.lock = threading.RLock()
        self.record = {'state': 'waiting_termux', 'error': '', 'attempts': 0}
        self.next_attempt = 0

    def status(self):
        with self.lock:
            return dict(self.record)

    def retry(self):
        with self.lock:
            if self.record['state'] == 'failed':
                self.record.update(state='waiting_termux', attempts=0, error='')
                self.next_attempt = 0

    def tick(self, service, stop, now=None):
        from . import runtime_manager as rm, providers
        now = time.monotonic() if now is None else now
        connected = rm._termux_record().get('status') == 'connected'
        with self.lock:
            if not connected:
                self.record.update(state='waiting_termux', attempts=0, error='')
                self.next_attempt = 0
                return
            if self.record['state'] in {'ready', 'installing'} or self.record['attempts'] >= 3 or now < self.next_attempt:
                return
            with service.lock:
                if service.updating or stop.is_set():
                    return
                service.dependency_setup_busy = True
            self.record.update(state='installing', error='', attempts=self.record['attempts'] + 1)
        try:
            result = ensure_dependency('uv', stop)
            if not result.get('ok') or result.get('exit_code') != 0:
                raise tools.ToolError('uv/uvx verification failed: ' + json.dumps(result, ensure_ascii=False))
            with self.lock:
                self.record.update(state='ready', error='', output=result.get('stdout', ''))
        except providers.Cancelled:
            with self.lock:
                self.record.update(state='waiting_termux', error='Preparation interrupted')
        except Exception as exc:
            with self.lock:
                self.next_attempt = now + 60 * self.record['attempts']
                self.record.update(state='failed', error=str(exc)[:3000])
        finally:
            with service.lock:
                service.dependency_setup_busy = False


DEPENDENCIES = DependencyPreparation()


def start_dependencies(service):
    if os.environ.get('NEWAL_DISABLE_AUTOMATION') == '1' or not os.environ.get('NEWAL_PACKAGED_ENGINE'):
        return None
    stop = threading.Event()
    def work():
        while not stop.is_set():
            try:
                DEPENDENCIES.tick(service, stop)
            except Exception as exc:
                with DEPENDENCIES.lock:
                    DEPENDENCIES.record.update(state='failed', error=str(exc)[:3000])
            stop.wait(5)
    threading.Thread(target=work, name='musabai-dependency-setup', daemon=True).start()
    return stop


def ensure_dependency(name, cancel=None, emit=None):
    """Install official Termux packages and verify executables in that same runtime."""
    from . import runtime_manager as rm, mcp_bundles, providers
    recipes = {
        'uv': ('uv', 'uv --version && uvx --version'),
        'uvx': ('uv', 'uv --version && uvx --version'),
        'node': ('nodejs-lts', 'node --version && npm --version'),
        'npm': ('nodejs-lts', 'node --version && npm --version'),
        'python': ('python', 'python --version'),
        'git': ('git', 'git --version'),
    }
    if name not in recipes:
        raise tools.ToolError('Supported managed dependencies: ' + ', '.join(recipes))
    while not _INSTALL_LOCK.acquire(timeout=0.2):
        if cancel is not None and cancel.is_set():
            raise providers.Cancelled()
    try:
        if cancel is not None and cancel.is_set():
            raise providers.Cancelled()
        package, probe = recipes[name]
        result = rm.execute(probe, rm.TERMUX)
        if result.get('status') == 'completed' and result.get('exit_code') == 0:
            return dict(result, ok=True, dependency=name, installed=True)
        if result.get('status') != 'completed':
            raise tools.ToolError('Dependency probe did not complete: ' + json.dumps(result, ensure_ascii=False))
        if emit:
            emit({'type':'status', 'text':'تثبيت ' + package + ' والتحقق منه داخل Termux…'})
        with mcp_bundles.cancel_scope(cancel):
            process = rm.process_start('pkg install -y ' + package)
            mcp_bundles._wait_termux_process(process['id'], timeout=600)
        rm._clear_cache()
        result = rm.execute(probe, rm.TERMUX)
        if result.get('status') != 'completed' or result.get('exit_code') != 0:
            raise tools.ToolError('Dependency verification failed: ' + json.dumps(result, ensure_ascii=False))
        return dict(result, ok=True, dependency=name, installed=True)
    finally:
        _INSTALL_LOCK.release()


@tools.tool('dependency_install', 'Install and verify an official Termux dependency without manual shell steps. uv/uvx use pkg install uv; installation succeeds only after both executables run. This does not prove every Python package supports Android.',
            {'name': {'type':'string','enum':['uv','uvx','node','npm','python','git']}}, ['name'], 'exec')
def dependency_install(ctx, name):
    _writable(ctx)
    try:
        result = ensure_dependency(name, getattr(ctx, 'cancel', None), getattr(ctx, 'emit', None))
        return json.dumps(result, ensure_ascii=False), result
    except (RuntimeError, OSError) as exc:
        raise tools.ToolError(str(exc)) from exc


def route_dependency_probe(ctx, command):
    """Route environment-only bash probes; project commands keep their original cwd."""
    from . import runtime_manager as rm
    names = r'(?:uvx?|node|npm|npx|python|git|bash)'
    part = r'(?:' + names + r'\s+(?:--version|-V|-v)|command\s+-v\s+' + names + r')\s*(?:2>&1)?'
    if not re.fullmatch(r'\s*' + part + r'(?:\s*(?:;|&&)\s*' + part + r')*\s*;?\s*', command):
        return None
    if not rm._phone_available():
        return None
    _writable(ctx)
    data = rm.execute(command, rm.TERMUX)
    output = str(data.get('stdout') or '') + str(data.get('stderr') or '')
    if getattr(ctx, 'emit', None):
        ctx.emit({'type':'output','text':output})
    return 'TERMUX · exit %s\n%s' % (data.get('exit_code'), output), {
        'runtime':rm.TERMUX, 'exit':data.get('exit_code'), 'output':output, 'command':command}


def _writable(ctx):
    from . import providers
    if getattr(ctx, 'cancel', None) is not None and ctx.cancel.is_set():
        raise providers.Cancelled()
    if getattr(getattr(ctx, 'session', None), 'mode', '') == 'read-only':
        raise tools.ToolError('Read-only mode cannot install capabilities')


def _refresh(ctx):
    from . import extensions
    ctx.skills = extensions.skills(ctx.root)
    agent = getattr(ctx, 'agent', None)
    if agent is not None:
        agent.skills = ctx.skills
        allowed = (getattr(agent, 'agent_def', None) or {}).get('tools')
        if ctx.skills and 'skill' not in agent.session.tool_names and (not allowed or 'skill' in allowed):
            agent.session.tool_names.append('skill')
        agent._schemas = None


@tools.tool('capability_catalog', 'Inspect bundled MCPs, installed skills and bundled plugins before choosing a missing capability.', {}, [], 'read')
def catalog(ctx):
    from . import mcp_bundles, extensions, plugins
    data = {'mcp': mcp_bundles.catalog(ctx.root),
            'skills': [{'name': s['name'], 'description': s['description']} for s in extensions.skills(ctx.root).values()],
            'plugin_marketplaces': plugins.marketplaces()}
    return json.dumps(data, ensure_ascii=False), {'ok': True}


@tools.tool('capability_ensure', 'Install and verify a bundled MCP, start it, and verify live tools/list. Use when its tools are needed for the current task. Never invent credentials or bypass a runtime blocker.',
            {'bundle': tools._s('bundle ID from capability_catalog')}, ['bundle'], 'exec')
def ensure(ctx, bundle):
    from . import mcp_bundles
    _writable(ctx)
    try:
        item = mcp_bundles._item(bundle)
        with mcp_bundles.cancel_scope(getattr(ctx, 'cancel', None)):
            ctx.emit({'type': 'status', 'text': 'Preparing and verifying MCP: ' + item['name']})
            if not mcp_bundles._installed_record(item, ctx.root):
                mcp_bundles.perform(ctx.root, bundle, 'enable')
            mcp_bundles.perform(ctx.root, bundle, 'start')
            result = mcp_bundles.perform(ctx.root, bundle, 'test')
        if result.get('tools', 0) <= 0:
            raise RuntimeError('MCP live verification returned no tools')
        _refresh(ctx)
        return json.dumps(result, ensure_ascii=False), dict(result, ok=True)
    except (ValueError, RuntimeError, OSError, tools.ToolError) as exc:
        raise tools.ToolError(str(exc)) from exc


@tools.tool('skill_create', 'Save an evidence-backed reusable SKILL.md in this project and load it immediately. A skill contains instructions, not a new executable tool or model training.',
            {k: tools._s(v) for k, v in {'name': 'lowercase skill name', 'description': 'when to use it',
             'instructions': 'reusable steps and verification', 'evidence': 'verified result supporting these steps'}.items()},
            ['name', 'description', 'instructions', 'evidence'], 'edit')
def create_skill(ctx, name, description, instructions, evidence):
    _writable(ctx)
    if not re.fullmatch(r'[a-z][a-z0-9-]{0,63}', str(name)):
        raise tools.ToolError('Use a lowercase skill name without paths')
    if any(not isinstance(x, str) or not x.strip() for x in (description, instructions, evidence)):
        raise tools.ToolError('Skill instructions, description and real evidence are required')
    if len(instructions) > 24000 or len(description) > 500 or len(evidence) > 2000:
        raise tools.ToolError('Skill exceeds the bounded instructions size')
    root = Path(ctx.root).resolve()
    directory = root / '.newal' / 'skills' / name
    if directory.is_symlink() or not directory.resolve().is_relative_to(root):
        raise tools.ToolError('Skill path escapes the project')
    path = directory / 'SKILL.md'
    if path.exists() or path.is_symlink():
        raise tools.ToolError('This skill already exists; inspect it before editing')
    directory.mkdir(parents=True, exist_ok=True)
    if hasattr(ctx, 'before_change'):
        ctx.before_change(str(path))
    # JSON strings are valid YAML scalar values and cannot inject front matter.
    body = '---\nname: %s\ndescription: %s\n---\n\n%s\n\nEvidence: %s\n' % (
        json.dumps(name), json.dumps(description, ensure_ascii=False), instructions.strip(), evidence.strip())
    temporary = path.with_suffix('.tmp')
    temporary.write_text(body, encoding='utf-8')
    temporary.replace(path)
    if hasattr(ctx, 'after_change'):
        ctx.after_change(str(path))
    _refresh(ctx)
    return str(path), {'ok': True, 'skill': name}


@tools.tool('plugin_install', 'Install a bundled plugin from the shipped newal marketplace into this project. The packaged plugin brings real commands/skills/hooks; external repository code is not executed by this tool.',
            {'name': tools._s('bundled plugin name from capability_catalog')}, ['name'], 'exec')
def install_plugin(ctx, name):
    from . import plugins
    _writable(ctx)
    markets = [m for m in plugins.marketplaces() if m['name'] == plugins.BUILTIN]
    known = {p['name'] for m in markets for p in m['plugins']}
    if name not in known:
        raise tools.ToolError('Only named plugins from the shipped newal marketplace are supported')
    existing = next((p for p in plugins.listing(ctx.root) if p['name'] == name), None)
    result = existing or plugins.install(name + '@' + plugins.BUILTIN, ctx.root)
    _refresh(ctx)
    return json.dumps(result, ensure_ascii=False), {'ok': True, 'plugin': name}


@tools.tool('mcp_registry_search', 'Discover published MCP Registry entries. Published metadata is untrusted; only compatible verified entries can be installed.',
            {'query': tools._s('capability to discover')}, ['query'], 'read')
def registry_search(ctx, query):
    from . import mcp_registry
    return json.dumps(mcp_registry.search(query), ensure_ascii=False), {'ok': True}


@tools.tool('mcp_registry_install', 'Verify and add a literal HTTPS MCP Registry remote requiring no headers or URL variables. Package commands from registry metadata are never executed.',
            {'name': tools._s('exact published registry server name')}, ['name'], 'exec')
def registry_install(ctx, name):
    from . import mcp_registry
    _writable(ctx)
    try:
        result = mcp_registry.install(ctx.root, name)
        _refresh(ctx)
        return json.dumps(result, ensure_ascii=False), result
    except (ValueError, RuntimeError, OSError) as exc:
        raise tools.ToolError(str(exc)) from exc
