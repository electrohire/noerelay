from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from scripts.remote_service_smoke import SmokeCheckError, main


VALID_ENV = {
    "OPENROUTER_API_KEY": "test-openrouter-secret",
    "HF_TOKEN": "test-hugging-face-secret",
    "OPENROUTER_BASE_URL": "https://openrouter.ai/api/v1",
    "OPENROUTER_HTTP_REFERER": "https://github.com/electrohire/noerelay",
    "OPENROUTER_APP_TITLE": "NoeRelay",
    "NOERELAY_LIVE_TESTS": "0",
}


class RemoteSmokeTests(unittest.TestCase):
    def test_rejects_missing_secret_without_network_access(self):
        environment = {key: value for key, value in VALID_ENV.items() if key != "HF_TOKEN"}
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(SmokeCheckError, "HF_TOKEN"):
                main()

    def test_rejects_unapproved_openrouter_endpoint(self):
        environment = {**VALID_ENV, "OPENROUTER_BASE_URL": "https://example.invalid/api/v1"}
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(SmokeCheckError, "approved endpoint"):
                main()

    def test_validates_credentials_and_resolves_benchmark_revisions(self):
        responses = [
            {"data": {"is_free_tier": False}},
            {"name": "test-identity"},
            {"sha": "a" * 40},
            {"sha": "b" * 40},
        ]
        with patch.dict(os.environ, VALID_ENV, clear=True):
            with patch("scripts.remote_service_smoke._request_json", side_effect=responses) as request:
                self.assertEqual(main(), 0)
        self.assertEqual(request.call_count, 4)


if __name__ == "__main__":
    unittest.main()
