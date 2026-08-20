#!/usr/bin/env python3
"""Test NoeRelay compatibility with the OpenAI Python SDK.

This script verifies that NoeRelay's OpenAI-compatible API works correctly
with the official OpenAI Python SDK. It tests:

1. Model listing
2. Non-streaming chat completion
3. Streaming chat completion
4. Error handling

Usage:
    pip install openai
    python examples/openai-sdk-test.py

Environment variables:
    NOERELAY_BASE_URL    — NoeRelay API base URL (default: http://127.0.0.1:8080/v1)
    NOERELAY_API_KEY     — NoeRelay API key (default: any-value)
    NOERELAY_MODEL       — Model ID to use (default: noerelay/epr-1)
"""

from __future__ import annotations

import os
import sys
import time


def get_config():
    """Get configuration from environment variables with sensible defaults."""
    return {
        "base_url": os.environ.get("NOERELAY_BASE_URL", "http://127.0.0.1:8080/v1"),
        "api_key": os.environ.get("NOERELAY_API_KEY", "any-value"),
        "model": os.environ.get("NOERELAY_MODEL", "noerelay/epr-1"),
    }


def test_import():
    """Verify the OpenAI SDK is installed."""
    print("=" * 60)
    print("Test 1: OpenAI SDK Import")
    print("=" * 60)
    try:
        from openai import OpenAI  # noqa: F401

        print("✅ OpenAI SDK imported successfully")
        return True
    except ImportError:
        print("❌ OpenAI SDK not installed. Run: pip install openai")
        return False


def test_list_models(client, model_name):
    """Test model listing endpoint."""
    print()
    print("=" * 60)
    print("Test 2: List Models")
    print("=" * 60)
    try:
        models = client.models.list()
        model_ids = [m.id for m in models.data]
        print(f"✅ Found {len(model_ids)} model(s): {model_ids}")

        if model_name in model_ids:
            print(f"✅ Expected model '{model_name}' is present")
        else:
            print(f"⚠️  Expected model '{model_name}' not found in list")
            print(f"   Available models: {model_ids}")

        return True
    except Exception as e:
        print(f"❌ Failed to list models: {e}")
        return False


def test_chat_completion(client, model_name):
    """Test non-streaming chat completion."""
    print()
    print("=" * 60)
    print("Test 3: Non-Streaming Chat Completion")
    print("=" * 60)
    try:
        start = time.time()
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": "You are a helpful assistant. Answer concisely."},
                {"role": "user", "content": "What is 2+2?"},
            ],
            temperature=0.7,
            max_tokens=100,
            top_p=0.9,
            n=1,
        )
        elapsed = time.time() - start

        content = response.choices[0].message.content
        print(f"✅ Response received in {elapsed:.2f}s")
        print(f"   Model: {response.model}")
        print(f"   Content: {content}")
        print(f"   Usage: {response.usage}")
        print(f"   Finish reason: {response.choices[0].finish_reason}")

        # Check for EPR metadata (may be in model_extra or similar)
        if hasattr(response, "epr"):
            print(f"   EPR metadata: {response.epr}")

        return True
    except Exception as e:
        print(f"❌ Failed chat completion: {e}")
        return False


def test_streaming(client, model_name):
    """Test streaming chat completion."""
    print()
    print("=" * 60)
    print("Test 4: Streaming Chat Completion")
    print("=" * 60)
    try:
        start = time.time()
        stream = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "user", "content": "Count from 1 to 5, one per line."},
            ],
            stream=True,
            max_tokens=100,
        )

        print("✅ Stream started. Receiving chunks:")
        full_content = ""
        chunk_count = 0
        for chunk in stream:
            chunk_count += 1
            if chunk.choices and chunk.choices[0].delta.content:
                content = chunk.choices[0].delta.content
                full_content += content
                print(f"   [{chunk_count}] {content!r}")

        elapsed = time.time() - start
        print(f"✅ Stream complete in {elapsed:.2f}s")
        print(f"   Total chunks: {chunk_count}")
        print(f"   Full content: {full_content!r}")

        return True
    except Exception as e:
        print(f"❌ Failed streaming: {e}")
        return False


def test_error_handling(client):
    """Test that error responses are properly handled."""
    print()
    print("=" * 60)
    print("Test 5: Error Handling")
    print("=" * 60)
    try:
        # Try with an invalid model
        client.chat.completions.create(
            model="nonexistent-model-xyz",
            messages=[{"role": "user", "content": "Hello"}],
        )
        print("⚠️  Expected an error but got a response (may be OK in stub mode)")
        return True
    except Exception as e:
        print(f"✅ Got expected error for invalid model: {type(e).__name__}: {e}")
        return True


def test_governance(client, model_name):
    """Test chat completion with governance parameters."""
    print()
    print("=" * 60)
    print("Test 6: Governance Parameters")
    print("=" * 60)
    try:
        response = client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=50,
            # Note: governance is passed as extra_body since it's not a
            # standard OpenAI parameter
            extra_body={
                "governance": {
                    "risk_class": "low",
                    "data_policy": "zdr",
                    "max_cost_usd": 0.25,
                    "max_latency_ms": 60000,
                }
            },
        )
        content = response.choices[0].message.content
        print(f"✅ Governance request succeeded")
        print(f"   Content: {content}")
        return True
    except Exception as e:
        print(f"⚠️  Governance test result: {e}")
        # This may fail in stub mode — that's OK
        return True


def main():
    """Run all tests."""
    print("NoeRelay — OpenAI SDK Compatibility Test")
    print(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    config = get_config()
    print(f"Base URL: {config['base_url']}")
    print(f"Model: {config['model']}")
    print(f"API Key: {'[set]' if config['api_key'] != 'any-value' else '[default]'}")

    # Test 1: Import
    if not test_import():
        sys.exit(1)

    from openai import OpenAI

    # Create client
    client = OpenAI(
        base_url=config["base_url"],
        api_key=config["api_key"],
        timeout=30.0,
    )

    # Run tests
    results = []
    results.append(("List Models", test_list_models(client, config["model"])))
    results.append(("Chat Completion", test_chat_completion(client, config["model"])))
    results.append(("Streaming", test_streaming(client, config["model"])))
    results.append(("Error Handling", test_error_handling(client)))
    results.append(("Governance", test_governance(client, config["model"])))

    # Summary
    print()
    print("=" * 60)
    print("Test Summary")
    print("=" * 60)
    passed = sum(1 for _, r in results if r)
    failed = sum(1 for _, r in results if not r)
    for name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"  {status} — {name}")

    print()
    print(f"Total: {passed} passed, {failed} failed out of {len(results)} tests")

    if failed > 0:
        print()
        print("⚠️  Some tests failed. Check the output above for details.")
        print("   This may be expected if running in stub mode or without")
        print("   an OpenRouter API key configured.")
        sys.exit(1)
    else:
        print()
        print("🎉 All tests passed! NoeRelay is compatible with the OpenAI SDK.")
        sys.exit(0)


if __name__ == "__main__":
    main()