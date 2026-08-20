"""Tests for structured logging with secret redaction."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reference"))

from gateway.structured_logging import (
    JsonFormatter,
    StructuredLogger,
    _redact_secrets,
    _redact_dict,
)


class SecretRedactionTests(unittest.TestCase):
    """Tests for content-aware secret redaction in structured logging."""

    def test_redact_openrouter_key(self):
        """OpenRouter API keys are redacted."""
        text = "Using key sk-or-v1-abc123def456ghi789jkl012mno345pqr678stu"
        redacted = _redact_secrets(text)
        self.assertNotIn("sk-or-v1", redacted)
        self.assertIn("[REDACTED:OPENROUTER_KEY]", redacted)

    def test_redact_openai_style_key(self):
        """OpenAI-style API keys are redacted."""
        text = "Authorization: Bearer sk-abcdefghijklmnopqrstuvwxyz123456"
        redacted = _redact_secrets(text)
        self.assertNotIn("sk-abcdef", redacted)
        self.assertIn("[REDACTED", redacted)

    def test_redact_hf_token(self):
        """Hugging Face tokens are redacted."""
        text = "Using token hf_abcdefghijklmnopqrstuvwxyz for HF"
        redacted = _redact_secrets(text)
        self.assertNotIn("hf_abcdef", redacted)
        self.assertIn("[REDACTED:HF_TOKEN]", redacted)

    def test_redact_bearer_token(self):
        """Bearer tokens in Authorization headers are redacted."""
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0"
        redacted = _redact_secrets(text)
        self.assertNotIn("eyJhbGci", redacted)
        self.assertIn("Bearer [REDACTED]", redacted)

    def test_redact_key_value_secrets(self):
        """JSON key-value secret patterns are redacted."""
        text = '{"api_key": "my-secret-value-12345", "name": "test"}'
        redacted = _redact_secrets(text)
        self.assertNotIn("my-secret-value", redacted)
        self.assertIn("[REDACTED]", redacted)

    def test_redact_password_field(self):
        """Password fields are redacted."""
        text = 'password=super-secret-password-123'
        redacted = _redact_secrets(text)
        self.assertNotIn("super-secret-password", redacted)
        self.assertIn("[REDACTED]", redacted)

    def test_redact_token_field(self):
        """Token fields are redacted."""
        text = 'token=abc123def456ghi789'
        redacted = _redact_secrets(text)
        self.assertNotIn("abc123def", redacted)
        self.assertIn("[REDACTED]", redacted)

    def test_non_secret_text_passes_through(self):
        """Non-secret text is not modified."""
        text = "Processing request for model anthropic/claude-3.5-sonnet"
        redacted = _redact_secrets(text)
        self.assertEqual(text, redacted)

    def test_redact_dict_recursive(self):
        """Nested dictionaries have secrets redacted recursively."""
        obj = {
            "message": "ok",
            "auth": {
                "api_key": "sk-secret-key-12345",
                "token": "hf_abcdefghijklmnopqrstuvwxyz",
            },
            "items": [
                "safe",
                "sk-or-v1-secretkey1234567890abcdefghijklmnopqrstuv",
            ],
        }
        redacted = _redact_dict(obj)
        self.assertEqual(redacted["message"], "ok")
        # Keys matching secret patterns get their values replaced with [REDACTED]
        self.assertEqual(redacted["auth"]["api_key"], "[REDACTED]")
        self.assertEqual(redacted["auth"]["token"], "[REDACTED]")
        self.assertEqual(redacted["items"][0], "safe")
        self.assertIn("[REDACTED:OPENROUTER_KEY]", redacted["items"][1])

    def test_empty_string(self):
        """Empty string is handled."""
        self.assertEqual(_redact_secrets(""), "")

    def test_no_false_positives(self):
        """Normal model IDs and URLs are not redacted."""
        texts = [
            "model_id=anthropic/claude-3.5-sonnet",
            "https://openrouter.ai/api/v1/chat/completions",
            "run_id=run-abc123-def456",
            "cost_usd=0.015",
        ]
        for text in texts:
            redacted = _redact_secrets(text)
            self.assertEqual(text, redacted, f"False positive for: {text}")

    def test_json_formatter_redacts(self):
        """JsonFormatter applies redaction to log messages."""
        import logging

        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="Using key sk-or-v1-abc123def456ghi789jkl012mno345pqr678stu",
            args=(), exc_info=None,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        self.assertIn("[REDACTED:OPENROUTER_KEY]", parsed["message"])
        self.assertNotIn("sk-or-v1", parsed["message"])

    def test_json_formatter_redacts_extra_fields(self):
        """JsonFormatter applies redaction to extra fields on log records."""
        import logging

        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="Request completed",
            args=(), exc_info=None,
        )
        record.correlation_id = "run-123"
        record.model_id = "anthropic/claude-3.5-sonnet"
        record.cost = 0.015
        output = formatter.format(record)
        parsed = json.loads(output)
        self.assertEqual(parsed["correlation_id"], "run-123")
        self.assertEqual(parsed["model_id"], "anthropic/claude-3.5-sonnet")
        self.assertEqual(parsed["cost"], 0.015)

    def test_structured_logger_redacts(self):
        """StructuredLogger._log applies redaction."""
        import logging
        import io

        logger = StructuredLogger(name="test-redact", output="stdout")
        # Capture log output
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JsonFormatter())
        logger._logger.handlers.clear()
        logger._logger.addHandler(handler)

        logger.info("Using API key sk-or-v1-testkey1234567890abcdefghijklmno")
        output = stream.getvalue().strip()
        parsed = json.loads(output)
        self.assertIn("[REDACTED:OPENROUTER_KEY]", parsed["message"])
        self.assertNotIn("sk-or-v1", parsed["message"])

    def test_structured_logger_redacts_kwargs(self):
        """StructuredLogger._log redacts secrets in kwargs."""
        import logging
        import io

        logger = StructuredLogger(name="test-redact-kwargs", output="stdout")
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JsonFormatter())
        logger._logger.handlers.clear()
        logger._logger.addHandler(handler)

        logger.info("Deployment config", api_key="sk-secret-value-12345")
        output = stream.getvalue().strip()
        parsed = json.loads(output)
        # Key-based redaction replaces the entire value with [REDACTED]
        self.assertEqual(parsed.get("api_key", ""), "[REDACTED]")


if __name__ == "__main__":
    unittest.main()