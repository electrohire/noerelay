"""Model Lifecycle Manager for the NoeRelay gateway.

Provides capabilities to:
(1) discover and recommend models to download from HuggingFace based on benchmarks,
(2) download models via Ollama,
(3) analyze and recommend removing underperforming local models,
(4) recommend cloud model changes on OpenRouter over time, and
(5) rank models by true total cost of ownership (TCO) including rework,
    human intervention, escalation, latency, and infrastructure costs.

Dependency-free Python (stdlib only).  Uses ``urllib.request`` for HTTP,
``json`` for parsing, and ``unittest`` for tests.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

from .cost_model import TrueCostModel


# ---------------------------------------------------------------------------
# HuggingFace Model Discovery
# ---------------------------------------------------------------------------


class HuggingFaceModelDiscovery:
    """Discover models on HuggingFace Hub suitable for local deployment.

    Queries the HuggingFace API to find models matching criteria like:
    - Task type (text-generation, coding, vision)
    - Size constraints (fits in available VRAM)
    - License (open-source)
    - Benchmark performance (from model cards or HF Leaderboards)
    """

    def __init__(self, hf_token: str | None = None) -> None:
        self._hf_token = hf_token
        self._base_url = "https://huggingface.co/api"

    def _api_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "Accept": "application/json",
            "User-Agent": "NoeRelay/0.1.0",
        }
        if self._hf_token:
            headers["Authorization"] = f"Bearer {self._hf_token}"
        return headers

    def _request(self, path: str) -> dict[str, Any] | list[Any]:
        """Send a GET request to the HuggingFace API and return parsed JSON."""
        url = f"{self._base_url}{path}"
        req = urllib.request.Request(url, headers=self._api_headers(), method="GET")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8")
            except Exception:
                detail = "(unable to read error body)"
            raise RuntimeError(
                f"HuggingFace API HTTP {exc.code} from {url}: {detail[:500]}"
            ) from None
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"HuggingFace API transport error for {url}: {exc.reason}"
            ) from None
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RuntimeError(
                f"HuggingFace API unparseable response from {url}: {exc}"
            ) from None

    def search_models(
        self,
        task: str = "text-generation",
        max_size_gb: float = 20.0,
        sort: str = "downloads",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Search HuggingFace for models matching criteria.

        Returns list of model info dicts with:
        - model_id (e.g., "Qwen/Qwen3-8B")
        - downloads
        - likes
        - pipeline_tag
        - library_name (e.g., "transformers", "gguf")
        - tags
        - config (if available)
        """
        params = f"filter={task}&sort={sort}&limit={max(limit, 1)}&direction=-1&full=true"
        url_path = f"/models?{params}"
        try:
            raw = self._request(url_path)
        except RuntimeError:
            return []

        if not isinstance(raw, list):
            return []

        results: list[dict[str, Any]] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            info: dict[str, Any] = {
                "model_id": entry.get("id", entry.get("modelId", "")),
                "downloads": entry.get("downloads", 0),
                "likes": entry.get("likes", 0),
                "pipeline_tag": entry.get("pipeline_tag", ""),
                "library_name": entry.get("library_name", ""),
                "tags": entry.get("tags", []),
                "config": entry.get("config") or {},
            }

            # Filter by size if config is available
            config = info["config"]
            if isinstance(config, dict):
                num_params = config.get("num_parameters")
                if num_params is not None:
                    try:
                        n_params = int(num_params)
                        # Rough estimate: each parameter ~2 bytes (fp16)
                        estimated_gb = n_params * 2 / (1024**3)
                        if estimated_gb > max_size_gb:
                            continue
                    except (ValueError, TypeError):
                        pass

            # Only include models with model_id
            if info["model_id"]:
                results.append(info)

        return results[:limit]

    def get_model_info(self, model_id: str) -> dict[str, Any]:
        """Get detailed info about a specific model.

        Returns:
        - model_id
        - downloads, likes
        - config (hidden_size, num_parameters, etc.)
        - siblings (available files/quantizations)
        - tags
        - pipeline_tag
        - library_name
        """
        try:
            raw = self._request(f"/models/{model_id}")
        except RuntimeError:
            return {"model_id": model_id, "error": "Failed to fetch model info"}

        if not isinstance(raw, dict):
            return {"model_id": model_id, "error": "Unexpected response format"}

        return {
            "model_id": raw.get("id", raw.get("modelId", model_id)),
            "downloads": raw.get("downloads", 0),
            "likes": raw.get("likes", 0),
            "config": raw.get("config") or {},
            "siblings": raw.get("siblings", []),
            "tags": raw.get("tags", []),
            "pipeline_tag": raw.get("pipeline_tag", ""),
            "library_name": raw.get("library_name", ""),
        }

    def get_model_benchmarks(self, model_id: str) -> dict[str, float]:
        """Get benchmark performance for a model from its model card.

        Parses the README.md model card for benchmark tables.
        Returns dict of benchmark_name -> score.
        """
        text = self._fetch_readme(model_id)
        if not text:
            return {}
        return _parse_benchmarks_from_markdown(text)

    def _fetch_readme(self, model_id: str) -> str:
        """Fetch the raw README.md for a model from HuggingFace.

        Tries the raw URL first, then falls back to the API endpoint.
        """
        raw_url = f"https://huggingface.co/{model_id}/raw/main/README.md"
        try:
            req = urllib.request.Request(
                raw_url, headers=self._api_headers(), method="GET"
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read().decode("utf-8")
        except Exception:
            pass

        # Fallback: try the API endpoint for README content
        try:
            raw = self._request(f"/models/{model_id}")
            if isinstance(raw, dict):
                card_data = raw.get("cardData", {})
                if isinstance(card_data, dict):
                    return json.dumps(card_data)
                safetensors = raw.get("safetensors", {})
                if isinstance(safetensors, dict) and "parameters" in safetensors:
                    return json.dumps(raw)
        except RuntimeError:
            pass

        return ""

    def recommend_models_for_download(
        self,
        task: str = "text-generation",
        max_size_gb: float = 20.0,
        min_downloads: int = 1000,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Recommend models to download based on benchmarks and popularity.

        Returns ranked list of recommendations with:
        - model_id
        - ollama_name (converted from HF format to Ollama format)
        - estimated_size_gb
        - benchmark_scores
        - recommendation_reason
        - download_command (e.g., "ollama pull qwen3:8b")
        """
        models = self.search_models(
            task=task, max_size_gb=max_size_gb, sort="downloads", limit=limit * 2
        )

        recommendations: list[dict[str, Any]] = []

        for model in models:
            model_id = str(model.get("model_id", ""))
            downloads = model.get("downloads", 0)
            if downloads < min_downloads:
                continue

            # Get benchmark scores
            benchmarks = self.get_model_benchmarks(model_id)

            # Estimate size
            config = model.get("config", {})
            estimated_size_gb = 0.0
            if isinstance(config, dict):
                num_params = config.get("num_parameters")
                if num_params is not None:
                    try:
                        estimated_size_gb = int(num_params) * 2 / (1024**3)
                    except (ValueError, TypeError):
                        pass

            # Convert to Ollama name
            ollama_name = _hf_to_ollama_name(model_id)

            # Compute recommendation score
            avg_benchmark = 0.0
            if benchmarks:
                avg_benchmark = sum(benchmarks.values()) / len(benchmarks)

            # Score = benchmark * downloads / size
            score = (
                avg_benchmark * downloads / max(estimated_size_gb, 0.1)
                if avg_benchmark > 0
                else downloads / max(estimated_size_gb, 0.1)
            )

            reason_parts: list[str] = []
            if downloads >= 100000:
                reason_parts.append("highly popular")
            elif downloads >= 10000:
                reason_parts.append("popular")
            if avg_benchmark > 0.8:
                reason_parts.append("excellent benchmarks")
            elif avg_benchmark > 0.6:
                reason_parts.append("good benchmarks")
            if estimated_size_gb <= 8:
                reason_parts.append("fits in small VRAM")
            elif estimated_size_gb <= 20:
                reason_parts.append("moderate VRAM required")

            reason = "; ".join(reason_parts) if reason_parts else "meets criteria"

            recommendations.append(
                {
                    "model_id": model_id,
                    "ollama_name": ollama_name,
                    "estimated_size_gb": round(estimated_size_gb, 2),
                    "benchmark_scores": benchmarks,
                    "downloads": downloads,
                    "score": round(score, 2),
                    "recommendation_reason": reason,
                    "download_command": f"ollama pull {ollama_name}",
                    "pipeline_tag": model.get("pipeline_tag", ""),
                    "library_name": model.get("library_name", ""),
                }
            )

        # Sort by score descending
        recommendations.sort(key=lambda x: x["score"], reverse=True)
        return recommendations[:limit]


def _hf_to_ollama_name(hf_model_id: str) -> str:
    """Convert a HuggingFace model ID to an Ollama-compatible name.

    Examples:
        Qwen/Qwen3-8B -> qwen3:8b
        meta-llama/Llama-3.2-3B-Instruct -> llama3.2:3b
        microsoft/Phi-3-mini-4k-instruct -> phi3:mini
    """
    # Remove org prefix
    name = hf_model_id.split("/")[-1] if "/" in hf_model_id else hf_model_id
    name = name.lower()

    # Try to extract size and format nicely
    # Pattern: model-name-<size>-<variant>
    # Common patterns: Qwen3-8B, Llama-3.2-3B, Phi-3-mini
    size_match = re.search(r"(\d+\.?\d*)\s*[bB]", name)
    if size_match:
        size = size_match.group(1)
        base = name[: size_match.start()].rstrip("-")
        # Clean up base name
        base = base.replace("-instruct", "").replace("-chat", "")
        return f"{base}:{size}b"

    # Fallback: just lowercase and replace slashes
    return name.replace("/", "-")


def _parse_benchmarks_from_markdown(text: str) -> dict[str, float]:
    """Parse benchmark scores from a model card README.md.

    Looks for common benchmark patterns in markdown tables:
    - MMLU: 0.85
    - HumanEval: 0.72
    - GSM8K: 0.91
    etc.
    """
    benchmarks: dict[str, float] = {}

    # Common benchmark names
    known_benchmarks = [
        "mmlu",
        "mmlu-pro",
        "humaneval",
        "humaneval+",
        "gsm8k",
        "math",
        "bbh",
        "hellaswag",
        "arc-challenge",
        "arc-easy",
        "winogrande",
        "truthfulqa",
        "boolq",
        "piqa",
        "ifeval",
        "mbpp",
        "livecodebench",
        "bigcodebench",
        "aider",
        "swe-bench",
        "spider",
        "bird-sql",
        "codeforces",
    ]

    lines = text.split("\n")
    for line in lines:
        line_lower = line.lower()
        for bm in known_benchmarks:
            if bm in line_lower:
                # Try to find a percentage or float value
                # Look for patterns like: | 85.3% | or | 0.853 |
                pct_match = re.search(r"(\d+\.?\d*)\s*%", line)
                if pct_match:
                    benchmarks[bm] = float(pct_match.group(1)) / 100.0
                    continue

                # Look for decimal values like 0.853
                val_match = re.search(r"(\d+\.\d+)", line)
                if val_match:
                    val = float(val_match.group(1))
                    if 0 < val <= 1.0:
                        benchmarks[bm] = val

    return benchmarks


# ---------------------------------------------------------------------------
# Ollama Model Manager
# ---------------------------------------------------------------------------


class OllamaModelManager:
    """Manage local model downloads via Ollama.

    Uses the Ollama API (http://127.0.0.1:11434) to:
    - List installed models
    - Pull (download) new models
    - Delete models
    - Get model details (size, parameters, quantization)
    """

    def __init__(self, base_url: str = "http://127.0.0.1:11434") -> None:
        self._base_url = base_url.rstrip("/")

    def _request(self, path: str, method: str = "GET", body: dict[str, Any] | None = None) -> dict[str, Any] | list[Any]:
        """Send a request to the Ollama API."""
        url = f"{self._base_url}{path}"
        data: bytes | None = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8")
            except Exception:
                detail = "(unable to read error body)"
            raise RuntimeError(
                f"Ollama API HTTP {exc.code} from {url}: {detail[:500]}"
            ) from None
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"Ollama API transport error for {url}: {exc.reason}"
            ) from None
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RuntimeError(
                f"Ollama API unparseable response from {url}: {exc}"
            ) from None

    def list_models(self) -> list[dict[str, Any]]:
        """List all installed models with details.

        Returns: name, size, digest, details (parameter_size, quantization_level, family)
        """
        try:
            raw = self._request("/api/tags")
        except RuntimeError:
            return []

        if not isinstance(raw, dict):
            return []

        models = raw.get("models", [])
        result: list[dict[str, Any]] = []
        for m in models:
            if not isinstance(m, dict):
                continue
            details = m.get("details", {}) or {}
            result.append(
                {
                    "name": m.get("name", ""),
                    "size": m.get("size", 0),
                    "digest": m.get("digest", ""),
                    "modified_at": m.get("modified_at", ""),
                    "parameter_size": details.get("parameter_size", ""),
                    "quantization_level": details.get("quantization_level", ""),
                    "family": details.get("family", ""),
                }
            )
        return result

    def pull_model(self, model_name: str) -> dict[str, Any]:
        """Pull (download) a model. Returns status dict.

        Note: This is a long-running operation. The Ollama API streams progress.
        For the skeleton, we start the pull and return immediately with status.
        """
        try:
            raw = self._request("/api/pull", method="POST", body={"name": model_name})
            if isinstance(raw, dict):
                return {"status": raw.get("status", "started"), "model": model_name}
            return {"status": "started", "model": model_name}
        except RuntimeError as exc:
            return {"status": "error", "model": model_name, "error": str(exc)}

    def delete_model(self, model_name: str) -> dict[str, Any]:
        """Delete a model from local storage."""
        try:
            raw = self._request("/api/delete", method="DELETE", body={"name": model_name})
            if isinstance(raw, dict):
                return {"status": "deleted", "model": model_name}
            return {"status": "deleted", "model": model_name}
        except RuntimeError as exc:
            return {"status": "error", "model": model_name, "error": str(exc)}

    def get_model_details(self, model_name: str) -> dict[str, Any]:
        """Get details for a specific model."""
        try:
            raw = self._request("/api/show", method="POST", body={"name": model_name})
        except RuntimeError:
            return {"model": model_name, "error": "Failed to fetch model details"}

        if not isinstance(raw, dict):
            return {"model": model_name, "error": "Unexpected response format"}

        details = raw.get("details", {}) or {}
        return {
            "model": model_name,
            "modelfile": raw.get("modelfile", ""),
            "parameters": raw.get("parameters", ""),
            "template": raw.get("template", ""),
            "details": {
                "parameter_size": details.get("parameter_size", ""),
                "quantization_level": details.get("quantization_level", ""),
                "family": details.get("family", ""),
                "format": details.get("format", ""),
            },
            "size": raw.get("size", raw.get("model_info", {}).get("size", 0) if isinstance(raw.get("model_info"), dict) else 0),
        }

    def get_disk_usage(self) -> dict[str, Any]:
        """Get total disk usage of all models."""
        models = self.list_models()
        total_size = sum(m.get("size", 0) for m in models)
        return {
            "total_size_bytes": total_size,
            "total_size_gb": round(total_size / (1024**3), 2),
            "model_count": len(models),
            "models": [
                {
                    "name": m["name"],
                    "size_gb": round(m.get("size", 0) / (1024**3), 2),
                }
                for m in models
            ],
        }


# ---------------------------------------------------------------------------
# Model Performance Analyzer
# ---------------------------------------------------------------------------


class ModelPerformanceAnalyzer:
    """Analyze model performance from benchmark results and recommend changes.

    Tracks benchmark results per model over time and recommends:
    - Which models to keep (high accuracy, low true cost, good latency)
    - Which models to remove (high true cost, superseded by better models)
    - Which new models to download (better benchmarks than current)

    Uses ``TrueCostModel`` to factor in total cost of ownership including
    rework, human intervention, escalation, latency, and infrastructure costs
    — not just per-token cost.
    """

    def __init__(self, cost_model: TrueCostModel | None = None) -> None:
        self._history: dict[str, list[dict[str, Any]]] = {}
        self._cost_model = cost_model if cost_model is not None else TrueCostModel()

    def record_benchmark(self, model_id: str, results: dict[str, Any]) -> None:
        """Record benchmark results for a model."""
        if model_id not in self._history:
            self._history[model_id] = []
        self._history[model_id].append(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "results": results,
            }
        )

    def get_model_stats(self, model_id: str) -> dict[str, Any]:
        """Get aggregated stats for a model across all benchmark runs."""
        history = self._history.get(model_id, [])
        if not history:
            return {"model_id": model_id, "runs": 0}

        all_results = [h["results"] for h in history]
        accuracies = [
            r.get("accuracy", 0.0) for r in all_results if isinstance(r, dict)
        ]
        tokens_per_correct = [
            r.get("tokens_per_correct_answer", float("inf"))
            for r in all_results
            if isinstance(r, dict)
        ]
        cost_per_correct = [
            r.get("cost_per_correct_usd", 0.0) for r in all_results if isinstance(r, dict)
        ]
        latencies = [
            r.get("mean_latency_ms", 0.0) for r in all_results if isinstance(r, dict)
        ]
        rework_rates = [
            r.get("rework_rate", 0.0) for r in all_results if isinstance(r, dict)
        ]
        human_intervention_rates = [
            r.get("human_intervention_rate", 0.0) for r in all_results if isinstance(r, dict)
        ]
        escalation_rates = [
            r.get("escalation_rate", 0.0) for r in all_results if isinstance(r, dict)
        ]
        tokens_per_case_vals = [
            r.get("mean_tokens_per_case", 0.0) for r in all_results if isinstance(r, dict)
        ]

        mean_accuracy = _mean(accuracies)
        mean_tokens_per_correct = _mean(tokens_per_correct)
        # Derive mean_tokens_per_case if not directly provided
        if any(v > 0 for v in tokens_per_case_vals):
            mean_tokens_per_case = _mean(tokens_per_case_vals)
        else:
            mean_tokens_per_case = mean_tokens_per_correct * mean_accuracy if mean_accuracy > 0 else mean_tokens_per_correct

        return {
            "model_id": model_id,
            "runs": len(history),
            "accuracy": round(mean_accuracy, 4),
            "mean_accuracy": round(mean_accuracy, 4),
            "mean_tokens_per_correct": round(mean_tokens_per_correct, 2),
            "mean_tokens_per_case": round(mean_tokens_per_case, 2),
            "mean_cost_per_correct": round(_mean(cost_per_correct), 6),
            "mean_latency_ms": round(_mean(latencies), 2),
            "rework_rate": round(_mean(rework_rates), 4),
            "human_intervention_rate": round(_mean(human_intervention_rates), 4),
            "escalation_rate": round(_mean(escalation_rates), 4),
            "last_run": history[-1]["timestamp"],
        }

    def rank_models(self, model_ids: list[str] | None = None) -> list[dict[str, Any]]:
        """Rank models by true total cost of ownership (lowest cost is best).

        Uses TrueCostModel to factor in direct cost, rework, human intervention,
        escalation, latency, and infrastructure costs. Also includes the legacy
        ``score`` field for backward compatibility.

        Legacy score formula:
        Score = accuracy * 100 - tokens_per_correct * 0.01 - cost_per_correct * 1000 - mean_latency * 0.001
        """
        if model_ids is None:
            model_ids = list(self._history.keys())

        ranked: list[dict[str, Any]] = []
        for mid in model_ids:
            stats = self.get_model_stats(mid)
            if stats["runs"] == 0:
                continue
            # Legacy score for backward compatibility
            score = (
                stats["mean_accuracy"] * 100
                - stats["mean_tokens_per_correct"] * 0.01
                - stats["mean_cost_per_correct"] * 1000
                - stats["mean_latency_ms"] * 0.001
            )
            ranked.append({**stats, "score": round(score, 4)})

        # Rank by true cost (lowest first)
        if len(ranked) > 1:
            try:
                true_ranked = self._cost_model.rank_by_true_cost(ranked)
                # Merge true cost info back
                cost_map = {m["model_id"]: m for m in true_ranked}
                for r in ranked:
                    tc = cost_map.get(r["model_id"], {})
                    r["true_cost_per_correct"] = tc.get("true_cost_per_correct", float("inf"))
                    r["cost_breakdown"] = tc.get("cost_breakdown", {})
                ranked.sort(key=lambda x: x.get("true_cost_per_correct", float("inf")))
            except Exception:
                # Fall back to legacy score ranking if TrueCostModel fails
                ranked.sort(key=lambda x: x["score"], reverse=True)
        elif ranked:
            # Single model: compute true cost anyway
            try:
                true_ranked = self._cost_model.rank_by_true_cost(ranked)
                if true_ranked:
                    ranked[0]["true_cost_per_correct"] = true_ranked[0].get("true_cost_per_correct", float("inf"))
                    ranked[0]["cost_breakdown"] = true_ranked[0].get("cost_breakdown", {})
            except Exception:
                pass

        return ranked

    def recommend_removals(
        self, model_ids: list[str], min_runs: int = 3
    ) -> list[dict[str, Any]]:
        """Recommend models to remove based on poor performance.

        A model is recommended for removal if:
        - It has at least min_runs benchmark results
        - Its accuracy is below 50% OR
        - Its true cost per correct is > 2x the median OR
        - Its cost_per_correct is > 2x the median OR
        - Its tokens_per_correct is > 2x the median OR
        - It's ranked in the bottom 25% overall
        """
        if len(model_ids) < 2:
            return []

        ranked = self.rank_models(model_ids)
        if len(ranked) < 2:
            return []

        costs = [r["mean_cost_per_correct"] for r in ranked]
        tokens = [r["mean_tokens_per_correct"] for r in ranked]
        true_costs = [r.get("true_cost_per_correct", float("inf")) for r in ranked]
        median_cost = sorted(costs)[len(costs) // 2]
        median_tokens = sorted(tokens)[len(tokens) // 2]
        median_true_cost = sorted(true_costs)[len(true_costs) // 2]

        recommendations: list[dict[str, Any]] = []
        bottom_threshold = max(1, len(ranked) // 4)
        for i, r in enumerate(ranked):
            reasons: list[str] = []
            if r["runs"] < min_runs:
                continue
            if r["mean_accuracy"] < 0.5:
                reasons.append(f"low_accuracy_{r['mean_accuracy']:.2f}")
            if median_cost > 0 and r["mean_cost_per_correct"] > 2 * median_cost:
                reasons.append(f"high_cost_{r['mean_cost_per_correct']:.6f}")
            if median_tokens > 0 and r["mean_tokens_per_correct"] > 2 * median_tokens:
                reasons.append(f"high_tokens_{r['mean_tokens_per_correct']:.1f}")
            if (
                median_true_cost > 0
                and r.get("true_cost_per_correct", float("inf")) > 2 * median_true_cost
            ):
                reasons.append(
                    f"high_true_cost_{r.get('true_cost_per_correct', 0):.8f}"
                )
            if i >= len(ranked) - bottom_threshold:
                reasons.append(f"bottom_ranked_{i + 1}_of_{len(ranked)}")
            if reasons:
                recommendations.append(
                    {
                        "model_id": r["model_id"],
                        "reasons": reasons,
                        "stats": r,
                        "removal_command": f"ollama rm {r['model_id']}",
                    }
                )
        return recommendations

    def recommend_downloads(
        self,
        available_local: list[str],
        hf_discovery: HuggingFaceModelDiscovery,
        task: str = "text-generation",
        max_size_gb: float = 20.0,
    ) -> list[dict[str, Any]]:
        """Recommend new models to download.

        Compares current local models against HuggingFace recommendations
        and suggests downloads for models that would improve performance.
        """
        hf_recs = hf_discovery.recommend_models_for_download(
            task=task, max_size_gb=max_size_gb, limit=10
        )

        local_ollama_names = {_hf_to_ollama_name(lm) for lm in available_local}
        local_lower = {lm.lower() for lm in available_local}

        recommendations: list[dict[str, Any]] = []
        for rec in hf_recs:
            ollama_name = rec.get("ollama_name", "")
            model_id = rec.get("model_id", "")
            # Skip if already installed
            if ollama_name.lower() in local_lower or model_id.lower() in local_lower:
                continue
            if ollama_name.lower() in local_ollama_names:
                continue

            rec["already_installed"] = False
            recommendations.append(rec)

        return recommendations

    def explain_ranking(self, model_ids: list[str] | None = None) -> dict[str, Any]:
        """Return a detailed explanation of the model ranking with cost breakdowns.

        Includes the true cost per correct answer for each model and a
        breakdown of all cost components.
        """
        ranked = self.rank_models(model_ids)
        if not ranked:
            return {"ranked_models": [], "explanation": "No benchmark data available."}

        explanations: list[dict[str, Any]] = []
        for r in ranked:
            entry: dict[str, Any] = {
                "model_id": r["model_id"],
                "true_cost_per_correct": r.get("true_cost_per_correct"),
                "cost_breakdown": r.get("cost_breakdown", {}),
                "accuracy": r.get("mean_accuracy", r.get("accuracy", 0)),
                "rework_rate": r.get("rework_rate", 0),
                "human_intervention_rate": r.get("human_intervention_rate", 0),
                "escalation_rate": r.get("escalation_rate", 0),
                "legacy_score": r.get("score", 0),
            }
            explanations.append(entry)

        return {
            "ranked_models": explanations,
            "total_models_tracked": len(self._history),
            "cost_model_defaults": dict(self._cost_model._params),
        }

    def history(self) -> dict[str, list[dict[str, Any]]]:
        """Return the full benchmark history."""
        return dict(self._history)


# ---------------------------------------------------------------------------
# OpenRouter Model Discovery
# ---------------------------------------------------------------------------


class OpenRouterModelDiscovery:
    """Discover and recommend cloud models from OpenRouter.

    Queries the OpenRouter API to find available models, their pricing,
    and capabilities. Recommends model changes over time.
    """

    def __init__(self, api_key: str, base_url: str = "https://openrouter.ai/api/v1") -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")

    def _api_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/json",
            "User-Agent": "NoeRelay/0.1.0",
        }

    def list_models(self) -> list[dict[str, Any]]:
        """List all available models on OpenRouter with pricing and capabilities.

        Returns list of model info with:
        - id (e.g., "qwen/qwen3.6-35b-a3b")
        - name
        - pricing (prompt, completion per token)
        - context_length
        - architecture (modality, input_modalities, output_modalities)
        - top_provider (context_length, max_completion_tokens)
        """
        url = f"{self._base_url}/models"
        req = urllib.request.Request(url, headers=self._api_headers(), method="GET")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
                data = json.loads(raw)
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8")
            except Exception:
                detail = "(unable to read error body)"
            raise RuntimeError(
                f"OpenRouter API HTTP {exc.code} from {url}: {detail[:500]}"
            ) from None
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"OpenRouter API transport error for {url}: {exc.reason}"
            ) from None
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RuntimeError(
                f"OpenRouter API unparseable response from {url}: {exc}"
            ) from None

        if isinstance(data, dict):
            models = data.get("data", [])
            if isinstance(models, list):
                result: list[dict[str, Any]] = []
                for m in models:
                    if not isinstance(m, dict):
                        continue
                    result.append(
                        {
                            "id": m.get("id", ""),
                            "name": m.get("name", ""),
                            "pricing": m.get("pricing", {}),
                            "context_length": m.get("context_length", 0),
                            "architecture": m.get("architecture", {}),
                            "top_provider": m.get("top_provider", {}),
                            "description": m.get("description", ""),
                        }
                    )
                return result
        return []

    def get_model_pricing(self, model_id: str) -> dict[str, Any]:
        """Get pricing for a specific model."""
        try:
            models = self.list_models()
        except RuntimeError:
            return {
                "model_id": model_id,
                "prompt_per_1k": 0.0,
                "completion_per_1k": 0.0,
                "context_length": 0,
            }

        for m in models:
            if m["id"] == model_id:
                pricing = m.get("pricing", {})
                return {
                    "model_id": model_id,
                    "prompt_per_1k": round(
                        float(pricing.get("prompt", 0)) * 1000, 6
                    ),
                    "completion_per_1k": round(
                        float(pricing.get("completion", 0)) * 1000, 6
                    ),
                    "context_length": m.get("context_length", 0),
                }
        return {
            "model_id": model_id,
            "prompt_per_1k": 0.0,
            "completion_per_1k": 0.0,
            "context_length": 0,
        }

    def recommend_cloud_models(
        self,
        current_portfolio: list[dict[str, Any]],
        task: str = "text-generation",
        max_cost_per_1k: float = 0.01,
    ) -> list[dict[str, Any]]:
        """Recommend cloud model changes.

        Compares current portfolio against available OpenRouter models
        and recommends:
        - Cheaper alternatives with similar capabilities
        - Better models that have become available
        - Models to remove from portfolio (too expensive, deprecated)
        """
        try:
            all_models = self.list_models()
        except RuntimeError:
            return [
                {
                    "type": "error",
                    "message": "Failed to fetch OpenRouter models",
                }
            ]

        current_ids = {str(m.get("model_id", m.get("id", ""))) for m in current_portfolio}

        recommendations: list[dict[str, Any]] = []

        for m in all_models:
            model_id = m.get("id", "")
            if not model_id:
                continue

            pricing = m.get("pricing", {})
            prompt_cost = float(pricing.get("prompt", 0)) * 1000
            completion_cost = float(pricing.get("completion", 0)) * 1000
            total_per_1k = prompt_cost + completion_cost

            if total_per_1k > max_cost_per_1k:
                continue

            # Filter by task
            arch = m.get("architecture", {})
            modality = str(arch.get("modality", "")).lower()
            if task == "text-generation" and modality not in ("", "text", "text+image"):
                continue

            if model_id in current_ids:
                continue

            recommendations.append(
                {
                    "type": "new_model",
                    "model_id": model_id,
                    "name": m.get("name", ""),
                    "prompt_cost_per_1k": round(prompt_cost, 6),
                    "completion_cost_per_1k": round(completion_cost, 6),
                    "total_cost_per_1k": round(total_per_1k, 6),
                    "context_length": m.get("context_length", 0),
                    "description": m.get("description", ""),
                }
            )

        # Sort by cost (cheapest first)
        recommendations.sort(key=lambda x: x["total_cost_per_1k"])
        return recommendations[:20]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mean(values: list[float]) -> float:
    """Compute the arithmetic mean of a list of floats."""
    if not values:
        return 0.0
    return sum(values) / len(values)