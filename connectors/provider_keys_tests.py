import json
import unittest
from types import SimpleNamespace
from unittest import mock

from . import provider_keys


class ProviderKeysTests(unittest.TestCase):
    def test_public_status_exposes_real_cooldown_without_secrets(self):
        pool = provider_keys.provider_pool
        pool.reset_health(); self.addCleanup(pool.reset_health)
        spec = next(s for s in pool.FREE_POOL if s.get('secret_id') == 'groq')
        pool._mark_failure(spec, provider_keys.providers.ProviderError('limited', 429))
        with mock.patch.object(provider_keys, 'configured', return_value=True):
            provider_keys.route(self.handler(), 'GET', '/api/free-providers')
        item = self.response[0]['providers'][0]
        self.assertEqual(item['health']['state'], 'rate_limited')
        self.assertGreater(item['health']['retry_after_seconds'], 0)

    def test_replacing_one_key_resets_only_that_provider_health(self):
        pool = provider_keys.provider_pool
        pool.reset_health(); self.addCleanup(pool.reset_health)
        a = next(s for s in pool.FREE_POOL if s.get('secret_id') == 'groq')
        b = next(s for s in pool.FREE_POOL if s.get('secret_id') == 'nvidia')
        pool._mark_failure(a, provider_keys.providers.ProviderError('old key', 401))
        pool._mark_failure(b, provider_keys.providers.ProviderError('limited', 429))
        with mock.patch.object(provider_keys, '_phone', return_value={'ok': True}):
            provider_keys.route(self.handler(), 'POST', '/api/free-providers/save', {'provider':'groq', 'key':'new-key-123'})
        self.assertTrue(pool._available(a))
        self.assertFalse(pool._available(b))


    def handler(self):
        h = SimpleNamespace()
        h._json = lambda data, status=200: setattr(self, "response", (data, status))
        return h

    def test_status_never_returns_secret(self):
        h = self.handler()

        def phone_call(action, timeout=30, provider="", **extra):
            self.assertEqual(action, "provider_secret_status")
            return {"ok": True, "configured": provider in {"groq", "nvidia"}, "value": "must-never-leak"}

        with mock.patch.object(provider_keys.phone, "call", side_effect=phone_call):
            self.assertTrue(provider_keys.route(h, "GET", "/api/free-providers"))
        body = self.response[0]
        self.assertTrue(body["free_only"])
        self.assertEqual([x["id"] for x in body["providers"]], ["groq", "gemini", "openrouter", "nvidia"])
        self.assertNotIn("must-never-leak", json.dumps(body))

    def test_save_and_remove_use_android_secure_storage(self):
        h = self.handler()
        calls = []

        def phone_call(action, timeout=30, **kwargs):
            calls.append((action, kwargs))
            return {"ok": True, "configured": action == "provider_secret_set"}

        with mock.patch.object(provider_keys.phone, "call", side_effect=phone_call):
            provider_keys.route(h, "POST", "/api/free-providers/save",
                                {"provider": "groq", "key": "secret-value-123"})
            self.assertEqual(self.response, ({"ok": True, "configured": True}, 200))
            provider_keys.route(h, "POST", "/api/free-providers/remove", {"provider": "groq"})
            self.assertEqual(self.response, ({"ok": True, "configured": False}, 200))
        self.assertEqual(calls[0][0], "provider_secret_set")
        self.assertEqual(calls[0][1]["value"], "secret-value-123")
        self.assertEqual(calls[1][0], "provider_secret_remove")

    def test_invalid_key_is_not_saved(self):
        h = self.handler()
        with mock.patch.object(provider_keys.phone, "call") as call:
            provider_keys.route(h, "POST", "/api/free-providers/save",
                                {"provider": "gemini", "key": "bad key"})
        self.assertEqual(self.response[1], 400)
        call.assert_not_called()

    def test_real_test_path_uses_saved_key_but_does_not_echo_provider_error(self):
        h = self.handler()
        with mock.patch.object(provider_keys, "_test", side_effect=provider_keys.providers.ProviderError(
                "HTTP body containing private metadata", 401, "private")):
            provider_keys.route(h, "POST", "/api/free-providers/test", {"provider": "openrouter"})
        self.assertEqual(self.response[1], 400)
        rendered = json.dumps(self.response[0])
        self.assertIn("HTTP 401", rendered)
        self.assertNotIn("private metadata", rendered)
        self.assertNotIn("private", rendered)


if __name__ == "__main__":
    unittest.main()
