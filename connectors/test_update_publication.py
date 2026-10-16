import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

HERE=Path(__file__).resolve().parent

class UpdatePublicationTests(unittest.TestCase):
    def test_channel_can_only_reference_exact_phase10_ci_artifact(self):
        file=HERE/'publish_update_channel.py'
        self.assertTrue(file.is_file(), 'CI has no public automatic update publication path')
        spec=importlib.util.spec_from_file_location('publisher',file)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            commit='a'*40
            manifest={'source_repository':'Musabgpt/NewAl-Cloud','source_commit':commit,'version_code':333,
                      'compatibility_id':'action125-python314-v1','channel':'candidate','native_required':False}
            with zipfile.ZipFile(root/'MusabAI-Hot-Update.zip','w') as z:
                z.writestr('manifest.json',json.dumps(manifest))
                z.writestr('newal_code/native-compat.json',json.dumps({'sha256':'b'*64}))
            env={'GITHUB_REPOSITORY':'Musabgpt/NewAl-Cloud','GITHUB_REF_NAME':'phase10/final-validation','GITHUB_SHA':commit}
            with patch.dict(os.environ,env,clear=True):
                feed=module.build_feed(root)
                self.assertEqual(feed['native_fingerprint'],'b'*64)
                self.assertIn('/musabai-phase10-v333/',feed['bundle_url'])
                self.assertEqual(feed['source_commit'],commit)
            with patch.dict(os.environ,dict(env,GITHUB_REF_NAME='phase11/next'),clear=True):
                with self.assertRaises(ValueError):module.build_feed(root)
