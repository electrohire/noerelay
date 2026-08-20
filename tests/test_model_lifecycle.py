"""Tests for the Model Lifecycle Manager.

Tests:
- HuggingFaceModelDiscovery: search_models, get_model_info, recommend_models_for_download (mock HTTP)
- OllamaModelManager: list_models, pull_model, delete_model, get_disk_usage
- ModelPerformanceAnalyzer: record_benchmark, get_model_stats, rank_models, recommend_removals
- OpenRouterModelDiscovery: list_models, get_model_pricing, recommend_cloud_models (mock HTTP)
- Gateway endpoints: /models/recommendations, /models/cloud, /models/ranking
"""

from __future__ import annotations

import io
import json
import sys
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reference"))

from gateway.config import GatewayConfig
from gateway.cost_model import CostComponents, TrueCostModel
from gateway.model_lifecycle import (
    HuggingFaceModelDiscovery,
    ModelPerformanceAnalyzer,
    OllamaModelManager,
    OpenRouterModelDiscovery,
    _hf_to_ollama_name,
    _parse_benchmarks_from_markdown,
)


# ---------------------------------------------------------------------------
# Mock HTTP helpers
# ---------------------------------------------------------------------------


def _mock_urlopen(response_body: str, status: int = 200) -> callable:
    """Create a mock urlopen that returns a given response body."""

    class MockResponse:
        def __init__(self, body: str, code: int):
            self._body = body
            self.code = code

        def read(self):
            return self._body.encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    def mock_open(*args, **kwargs):
        return MockResponse(response_body, status)

    return mock_open


# ---------------------------------------------------------------------------
# HuggingFaceDiscoveryTests
# ---------------------------------------------------------------------------


class HuggingFaceDiscoveryTests(unittest.TestCase):
    def test_hf_to_ollama_name_conversion(self):
        """Test HuggingFace model ID to Ollama name conversion."""
        self.assertEqual(_hf_to_ollama_name("Qwen/Qwen3-8B"), "qwen3:8b")
        self.assertEqual(_hf_to_ollama_name("meta-llama/Llama-3.2-3B-Instruct"), "llama-3.2:3b")
        self.assertEqual(_hf_to_ollama_name("microsoft/Phi-3-mini-4k"), "phi-3-mini-4k")

    def test_hf_to_ollama_name_no_slash(self):
        self.assertEqual(_hf_to_ollama_name("bert-base-uncased"), "bert-base-uncased")

    def test_hf_to_ollama_name_no_size(self):
        result = _hf_to_ollama_name("some/model-without-size")
        self.assertEqual(result, "model-without-size")

    def test_parse_benchmarks_from_markdown(self):
        """Test benchmark parsing from model card markdown."""
        md = """
# Model Card

## Benchmarks

| Benchmark | Score |
|-----------|-------|
| MMLU      | 85.3% |
| HumanEval | 72.1% |
| GSM8K     | 91.0% |
"""
        benchmarks = _parse_benchmarks_from_markdown(md)
        self.assertIn("mmlu", benchmarks)
        self.assertAlmostEqual(benchmarks["mmlu"], 0.853)
        self.assertIn("humaneval", benchmarks)
        self.assertAlmostEqual(benchmarks["humaneval"], 0.721)

    def test_parse_benchmarks_decimal_format(self):
        """Test benchmark parsing with decimal format."""
        md = """
| Benchmark | Score |
|-----------|-------|
| MMLU | 0.853 |
| BBH  | 0.674 |
"""
        benchmarks = _parse_benchmarks_from_markdown(md)
        self.assertIn("mmlu", benchmarks)
        self.assertAlmostEqual(benchmarks["mmlu"], 0.853)
        self.assertIn("bbh", benchmarks)
        self.assertAlmostEqual(benchmarks["bbh"], 0.674)

    def test_parse_benchmarks_empty(self):
        """Test benchmark parsing with empty string."""
        benchmarks = _parse_benchmarks_from_markdown("")
        self.assertEqual(benchmarks, {})

    def test_search_models_mock(self):
        """Test search_models with mocked HuggingFace API."""
        mock_response = json.dumps([
            {
                "id": "Qwen/Qwen3-8B",
                "downloads": 500000,
                "likes": 1200,
                "pipeline_tag": "text-generation",
                "library_name": "transformers",
                "tags": ["qwen", "llm"],
                "config": {"num_parameters": 8000000000},
            },
            {
                "id": "meta-llama/Llama-3.2-3B",
                "downloads": 1000000,
                "likes": 3000,
                "pipeline_tag": "text-generation",
                "library_name": "transformers",
                "tags": ["llama", "llm"],
                "config": {"num_parameters": 3200000000},
            },
            {
                "id": "big-model/big-70B",
                "downloads": 100,
                "likes": 5,
                "pipeline_tag": "text-generation",
                "library_name": "transformers",
                "tags": ["large"],
                "config": {"num_parameters": 70000000000},
            },
        ])

        with patch.object(urllib.request, "urlopen", _mock_urlopen(mock_response)):
            discovery = HuggingFaceModelDiscovery()
            results = discovery.search_models(
                task="text-generation", max_size_gb=20.0, limit=10
            )

        # The 70B model should be filtered out (too large)
        self.assertGreaterEqual(len(results), 2)
        model_ids = [r["model_id"] for r in results]
        self.assertIn("Qwen/Qwen3-8B", model_ids)
        self.assertIn("meta-llama/Llama-3.2-3B", model_ids)
        self.assertNotIn("big-model/big-70B", model_ids)

    def test_search_models_error_returns_empty(self):
        """Test search_models returns empty list on API error."""
        with patch.object(urllib.request, "urlopen", side_effect=urllib.error.URLError("connection refused")):
            discovery = HuggingFaceModelDiscovery()
            results = discovery.search_models()
            self.assertEqual(results, [])

    def test_get_model_info_mock(self):
        """Test get_model_info with mocked API."""
        mock_response = json.dumps({
            "id": "Qwen/Qwen3-8B",
            "downloads": 500000,
            "likes": 1200,
            "pipeline_tag": "text-generation",
            "library_name": "transformers",
            "config": {"num_parameters": 8000000000, "hidden_size": 4096},
            "siblings": [
                {"rfilename": "model.safetensors"},
                {"rfilename": "config.json"},
            ],
            "tags": ["qwen", "llm"],
        })

        with patch.object(urllib.request, "urlopen", _mock_urlopen(mock_response)):
            discovery = HuggingFaceModelDiscovery()
            info = discovery.get_model_info("Qwen/Qwen3-8B")

        self.assertEqual(info["model_id"], "Qwen/Qwen3-8B")
        self.assertEqual(info["downloads"], 500000)
        self.assertEqual(info["config"]["hidden_size"], 4096)
        self.assertEqual(len(info["siblings"]), 2)

    def test_get_model_info_error(self):
        """Test get_model_info returns error dict on failure."""
        with patch.object(urllib.request, "urlopen", side_effect=urllib.error.URLError("connection refused")):
            discovery = HuggingFaceModelDiscovery()
            info = discovery.get_model_info("nonexistent/model")
            self.assertIn("error", info)

    def test_get_model_benchmarks_mock(self):
        """Test get_model_benchmarks with mocked README."""
        mock_readme = """
# Qwen3-8B

## Evaluation Results

| Benchmark    | Score |
|-------------|-------|
| MMLU        | 85.3% |
| HumanEval   | 72.1% |
| GSM8K       | 91.0% |
| BBH         | 0.674 |
| HellaSwag   | 83.5% |
"""
        with patch.object(urllib.request, "urlopen", _mock_urlopen(mock_readme)):
            discovery = HuggingFaceModelDiscovery()
            benchmarks = discovery.get_model_benchmarks("Qwen/Qwen3-8B")

        self.assertIn("mmlu", benchmarks)
        self.assertGreater(len(benchmarks), 0)

    def test_get_model_benchmarks_empty_readme(self):
        """Test get_model_benchmarks with empty README."""
        with patch.object(urllib.request, "urlopen", _mock_urlopen("")):
            discovery = HuggingFaceModelDiscovery()
            benchmarks = discovery.get_model_benchmarks("Qwen/Qwen3-8B")
        self.assertEqual(benchmarks, {})

    def test_get_model_benchmarks_fetch_error(self):
        """Test get_model_benchmarks when fetch fails."""
        with patch.object(urllib.request, "urlopen", side_effect=urllib.error.URLError("connection refused")):
            discovery = HuggingFaceModelDiscovery()
            benchmarks = discovery.get_model_benchmarks("Qwen/Qwen3-8B")
        self.assertEqual(benchmarks, {})

    def test_recommend_models_for_download_mock(self):
        """Test recommend_models_for_download with mocked API."""
        search_response = json.dumps([
            {
                "id": "Qwen/Qwen3-8B",
                "downloads": 500000,
                "likes": 1200,
                "pipeline_tag": "text-generation",
                "library_name": "transformers",
                "tags": ["qwen", "llm"],
                "config": {"num_parameters": 8000000000},
            },
            {
                "id": "meta-llama/Llama-3.2-3B",
                "downloads": 1000000,
                "likes": 3000,
                "pipeline_tag": "text-generation",
                "library_name": "transformers",
                "tags": ["llama", "llm"],
                "config": {"num_parameters": 3200000000},
            },
        ])

        readme = """
| MMLU | 85.3% |
| HumanEval | 72.1% |
"""

        # We need to mock two different URL calls: search and readme
        original_urlopen = urllib.request.urlopen

        def mock_urlopen_side_effect(req, *args, **kwargs):
            url = req.get_full_url() if hasattr(req, "get_full_url") else str(req)
            if "README" in url:
                return _mock_urlopen(readme)()
            return _mock_urlopen(search_response)()

        with patch.object(urllib.request, "urlopen", side_effect=mock_urlopen_side_effect):
            discovery = HuggingFaceModelDiscovery()
            recommendations = discovery.recommend_models_for_download(
                task="text-generation", max_size_gb=20.0, min_downloads=1000, limit=5
            )

        self.assertGreaterEqual(len(recommendations), 1)
        for rec in recommendations:
            self.assertIn("model_id", rec)
            self.assertIn("ollama_name", rec)
            self.assertIn("download_command", rec)
            self.assertIn("recommendation_reason", rec)
            self.assertTrue(rec["download_command"].startswith("ollama pull"))

    def test_init_with_token(self):
        """Test initialization with HF token."""
        discovery = HuggingFaceModelDiscovery(hf_token="hf_test_token")
        self.assertEqual(discovery._hf_token, "hf_test_token")

    def test_init_without_token(self):
        """Test initialization without HF token."""
        discovery = HuggingFaceModelDiscovery()
        self.assertIsNone(discovery._hf_token)


# ---------------------------------------------------------------------------
# OllamaManagerTests
# ---------------------------------------------------------------------------


class OllamaManagerTests(unittest.TestCase):
    def test_list_models_mock(self):
        """Test list_models with mocked Ollama API."""
        mock_response = json.dumps({
            "models": [
                {
                    "name": "qwen3:8b",
                    "size": 5000000000,
                    "digest": "abc123",
                    "modified_at": "2024-01-01T00:00:00Z",
                    "details": {
                        "parameter_size": "8B",
                        "quantization_level": "Q4_K_M",
                        "family": "qwen3",
                    },
                },
                {
                    "name": "qwen3-coder:30b",
                    "size": 18000000000,
                    "digest": "def456",
                    "modified_at": "2024-01-02T00:00:00Z",
                    "details": {
                        "parameter_size": "30B",
                        "quantization_level": "Q4_K_M",
                        "family": "qwen3",
                    },
                },
            ]
        })

        with patch.object(urllib.request, "urlopen", _mock_urlopen(mock_response)):
            manager = OllamaModelManager()
            models = manager.list_models()

        self.assertEqual(len(models), 2)
        self.assertEqual(models[0]["name"], "qwen3:8b")
        self.assertEqual(models[0]["parameter_size"], "8B")
        self.assertEqual(models[1]["name"], "qwen3-coder:30b")

    def test_list_models_error_returns_empty(self):
        """Test list_models returns empty list on error."""
        with patch.object(urllib.request, "urlopen", side_effect=urllib.error.URLError("connection refused")):
            manager = OllamaModelManager()
            models = manager.list_models()
            self.assertEqual(models, [])

    def test_pull_model_mock(self):
        """Test pull_model with mocked response."""
        mock_response = json.dumps({"status": "pulling manifest"})

        with patch.object(urllib.request, "urlopen", _mock_urlopen(mock_response)):
            manager = OllamaModelManager()
            result = manager.pull_model("qwen3:8b")

        self.assertEqual(result["status"], "pulling manifest")
        self.assertEqual(result["model"], "qwen3:8b")

    def test_pull_model_error(self):
        """Test pull_model returns error on failure."""
        with patch.object(urllib.request, "urlopen", side_effect=urllib.error.URLError("connection refused")):
            manager = OllamaModelManager()
            result = manager.pull_model("qwen3:8b")
            self.assertEqual(result["status"], "error")

    def test_delete_model_mock(self):
        """Test delete_model with mocked response."""
        mock_response = json.dumps({"status": "deleted"})

        with patch.object(urllib.request, "urlopen", _mock_urlopen(mock_response)):
            manager = OllamaModelManager()
            result = manager.delete_model("qwen3:8b")

        self.assertEqual(result["status"], "deleted")
        self.assertEqual(result["model"], "qwen3:8b")

    def test_delete_model_error(self):
        """Test delete_model returns error on failure."""
        with patch.object(urllib.request, "urlopen", side_effect=urllib.error.URLError("connection refused")):
            manager = OllamaModelManager()
            result = manager.delete_model("qwen3:8b")
            self.assertEqual(result["status"], "error")

    def test_get_model_details_mock(self):
        """Test get_model_details with mocked response."""
        mock_response = json.dumps({
            "modelfile": "FROM qwen3:8b",
            "parameters": "temperature 0.7",
            "template": "{{ .Prompt }}",
            "details": {
                "parameter_size": "8B",
                "quantization_level": "Q4_K_M",
                "family": "qwen3",
                "format": "gguf",
            },
        })

        with patch.object(urllib.request, "urlopen", _mock_urlopen(mock_response)):
            manager = OllamaModelManager()
            details = manager.get_model_details("qwen3:8b")

        self.assertEqual(details["model"], "qwen3:8b")
        self.assertEqual(details["details"]["parameter_size"], "8B")
        self.assertEqual(details["details"]["family"], "qwen3")

    def test_get_model_details_error(self):
        """Test get_model_details returns error on failure."""
        with patch.object(urllib.request, "urlopen", side_effect=urllib.error.URLError("connection refused")):
            manager = OllamaModelManager()
            details = manager.get_model_details("qwen3:8b")
            self.assertIn("error", details)

    def test_get_disk_usage_mock(self):
        """Test get_disk_usage with mocked models."""
        mock_response = json.dumps({
            "models": [
                {
                    "name": "qwen3:8b",
                    "size": 5000000000,
                    "digest": "abc",
                    "modified_at": "",
                    "details": {"parameter_size": "8B"},
                },
                {
                    "name": "qwen3-coder:30b",
                    "size": 18000000000,
                    "digest": "def",
                    "modified_at": "",
                    "details": {"parameter_size": "30B"},
                },
            ]
        })

        with patch.object(urllib.request, "urlopen", _mock_urlopen(mock_response)):
            manager = OllamaModelManager()
            usage = manager.get_disk_usage()

        self.assertEqual(usage["model_count"], 2)
        self.assertEqual(usage["total_size_bytes"], 23000000000)
        self.assertGreater(usage["total_size_gb"], 20)  # ~21.4 GB
        self.assertEqual(len(usage["models"]), 2)

    def test_get_disk_usage_empty(self):
        """Test get_disk_usage with no models."""
        mock_response = json.dumps({"models": []})

        with patch.object(urllib.request, "urlopen", _mock_urlopen(mock_response)):
            manager = OllamaModelManager()
            usage = manager.get_disk_usage()

        self.assertEqual(usage["model_count"], 0)
        self.assertEqual(usage["total_size_bytes"], 0)
        self.assertEqual(usage["total_size_gb"], 0.0)

    def test_init_with_custom_url(self):
        """Test initialization with custom base URL."""
        manager = OllamaModelManager(base_url="http://localhost:9999")
        self.assertEqual(manager._base_url, "http://localhost:9999")


# ---------------------------------------------------------------------------
# PerformanceAnalyzerTests
# ---------------------------------------------------------------------------


class PerformanceAnalyzerTests(unittest.TestCase):
    def setUp(self):
        self.analyzer = ModelPerformanceAnalyzer()

    def test_record_benchmark_creates_history(self):
        self.analyzer.record_benchmark("qwen3:8b", {
            "accuracy": 0.85,
            "tokens_per_correct_answer": 150.0,
            "cost_per_correct_usd": 0.0,
            "mean_latency_ms": 500.0,
        })
        history = self.analyzer.history()
        self.assertIn("qwen3:8b", history)
        self.assertEqual(len(history["qwen3:8b"]), 1)

    def test_record_multiple_benchmarks(self):
        for _ in range(5):
            self.analyzer.record_benchmark("qwen3:8b", {
                "accuracy": 0.85,
                "tokens_per_correct_answer": 150.0,
                "cost_per_correct_usd": 0.0,
                "mean_latency_ms": 500.0,
            })
        history = self.analyzer.history()
        self.assertEqual(len(history["qwen3:8b"]), 5)

    def test_get_model_stats_no_data(self):
        stats = self.analyzer.get_model_stats("unknown-model")
        self.assertEqual(stats["model_id"], "unknown-model")
        self.assertEqual(stats["runs"], 0)

    def test_get_model_stats_with_data(self):
        self.analyzer.record_benchmark("qwen3:8b", {
            "accuracy": 0.80,
            "tokens_per_correct_answer": 200.0,
            "cost_per_correct_usd": 0.0,
            "mean_latency_ms": 400.0,
        })
        self.analyzer.record_benchmark("qwen3:8b", {
            "accuracy": 0.90,
            "tokens_per_correct_answer": 100.0,
            "cost_per_correct_usd": 0.0,
            "mean_latency_ms": 600.0,
        })

        stats = self.analyzer.get_model_stats("qwen3:8b")
        self.assertEqual(stats["runs"], 2)
        self.assertAlmostEqual(stats["mean_accuracy"], 0.85)
        self.assertAlmostEqual(stats["mean_tokens_per_correct"], 150.0)
        self.assertAlmostEqual(stats["mean_cost_per_correct"], 0.0)
        self.assertAlmostEqual(stats["mean_latency_ms"], 500.0)

    def test_rank_models(self):
        # Good model
        self.analyzer.record_benchmark("good-model", {
            "accuracy": 0.95,
            "tokens_per_correct_answer": 100.0,
            "cost_per_correct_usd": 0.0,
            "mean_latency_ms": 300.0,
        })
        # Bad model
        self.analyzer.record_benchmark("bad-model", {
            "accuracy": 0.40,
            "tokens_per_correct_answer": 500.0,
            "cost_per_correct_usd": 0.005,
            "mean_latency_ms": 2000.0,
        })
        # Medium model
        self.analyzer.record_benchmark("medium-model", {
            "accuracy": 0.70,
            "tokens_per_correct_answer": 200.0,
            "cost_per_correct_usd": 0.001,
            "mean_latency_ms": 800.0,
        })

        ranked = self.analyzer.rank_models()
        self.assertEqual(len(ranked), 3)
        # Good model should be first
        self.assertEqual(ranked[0]["model_id"], "good-model")
        # Bad model should be last
        self.assertEqual(ranked[2]["model_id"], "bad-model")

    def test_rank_models_empty(self):
        ranked = self.analyzer.rank_models()
        self.assertEqual(ranked, [])

    def test_rank_models_specific_ids(self):
        self.analyzer.record_benchmark("model-a", {
            "accuracy": 0.80,
            "tokens_per_correct_answer": 150.0,
            "cost_per_correct_usd": 0.0,
            "mean_latency_ms": 400.0,
        })
        self.analyzer.record_benchmark("model-b", {
            "accuracy": 0.60,
            "tokens_per_correct_answer": 300.0,
            "cost_per_correct_usd": 0.0,
            "mean_latency_ms": 800.0,
        })

        ranked = self.analyzer.rank_models(["model-a"])
        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0]["model_id"], "model-a")

    def test_recommend_removals_low_accuracy(self):
        # Record 3+ runs of low accuracy for model-b
        for _ in range(4):
            self.analyzer.record_benchmark("model-a", {
                "accuracy": 0.90,
                "tokens_per_correct_answer": 100.0,
                "cost_per_correct_usd": 0.0,
                "mean_latency_ms": 300.0,
            })
            self.analyzer.record_benchmark("model-b", {
                "accuracy": 0.30,
                "tokens_per_correct_answer": 200.0,
                "cost_per_correct_usd": 0.0,
                "mean_latency_ms": 500.0,
            })

        removals = self.analyzer.recommend_removals(
            ["model-a", "model-b"], min_runs=3
        )
        self.assertGreaterEqual(len(removals), 1)
        removal_ids = [r["model_id"] for r in removals]
        self.assertIn("model-b", removal_ids)
        # Good model should not be in removals
        self.assertNotIn("model-a", removal_ids)

    def test_recommend_removals_insufficient_data(self):
        # Only 2 runs - below min_runs=3
        for _ in range(2):
            self.analyzer.record_benchmark("model-a", {
                "accuracy": 0.30,
                "tokens_per_correct_answer": 200.0,
                "cost_per_correct_usd": 0.0,
                "mean_latency_ms": 500.0,
            })

        removals = self.analyzer.recommend_removals(["model-a"], min_runs=3)
        self.assertEqual(removals, [])

    def test_recommend_removals_few_models(self):
        """Test recommend_removals with fewer than 2 models."""
        removals = self.analyzer.recommend_removals(["model-a"])
        self.assertEqual(removals, [])

    def test_recommend_removals_bottom_ranked(self):
        # Create 8 models, so bottom 25% = 2 models
        for i in range(8):
            for _ in range(4):
                self.analyzer.record_benchmark(f"model-{i}", {
                    "accuracy": 0.50 + (i * 0.05),  # 0.50 to 0.85
                    "tokens_per_correct_answer": 200.0 - (i * 10),
                    "cost_per_correct_usd": 0.0,
                    "mean_latency_ms": 500.0,
                })

        removals = self.analyzer.recommend_removals(
            [f"model-{i}" for i in range(8)], min_runs=3
        )
        # Bottom 2 models (model-0, model-1) should be flagged
        removal_ids = [r["model_id"] for r in removals]
        self.assertIn("model-0", removal_ids)
        self.assertIn("model-1", removal_ids)

    def test_recommend_downloads(self):
        """Test recommend_downloads filters out already-installed models."""
        search_response = json.dumps([
            {
                "id": "Qwen/Qwen3-8B",
                "downloads": 500000,
                "likes": 1200,
                "pipeline_tag": "text-generation",
                "library_name": "transformers",
                "tags": ["qwen"],
                "config": {"num_parameters": 8000000000},
            },
            {
                "id": "NewOrg/NewModel-7B",
                "downloads": 200000,
                "likes": 500,
                "pipeline_tag": "text-generation",
                "library_name": "transformers",
                "tags": ["new"],
                "config": {"num_parameters": 7000000000},
            },
        ])

        readme = "| MMLU | 85.3% |\n"

        def mock_urlopen_side_effect(req, *args, **kwargs):
            url = req.get_full_url() if hasattr(req, "get_full_url") else str(req)
            if "README" in url:
                return _mock_urlopen(readme)()
            return _mock_urlopen(search_response)()

        with patch.object(urllib.request, "urlopen", side_effect=mock_urlopen_side_effect):
            hf = HuggingFaceModelDiscovery()
            analyzer = ModelPerformanceAnalyzer()
            # qwen3:8b is already installed
            recommendations = analyzer.recommend_downloads(
                available_local=["qwen3:8b"],
                hf_discovery=hf,
            )

        # Should not recommend qwen3:8b (already installed)
        for rec in recommendations:
            self.assertNotEqual(rec.get("ollama_name", "").lower(), "qwen3:8b")

    def test_recommend_downloads_all_installed(self):
        """Test recommend_downloads when all models are already installed."""
        search_response = json.dumps([
            {
                "id": "Qwen/Qwen3-8B",
                "downloads": 500000,
                "likes": 1200,
                "pipeline_tag": "text-generation",
                "library_name": "transformers",
                "tags": ["qwen"],
                "config": {"num_parameters": 8000000000},
            },
        ])

        readme = "| MMLU | 85.3% |\n"

        def mock_urlopen_side_effect(req, *args, **kwargs):
            url = req.get_full_url() if hasattr(req, "get_full_url") else str(req)
            if "README" in url:
                return _mock_urlopen(readme)()
            return _mock_urlopen(search_response)()

        with patch.object(urllib.request, "urlopen", side_effect=mock_urlopen_side_effect):
            hf = HuggingFaceModelDiscovery()
            analyzer = ModelPerformanceAnalyzer()
            recommendations = analyzer.recommend_downloads(
                available_local=["qwen3:8b"],
                hf_discovery=hf,
            )

        self.assertEqual(len(recommendations), 0)

    def test_score_computation(self):
        """Test that score is computed correctly."""
        self.analyzer.record_benchmark("model-a", {
            "accuracy": 1.0,
            "tokens_per_correct_answer": 0.0,
            "cost_per_correct_usd": 0.0,
            "mean_latency_ms": 0.0,
        })

        ranked = self.analyzer.rank_models(["model-a"])
        self.assertEqual(len(ranked), 1)
        # Perfect score: 100 * 1.0 - 0 - 0 - 0 = 100.0
        self.assertAlmostEqual(ranked[0]["score"], 100.0)

    def test_removal_reasons_include_commands(self):
        """Test that removal recommendations include removal commands."""
        for _ in range(4):
            self.analyzer.record_benchmark("bad-model", {
                "accuracy": 0.30,
                "tokens_per_correct_answer": 500.0,
                "cost_per_correct_usd": 0.005,
                "mean_latency_ms": 2000.0,
            })
            self.analyzer.record_benchmark("good-model", {
                "accuracy": 0.90,
                "tokens_per_correct_answer": 100.0,
                "cost_per_correct_usd": 0.0,
                "mean_latency_ms": 300.0,
            })

        removals = self.analyzer.recommend_removals(
            ["bad-model", "good-model"], min_runs=3
        )
        for r in removals:
            self.assertIn("removal_command", r)
            self.assertTrue(r["removal_command"].startswith("ollama rm"))


# ---------------------------------------------------------------------------
# OpenRouterDiscoveryTests
# ---------------------------------------------------------------------------


class OpenRouterDiscoveryTests(unittest.TestCase):
    def test_list_models_mock(self):
        """Test list_models with mocked OpenRouter API."""
        mock_response = json.dumps({
            "data": [
                {
                    "id": "qwen/qwen3.6-35b-a3b",
                    "name": "Qwen 3.6 35B A3B",
                    "pricing": {"prompt": "0.00000035", "completion": "0.00000070"},
                    "context_length": 131072,
                    "architecture": {"modality": "text", "input_modalities": ["text"]},
                    "top_provider": {"context_length": 131072},
                    "description": "Qwen 3.6 model",
                },
                {
                    "id": "openai/gpt-4o",
                    "name": "GPT-4o",
                    "pricing": {"prompt": "0.000005", "completion": "0.000015"},
                    "context_length": 128000,
                    "architecture": {"modality": "text+image"},
                    "top_provider": {"context_length": 128000},
                    "description": "GPT-4o model",
                },
            ]
        })

        with patch.object(urllib.request, "urlopen", _mock_urlopen(mock_response)):
            discovery = OpenRouterModelDiscovery(api_key="test-key")
            models = discovery.list_models()

        self.assertEqual(len(models), 2)
        self.assertEqual(models[0]["id"], "qwen/qwen3.6-35b-a3b")
        self.assertEqual(models[1]["id"], "openai/gpt-4o")

    def test_list_models_error(self):
        """Test list_models raises RuntimeError on failure."""
        with patch.object(urllib.request, "urlopen", side_effect=urllib.error.URLError("connection refused")):
            discovery = OpenRouterModelDiscovery(api_key="test-key")
            with self.assertRaises(RuntimeError):
                discovery.list_models()

    def test_get_model_pricing_mock(self):
        """Test get_model_pricing with mocked API."""
        mock_response = json.dumps({
            "data": [
                {
                    "id": "qwen/qwen3.6-35b-a3b",
                    "name": "Qwen 3.6 35B",
                    "pricing": {"prompt": "0.00000035", "completion": "0.00000070"},
                    "context_length": 131072,
                    "architecture": {},
                    "top_provider": {},
                },
            ]
        })

        with patch.object(urllib.request, "urlopen", _mock_urlopen(mock_response)):
            discovery = OpenRouterModelDiscovery(api_key="test-key")
            pricing = discovery.get_model_pricing("qwen/qwen3.6-35b-a3b")

        self.assertEqual(pricing["model_id"], "qwen/qwen3.6-35b-a3b")
        self.assertAlmostEqual(pricing["prompt_per_1k"], 0.00035)
        self.assertAlmostEqual(pricing["completion_per_1k"], 0.0007)

    def test_get_model_pricing_not_found(self):
        """Test get_model_pricing for unknown model."""
        mock_response = json.dumps({"data": []})

        with patch.object(urllib.request, "urlopen", _mock_urlopen(mock_response)):
            discovery = OpenRouterModelDiscovery(api_key="test-key")
            pricing = discovery.get_model_pricing("unknown/model")

        self.assertEqual(pricing["model_id"], "unknown/model")
        self.assertEqual(pricing["prompt_per_1k"], 0.0)

    def test_get_model_pricing_error(self):
        """Test get_model_pricing returns zeros on error."""
        with patch.object(urllib.request, "urlopen", side_effect=urllib.error.URLError("connection refused")):
            discovery = OpenRouterModelDiscovery(api_key="test-key")
            pricing = discovery.get_model_pricing("any/model")
            self.assertEqual(pricing["prompt_per_1k"], 0.0)

    def test_recommend_cloud_models_mock(self):
        """Test recommend_cloud_models with mocked API."""
        mock_response = json.dumps({
            "data": [
                {
                    "id": "qwen/qwen3.6-35b-a3b",
                    "name": "Qwen 3.6 35B",
                    "pricing": {"prompt": "0.00000035", "completion": "0.00000070"},
                    "context_length": 131072,
                    "architecture": {"modality": "text"},
                    "top_provider": {},
                    "description": "Great model",
                },
                {
                    "id": "openai/gpt-4o",
                    "name": "GPT-4o",
                    "pricing": {"prompt": "0.000005", "completion": "0.000015"},
                    "context_length": 128000,
                    "architecture": {"modality": "text+image"},
                    "top_provider": {},
                    "description": "Expensive model",
                },
            ]
        })

        with patch.object(urllib.request, "urlopen", _mock_urlopen(mock_response)):
            discovery = OpenRouterModelDiscovery(api_key="test-key")
            recommendations = discovery.recommend_cloud_models(
                current_portfolio=[{"model_id": "openai/gpt-4o"}],
                task="text-generation",
                max_cost_per_1k=0.01,
            )

        # Should recommend qwen model (cheap, not in portfolio)
        # GPT-4o should be filtered out (already in portfolio)
        self.assertGreaterEqual(len(recommendations), 1)
        rec_ids = [r["model_id"] for r in recommendations]
        self.assertIn("qwen/qwen3.6-35b-a3b", rec_ids)
        self.assertNotIn("openai/gpt-4o", rec_ids)

    def test_recommend_cloud_models_cost_filter(self):
        """Test that expensive models are filtered out."""
        mock_response = json.dumps({
            "data": [
                {
                    "id": "expensive/model",
                    "name": "Expensive",
                    "pricing": {"prompt": "0.00001", "completion": "0.00002"},
                    "context_length": 100000,
                    "architecture": {"modality": "text"},
                    "top_provider": {},
                    "description": "Too expensive",
                },
            ]
        })

        with patch.object(urllib.request, "urlopen", _mock_urlopen(mock_response)):
            discovery = OpenRouterModelDiscovery(api_key="test-key")
            recommendations = discovery.recommend_cloud_models(
                current_portfolio=[],
                max_cost_per_1k=0.001,  # Very low threshold
            )

        # The expensive model costs 0.00003 per 1K, which is > 0.001? No wait...
        # prompt=0.00001 per token, so per 1K = 0.01, completion=0.00002 per token = 0.02 per 1K
        # Total = 0.03 per 1K. That's way over 0.001. So it should be filtered out.
        self.assertEqual(len(recommendations), 0)

    def test_recommend_cloud_models_error(self):
        """Test recommend_cloud_models returns error on failure."""
        with patch.object(urllib.request, "urlopen", side_effect=urllib.error.URLError("connection refused")):
            discovery = OpenRouterModelDiscovery(api_key="test-key")
            recommendations = discovery.recommend_cloud_models(
                current_portfolio=[],
            )

        self.assertEqual(len(recommendations), 1)
        self.assertEqual(recommendations[0]["type"], "error")

    def test_init_with_custom_base_url(self):
        """Test initialization with custom base URL."""
        discovery = OpenRouterModelDiscovery(
            api_key="test-key", base_url="https://custom.openrouter.ai/api/v1"
        )
        self.assertEqual(discovery._base_url, "https://custom.openrouter.ai/api/v1")


# ---------------------------------------------------------------------------
# GatewayEndpointTests
# ---------------------------------------------------------------------------


class GatewayEndpointTests(unittest.TestCase):
    def setUp(self):
        from gateway.pipeline import PipelineContext
        from gateway.openrouter import StubOpenRouterClient
        from gateway.runs import RunRegistry
        from gateway.statemachine import VerificationStateMachine

        policy = json.loads(
            (ROOT / "spec" / "routing-policy.json").read_text("utf-8")
        )
        portfolio = json.loads(
            (ROOT / "examples" / "candidate-actions.json").read_text("utf-8")
        )
        spec = json.loads(
            (ROOT / "spec" / "verification-state-machine.json").read_text("utf-8")
        )
        config = GatewayConfig.from_env(
            {"NOERELAY_GATEWAY_HOST": "127.0.0.1", "NOERELAY_GATEWAY_PORT": "0"}
        )
        self.ctx = PipelineContext(
            config=config,
            policy=policy,
            portfolio=portfolio,
            openrouter_client=StubOpenRouterClient(policy),
            state_machine=VerificationStateMachine(spec),
            registry=RunRegistry(),
        )

    def test_handle_model_recommendations(self):
        """Test handle_model_recommendations returns valid response."""
        from gateway.handlers import handle_model_recommendations

        status, body = handle_model_recommendations(self.ctx)
        self.assertEqual(status, 200)
        self.assertEqual(body["object"], "model_recommendations")
        self.assertIn("local_models", body)
        self.assertIn("download_recommendations", body)
        self.assertIn("removal_recommendations", body)
        self.assertIsInstance(body["local_models"], list)
        self.assertIsInstance(body["download_recommendations"], list)
        self.assertIsInstance(body["removal_recommendations"], list)

    def test_handle_model_cloud_no_api_key(self):
        """Test handle_model_cloud without API key."""
        from gateway.handlers import handle_model_cloud

        # Ensure no API key is set
        with patch.dict("os.environ", {}, clear=True):
            status, body = handle_model_cloud(self.ctx)
            self.assertEqual(status, 200)
            self.assertIn("error", body)
            self.assertEqual(body["models"], [])

    def test_handle_model_ranking(self):
        """Test handle_model_ranking returns valid response."""
        from gateway.handlers import handle_model_ranking

        status, body = handle_model_ranking(self.ctx)
        self.assertEqual(status, 200)
        self.assertEqual(body["object"], "model_ranking")
        self.assertIn("ranked_models", body)
        self.assertIn("total_models_tracked", body)
        self.assertIsInstance(body["ranked_models"], list)


# ---------------------------------------------------------------------------
# TrueCostModelTests
# ---------------------------------------------------------------------------


class TrueCostModelTests(unittest.TestCase):
    """Tests for the TrueCostModel and CostComponents."""

    def test_cost_components_direct_cost(self):
        """Test direct cost computation from per-token cost and tokens."""
        cc = CostComponents(
            per_token_cost_usd=0.005,
            tokens_per_case=500.0,
            rework_rate=0.0,
            rework_cost_per_incident_usd=0.01,
            human_intervention_rate=0.0,
            human_time_per_intervention_minutes=15.0,
            human_hourly_rate_usd=75.0,
            escalation_rate=0.0,
            escalation_cost_per_incident_usd=0.05,
            mean_latency_ms=0.0,
            latency_cost_per_second_usd=0.0,
            infrastructure_cost_per_hour_usd=0.0,
            utilization_rate=1.0,
        )
        # direct = 0.005 * (500/1000) = 0.0025
        self.assertAlmostEqual(cc.direct_cost_per_case, 0.0025)

    def test_cost_components_rework_cost(self):
        """Test rework cost computation."""
        cc = CostComponents(
            per_token_cost_usd=0.0,
            tokens_per_case=0.0,
            rework_rate=0.30,
            rework_cost_per_incident_usd=0.01,
            human_intervention_rate=0.0,
            human_time_per_intervention_minutes=15.0,
            human_hourly_rate_usd=75.0,
            escalation_rate=0.0,
            escalation_cost_per_incident_usd=0.05,
            mean_latency_ms=0.0,
            latency_cost_per_second_usd=0.0,
            infrastructure_cost_per_hour_usd=0.0,
            utilization_rate=1.0,
        )
        # rework = 0.30 * 0.01 = 0.003
        self.assertAlmostEqual(cc.rework_cost_per_case, 0.003)

    def test_cost_components_human_intervention_cost(self):
        """Test human intervention cost computation."""
        cc = CostComponents(
            per_token_cost_usd=0.0,
            tokens_per_case=0.0,
            rework_rate=0.0,
            rework_cost_per_incident_usd=0.01,
            human_intervention_rate=0.10,
            human_time_per_intervention_minutes=15.0,
            human_hourly_rate_usd=75.0,
            escalation_rate=0.0,
            escalation_cost_per_incident_usd=0.05,
            mean_latency_ms=0.0,
            latency_cost_per_second_usd=0.0,
            infrastructure_cost_per_hour_usd=0.0,
            utilization_rate=1.0,
        )
        # human = 0.10 * (15/60) * 75.0 = 0.10 * 0.25 * 75 = 1.875
        self.assertAlmostEqual(cc.human_cost_per_case, 1.875)

    def test_cost_components_escalation_cost(self):
        """Test escalation cost computation."""
        cc = CostComponents(
            per_token_cost_usd=0.0,
            tokens_per_case=0.0,
            rework_rate=0.0,
            rework_cost_per_incident_usd=0.01,
            human_intervention_rate=0.0,
            human_time_per_intervention_minutes=15.0,
            human_hourly_rate_usd=75.0,
            escalation_rate=0.20,
            escalation_cost_per_incident_usd=0.05,
            mean_latency_ms=0.0,
            latency_cost_per_second_usd=0.0,
            infrastructure_cost_per_hour_usd=0.0,
            utilization_rate=1.0,
        )
        # escalation = 0.20 * 0.05 = 0.01
        self.assertAlmostEqual(cc.escalation_cost_per_case, 0.01)

    def test_cost_components_latency_cost(self):
        """Test latency opportunity cost computation."""
        cc = CostComponents(
            per_token_cost_usd=0.0,
            tokens_per_case=0.0,
            rework_rate=0.0,
            rework_cost_per_incident_usd=0.01,
            human_intervention_rate=0.0,
            human_time_per_intervention_minutes=15.0,
            human_hourly_rate_usd=75.0,
            escalation_rate=0.0,
            escalation_cost_per_incident_usd=0.05,
            mean_latency_ms=2000.0,
            latency_cost_per_second_usd=0.001,
            infrastructure_cost_per_hour_usd=0.0,
            utilization_rate=1.0,
        )
        # latency = (2000/1000) * 0.001 = 0.002
        self.assertAlmostEqual(cc.latency_cost_per_case, 0.002)

    def test_cost_components_infrastructure_cost(self):
        """Test infrastructure cost computation for local models."""
        cc = CostComponents(
            per_token_cost_usd=0.0,
            tokens_per_case=0.0,
            rework_rate=0.0,
            rework_cost_per_incident_usd=0.01,
            human_intervention_rate=0.0,
            human_time_per_intervention_minutes=15.0,
            human_hourly_rate_usd=75.0,
            escalation_rate=0.0,
            escalation_cost_per_incident_usd=0.05,
            mean_latency_ms=0.0,
            latency_cost_per_second_usd=0.0,
            infrastructure_cost_per_hour_usd=0.50,
            utilization_rate=0.3,
        )
        # cases_per_hour = 1000 * 0.3 = 300
        # infra = 0.50 / 300 = 0.001666...
        self.assertAlmostEqual(cc.infrastructure_cost_per_case, 0.50 / 300, places=8)

    def test_total_cost_per_case_sums_all_components(self):
        """Test that total_cost_per_case sums all components correctly."""
        cc = CostComponents(
            per_token_cost_usd=0.005,
            tokens_per_case=500.0,
            rework_rate=0.30,
            rework_cost_per_incident_usd=0.01,
            human_intervention_rate=0.10,
            human_time_per_intervention_minutes=15.0,
            human_hourly_rate_usd=75.0,
            escalation_rate=0.20,
            escalation_cost_per_incident_usd=0.05,
            mean_latency_ms=2000.0,
            latency_cost_per_second_usd=0.001,
            infrastructure_cost_per_hour_usd=0.50,
            utilization_rate=0.3,
        )
        expected = (
            cc.direct_cost_per_case
            + cc.rework_cost_per_case
            + cc.human_cost_per_case
            + cc.escalation_cost_per_case
            + cc.latency_cost_per_case
            + cc.infrastructure_cost_per_case
        )
        self.assertAlmostEqual(cc.total_cost_per_case, expected)

    def test_to_dict_returns_all_keys(self):
        """Test that to_dict returns all cost component keys."""
        cc = CostComponents(
            per_token_cost_usd=0.0,
            tokens_per_case=0.0,
            rework_rate=0.0,
            rework_cost_per_incident_usd=0.01,
            human_intervention_rate=0.0,
            human_time_per_intervention_minutes=15.0,
            human_hourly_rate_usd=75.0,
            escalation_rate=0.0,
            escalation_cost_per_incident_usd=0.05,
            mean_latency_ms=0.0,
            latency_cost_per_second_usd=0.0,
            infrastructure_cost_per_hour_usd=0.0,
            utilization_rate=1.0,
        )
        d = cc.to_dict()
        expected_keys = {
            "direct", "rework", "human", "escalation",
            "latency", "infrastructure", "total_per_case",
            "compression_ratio", "tokens_saved", "cost_savings",
            "original_tokens_per_case", "estimated_cost_no_compression",
        }
        self.assertEqual(set(d.keys()), expected_keys)

    def test_cheaper_per_token_can_be_more_expensive_overall(self):
        """A model with 0% rework but higher per-token can be cheaper than
        a model with 30% rework but lower per-token cost."""
        tcm = TrueCostModel()

        # Model A: higher per-token, but no rework
        model_a = {
            "model_id": "expensive-but-reliable",
            "accuracy": 0.95,
            "mean_tokens_per_case": 500.0,
            "mean_cost_per_correct": 0.005,  # $0.005 per correct, so per-token = 0.005 / (500/1000) = 0.01
            "mean_latency_ms": 300.0,
            "rework_rate": 0.0,
            "human_intervention_rate": 0.0,
            "escalation_rate": 0.0,
        }

        # Model B: cheaper per-token, but 30% rework
        model_b = {
            "model_id": "cheap-but-unreliable",
            "accuracy": 0.70,  # 30% failure rate = 30% rework
            "mean_tokens_per_case": 500.0,
            "mean_cost_per_correct": 0.001,  # $0.001 per correct, so per-token = 0.001 / (500/1000) = 0.002
            "mean_latency_ms": 300.0,
            "rework_rate": 0.30,
            "human_intervention_rate": 0.0,
            "escalation_rate": 0.0,
        }

        cost_a = tcm.compute_true_cost_per_correct(model_a)
        cost_b = tcm.compute_true_cost_per_correct(model_b)

        # Model A should be cheaper overall despite higher per-token cost
        self.assertLess(cost_a, cost_b,
            f"Expensive-but-reliable (${cost_a:.8f}) should be cheaper than "
            f"cheap-but-unreliable (${cost_b:.8f})")

    def test_human_intervention_dominates_cost(self):
        """Human intervention cost should dominate when HIR is high."""
        tcm = TrueCostModel(human_hourly_rate_usd=100.0)

        model = {
            "model_id": "needs-human-review",
            "accuracy": 0.80,
            "mean_tokens_per_case": 500.0,
            "mean_cost_per_correct": 0.001,
            "mean_latency_ms": 300.0,
            "rework_rate": 0.0,
            "human_intervention_rate": 0.20,  # 20% of cases need human review
            "escalation_rate": 0.0,
        }

        components = tcm.compute_cost(model)
        # Human cost should be the dominant component
        self.assertGreater(components.human_cost_per_case, components.direct_cost_per_case)
        # human = 0.20 * (15/60) * 100 = 0.20 * 0.25 * 100 = 5.0
        self.assertAlmostEqual(components.human_cost_per_case, 5.0)

    def test_local_models_have_infrastructure_cost(self):
        """Local models should have infrastructure cost, cloud models should not."""
        tcm = TrueCostModel()

        model = {
            "model_id": "test-model",
            "accuracy": 0.90,
            "mean_tokens_per_case": 500.0,
            "mean_cost_per_correct": 0.0,
            "mean_latency_ms": 300.0,
            "rework_rate": 0.0,
            "human_intervention_rate": 0.0,
            "escalation_rate": 0.0,
        }

        local_cost = tcm.compute_cost(model, is_local=True)
        cloud_cost = tcm.compute_cost(model, is_local=False)

        # Local should have infrastructure cost
        self.assertGreater(local_cost.infrastructure_cost_per_case, 0.0)
        # Cloud should have no infrastructure cost
        self.assertEqual(cloud_cost.infrastructure_cost_per_case, 0.0)

    def test_latency_cost_is_non_trivial_for_slow_models(self):
        """Latency cost should be non-trivial for slow models."""
        tcm = TrueCostModel()

        fast_model = {
            "model_id": "fast-model",
            "accuracy": 0.90,
            "mean_tokens_per_case": 500.0,
            "mean_cost_per_correct": 0.001,
            "mean_latency_ms": 100.0,
            "rework_rate": 0.0,
            "human_intervention_rate": 0.0,
            "escalation_rate": 0.0,
        }

        slow_model = {
            "model_id": "slow-model",
            "accuracy": 0.90,
            "mean_tokens_per_case": 500.0,
            "mean_cost_per_correct": 0.001,
            "mean_latency_ms": 5000.0,
            "rework_rate": 0.0,
            "human_intervention_rate": 0.0,
            "escalation_rate": 0.0,
        }

        fast_cost = tcm.compute_cost(fast_model)
        slow_cost = tcm.compute_cost(slow_model)

        # Slow model should have higher latency cost
        self.assertGreater(slow_cost.latency_cost_per_case, fast_cost.latency_cost_per_case)
        # The difference should be meaningful
        self.assertGreater(
            slow_cost.latency_cost_per_case - fast_cost.latency_cost_per_case,
            0.001,
        )

    def test_rank_by_true_cost_ordering(self):
        """Test that rank_by_true_cost orders models correctly."""
        tcm = TrueCostModel()

        models = [
            {
                "model_id": "cheap-reliable",
                "accuracy": 0.95,
                "mean_tokens_per_case": 500.0,
                "mean_cost_per_correct": 0.001,
                "mean_latency_ms": 100.0,
                "rework_rate": 0.0,
                "human_intervention_rate": 0.0,
                "escalation_rate": 0.0,
            },
            {
                "model_id": "expensive-unreliable",
                "accuracy": 0.60,
                "mean_tokens_per_case": 500.0,
                "mean_cost_per_correct": 0.01,
                "mean_latency_ms": 2000.0,
                "rework_rate": 0.40,
                "human_intervention_rate": 0.20,
                "escalation_rate": 0.30,
            },
            {
                "model_id": "mid-range",
                "accuracy": 0.85,
                "mean_tokens_per_case": 500.0,
                "mean_cost_per_correct": 0.003,
                "mean_latency_ms": 500.0,
                "rework_rate": 0.05,
                "human_intervention_rate": 0.02,
                "escalation_rate": 0.05,
            },
        ]

        ranked = tcm.rank_by_true_cost(models)

        # Should have 3 models
        self.assertEqual(len(ranked), 3)
        # cheap-reliable should be first (lowest true cost)
        self.assertEqual(ranked[0]["model_id"], "cheap-reliable")
        # expensive-unreliable should be last (highest true cost)
        self.assertEqual(ranked[2]["model_id"], "expensive-unreliable")
        # All should have cost_breakdown
        for r in ranked:
            self.assertIn("cost_breakdown", r)
            self.assertIn("true_cost_per_correct", r)

    def test_explain_cost_difference_output(self):
        """Test explain_cost_difference returns expected structure."""
        tcm = TrueCostModel()

        model_a = {
            "model_id": "model-a",
            "accuracy": 0.95,
            "mean_tokens_per_case": 500.0,
            "mean_cost_per_correct": 0.005,
            "mean_latency_ms": 300.0,
            "rework_rate": 0.0,
            "human_intervention_rate": 0.0,
            "escalation_rate": 0.0,
        }

        model_b = {
            "model_id": "model-b",
            "accuracy": 0.70,
            "mean_tokens_per_case": 500.0,
            "mean_cost_per_correct": 0.001,
            "mean_latency_ms": 300.0,
            "rework_rate": 0.30,
            "human_intervention_rate": 0.0,
            "escalation_rate": 0.0,
        }

        explanation = tcm.explain_cost_difference(model_a, model_b)

        self.assertEqual(explanation["model_a"], "model-a")
        self.assertEqual(explanation["model_b"], "model-b")
        self.assertIn("per_token_cheaper", explanation)
        self.assertIn("true_cost_cheaper", explanation)
        self.assertIn("cost_difference", explanation)
        self.assertIn("explanation", explanation)
        # Check cost_difference keys
        diff_keys = {"direct", "rework", "human", "escalation", "latency", "infrastructure"}
        self.assertEqual(set(explanation["cost_difference"].keys()), diff_keys)
        # Explanation should be a non-empty string
        self.assertIsInstance(explanation["explanation"], str)
        self.assertGreater(len(explanation["explanation"]), 0)

    def test_lower_accuracy_increases_true_cost(self):
        """Lower accuracy should result in higher true cost per correct."""
        tcm = TrueCostModel()

        high_accuracy = {
            "model_id": "high-acc",
            "accuracy": 0.95,
            "mean_tokens_per_case": 500.0,
            "mean_cost_per_correct": 0.001,
            "mean_latency_ms": 300.0,
            "rework_rate": 0.0,
            "human_intervention_rate": 0.0,
            "escalation_rate": 0.0,
        }

        low_accuracy = {
            "model_id": "low-acc",
            "accuracy": 0.50,
            "mean_tokens_per_case": 500.0,
            "mean_cost_per_correct": 0.001,
            "mean_latency_ms": 300.0,
            "rework_rate": 0.0,
            "human_intervention_rate": 0.0,
            "escalation_rate": 0.0,
        }

        high_cost = tcm.compute_true_cost_per_correct(high_accuracy)
        low_cost = tcm.compute_true_cost_per_correct(low_accuracy)

        # Lower accuracy should mean higher cost per correct (since same total cost
        # is spread over fewer correct answers)
        self.assertGreater(low_cost, high_cost)

    def test_zero_accuracy_returns_infinity(self):
        """Zero accuracy should return infinity for true cost per correct."""
        tcm = TrueCostModel()

        model = {
            "model_id": "zero-acc",
            "accuracy": 0.0,
            "mean_tokens_per_case": 500.0,
            "mean_cost_per_correct": 0.001,
            "mean_latency_ms": 300.0,
            "rework_rate": 0.0,
            "human_intervention_rate": 0.0,
            "escalation_rate": 0.0,
        }

        cost = tcm.compute_true_cost_per_correct(model)
        self.assertEqual(cost, float("inf"))

    def test_default_parameters(self):
        """Test that default parameters are set correctly."""
        tcm = TrueCostModel()
        self.assertEqual(tcm._params["human_hourly_rate_usd"], 75.0)
        self.assertEqual(tcm._params["human_time_per_intervention_minutes"], 15.0)
        self.assertEqual(tcm._params["rework_cost_per_incident_usd"], 0.01)
        self.assertEqual(tcm._params["escalation_cost_per_incident_usd"], 0.05)
        self.assertEqual(tcm._params["latency_cost_per_second_usd"], 0.001)
        self.assertEqual(tcm._params["infrastructure_cost_per_hour_usd"], 0.50)
        self.assertEqual(tcm._params["utilization_rate"], 0.3)

    def test_parameter_overrides(self):
        """Test that parameter overrides work correctly."""
        tcm = TrueCostModel(
            human_hourly_rate_usd=100.0,
            infrastructure_cost_per_hour_usd=1.0,
        )
        self.assertEqual(tcm._params["human_hourly_rate_usd"], 100.0)
        self.assertEqual(tcm._params["infrastructure_cost_per_hour_usd"], 1.0)
        # Unchanged defaults
        self.assertEqual(tcm._params["human_time_per_intervention_minutes"], 15.0)

    def test_analyzer_with_cost_model_integration(self):
        """Test that ModelPerformanceAnalyzer integrates with TrueCostModel."""
        cost_model = TrueCostModel()
        analyzer = ModelPerformanceAnalyzer(cost_model=cost_model)

        analyzer.record_benchmark("test-model", {
            "accuracy": 0.90,
            "tokens_per_correct_answer": 200.0,
            "cost_per_correct_usd": 0.001,
            "mean_latency_ms": 500.0,
            "rework_rate": 0.05,
            "human_intervention_rate": 0.02,
            "escalation_rate": 0.01,
        })

        ranked = analyzer.rank_models(["test-model"])
        self.assertEqual(len(ranked), 1)
        self.assertIn("true_cost_per_correct", ranked[0])
        self.assertIn("cost_breakdown", ranked[0])
        self.assertIn("score", ranked[0])  # Legacy score still present

    def test_explain_ranking(self):
        """Test that explain_ranking returns cost breakdowns."""
        cost_model = TrueCostModel()
        analyzer = ModelPerformanceAnalyzer(cost_model=cost_model)

        analyzer.record_benchmark("model-a", {
            "accuracy": 0.90,
            "tokens_per_correct_answer": 200.0,
            "cost_per_correct_usd": 0.001,
            "mean_latency_ms": 500.0,
            "rework_rate": 0.05,
            "human_intervention_rate": 0.02,
            "escalation_rate": 0.01,
        })

        result = analyzer.explain_ranking(["model-a"])
        self.assertIn("ranked_models", result)
        self.assertIn("total_models_tracked", result)
        self.assertIn("cost_model_defaults", result)
        self.assertEqual(len(result["ranked_models"]), 1)
        self.assertEqual(result["ranked_models"][0]["model_id"], "model-a")
        self.assertIn("cost_breakdown", result["ranked_models"][0])

    def test_explain_ranking_empty(self):
        """Test explain_ranking with no models."""
        analyzer = ModelPerformanceAnalyzer()
        result = analyzer.explain_ranking([])
        self.assertEqual(result["ranked_models"], [])
        self.assertIn("explanation", result)


if __name__ == "__main__":
    unittest.main()