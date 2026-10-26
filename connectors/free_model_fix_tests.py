"""Regression tests for reported WhatsApp overlay + NVIDIA free model failures."""
import unittest
from types import SimpleNamespace
from unittest import mock

from . import models, nvidia_models, provider_pool, providers


class FreeOverlayRegressionTests(unittest.TestCase):
    def test_nemotron_declares_tools_streaming_reasoning(self):
        spec = nvidia_models._spec("nvidia/nemotron-3-ultra-550b-a55b")
        self.assertTrue({"reasoning", "tools", "streaming", "text"}.issubset(spec["capabilities"]))
        self.assertTrue(provider_pool._supports(spec, {"text", "tools", "streaming", "reasoning"}))

    def test_nvidia_non_reasoning_models_are_not_mislabelled(self):
        spec = nvidia_models._spec("meta/llama-3.1-8b-instruct")
        self.assertNotIn("reasoning", spec["capabilities"])

    def test_nvidia_request_keeps_tools_but_drops_unsupported_reasoning_effort(self):
        spec = nvidia_models._spec("nvidia/nemotron-3-ultra-550b-a55b")
        backend = provider_pool.provider(dict(spec, api_key="private"))
        payload = backend.body(
            spec["model"],
            [{"role": "user", "content": "Hi"}],
            [{"type": "function", "function": {"name": "phone", "parameters": {"type": "object"}}}],
            64, None, "medium", None)
        self.assertEqual(payload["model"], spec["model"])
        self.assertIn("tools", payload)
        self.assertNotIn("reasoning_effort", payload)
        self.assertNotIn("private", str(payload))

    def test_missing_nvidia_key_is_actionable_not_capability_confusion(self):
        spec = nvidia_models._spec("nvidia/nemotron-3-ultra-550b-a55b")
        current = SimpleNamespace(spec=spec, provider=object(), model_name=spec["model"])
        with mock.patch.object(provider_pool, "_key", return_value=""), \
             mock.patch.object(provider_pool, "candidates", return_value=[]):
            with self.assertRaises(providers.ProviderError) as caught:
                provider_pool.chat(current, [{"role": "user", "content": "Hi"}],
                                   tools=[{"type": "function", "function": {"name": "phone"}}],
                                   reasoning="medium")
        self.assertIn("NVIDIA", str(caught.exception))
        self.assertNotIn("supports this request", str(caught.exception))

    def test_selected_nvidia_uses_secure_nim_client(self):
        spec = nvidia_models._spec("nvidia/nemotron-3-ultra-550b-a55b")
        with mock.patch.object(models, "_key", return_value="sample-key"):
            client = models.connect(spec)
        self.assertIsInstance(client.provider, provider_pool.NvidiaNimCompat)
        self.assertEqual(client.model_name, spec["model"])

    def test_selected_nvidia_without_key_fails_before_network(self):
        spec = nvidia_models._spec("nvidia/nemotron-3-ultra-550b-a55b")
        with mock.patch.object(models, "_key", return_value=""):
            with self.assertRaisesRegex(ValueError, "NVIDIA not connected"):
                models.connect(spec)

    def test_anonymous_auto_free_has_no_api_key(self):
        with mock.patch.object(models.catalog, "MODELS", []), \
             mock.patch.object(models, "shared_ggufs", return_value=[]), \
             mock.patch.object(models, "_desktop_models", return_value=[]), \
             mock.patch.object(models.onetap, "registered", return_value={}), \
             mock.patch.object(models.settings, "user", return_value={"models": {}}), \
             mock.patch.object(nvidia_models, "models", return_value=[]), \
             mock.patch.object(models.settings, "MODEL_DIRS", []):
            registry = models.registry()
        item = registry["kilo-public/auto-free"]
        self.assertTrue(item["free"])
        self.assertFalse(bool(item.get("api_key")))
        self.assertTrue({"text", "tools", "streaming"}.issubset(item["capabilities"]))


if __name__ == "__main__":
    unittest.main()
