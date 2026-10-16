"""Phase 10 automatic GitHub channel, sharing the verified Phase 9 updater.

No live engine edits, no silent APK install, and no restart during a task. Python
activation asks the existing Android WebView bridge to restart when quiescent.
"""
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request

from . import evolution as e, settings, tools

BRANCH = 'phase10/final-validation'
CHANNEL_URL = 'https://github.com/' + e.UPDATE_REPOSITORY + '/releases/download/musabai-phase10-channel/MusabAI-Update-Channel.json'
_LOCK = threading.RLock()
_CHECK_LOCK = threading.Lock()


def asset_url(version):
    return 'https://github.com/%s/releases/download/musabai-phase10-v%d/MusabAI-Hot-Update.zip' % (e.UPDATE_REPOSITORY, version)


def enabled():
    return settings.user().get('automatic_updates', True) is True


def configure(value):
    if type(value) is not bool:
        raise tools.ToolError('enabled must be a boolean')
    with _LOCK:
        settings.save({'automatic_updates': value})
        return status()


def _record():
    return e._read_json(e.home() / 'automatic.json')


def _save(**fields):
    record = dict(_record(), **fields, updated_at=time.time())
    e.save(e.home() / 'automatic.json', record)
    return record


def native_fingerprint():
    return e._read_json(e._trusted_root() / 'native-compat.json').get('sha256')


def validate_feed(feed):
    if not isinstance(feed, dict) or feed.get('schema') != 1:
        raise tools.ToolError('Invalid automatic update channel')
    version = feed.get('version_code')
    if type(version) is not int or version <= 0:
        raise tools.ToolError('Invalid channel version')
    if feed.get('repository') != e.UPDATE_REPOSITORY or feed.get('branch') != BRANCH:
        raise tools.ToolError('Automatic update is outside the authorized Phase 10 repository/branch')
    if feed.get('compatibility_id') != e._compatibility() or feed.get('native_required') is not False:
        raise tools.ToolError('This channel requires a different native APK')
    if not native_fingerprint() or feed.get('native_fingerprint') != native_fingerprint():
        raise tools.ToolError('This release changes native code; Android APK confirmation is required')
    if feed.get('bundle_url') != asset_url(version):
        raise tools.ToolError('Update asset is not the exact versioned repository release')
    if not re.fullmatch(r'[0-9a-f]{64}', str(feed.get('sha256') or '')) or not re.fullmatch(r'[0-9a-f]{40}', str(feed.get('source_commit') or '')):
        raise tools.ToolError('Channel integrity or provenance is missing')
    return feed


def read_feed():
    request = urllib.request.Request(CHANNEL_URL, headers={'User-Agent':'MusabAI-Automatic-Update/1'})
    with urllib.request.urlopen(request, timeout=20) as response:
        final = urllib.parse.urlsplit(response.geturl())
        if final.scheme != 'https' or final.hostname not in e._ALLOWED_DOWNLOAD_HOSTS:
            raise tools.ToolError('Update channel redirected outside approved GitHub hosts')
        raw = response.read(32769)
    if len(raw) > 32768:
        raise tools.ToolError('Update channel exceeds size limit')
    return validate_feed(json.loads(raw))


def request_activation(candidate, automatic=False):
    with _LOCK:
        path = e.candidate(candidate)
        record = e._record(candidate)
        if record.get('status') not in {'verified','ready_to_activate'} or e.digest(path/'newal_code') != record.get('verified_digest'):
            raise tools.ToolError('Candidate must pass unchanged verification before automatic activation')
        _save(state='waiting_idle', automatic_requested=bool(automatic), candidate=candidate, source_commit=record.get('source_commit') or '', restart_ready=False, error='')
        return {'ok':True, 'state':'waiting_idle', 'candidate':candidate, 'restart_required':record.get('restart_required',True)}


def cancel_revision(candidate):
    with _LOCK:
        record = _record()
        if record.get('candidate') != candidate:
            return
        blocked = record.get('blocked_commits', []) + ([record['source_commit']] if record.get('source_commit') else [])
        _save(state='rolled_back', restart_ready=False, blocked_commits=list(dict.fromkeys(blocked))[-20:])


def status():
    with _LOCK:
        record = _record()
        candidate = record.get('candidate')
        active = e._active_marker()
        if candidate and active.get('id') == candidate and active.get('state') == 'active' and active.get('health_confirmed') is True and e._running_revision() == candidate:
            if record.get('state') != 'active':
                record = _save(state='active', restart_ready=False, error='')
        failure = e._read_json(e.home()/'last-failure.json')
        if candidate and failure.get('failed_revision') == candidate and active.get('id') != candidate:
            record = _save(state='rolled_back', restart_ready=False, error=failure.get('error') or failure.get('reason') or 'Startup health failed')
        return dict(record, enabled=enabled(), restart_ready=record.get('restart_ready') is True)


def apply_pending(service):
    with _LOCK, service.lock:
        record = status()
        if record.get('state') != 'waiting_idle' or (record.get('automatic_requested') and not enabled()):
            return record
        # An unstarted thread is reserved work, too; activation cannot race t.start().
        if any(t.is_alive() or t.ident is None for t in service.threads.values()):
            return record
        from . import managed_linux, mcp_bundles
        if getattr(service, 'dependency_setup_busy', False) or managed_linux.LOCK.locked() or mcp_bundles.operations_busy():
            return record
        candidate = record['candidate']
        # Prevent the service from accepting a new turn while switching/restarting.
        service.updating = candidate
        try:
            result = e.activate(candidate)  # rechecks integrity/version at this boundary
        except Exception as exc:
            service.updating = ''
            blocked = record.get('blocked_commits', []) + ([record['source_commit']] if record.get('source_commit') else [])
            _save(state='failed_activation', restart_ready=False, error=str(exc), blocked_commits=list(dict.fromkeys(blocked))[-20:])
            raise
        if result.get('restart_required'):
            return _save(state='activating', restart_ready=True, error='')
        service.updating = ''
        return _save(state='active', restart_ready=False, error='')


def check(service):
    with _CHECK_LOCK:
        pending = status()
        if pending.get('state') in {'waiting_idle','activating'}:
            return apply_pending(service)
        # Verification runs Python suites and starts subprocesses; leave active
        # conversations their CPU/memory and check again after the task finishes.
        from . import managed_linux, mcp_bundles
        with service.lock:
            if mcp_bundles.operations_busy() or managed_linux.LOCK.locked() or getattr(service, "dependency_setup_busy", False) or any(t.is_alive() or t.ident is None for t in service.threads.values()):
                return pending
        if not enabled():
            return pending
        feed = read_feed()
        if feed['version_code'] <= e._current_version_code():
            return pending
        # A failed version never creates an endless download/rollback loop.
        if feed['source_commit'] in pending.get('blocked_commits', []):
            return pending
        attempts = pending.get('download_attempts', 0) if pending.get('source_commit') == feed['source_commit'] else 0
        _save(state='downloading', available_version=feed['version_code'], source_commit=feed['source_commit'], download_attempts=attempts + 1, error='')
        downloaded = False
        try:
            staged = e._MANAGER.stage(feed['bundle_url'], feed['sha256'])
            downloaded = True
            if staged.get('version_code') != feed['version_code'] or staged.get('source_commit') != feed['source_commit']:
                raise tools.ToolError('Downloaded update does not match the channel provenance')
            _save(state='verifying', candidate=staged['id'])
            verified = e._MANAGER.verify(staged['id'])
            if verified.get('status') != 'ready_to_activate':
                detail = verified.get('results') or []
                raise tools.ToolError('Automatic update verification failed: ' + str(detail[-1].get('output','') if detail else 'no successful checks')[-1500:])
            request_activation(verified['id'], automatic=True)
            return apply_pending(service)
        except Exception as exc:
            # Network interruption may retry; invalid bytes, failed checks and
            # activation errors cannot loop indefinitely or replace the engine.
            transport_error = isinstance(exc, (OSError, TimeoutError)) or isinstance(exc.__cause__, (OSError, TimeoutError))
            retry_download = not downloaded and transport_error and attempts < 2
            blocked = pending.get('blocked_commits', [])
            if not retry_download:
                blocked = list(dict.fromkeys(blocked + [feed['source_commit']]))[-20:]
            _save(state='download_retry' if retry_download else 'failed_verification', restart_ready=False, error=str(exc)[:2000], blocked_commits=blocked)
            raise


def start(service):
    # Verification subprocesses and desktop/CI imports must never start network jobs.
    if os.environ.get('NEWAL_DISABLE_AUTOMATION') == '1' or not os.environ.get('NEWAL_PACKAGED_ENGINE'):
        return None
    stop = threading.Event()
    def work():
        while not stop.is_set():
            try:
                record = status()
                if record.get('state') == 'rolled_back' and record.get('source_commit'):
                    _save(blocked_commits=list(dict.fromkeys(record.get('blocked_commits',[]) + [record['source_commit']]))[-20:])
                check(service)
            except Exception as exc:
                # Keep exact diagnostics visible; the running engine stays intact.
                _save(error=str(exc)[:2000])
            pending = status().get('state') in {'waiting_idle','activating'}
            stop.wait(2 if pending else 60)
    thread = threading.Thread(target=work, name='musabai-automatic-update', daemon=True)
    thread.start()
    return stop
