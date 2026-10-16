"""Automatic updates must use the existing verification/rollback path and wait for idle."""
import importlib
import importlib.util
import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from . import evolution_tests as fixtures
from . import evolution as e


class AutomaticUpdateTests(unittest.TestCase):
    setUp = fixtures.EvolutionTests.setUp
    _bundle = fixtures.EvolutionTests._bundle
    def auto(self):
        self.assertIsNotNone(importlib.util.find_spec('newal_code.auto_update'), 'automatic GitHub update path missing')
        return importlib.import_module('newal_code.auto_update')

    def service(self, busy=False):
        thread = SimpleNamespace(is_alive=lambda: busy, ident=1)
        return SimpleNamespace(lock=threading.RLock(), threads={'task':thread}, updating='', agents={})

    def test_update_switch_persists_and_rejects_non_boolean(self):
        auto = self.auto()
        with patch.object(auto.settings, 'save') as save, patch.object(auto, 'status', return_value={'enabled':False}):
            self.assertFalse(auto.configure(False)['enabled'])
            save.assert_called_once_with({'automatic_updates':False})
            for value in ('false', None, 0, {}):
                with self.assertRaises(Exception):
                    auto.configure(value)
            self.assertEqual(save.call_count, 1)

    def test_feed_is_restricted_to_exact_repository_phase10_and_compatible_engine(self):
        auto=self.auto()
        feed={'schema':1, 'repository':e.UPDATE_REPOSITORY, 'branch':'phase10/final-validation',
              'version_code':101, 'compatibility_id':e.UPDATE_COMPATIBILITY, 'sha256':'a'*64,
              'source_commit':'b'*40, 'native_fingerprint':'c'*64, 'bundle_url':auto.asset_url(101), 'native_required':False}
        fingerprint=patch.object(auto,'native_fingerprint',return_value='c'*64)
        fingerprint.start();self.addCleanup(fingerprint.stop)
        self.assertEqual(auto.validate_feed(feed)['version_code'], 101)
        for field, value in [('bundle_url','https://github.com/other/repo/releases/download/x/update.zip'),
                              ('branch','phase11/work'), ('compatibility_id','other-native'),
                              ('native_required',True), ('native_fingerprint','d'*64), ('sha256','missing')]:
            with self.subTest(field=field), self.assertRaises(e.tools.ToolError):
                auto.validate_feed(dict(feed, **{field:value}))

    def test_verification_failure_never_changes_active_selection(self):
        auto=self.auto(); archive, sha, manifest=self._bundle()
        record=e._MANAGER.stage(str(archive),sha)
        with patch.object(e, '_run_verification', return_value=(False,[{'exit':1,'output':'real verification failure'}],record['tree_sha256'])):
            verified=e._MANAGER.verify(record['id'])
        with self.assertRaises(e.tools.ToolError):
            auto.request_activation(verified['id'])
        self.assertFalse((e.home()/'active.json').exists())

    def test_verified_update_waits_for_task_then_requests_one_engine_restart(self):
        auto=self.auto(); archive, sha, manifest=self._bundle()
        record=e._MANAGER.stage(str(archive),sha)
        with patch.object(e, '_run_verification', return_value=(True,[{'exit':0}],record['tree_sha256'])):
            e._MANAGER.verify(record['id'])
        auto.request_activation(record['id'])
        busy=self.service(True)
        auto.apply_pending(busy)
        self.assertFalse((e.home()/'active.json').exists())
        self.assertFalse(auto.status()['restart_ready'])
        idle=self.service(False)
        auto.apply_pending(idle)
        self.assertEqual(e._active_marker()['id'],record['id'])
        self.assertEqual(idle.updating,record['id'])
        self.assertTrue(auto.status()['restart_ready'])
        marker=e._active_marker(); marker.update(state='active',health_confirmed=True)
        e.save(e.home()/'active.json',marker)
        with patch.object(e,'_running_revision',return_value=record['id']):
            self.assertFalse(auto.status()['restart_ready'])
        self.assertEqual(auto.status()['state'],'active')

    def test_candidate_changed_after_queue_is_not_retried_forever(self):
        auto=self.auto(); archive,sha,_=self._bundle()
        record=e._MANAGER.stage(str(archive),sha)
        with patch.object(e,'_run_verification',return_value=(True,[{'exit':0}],record['tree_sha256'])):
            e._MANAGER.verify(record['id'])
        auto.request_activation(record['id'])
        (e.candidate(record['id'])/'newal_code/server.py').write_text('changed after verification')
        service=self.service(False)
        with self.assertRaises(e.tools.ToolError):auto.apply_pending(service)
        self.assertFalse(auto.status()['restart_ready'])
        self.assertEqual(auto.status()['state'],'failed_activation')
        auto.apply_pending(service)
        self.assertFalse((e.home()/'active.json').exists())

    def test_manual_rollback_does_not_automatically_reapply_the_same_release(self):
        auto=self.auto(); archive,sha,_=self._bundle()
        record=e._MANAGER.stage(str(archive),sha)
        with patch.object(e,'_run_verification',return_value=(True,[{'exit':0}],record['tree_sha256'])):
            e._MANAGER.verify(record['id'])
        auto._save(source_commit='a'*40)
        auto.request_activation(record['id']);auto.apply_pending(self.service(False))
        e.rollback()
        self.assertFalse(auto.status()['restart_ready'])
        self.assertIn('a'*40,auto.status()['blocked_commits'])

    def test_interrupted_download_has_bounded_retries_without_poisoning_release_on_first_failure(self):
        auto = self.auto()
        feed = {'version_code': 101, 'source_commit': 'a'*40, 'bundle_url': auto.asset_url(101), 'sha256': 'b'*64}
        def interrupted(*args):
            try:
                raise OSError('network interrupted')
            except OSError as exc:
                raise e.tools.ToolError('Could not stage update: network interrupted') from exc
        with patch.object(auto, 'read_feed', return_value=feed), \
             patch.object(e._MANAGER, 'stage', side_effect=interrupted):
            for attempt in range(1, 4):
                with self.assertRaises(e.tools.ToolError):
                    auto.check(self.service(False))
                self.assertEqual(auto.status()['download_attempts'], attempt)
                self.assertEqual('a'*40 in auto.status().get('blocked_commits', []), attempt == 3)
            auto.check(self.service(False))
            self.assertEqual(auto.status()['download_attempts'], 3)

    def test_activation_waits_for_automatic_dependency_install(self):
        auto = self.auto()
        service = self.service()
        service.dependency_setup_busy = True
        record = {'state':'waiting_idle', 'candidate':'verified'}
        with patch.object(auto, 'status', return_value=record), patch.object(e, 'activate') as activate:
            self.assertEqual(auto.apply_pending(service), record)
        activate.assert_not_called()

    def test_activation_waits_for_linux_package_installation(self):
        from . import managed_linux
        auto = self.auto()
        record = {'state':'waiting_idle', 'candidate':'verified'}
        with managed_linux.LOCK, patch.object(auto, 'status', return_value=record), patch.object(e, 'activate') as activate:
            self.assertEqual(auto.apply_pending(self.service()), record)
        activate.assert_not_called()

    def test_active_chat_defers_download_and_expensive_verification(self):
        auto = self.auto()
        with patch.object(auto, 'read_feed') as feed, patch.object(e._MANAGER, 'verify') as verify:
            auto.check(self.service(True))
        feed.assert_not_called()
        verify.assert_not_called()

    def test_update_does_not_restart_during_mcp_lifecycle_operation(self):
        from . import mcp_bundles
        auto = self.auto()
        record = {'state': 'waiting_idle', 'candidate': 'verified'}
        with patch.object(mcp_bundles, 'operations_busy', return_value=True, create=True), \
             patch.object(auto, 'status', return_value=record), patch.object(e, 'activate') as activate:
            self.assertEqual(auto.apply_pending(self.service()), record)
        activate.assert_not_called()

    def test_disabling_automatic_updates_pauses_an_automatically_queued_candidate(self):
        auto = self.auto()
        record = {'state':'waiting_idle', 'candidate':'verified', 'automatic_requested':True}
        with patch.object(auto, 'status', return_value=record), patch.object(auto, 'enabled', return_value=False), \
             patch.object(e, 'activate') as activate:
            self.assertEqual(auto.apply_pending(self.service()), record)
        activate.assert_not_called()
