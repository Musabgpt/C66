"""Project-scoped review controls use the same persistent memory as the agent."""
import importlib
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from . import autonomy


class MemoryApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, {'NEWAL_MEMORY_HOME': self.temp.name + '/memory'})
        env.start()
        self.addCleanup(env.stop)
        self.sessions = {name: SimpleNamespace(root=self.temp.name + '/' + name) for name in ('one', 'two')}
        self.handler = SimpleNamespace(service=SimpleNamespace(get=lambda sid: self.sessions[sid]))
        self.handler._json = lambda data, status=200: setattr(self, 'response', (status, data))
        self.handler._query = lambda: self.query
        self.query = {'session': 'one'}
        self.response = None

    def route(self, method='GET', path='/api/memory', body=None):
        self.assertIsNotNone(importlib.util.find_spec('newal_code.memory_api'), 'Memory API must be packaged')
        return importlib.import_module('newal_code.memory_api').route(self.handler, method, path, body)

    def learn(self, sid='one'):
        store = autonomy.memory_for(self.sessions[sid].root)
        try:
            store.learn('PDF layout', 'Use Arabic glyph shaping', 'Observed rendered output')
        finally:
            store.close()

    def test_review_uses_existing_session_and_survives_reopen(self):
        self.learn()
        self.assertTrue(self.route())
        status, data = self.response
        self.assertEqual(status, 200)
        self.assertEqual(data['counts']['lessons'], 1)
        self.assertEqual(data['lessons'][0]['evidence'], 'Observed rendered output')
        self.query = {'session': 'two'}
        self.route()
        self.assertEqual(self.response[1]['counts']['lessons'], 0)

    def test_missing_or_unknown_session_never_uses_a_global_workspace(self):
        for sid in ('', '../one', 'missing'):
            with self.subTest(sid=sid):
                self.query = {'session': sid}
                self.route()
                self.assertEqual(self.response[0], 400)

    def test_forget_only_changes_selected_project(self):
        self.learn('one')
        self.learn('two')
        self.route()
        lesson_id = self.response[1]['lessons'][0]['id']
        self.route('POST', '/api/memory/forget', {'session': 'one', 'id': lesson_id})
        self.assertEqual(self.response, (200, {'ok': True}))
        self.route()
        self.assertEqual(self.response[1]['counts']['lessons'], 0)
        self.query = {'session': 'two'}
        self.route()
        self.assertEqual(self.response[1]['counts']['lessons'], 1)

    def test_disable_survives_reopen_without_erasing_lessons(self):
        self.learn()
        self.route('POST', '/api/memory/settings', {'session': 'one', 'enabled': False})
        self.route()
        self.assertIs(self.response[1]['enabled'], False)
        self.assertEqual(self.response[1]['counts']['lessons'], 1)
        self.route('POST', '/api/memory/settings', {'session': 'one', 'enabled': 'false'})
        self.assertEqual(self.response[0], 400)

    def test_clear_requires_confirmation_and_preserves_project_files(self):
        root = Path(self.sessions['one'].root)
        root.mkdir()
        owned = root / 'notes.md'
        owned.write_text('User-owned notes')
        self.learn()
        self.route('POST', '/api/memory/clear', {'session': 'one'})
        self.assertEqual(self.response[0], 400)
        self.route()
        self.assertEqual(self.response[1]['counts']['lessons'], 1)
        self.route('POST', '/api/memory/clear', {'session': 'one', 'confirm': True})
        self.route()
        self.assertEqual(self.response[1]['counts']['lessons'], 0)
        self.assertEqual(owned.read_text(), 'User-owned notes')

    def test_unrelated_routes_are_not_intercepted(self):
        self.assertFalse(self.route(path='/api/memory-old'))


if __name__ == '__main__':
    unittest.main()
