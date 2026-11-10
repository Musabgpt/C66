"""Hermetic tests for the free-pool OpenAI endpoint (fake providers, no network)."""

import io
import json
import time
import unittest

from . import automation_llm, provider_pool, providers


def spec(mid, caps=("text", "tools", "streaming"), base="https://free.example/v1"):
    return {"id": mid, "model": mid, "base_url": base, "provider": "openai", "free": True,
            "api_key": "", "capabilities": list(caps)}


class FakeProvider:
    """Behaves by model id: good*, empty, boom, stall, fenced, prose, tool."""
    calls = []

    def __init__(self, s):
        self.spec = s

    def chat(self, model, messages, tools=None, max_tokens=4096, temperature=None, reasoning=None,
             on_event=None, cancel=None, extra=None):
        FakeProvider.calls.append({"model": model, "messages": messages, "tools": tools,
                                   "max_tokens": max_tokens, "extra": extra})
        out = providers.Completion()
        out.finish = "stop"
        out.usage = {"prompt": 10, "cached": 2, "new": 0, "output": 5}
        if model.startswith("good"):
            out.content = "answer from " + model
        elif model == "empty":
            out.finish = "length"
        elif model == "boom":
            raise providers.ProviderError("upstream down", 503)
        elif model == "stall":
            cancel.wait(20)
            raise providers.Cancelled()
        elif model == "fenced":
            out.content = '```json\n{"action": "click", "index": 2}\n```'
        elif model == "prose":
            out.content = "I think you should click the second button."
        elif model == "tool":
            out.tool_calls = [{"id": "c1", "name": "post", "arguments": '{"text":"hi"}'}]
        return out


class AutomationLlmTests(unittest.TestCase):
    def setUp(self):
        self.saved = (provider_pool.candidates, provider_pool.provider, provider_pool._kilo_models,
                      automation_llm.STALL_SECONDS, automation_llm._primary)
        provider_pool.HEALTH.clear()
        automation_llm._LAST_GOOD["id"] = ""
        FakeProvider.calls = []
        provider_pool.provider = FakeProvider
        provider_pool._kilo_models = lambda: []
        self.pool([spec("good")])

    def tearDown(self):
        (provider_pool.candidates, provider_pool.provider, provider_pool._kilo_models,
         automation_llm.STALL_SECONDS, automation_llm._primary) = self.saved
        provider_pool.HEALTH.clear()

    def pool(self, specs):
        """The models the endpoint may use, in the pool's own order: the first is the primary."""
        automation_llm._primary = lambda model="": dict(specs[0])
        provider_pool.candidates = lambda current, required=None, include_cooling=False: [
            dict(s) for s in specs[1:]
            if (include_cooling or provider_pool._available(s))
            and (required is None or provider_pool._supports(s, required))]

    def ask(self, **extra):
        body = {"messages": [{"role": "user", "content": "what now?"}]}
        body.update(extra)
        return automation_llm.complete(body)

    # ------------------------------------------------------------ the answer

    def test_a_good_answer_has_the_openai_shape(self):
        status, resp = self.ask()
        self.assertEqual(status, 200)
        self.assertEqual(resp["object"], "chat.completion")
        self.assertEqual(resp["choices"][0]["message"], {"role": "assistant", "content": "answer from good"})
        self.assertEqual(resp["choices"][0]["finish_reason"], "stop")
        self.assertEqual(resp["usage"], {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17})
        self.assertEqual(resp["model"], "good")

    def test_tool_calls_and_tool_choice(self):
        self.pool([spec("tool")])
        tools = [{"type": "function", "function": {"name": "post"}}]
        status, resp = self.ask(tools=tools, tool_choice="required")
        self.assertEqual(status, 200)
        call = resp["choices"][0]["message"]["tool_calls"][0]
        self.assertEqual((call["type"], call["function"]["name"]), ("function", "post"))
        self.assertEqual(resp["choices"][0]["finish_reason"], "tool_calls")
        self.assertEqual(FakeProvider.calls[0]["extra"], {"tool_choice": "required"})

    def test_missing_messages_is_rejected(self):
        status, resp = automation_llm.complete({"model": "auto"})
        self.assertEqual(status, 400)
        self.assertIn("error", resp)

    # ------------------------------------------------------------ never empty, never stuck

    def test_the_output_budget_has_a_floor_for_reasoning_models(self):
        self.ask(max_tokens=100)
        self.assertEqual(FakeProvider.calls[-1]["max_tokens"], automation_llm.MIN_OUTPUT_TOKENS)
        self.ask(max_tokens=9000)
        self.assertEqual(FakeProvider.calls[-1]["max_tokens"], 9000)

    def test_an_empty_answer_moves_to_the_next_model(self):
        self.pool([spec("empty"), spec("good")])
        status, resp = self.ask()
        self.assertEqual((status, resp["model"]), (200, "good"))
        self.assertFalse(provider_pool._available(spec("empty")))      # it cools down: not asked again
        FakeProvider.calls = []
        self.ask()
        self.assertEqual([c["model"] for c in FakeProvider.calls], ["good"])

    def test_a_failing_model_moves_to_the_next_one(self):
        self.pool([spec("boom"), spec("good")])
        status, resp = self.ask()
        self.assertEqual((status, resp["model"]), (200, "good"))

    def test_a_stalled_model_is_cancelled_and_the_next_one_answers(self):
        automation_llm.STALL_SECONDS = 0.4
        self.pool([spec("stall"), spec("good")])
        started = time.monotonic()
        status, resp = self.ask()
        self.assertEqual((status, resp["model"]), (200, "good"))
        self.assertLess(time.monotonic() - started, 5)
        self.assertFalse(provider_pool._available(spec("stall")))

    def test_when_every_model_fails_the_error_names_them(self):
        self.pool([spec("boom"), spec("empty")])
        status, resp = self.ask()
        self.assertEqual(status, 502)
        self.assertIn("boom", resp["error"]["message"])
        self.assertIn("empty", resp["error"]["message"])

    def test_the_model_that_answered_last_is_asked_first(self):
        self.pool([spec("good-a"), spec("good-b")])
        self.ask()
        self.assertEqual(FakeProvider.calls[-1]["model"], "good-a")
        automation_llm._LAST_GOOD["id"] = "good-b"
        provider_pool.HEALTH.clear()
        self.ask()
        self.assertEqual(FakeProvider.calls[-1]["model"], "good-b")

    def test_a_request_with_an_image_goes_to_a_model_that_sees(self):
        self.pool([spec("good-blind"), spec("good-eyes", caps=("text", "tools", "streaming", "vision"))])
        status, resp = automation_llm.complete({"messages": [{"role": "user", "content": [
            {"type": "text", "text": "what is on the screen?"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]}]})
        self.assertEqual((status, resp["model"]), (200, "good-eyes"))

    # ------------------------------------------------------------ structured output

    SCHEMA = {"type": "json_schema", "json_schema": {"name": "act", "schema": {
        "type": "object", "properties": {"action": {"type": "string"}}}}}

    def test_response_format_goes_to_a_model_that_supports_it(self):
        self.pool([spec("fenced", caps=("text", "tools", "streaming", "json"))])
        status, resp = self.ask(response_format=self.SCHEMA)
        self.assertEqual(status, 200)
        self.assertEqual(FakeProvider.calls[0]["extra"], {"response_format": self.SCHEMA})
        self.assertEqual(len(FakeProvider.calls[0]["messages"]), 1)            # nothing added to the prompt
        self.assertEqual(json.loads(resp["choices"][0]["message"]["content"]), {"action": "click", "index": 2})

    def test_without_native_support_the_schema_is_stated_and_the_json_checked(self):
        self.pool([spec("prose"), spec("fenced")])
        status, resp = self.ask(response_format=self.SCHEMA)
        self.assertEqual((status, resp["model"]), (200, "fenced"))             # prose was not JSON: next model
        sent = FakeProvider.calls[-1]
        self.assertIsNone(sent["extra"])
        self.assertIn("JSON schema", sent["messages"][-1]["content"])
        self.assertIn('"action"', sent["messages"][-1]["content"])
        self.assertEqual(json.loads(resp["choices"][0]["message"]["content"])["action"], "click")

    def test_a_model_with_native_json_is_preferred_for_structured_output(self):
        self.pool([spec("good-plain"), spec("fenced", caps=("text", "tools", "streaming", "json"))])
        status, resp = self.ask(response_format=self.SCHEMA)
        self.assertEqual(resp["model"], "fenced")
        self.assertEqual([c["model"] for c in FakeProvider.calls], ["fenced"])

    # ------------------------------------------------------------ the routes

    class Handler:
        def __init__(self):
            self.wfile = io.BytesIO()
            self.headers_sent = []
            self.status = None
            self.data = None

        def _json(self, data, status=200):
            self.data, self.status = data, status

        def send_response(self, status):
            self.status = status

        def send_header(self, name, value):
            self.headers_sent.append((name, value))

        def end_headers(self):
            pass

    def test_routes(self):
        h = self.Handler()
        self.assertTrue(automation_llm.route(h, "GET", "/v1/free/models"))
        self.assertIn("kilo-auto/free", [m["id"] for m in h.data["data"]])
        h = self.Handler()
        self.assertTrue(automation_llm.route(h, "POST", "/v1/free/chat/completions",
                                             {"messages": [{"role": "user", "content": "hi"}]}))
        self.assertEqual((h.status, h.data["choices"][0]["message"]["content"]), (200, "answer from good"))
        self.assertFalse(automation_llm.route(self.Handler(), "GET", "/v1/other"))

    def test_stream_true_is_served_as_server_sent_events(self):
        self.pool([spec("tool")])
        h = self.Handler()
        automation_llm.route(h, "POST", "/v1/free/chat/completions",
                             {"stream": True, "messages": [{"role": "user", "content": "post hi"}],
                              "tools": [{"type": "function", "function": {"name": "post"}}]})
        self.assertEqual(h.status, 200)
        self.assertIn(("Content-Type", "text/event-stream; charset=utf-8"), h.headers_sent)
        lines = [l[6:] for l in h.wfile.getvalue().decode().split("\n") if l.startswith("data: ")]
        self.assertEqual(lines[-1], "[DONE]")
        first, last = json.loads(lines[0]), json.loads(lines[-2])
        self.assertEqual(first["object"], "chat.completion.chunk")
        delta = first["choices"][0]["delta"]
        self.assertEqual(delta["role"], "assistant")
        self.assertEqual((delta["tool_calls"][0]["index"], delta["tool_calls"][0]["function"]["name"]), (0, "post"))
        self.assertEqual(last["choices"][0]["finish_reason"], "tool_calls")
        self.assertIn("usage", last)


if __name__ == "__main__":
    unittest.main()
