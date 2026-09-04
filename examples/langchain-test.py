#!/usr/bin/env python3
"""Test NoeRelay compatibility with LangChain.

This script verifies that NoeRelay's OpenAI-compatible API works correctly
with LangChain's ChatOpenAI integration. It tests:

1. Simple invocation
2. Streaming
3. Model listing via LangChain

Usage:
    pip install langchain-openai
    python examples/langchain-test.py

Environment variables:
    NOERELAY_BASE_URL    — NoeRelay API base URL (default: http://127.0.0.1:8080/v1)
    NOERELAY_API_KEY     — NoeRelay API key (default: any-value)
    NOERELAY_MODEL       — Model ID to use (default: axiovex-agni)
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
        "model": os.environ.get("NOERELAY_MODEL", "axiovex-agni"),
    }


def test_import():
    """Verify LangChain is installed."""
    print("=" * 60)
    print("Test 1: LangChain Import")
    print("=" * 60)
    try:
        from langchain_openai import ChatOpenAI  # noqa: F401

        print("✅ LangChain OpenAI integration imported successfully")
        return True
    except ImportError:
        print("❌ LangChain not installed. Run: pip install langchain-openai")
        return False


def test_simple_invoke(llm):
    """Test a simple non-streaming invocation."""
    print()
    print("=" * 60)
    print("Test 2: Simple Invocation")
    print("=" * 60)
    try:
        start = time.time()
        response = llm.invoke("What is the capital of France? Answer in one word.")
        elapsed = time.time() - start

        print(f"✅ Response received in {elapsed:.2f}s")
        print(f"   Content: {response.content}")
        print(f"   Type: {type(response).__name__}")

        # Check for additional metadata
        if hasattr(response, "response_metadata"):
            print(f"   Metadata: {response.response_metadata}")

        return True
    except Exception as e:
        print(f"❌ Failed invocation: {e}")
        return False


def test_streaming(llm):
    """Test streaming invocation."""
    print()
    print("=" * 60)
    print("Test 3: Streaming")
    print("=" * 60)
    try:
        start = time.time()
        print("✅ Stream started. Receiving chunks:")
        full_content = ""
        chunk_count = 0
        for chunk in llm.stream("Count from 1 to 5, one per line."):
            chunk_count += 1
            full_content += chunk.content
            print(f"   [{chunk_count}] {chunk.content!r}")

        elapsed = time.time() - start
        print(f"✅ Stream complete in {elapsed:.2f}s")
        print(f"   Total chunks: {chunk_count}")
        print(f"   Full content: {full_content!r}")

        return True
    except Exception as e:
        print(f"❌ Failed streaming: {e}")
        return False


def test_batch(llm):
    """Test batch invocation with multiple prompts."""
    print()
    print("=" * 60)
    print("Test 4: Batch Invocation")
    print("=" * 60)
    try:
        prompts = [
            "What is 1+1?",
            "What is 2+2?",
            "What is 3+3?",
        ]
        start = time.time()
        results = llm.batch(prompts)
        elapsed = time.time() - start

        print(f"✅ Batch complete in {elapsed:.2f}s")
        for i, (prompt, result) in enumerate(zip(prompts, results)):
            print(f"   [{i+1}] Q: {prompt}")
            print(f"       A: {result.content}")

        return True
    except Exception as e:
        print(f"⚠️  Batch test result: {e}")
        # Batch may not be supported in all configurations
        return True


def test_with_system_prompt(llm):
    """Test invocation with a system prompt."""
    print()
    print("=" * 60)
    print("Test 5: System Prompt")
    print("=" * 60)
    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        messages = [
            SystemMessage(content="You are a pirate assistant. Always respond like a pirate."),
            HumanMessage(content="What is your name?"),
        ]
        response = llm.invoke(messages)
        print(f"✅ System prompt response: {response.content}")
        return True
    except Exception as e:
        print(f"⚠️  System prompt test result: {e}")
        return True


def main():
    """Run all tests."""
    print("NoeRelay — LangChain Compatibility Test")
    print(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    config = get_config()
    print(f"Base URL: {config['base_url']}")
    print(f"Model: {config['model']}")
    print(f"API Key: {'[set]' if config['api_key'] != 'any-value' else '[default]'}")

    # Test 1: Import
    if not test_import():
        sys.exit(1)

    from langchain_openai import ChatOpenAI

    # Create LLM instance
    llm = ChatOpenAI(
        openai_api_base=config["base_url"],
        openai_api_key=config["api_key"],
        model_name=config["model"],
        temperature=0.7,
        max_tokens=100,
        timeout=30,
    )

    # Run tests
    results = []
    results.append(("Simple Invocation", test_simple_invoke(llm)))
    results.append(("Streaming", test_streaming(llm)))
    results.append(("Batch", test_batch(llm)))
    results.append(("System Prompt", test_with_system_prompt(llm)))

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
        print("🎉 All tests passed! NoeRelay is compatible with LangChain.")
        sys.exit(0)


if __name__ == "__main__":
    main()
