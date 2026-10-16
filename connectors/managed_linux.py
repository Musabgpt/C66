"""Vetted Linux MCP dependencies in a dedicated Termux proot container.

Android/bionic cannot consume Linux PyTorch or sharp wheels. Keep these packages
in a glibc environment with Python 3.11, and finish installation before starting
MCP stdio. Installation markers never substitute for MCP initialize/tools/list.
"""
import hashlib
import shlex
import threading
from . import runtime_manager as rm, tools

CONTAINER = 'musabai-mcp'
IMAGE = 'debian:bookworm'
LOCK = threading.Lock()
NODE = '/opt/musabai/node22/node_modules/node/bin/node'
RECIPES = {
    'browser-use': ('browser-use==0.13.5', 'browser-use', ['--mcp']),
    'docling': ('docling-mcp[local]==3.3.0', 'docling-mcp-server', ['--transport', 'stdio']),
    'android': ('@us-all/android-mcp@1.14.4', 'node_modules/@us-all/android-mcp/dist/index.js', []),
}


def location(bundle):
    package, entry, args = RECIPES[bundle]
    digest = hashlib.sha256(package.encode()).hexdigest()[:12]
    root = '/opt/musabai/' + bundle + '/' + digest
    return root, (root + '/' + entry if bundle == 'android' else root + '/bin/' + entry), args


def login(command):
    return 'proot-distro login --shared-tmp ' + CONTAINER + ' -- /bin/sh -lc ' + shlex.quote(command)


def spec(item):
    root, entry, args = location(item['id'])
    env = dict(item.get('env') or {})
    if item['id'] == 'browser-use':
        env.update(BROWSER_USE_HEADLESS='true', BROWSER_USE_DISABLE_EXTENSIONS='true', BROWSER_USE_CONFIG_DIR='/opt/musabai/browser-config', ANONYMIZED_TELEMETRY='false')
    command = ['/usr/bin/env'] + [k + '=' + str(v) for k, v in env.items()]
    if item['id'] == 'android':
        command.append(NODE)
    command += [entry] + args
    return {'command':'proot-distro',
            'args':['login','--shared-tmp',CONTAINER,'--'] + command,
            'env':{}, 'termux_cwd':rm.termux_home()}


def ensure(item, cancel=None):
    from . import mcp_bundles as bundles, providers
    while not LOCK.acquire(timeout=.2):
        if cancel is not None and cancel.is_set():
            raise providers.Cancelled()
    dependency_lock_held = False
    try:
        from .automation import _INSTALL_LOCK
        while not _INSTALL_LOCK.acquire(timeout=.2):
            if cancel is not None and cancel.is_set():
                raise providers.Cancelled()
        dependency_lock_held = True
        from . import auto_update
        with auto_update._LOCK:
            if auto_update.status().get('state') == 'activating':
                raise tools.ToolError('Engine update is activating; retry MCP setup after restart')
        with bundles.cancel_scope(cancel):
            bundles._cancel_setup()
            def run(command):
                process = rm.process_start(command)
                bundles._wait_termux_process(process['id'], timeout=1800)
            def exists(command):
                result = rm.execute(command, rm.TERMUX)
                if result.get('status') != 'completed':
                    raise tools.ToolError('Linux runtime probe failed: ' + str(result))
                return result.get('exit_code') == 0
            if not exists('command -v proot-distro'):
                run('pkg install -y proot-distro')
            # Probe our dedicated container; never replace a user distribution.
            if not exists(login('test -f /etc/debian_version')):
                run('proot-distro install ' + IMAGE + ' --name ' + CONTAINER)
            root, entry, _ = location(item['id'])
            probe = 'test -f ' + shlex.quote(entry)
            if item['id'] == 'android':
                probe += ' && cd ' + shlex.quote(root) + ' && ' + NODE + ' -e ' + shlex.quote("require('sharp'); if (+process.versions.node.split('.')[0] < 22) process.exit(1)")
            else:
                module = 'docling_mcp' if item['id'] == 'docling' else 'browser_use'
                check = 'import ' + module
                if item['id'] == 'browser-use':
                    check += '; from importlib.metadata import version; assert version("pydantic") == "2.12.5"'
                probe += ' && ' + root + '/bin/python -c ' + shlex.quote(check)
            if item['id'] == 'browser-use':
                probe += ' && test -x /usr/bin/chromium && test -f /opt/musabai/browser-config/config.json'
            if exists(login(probe)):
                rm._clear_cache()
                return
            packages = 'ca-certificates python3 python3-venv'
            if item['id'] == 'android':
                packages += ' nodejs npm adb'
            elif item['id'] == 'browser-use':
                packages += ' chromium'
            else:
                packages += ' libglib2.0-0 libgl1 libgomp1'
            run(login('export DEBIAN_FRONTEND=noninteractive; apt-get update && apt-get install -y --no-install-recommends ' + packages))
            package = RECIPES[item['id']][0]
            if item['id'] == 'android':
                run(login('npm install --prefix /opt/musabai/node22 --ignore-scripts --no-audit --no-fund node@22.22.0 && cd /opt/musabai/node22/node_modules/node && node installArchSpecificPackage.js && ' + NODE + ' --version'))
                install = 'export PATH=/opt/musabai/node22/node_modules/node/bin:$PATH; mkdir -p {r} && npm install --prefix {r} --no-audit --no-fund --omit=dev {p}'.format(r=shlex.quote(root), p=shlex.quote(package))
            else:
                install = 'export MAX_JOBS=2 CMAKE_BUILD_PARALLEL_LEVEL=2 MAKEFLAGS=-j2; python3 -m venv {r} && {r}/bin/python -m pip install --upgrade pip && {r}/bin/python -m pip install {p}'.format(r=shlex.quote(root),p=shlex.quote(package) + (' pydantic==2.12.5' if item['id'] == 'browser-use' else ''))
            run(login(install))
            if item['id'] == 'browser-use':
                # Supported server profile: headless Debian Chromium, no Android binaries.
                import json
                config = {'browser_profile':{'musabai':{'id':'musabai','default':True,'headless':True,'executable_path':'/usr/bin/chromium','chromium_sandbox':False,'enable_default_extensions':False,'keep_alive':False}}, 'llm':{}, 'agent':{}}
                run(login('mkdir -p /opt/musabai/browser-config && printf %s ' + shlex.quote(json.dumps(config)) + ' > /opt/musabai/browser-config/config.json'))
            if not exists(login(probe)):
                raise tools.ToolError('Linux MCP dependency verification failed for ' + item['id'] + '; no MCP installation was saved')
            rm._clear_cache()
    finally:
        if dependency_lock_held:
            _INSTALL_LOCK.release()
        LOCK.release()
