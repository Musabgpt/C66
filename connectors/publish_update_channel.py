"""Publish only the already successful, persistent-signed Phase 10 CI artifact."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile

REPOSITORY = 'Musabgpt/NewAl-Cloud'
BRANCH = 'phase10/final-validation'


def build_feed(root):
    if os.environ.get('GITHUB_REPOSITORY') != REPOSITORY or os.environ.get('GITHUB_REF_NAME') != BRANCH:
        raise ValueError('Only the canonical Phase 10 branch can publish this channel')
    bundle = root / 'MusabAI-Hot-Update.zip'
    with zipfile.ZipFile(bundle) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        native = json.loads(archive.read('newal_code/native-compat.json'))['sha256']
    if (manifest.get('source_repository') != REPOSITORY
            or manifest.get('source_commit') != os.environ.get('GITHUB_SHA')
            or manifest.get('native_required') is not False or manifest.get('channel') != 'candidate'):
        raise ValueError('Artifact provenance does not match this verified CI commit')
    version = manifest['version_code']
    return {'schema':1, 'repository':REPOSITORY, 'branch':BRANCH,
            'version_code':version, 'source_commit':manifest['source_commit'],
            'compatibility_id':manifest['compatibility_id'], 'native_required':False,
            'native_fingerprint':native,
            'sha256':hashlib.sha256(bundle.read_bytes()).hexdigest(),
            'bundle_url':'https://github.com/%s/releases/download/musabai-phase10-v%d/MusabAI-Hot-Update.zip' % (REPOSITORY,version)}


def gh(*args, check=True):
    result = subprocess.run(['gh', *args, '--repo', REPOSITORY], capture_output=True, text=True)
    if check and result.returncode:
        raise RuntimeError('GitHub release operation failed: ' + result.stderr[-2000:])
    return result


def publish(root):
    feed=build_feed(root)
    version=feed['version_code']; tag='musabai-phase10-v%d' % version
    commit=feed['source_commit']
    files=[root/name for name in ('MusabAI-Hot-Update.zip','MusabAI-Hot-Update.zip.sha256',
                                  'MusabAI-Connectors.apk','MusabAI-Connectors.apk.sha256','signing-report.txt')]
    if any(not file.is_file() for file in files):
        raise ValueError('Complete persistent-signed artifact is required')
    channel=root/'MusabAI-Update-Channel.json'
    channel.write_text(json.dumps(feed,sort_keys=True)+'\n',encoding='utf-8')
    with tempfile.TemporaryDirectory(prefix='musabai-release-') as temp:
        body=Path(temp)/'notes.md'
        body.write_text('Phase 10 engine update from successful CI.\n\nSource commit: '+commit+
                        '\nBuild: '+str(version)+'\nAutomatic application still performs on-device verification and startup health.\n',encoding='utf-8')
        if gh('release','view',tag,check=False).returncode:
            gh('release','create',tag,'--target',commit,'--prerelease','--title','MusabAI Phase 10 v%d' % version,'--notes-file',str(body))
        gh('release','upload',tag,*[str(file) for file in files],'--clobber')
        channel_tag='musabai-phase10-channel'
        if gh('release','view',channel_tag,check=False).returncode:
            gh('release','create',channel_tag,'--target',commit,'--prerelease','--title','MusabAI Phase 10 automatic update channel','--notes-file',str(body))
        # The channel pointer is the last upload; readers always get immutable versioned assets.
        gh('release','upload',channel_tag,str(channel),'--clobber')
    print(json.dumps({'published_version':version,'source_commit':commit,'channel':channel_tag}))


if __name__=='__main__':
    publish(Path(os.environ.get('MUSABAI_ARTIFACT_DIR','.')).resolve())
