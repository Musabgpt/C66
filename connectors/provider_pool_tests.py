import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from . import provider_pool, provider_keys, providers


class ProviderPoolTests(unittest.TestCase):
    def test_request_boundary_recovers_after_reasoning_and_partial_tool_arguments(self):
        messages = [{'role': 'tool', 'tool_call_id': 'done', 'content': 'already written'}]
        events, resets, calls = [], [], []
        def interrupted(*args, **kwargs):
            calls.append('primary')
            kwargs['on_event']('reasoning', 'Preparing the game')
            kwargs['on_event']('tool_args', '{"path":"game.html",')
            raise providers.ProviderError('Upstream idle timeout exceeded')
        def good(model, history, **kwargs):
            calls.append('backup')
            self.assertIs(history, messages)
            self.assertNotIn('on_reset', kwargs)
            return 'complete response'
        c = SimpleNamespace(spec={'id':'current'}, model_name='current', provider=SimpleNamespace(chat=interrupted))
        backup = {'id':'backup/free', 'model':'backup', 'base_url':'https://backup.invalid', 'free':True}
        with patch.object(provider_pool, 'candidates', return_value=[backup]), \
             patch.object(provider_pool, 'provider', return_value=SimpleNamespace(chat=good)):
            result = provider_pool.chat_recovering(c, messages, on_event=lambda *e: events.append(e),
                                                  on_reset=lambda: resets.append(True), recovery_budget=0)
        self.assertEqual(result, 'complete response')
        self.assertEqual(calls, ['primary', 'backup'])
        self.assertEqual(resets, [True])
        self.assertEqual(c.spec['id'], 'backup/free')

    def test_upstream_timeout_does_not_repeat_same_slow_request(self):
        calls = []
        def slow(*args, **kwargs):
            calls.append('slow')
            raise providers.ProviderError('Upstream idle timeout exceeded')
        c = SimpleNamespace(spec={'id':'current'}, model_name='current', provider=SimpleNamespace(chat=slow))
        with patch.object(provider_pool, 'candidates', return_value=[]), patch.object(provider_pool.time, 'sleep'):
            with self.assertRaises(provider_pool.ProviderUnavailable):
                provider_pool.chat(c, [])
        self.assertEqual(calls, ['slow'])
        self.assertEqual(provider_pool.HEALTH.state('current')['state'], 'timeout')

    def test_partial_recovery_is_bounded_and_does_not_hide_final_failure(self):
        calls, resets = [], []
        def interrupted(*args, **kwargs):
            calls.append(args[0])
            kwargs['on_event']('text', 'Unfinished draft')
            raise providers.ProviderError('Upstream idle timeout exceeded')
        c = SimpleNamespace(spec={'id':'current'}, model_name='current', provider=SimpleNamespace(chat=interrupted))
        backups = [{'id': 'backup%d' % i, 'model': 'backup%d' % i, 'base_url': 'https://backup.invalid', 'free': True} for i in range(2)]
        with patch.object(provider_pool, '_available', return_value=True), \
             patch.object(provider_pool, 'candidates', return_value=backups), \
             patch.object(provider_pool, 'provider', return_value=SimpleNamespace(chat=interrupted)):
            with self.assertRaises(providers.ProviderError) as caught:
                provider_pool.chat_recovering(c, [], on_reset=lambda: resets.append(True), recovery_budget=0)
        self.assertEqual(calls, ['current', 'backup0', 'backup1'])
        self.assertEqual(len(resets), 3)
        self.assertIn('انقطع', provider_pool.public_error(caught.exception))
        self.assertNotIn('Upstream idle timeout exceeded', provider_pool.public_error(caught.exception))

    def test_stop_after_partial_error_prevents_recovery_request(self):
        import threading
        cancel = threading.Event()
        def interrupted(*args, **kwargs):
            kwargs['on_event']('reasoning', 'Starting')
            cancel.set()
            raise providers.ProviderError('Upstream idle timeout exceeded')
        c = SimpleNamespace(spec={'id':'current'}, model_name='current', provider=SimpleNamespace(chat=interrupted))
        with patch.object(provider_pool, 'candidates') as candidates:
            with self.assertRaises(providers.Cancelled):
                provider_pool.chat_recovering(c, [], cancel=cancel, on_reset=lambda: None)
        candidates.assert_not_called()

    def test_partial_stream_without_ui_callback_is_not_replayed(self):
        def partial(*args, **kwargs):
            kwargs['on_event']('text', 'partial answer')
            raise providers.ProviderError('network ended after output', 503)
        c = SimpleNamespace(spec={'id':'current'}, model_name='current', provider=SimpleNamespace(chat=partial))
        with patch.object(provider_pool, 'candidates') as candidates:
            with self.assertRaises(providers.ProviderError):
                provider_pool.chat(c, [])
        candidates.assert_not_called()

    def test_non_finite_retry_after_cannot_crash_recovery(self):
        for value in ('Infinity', 'NaN', '1e9999'):
            error = providers.ProviderError('busy', 429)
            error.headers = {'Retry-After': value}
            self.assertIsNone(provider_pool._retry_after_seconds(error))


    def setUp(self):
        provider_pool.reset_health()

    def test_kilo_can_use_another_verified_free_model_on_same_gateway(self):
        base = 'https://api.kilo.ai/api/gateway'
        client = SimpleNamespace(spec={'id':'kilo-auto/free', 'base_url':base})
        rows = {'data':[{'id':'stepfun/test:free','pricing':{'prompt':'0','completion':'0'},
                        'supported_parameters':['tools'],'architecture':{'input_modalities':['text','image']}}]}
        with patch.object(provider_pool, '_fetch_kilo_catalog', return_value=rows):
            items = provider_pool.candidates(client, {'tools','vision'})
        self.assertIn('stepfun/test:free', [x['model'] for x in items])

    def test_kilo_catalog_never_routes_to_paid_or_unknown_price(self):
        self.assertTrue(hasattr(provider_pool, '_kilo_models'))
        rows={'data':[{'id':'looks:free','pricing':{'prompt':'1','completion':'0'},'supported_parameters':['tools']},
                      {'id':'unknown:free','supported_parameters':['tools']}]}
        with patch.object(provider_pool,'_fetch_kilo_catalog',return_value=rows):
            self.assertEqual(provider_pool._kilo_models(), [])

    def test_catalog_discovers_more_than_six_and_rejects_hidden_charges(self):
        rows = [{'id':'free/%d' % i, 'pricing':{'prompt':'0','completion':'0'}, 'supported_parameters':['tools']} for i in range(14)]
        rows += [{'id':'charged-cache', 'pricing':{'prompt':'0','completion':'0','input_cache_write':'0.1'}, 'supported_parameters':['tools']}]
        with patch.object(provider_pool, '_fetch_kilo_catalog', return_value={'data':rows}):
            found = provider_pool._kilo_models()
        self.assertEqual(len(found), 14)
        self.assertNotIn('charged-cache', [s['id'] for s in found])

    def test_gateway_rate_limit_blocks_sibling_models(self):
        spec={'id':'kilo-auto/free','base_url':'https://api.kilo.ai/api/gateway'}
        provider_pool._mark_failure(spec, providers.ProviderError('account rate limited',429))
        self.assertFalse(provider_pool._available(dict(spec,id='other:free')))

    def test_rate_limit_recovers_after_cooldown_without_replaying_tools(self):
        self.assertTrue(hasattr(provider_pool, 'chat_recovering'), 'bounded automatic recovery missing')
        class RateThenGood:
            calls = 0
            def chat(self, *args, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise providers.ProviderError('rate limited', 429)
                return 'completed safely'
        class Clock:
            now = 0.0
            def wait(self, seconds):
                self.now += seconds
                return False
            def is_set(self): return False
        clock = Clock()
        api = RateThenGood()
        c = SimpleNamespace(spec={'id':'current', 'capabilities':['text','tools','streaming']}, model_name='current', provider=api)
        events=[]
        with patch.object(provider_pool, '_now', side_effect=lambda: clock.now), \
             patch.object(provider_pool, 'candidates', return_value=[]):
            result = provider_pool.chat_recovering(c, [], tools=[{'function':{'name':'write'}}],
                cancel=clock, on_status=events.append, recovery_budget=45)
        self.assertEqual(result, 'completed safely')
        self.assertEqual(api.calls, 2)
        self.assertTrue(events)
        self.assertGreaterEqual(clock.now, 30)

    def test_stop_cancels_provider_wait_before_any_retry(self):
        self.assertTrue(hasattr(provider_pool, 'chat_recovering'), 'bounded automatic recovery missing')
        token = SimpleNamespace(is_set=lambda: False, wait=lambda seconds: True)
        api = SimpleNamespace(chat=lambda *a, **k: (_ for _ in ()).throw(providers.ProviderError('rate limited', 429)))
        c = SimpleNamespace(spec={'id':'current'}, model_name='current', provider=api)
        with patch.object(provider_pool, 'candidates', return_value=[]):
            with self.assertRaises(providers.Cancelled):
                provider_pool.chat_recovering(c, [], cancel=token, recovery_budget=45)

    def test_recovery_never_retries_partial_stream_output(self):
        self.assertTrue(hasattr(provider_pool, 'chat_recovering'), 'bounded automatic recovery missing')
        calls=[]
        def partial(*args, **kwargs):
            calls.append('attempt')
            kwargs['on_event']('text', 'partial')
            raise providers.ProviderError('rate limited', 429)
        c=SimpleNamespace(spec={'id':'current'},model_name='current',provider=SimpleNamespace(chat=partial))
        with self.assertRaises(providers.ProviderError):
            provider_pool.chat_recovering(c, [], on_event=lambda *a: None, recovery_budget=45)
        self.assertEqual(calls, ['attempt'])

    def test_downloaded_capable_local_model_is_a_last_resort_without_credentials(self):
        import tempfile
        from pathlib import Path
        from . import models
        api=SimpleNamespace(chat=lambda *a, **k: (_ for _ in ()).throw(providers.ProviderError('rate limited',429)))
        current=SimpleNamespace(spec={'id':'current'},model_name='current',provider=api,server=None)
        backup=SimpleNamespace(spec={'id':'local-backup'},provider=object(),model_name='local',server=object(),chat=lambda *a, **k:'local result')
        with tempfile.TemporaryDirectory() as temp:
            file=Path(temp)/'already-downloaded.gguf';file.write_bytes(b'fixture')
            cfg={'models':{'local-backup':{'provider':'local','file':str(file),'capabilities':['text','tools','streaming']}}}
            with patch.object(provider_pool.settings,'user',return_value=cfg), \
                 patch.object(provider_pool,'candidates',return_value=[]), patch.object(models,'connect',return_value=backup):
                try:
                    result=provider_pool.chat_recovering(current, [], tools=[{'function':{'name':'read'}}],recovery_budget=0)
                except providers.ProviderError as exc:
                    result=str(exc)
                self.assertEqual(result,'local result')
        self.assertEqual(current.spec['id'],'local-backup')
        self.assertIs(current.server,backup.server)

    def test_free_pool_order_and_current_provider_is_not_repeated(self):
        c = SimpleNamespace(
            spec={"id": "current", "base_url": "https://current.example/v1"},
            model_name="current",
            provider=None,
        )
        env = {
            "GROQ_API_KEY": "g",
            "GEMINI_API_KEY": "m",
            "OPENROUTER_API_KEY": "o",
            "NVIDIA_API_KEY": "n",
        }
        with patch.dict(os.environ, env, clear=True), patch.object(provider_pool.settings, "user", return_value={}):
            ids = [x["id"] for x in provider_pool.candidates(c)]
        self.assertEqual(ids, [
            "groq/gpt-oss-120b-free",
            "gemini/3.7-flash-free",
            "openrouter/free",
            "nvidia/nemotron-3-ultra-free",
            "aihorde/anonymous-free",
        ])

    def test_skips_services_without_credentials(self):
        c = SimpleNamespace(spec={"id": "current"}, model_name="current", provider=None)
        with patch.dict(os.environ, {}, clear=True), patch.object(provider_pool.settings, "user", return_value={}):
            self.assertEqual(
                [x["id"] for x in provider_pool.candidates(c)],
                ["aihorde/anonymous-free"],
            )

    def test_android_keystore_credentials_feed_the_free_pool(self):
        c = SimpleNamespace(spec={"id": "current"}, model_name="current", provider=None)
        with patch.dict(os.environ, {}, clear=True), \
             patch.object(provider_keys, "secret", return_value="vault-secret-key"), \
             patch.object(provider_pool.settings, "user", return_value={}):
            ids = [x["id"] for x in provider_pool.candidates(c)]
        self.assertEqual(ids, [
            "groq/gpt-oss-120b-free",
            "gemini/3.7-flash-free",
            "openrouter/free",
            "nvidia/nemotron-3-ultra-free",
            "aihorde/anonymous-free",
        ])

    def test_paid_custom_fallback_is_never_silently_used(self):
        c = SimpleNamespace(spec={"id": "current"}, model_name="current", provider=None)
        configured = [
            {"id": "paid", "base_url": "https://paid.example/v1", "api_key": "x", "model": "expensive"},
            {"id": "free-custom", "base_url": "https://free.example/v1", "api_key": "x", "model": "free", "free": True},
        ]
        with patch.dict(os.environ, {}, clear=True), patch.object(
                provider_pool.settings, "user", return_value={"fallback_models": configured}):
            ids = [x["id"] for x in provider_pool.candidates(c)]
        self.assertEqual(ids, ["free-custom", "aihorde/anonymous-free"])

    def test_switches_after_overload_and_reports_real_target(self):
        class Bad:
            def chat(self, *a, **k):
                raise providers.ProviderError("overloaded", 503)

        class Good:
            def chat(self, *a, **k):
                return "ok"

        c = SimpleNamespace(
            spec={"id": "nvidia", "base_url": "https://integrate.api.nvidia.com/v1"},
            model_name="nvidia",
            provider=Bad(),
        )
        backup = {
            "id": "backup/free",
            "model": "backup",
            "base_url": "https://x.example/v1",
            "provider": "openai",
            "free": True,
        }
        with patch.object(provider_pool, "candidates", return_value=[backup]),              patch.object(provider_pool, "provider", return_value=Good()),              patch.object(provider_pool.time, "sleep", return_value=None),              patch.object(provider_pool.random, "random", return_value=0):
            self.assertEqual(provider_pool.chat(c, [], tools=[]), "ok")
        self.assertEqual(c.model_name, "backup")
        self.assertEqual(c._last_fallback["to"], "backup/free")

    def test_overloaded_provider_enters_cooldown_and_is_skipped(self):
        spec = {
            "id": "groq/gpt-oss-120b-free",
            "base_url": "https://api.groq.com/openai/v1",
            "model": "openai/gpt-oss-120b",
            "api_key": "g",
            "free": True,
        }
        provider_pool._mark_failure(spec, providers.ProviderError("overloaded", 503))
        c = SimpleNamespace(spec={"id": "current"}, model_name="current", provider=None)
        with patch.dict(os.environ, {"GROQ_API_KEY": "g"}, clear=True), patch.object(
                provider_pool.settings, "user", return_value={}):
            ids = [x["id"] for x in provider_pool.candidates(c)]
        self.assertNotIn("groq/gpt-oss-120b-free", ids)
        self.assertTrue(provider_pool.health_snapshot()["groq/gpt-oss-120b-free"]["cooling_down"])

    def test_does_not_fail_over_after_visible_stream_output(self):
        class Partial:
            def chat(self, *a, **k):
                k["on_event"]("text", "hello")
                raise providers.ProviderError("connection reset")

        c = SimpleNamespace(spec={"id": "current"}, model_name="current", provider=Partial())
        seen = []
        with patch.object(provider_pool, "candidates") as candidates,              patch.object(provider_pool.time, "sleep", return_value=None):
            with self.assertRaises(providers.ProviderError):
                provider_pool.chat(c, [], on_event=lambda kind, payload: seen.append((kind, payload)))
        self.assertEqual(seen, [("text", "hello")])
        candidates.assert_not_called()


    def test_request_capabilities_detects_vision_tools_and_streaming(self):
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": "Describe this image"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
            ],
        }]
        required = provider_pool.request_capabilities(
            messages,
            tools=[{"type": "function", "function": {"name": "lookup"}}],
        )
        self.assertTrue({"text", "vision", "tools", "streaming"}.issubset(required))

    def test_vision_candidates_exclude_text_only_models(self):
        c = SimpleNamespace(spec={"id": "current"}, model_name="current", provider=None)
        env = {
            "GROQ_API_KEY": "g",
            "GEMINI_API_KEY": "m",
            "OPENROUTER_API_KEY": "o",
            "NVIDIA_API_KEY": "n",
        }
        required = {"text", "vision", "streaming"}
        with patch.dict(os.environ, env, clear=True), patch.object(
                provider_pool.settings, "user", return_value={}):
            ids = [x["id"] for x in provider_pool.candidates(c, required)]
        self.assertEqual(ids, ["gemini/3.7-flash-free", "openrouter/free"])

    def test_tool_requirement_skips_undeclared_custom_fallback(self):
        c = SimpleNamespace(spec={"id": "current"}, model_name="current", provider=None)
        configured = [
            {
                "id": "text-only/free",
                "base_url": "https://text.example/v1",
                "api_key": "x",
                "model": "text-only",
                "free": True,
            },
            {
                "id": "tools/free",
                "base_url": "https://tools.example/v1",
                "api_key": "x",
                "model": "tools",
                "free": True,
                "capabilities": ["text", "streaming", "tools"],
            },
        ]
        with patch.dict(os.environ, {}, clear=True), patch.object(
                provider_pool.settings, "user", return_value={"fallback_models": configured}):
            ids = [x["id"] for x in provider_pool.candidates(
                c, {"text", "streaming", "tools"})]
        self.assertEqual(ids, ["tools/free"])

    def test_vision_request_skips_incompatible_current_before_network_call(self):
        class Never:
            calls = 0

            def chat(self, *a, **k):
                self.calls += 1
                raise AssertionError("text-only current provider must not receive an image")

        class Good:
            def chat(self, *a, **k):
                return "vision-ok"

        current = Never()
        c = SimpleNamespace(
            spec={
                "id": "text-only",
                "base_url": "https://text.example/v1",
                "capabilities": ["text", "tools", "streaming"],
            },
            model_name="text-only",
            provider=current,
        )
        backup = {
            "id": "vision/free",
            "model": "vision",
            "base_url": "https://vision.example/v1",
            "provider": "openai",
            "free": True,
            "capabilities": ["text", "vision", "streaming"],
        }
        messages = [{
            "role": "user",
            "content": [{"type": "image_url", "image_url": {"url": "https://example.test/a.png"}}],
        }]
        with patch.object(provider_pool, "candidates", return_value=[backup]), patch.object(
                provider_pool, "provider", return_value=Good()):
            self.assertEqual(provider_pool.chat(c, messages, tools=[]), "vision-ok")
        self.assertEqual(current.calls, 0)
        self.assertEqual(c._last_fallback["to"], "vision/free")

    def test_legacy_current_provider_keeps_existing_tool_behavior(self):
        class Good:
            def chat(self, *a, **k):
                return "ok"

        c = SimpleNamespace(
            spec={"id": "legacy-current", "base_url": "https://legacy.example/v1"},
            model_name="legacy-current",
            provider=Good(),
        )
        with patch.object(provider_pool, "candidates") as candidates:
            result = provider_pool.chat(
                c,
                [{"role": "user", "content": "Use a tool"}],
                tools=[{"type": "function", "function": {"name": "lookup"}}],
            )
        self.assertEqual(result, "ok")
        candidates.assert_not_called()

    def test_no_compatible_provider_returns_capability_safe_error(self):
        class Never:
            def chat(self, *a, **k):
                raise AssertionError("incompatible provider must not be called")

        c = SimpleNamespace(
            spec={
                "id": "text-only",
                "base_url": "https://text.example/v1",
                "capabilities": ["text", "streaming"],
            },
            model_name="text-only",
            provider=Never(),
        )
        messages = [{
            "role": "user",
            "content": [{"type": "image_url", "image_url": {"url": "https://example.test/a.png"}}],
        }]
        with patch.object(provider_pool, "candidates", return_value=[]):
            with self.assertRaises(providers.ProviderError) as caught:
                provider_pool.chat(c, messages)
        text = str(caught.exception).lower()
        self.assertIn("supports this request", text)
        self.assertIn("vision", text)
        self.assertNotIn("https://text.example", text)

    def test_aggregate_error_does_not_leak_backend_error_body(self):
        class CurrentBad:
            def chat(self, *a, **k):
                raise providers.ProviderError("sensitive backend detail token=abc", 503)

        class FallbackBad:
            def chat(self, *a, **k):
                raise providers.ProviderError("secret quota payload xyz", 429)

        c = SimpleNamespace(
            spec={
                "id": "current",
                "base_url": "https://current.example/v1",
                "capabilities": ["text", "streaming"],
            },
            model_name="current",
            provider=CurrentBad(),
        )
        backup = {
            "id": "backup/free",
            "model": "backup",
            "base_url": "https://backup.example/v1",
            "provider": "openai",
            "free": True,
            "capabilities": ["text", "streaming"],
        }
        with patch.object(provider_pool, "candidates", return_value=[backup]), \
             patch.object(provider_pool, "provider", return_value=FallbackBad()), \
             patch.object(provider_pool.time, "sleep", return_value=None), \
             patch.object(provider_pool.random, "random", return_value=0):
            with self.assertRaises(providers.ProviderError) as caught:
                provider_pool.chat(c, [{"role": "user", "content": "hello"}])
        text = str(caught.exception).lower()
        self.assertIn("temporarily unavailable", text)
        self.assertIn("rate limited", text)
        self.assertNotIn("token=abc", text)
        self.assertNotIn("quota payload", text)


    def test_retry_after_controls_cooldown_and_snapshot_hides_raw_body(self):
        spec = {
            "id": "rate-limited/free",
            "base_url": "https://rate.example/v1",
            "model": "m",
            "free": True,
        }
        error = providers.ProviderError("backend token=secret-value rate limited", 429,
                                        '{"error":{"retry_after":47,"detail":"private"}}')
        with patch.object(provider_pool, "_now", return_value=100.0):
            provider_pool._mark_failure(spec, error)
            state = provider_pool.health_snapshot()[spec["id"]]
        self.assertEqual(state["state"], "rate_limited")
        self.assertEqual(state["cooldown_until"], 147.0)
        self.assertEqual(state["retry_after_seconds"], 47)
        self.assertEqual(state["last_error_class"], "rate limited")
        self.assertNotIn("private", str(state))
        self.assertNotIn("secret-value", str(state))

    def test_404_capability_mismatch_fails_over_without_blind_retry(self):
        class Missing:
            def __init__(self):
                self.calls = 0

            def chat(self, *args, **kwargs):
                self.calls += 1
                raise providers.ProviderError("model route missing", 404)

        class Good:
            def chat(self, *args, **kwargs):
                return "ok"

        bad = Missing()
        current = SimpleNamespace(
            spec={
                "id": "current",
                "base_url": "https://current.example/v1",
                "capabilities": ["text", "streaming"],
            },
            model_name="current",
            provider=bad,
        )
        backup = {
            "id": "backup/free",
            "model": "backup",
            "base_url": "https://backup.example/v1",
            "provider": "openai",
            "free": True,
            "capabilities": ["text", "streaming"],
        }
        with patch.object(provider_pool, "candidates", return_value=[backup]), \
             patch.object(provider_pool, "provider", return_value=Good()), \
             patch.object(provider_pool.time, "sleep", return_value=None):
            self.assertEqual(provider_pool.chat(current, [{"role": "user", "content": "hello"}]), "ok")
        self.assertEqual(bad.calls, 1)
        self.assertEqual(provider_pool.health_snapshot()["current"]["state"], "capability_mismatch")

    def test_watchdog_stall_before_output_fails_over_to_next_provider(self):
        class Token:
            def __init__(self):
                self.consumed = 0

            def reason(self):
                return "watchdog_stall"

            def consume_watchdog(self):
                self.consumed += 1
                return True

        class Stalled:
            def chat(self, *args, **kwargs):
                raise providers.Cancelled()

        class Good:
            def chat(self, *args, **kwargs):
                return "recovered"

        token = Token()
        current = SimpleNamespace(
            spec={
                "id": "stalled/free",
                "base_url": "https://stalled.example/v1",
                "capabilities": ["text", "streaming"],
            },
            model_name="stalled",
            provider=Stalled(),
        )
        backup = {
            "id": "backup/free",
            "model": "backup",
            "base_url": "https://backup.example/v1",
            "provider": "openai",
            "free": True,
            "capabilities": ["text", "streaming"],
        }
        with patch.object(provider_pool, "candidates", return_value=[backup]), \
             patch.object(provider_pool, "provider", return_value=Good()):
            result = provider_pool.chat(
                current,
                [{"role": "user", "content": "hello"}],
                cancel=token,
            )
        self.assertEqual(result, "recovered")
        self.assertEqual(token.consumed, 1)
        self.assertEqual(current._last_fallback["to"], "backup/free")

    def test_user_stop_is_never_converted_to_provider_failover(self):
        class Token:
            def reason(self):
                return "user"

        class CancelledByUser:
            def chat(self, *args, **kwargs):
                raise providers.Cancelled()

        current = SimpleNamespace(
            spec={
                "id": "current",
                "base_url": "https://current.example/v1",
                "capabilities": ["text", "streaming"],
            },
            model_name="current",
            provider=CancelledByUser(),
        )
        with patch.object(provider_pool, "candidates") as candidates:
            with self.assertRaises(providers.Cancelled):
                provider_pool.chat(
                    current,
                    [{"role": "user", "content": "stop"}],
                    cancel=Token(),
                )
        candidates.assert_not_called()

    def test_freellmapi_is_not_a_single_point_of_failure(self):
        class GatewayDown:
            def chat(self, *args, **kwargs):
                raise providers.ProviderError("gateway unavailable", 503)

        class DirectGood:
            def chat(self, *args, **kwargs):
                return "direct-ok"

        current = SimpleNamespace(
            spec={
                "id": "freellmapi/auto-free",
                "base_url": "http://127.0.0.1:3001/v1",
                "capabilities": ["text", "streaming"],
            },
            model_name="auto",
            provider=GatewayDown(),
        )
        backup = {
            "id": "groq/gpt-oss-120b-free",
            "model": "openai/gpt-oss-120b",
            "base_url": "https://api.groq.com/openai/v1",
            "provider": "openai",
            "free": True,
            "capabilities": ["text", "streaming"],
        }
        with patch.object(provider_pool, "candidates", return_value=[backup]), \
             patch.object(provider_pool, "provider", return_value=DirectGood()), \
             patch.object(provider_pool.time, "sleep", return_value=None), \
             patch.object(provider_pool.random, "random", return_value=0):
            self.assertEqual(
                provider_pool.chat(current, [{"role": "user", "content": "hello"}]),
                "direct-ok",
            )
        self.assertEqual(current._last_fallback["to"], "groq/gpt-oss-120b-free")


if __name__ == "__main__":
    unittest.main()
