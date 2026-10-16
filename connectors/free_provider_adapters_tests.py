import json
import os
import unittest
from unittest.mock import patch

from . import free_provider_adapters as adapters
from . import providers


class _Response:
    status = 200

    def getheader(self, name):
        return None


class _Stream:
    last_timeout = None

    def __init__(self, url, body, headers, timeout=0):
        self.url = url
        self.body = body
        self.headers = headers
        self.timeout = timeout
        _Stream.last_timeout = timeout
        self.resp = _Response()

    def lines(self):
        yield 'data: ' + json.dumps({"choices": [{"delta": {"content": "hello "}}]})
        yield 'data: ' + json.dumps({"choices": [{"delta": {"content": "world"}, "finish_reason": "stop"}]})
        yield "data: [DONE]"

    def read_all(self):
        return ""

    def close(self):
        pass


class FreeProviderAdapterTests(unittest.TestCase):
    def test_freellmapi_is_optional_and_ai_horde_is_always_independent(self):
        with patch.dict(os.environ, {}, clear=True):
            specs = adapters.extra_free_specs()
        self.assertEqual([x["id"] for x in specs], ["aihorde/anonymous-free"])
        self.assertEqual(specs[0]["api_key"], adapters.AIHORDE_ANONYMOUS_KEY)

        with patch.dict(os.environ, {
            "FREELLMAPI_API_KEY": "free-router-key",
            "FREELLMAPI_BASE_URL": "http://127.0.0.1:3999/v1",
        }, clear=True):
            specs = adapters.extra_free_specs()
        self.assertEqual([x["id"] for x in specs],
                         ["freellmapi/auto-free", "aihorde/anonymous-free"])
        self.assertEqual(specs[0]["base_url"], "http://127.0.0.1:3999/v1")
        self.assertNotEqual(specs[0]["base_url"], specs[1]["base_url"])

    def test_ai_horde_adapter_streams_text_with_bounded_timeout(self):
        seen = []
        client = adapters.AIHordeProvider(timeout=120)
        with patch.object(client, "_resolve_model", return_value="live-model"), \
             patch.object(providers, "Stream", _Stream), \
             patch.object(providers, "_watch", return_value=None):
            result = client.chat(
                "auto",
                [{"role": "user", "content": "hi", "_internal": "do-not-send"}],
                on_event=lambda kind, payload: seen.append((kind, payload)),
            )
        self.assertEqual(result.content, "hello world")
        self.assertEqual(seen, [("text", "hello "), ("text", "world")])
        self.assertEqual(_Stream.last_timeout, 120)

    def test_ai_horde_rejects_tools_before_network(self):
        client = adapters.AIHordeProvider()
        with patch.object(client, "_resolve_model") as resolve:
            with self.assertRaises(providers.ProviderError) as caught:
                client.chat("auto", [], tools=[{"type": "function"}])
        self.assertEqual(caught.exception.status, 404)
        resolve.assert_not_called()


if __name__ == "__main__":
    unittest.main()
