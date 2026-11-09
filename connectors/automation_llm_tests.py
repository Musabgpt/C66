"""Hermetic tests for the free-pool OpenAI endpoint (no network)."""

import unittest

from . import automation_llm, providers


class _Client:
    model_name = "test-free-model"


class AutomationLlmTests(unittest.TestCase):
    def setUp(self):
        from . import models, provider_pool
        self._connect = models.connect
        self._chat = provider_pool.chat_recovering
        self._models = provider_pool._kilo_models
        models.connect = lambda spec, ctx=0: _Client()
        provider_pool._kilo_models = lambda: [
            {"id": "vendor/x:free", "model": "vendor/x:free"}]

    def tearDown(self):
        from . import models, provider_pool
        models.connect = self._connect
        provider_pool.chat_recovering = self._chat
        provider_pool._kilo_models = self._models

    def _completion(self, content="", tool_calls=()):
        out = providers.Completion()
        out.content = content
        out.tool_calls = [dict(c) for c in tool_calls]
        out.finish = "stop"
        out.usage = {"prompt": 10, "cached": 2, "new": 0, "output": 5}
        return out

    def test_tool_call_is_shaped_as_openai(self):
        from . import provider_pool
        provider_pool.chat_recovering = lambda client, messages, tools=None, **kw: self._completion(
            tool_calls=[{"id": "c1", "name": "post", "arguments": '{"text":"hi"}'}])
        status, resp = automation_llm.complete(
            {"messages": [{"role": "user", "content": "post hi"}],
             "tools": [{"type": "function", "function": {"name": "post"}}]})
        self.assertEqual(status, 200)
        self.assertEqual(resp["object"], "chat.completion")
        choice = resp["choices"][0]
        self.assertEqual(choice["finish_reason"], "tool_calls")
        tc = choice["message"]["tool_calls"][0]
        self.assertEqual(tc["type"], "function")
        self.assertEqual(tc["function"]["name"], "post")
        self.assertEqual(resp["usage"]["prompt_tokens"], 12)
        self.assertEqual(resp["usage"]["completion_tokens"], 5)
        self.assertEqual(resp["model"], "test-free-model")

    def test_plain_text_answer(self):
        from . import provider_pool
        provider_pool.chat_recovering = lambda client, messages, tools=None, **kw: self._completion(
            content="done")
        status, resp = automation_llm.complete(
            {"messages": [{"role": "user", "content": "say done"}]})
        self.assertEqual(status, 200)
        self.assertEqual(resp["choices"][0]["message"]["content"], "done")
        self.assertEqual(resp["choices"][0]["finish_reason"], "stop")
        self.assertNotIn("tool_calls", resp["choices"][0]["message"])

    def test_missing_messages_is_rejected(self):
        status, resp = automation_llm.complete({"model": "auto"})
        self.assertEqual(status, 400)
        self.assertIn("error", resp)

    def test_upstream_failure_maps_to_502(self):
        from . import provider_pool
        def boom(client, messages, tools=None, **kw):
            raise providers.ProviderError("down", 503)
        provider_pool.chat_recovering = boom
        status, resp = automation_llm.complete(
            {"messages": [{"role": "user", "content": "x"}]})
        self.assertEqual(status, 502)
        self.assertIn("error", resp)

    def test_models_list_contains_default(self):
        data = automation_llm.models_list()
        ids = [m["id"] for m in data["data"]]
        self.assertIn("kilo-auto/free", ids)
        self.assertIn("vendor/x:free", ids)

    def test_route_dispatch(self):
        from . import provider_pool
        provider_pool.chat_recovering = lambda client, messages, tools=None, **kw: self._completion(content="ok")
        captured = {}

        class H:
            def _json(self, data, status=200):
                captured["data"] = data
                captured["status"] = status

        self.assertTrue(automation_llm.route(H(), "GET", "/v1/free/models"))
        self.assertIn("data", captured["data"])
        self.assertTrue(automation_llm.route(
            H(), "POST", "/v1/free/chat/completions",
            {"messages": [{"role": "user", "content": "hi"}]}))
        self.assertEqual(captured["status"], 200)
        self.assertFalse(automation_llm.route(H(), "GET", "/v1/other"))


if __name__ == "__main__":
    unittest.main()
