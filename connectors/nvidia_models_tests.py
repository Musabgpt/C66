"""Regression checks for NVIDIA model registration without leaking credentials."""
import json
import unittest
from unittest import mock

from . import nvidia_models


class NvidiaModelsTests(unittest.TestCase):
    def setUp(self):
        nvidia_models._CACHE = (0.0, [])

    def test_default_model_and_security_metadata(self):
        model = nvidia_models._spec("meta/llama-3.3-70b-instruct")
        self.assertEqual(model["base_url"], "https://integrate.api.nvidia.com/v1")
        self.assertEqual(model["secret_id"], "nvidia")
        self.assertEqual(model["id"], "nvidia/meta/llama-3.3-70b-instruct")
        self.assertNotIn("api_key", model)
        self.assertIsNone(nvidia_models._spec("bad model; rm -rf /"))

    def test_no_key_never_requests_remote_catalog(self):
        with mock.patch.object(nvidia_models.provider_keys, "secret", return_value=""), \
             mock.patch.object(nvidia_models, "_fetch_catalog") as fetch:
            rows = nvidia_models.models()
        fetch.assert_not_called()
        self.assertEqual(len(rows), 1)

    def test_secure_discovery_and_cache_dedupe(self):
        key = "secret-only-in-memory"
        with mock.patch.object(nvidia_models.provider_keys, "secret", return_value=key), \
             mock.patch.object(nvidia_models, "_fetch_catalog",
                               return_value=["meta/llama-3.3-70b-instruct",
                                             "meta/llama-3.3-70b-instruct", "../bad"]) as fetch:
            first = nvidia_models.models()
            second = nvidia_models.models()
        fetch.assert_called_once_with(key)
        self.assertEqual(len(first), len(second))
        self.assertEqual(len({x["id"] for x in first}), len(first))
        self.assertNotIn(key, json.dumps(first))

    def test_network_failure_does_not_remove_configured_or_original_models(self):
        with mock.patch.object(nvidia_models.provider_keys, "secret", return_value="private"), \
             mock.patch.object(nvidia_models, "_fetch_catalog", side_effect=OSError("offline")):
            models = nvidia_models.models()
        self.assertEqual(len(models), 1)
