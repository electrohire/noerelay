"""Prometheus-format metrics endpoint for the NoeRelay gateway.

Exposes counters, gauges, and histograms in Prometheus text format.
Thread-safe via a reentrant lock.  Dependency-free (stdlib only).
"""

from __future__ import annotations

import threading
import time
from typing import Any


# ---------------------------------------------------------------------------
# Help / Type metadata
# ---------------------------------------------------------------------------

_HELP_TEXT: dict[str, str] = {
    "noerelay_runs_total": "Total number of runs processed.",
    "noerelay_runs_accepted_total": "Total number of runs accepted.",
    "noerelay_runs_escalated_total": "Total number of runs escalated.",
    "noerelay_runs_rejected_total": "Total number of runs rejected.",
    "noerelay_active_runs": "Number of runs currently active.",
    "noerelay_cache_size": "Current number of entries in the response cache.",
    "noerelay_local_models_count": "Number of discovered local models.",
    "noerelay_request_duration_seconds": "Request duration in seconds.",
    "noerelay_tokens_per_request": "Tokens consumed per request.",
    "noerelay_cost_per_request_usd": "Cost in USD per request.",
    "noerelay_model_requests_total": "Total requests per model.",
    "noerelay_model_tokens_total": "Total tokens per model.",
    "noerelay_model_cost_total": "Total cost in USD per model.",
    "noerelay_tenant_spend_total": "Total spend in USD per tenant.",
    "noerelay_risk_class_runs_total": "Total runs per risk class.",
    # RTK compression metrics (Phase 4)
    "noerelay_compression_total": "Total number of compression operations.",
    "noerelay_compression_tokens_saved_total": "Total tokens saved by compression.",
    "noerelay_compression_cache_hits_total": "Total compression cache hits.",
    "noerelay_compression_cache_misses_total": "Total compression cache misses.",
    "noerelay_compression_avg_ratio": "Average compression ratio (0-1).",
    "noerelay_compression_avg_quality": "Average compression quality score (0-1).",
}

_TYPE_TEXT: dict[str, str] = {
    "noerelay_runs_total": "counter",
    "noerelay_runs_accepted_total": "counter",
    "noerelay_runs_escalated_total": "counter",
    "noerelay_runs_rejected_total": "counter",
    "noerelay_active_runs": "gauge",
    "noerelay_cache_size": "gauge",
    "noerelay_local_models_count": "gauge",
    "noerelay_request_duration_seconds": "histogram",
    "noerelay_tokens_per_request": "histogram",
    "noerelay_cost_per_request_usd": "histogram",
    "noerelay_model_requests_total": "counter",
    "noerelay_model_tokens_total": "counter",
    "noerelay_model_cost_total": "counter",
    "noerelay_tenant_spend_total": "counter",
    "noerelay_risk_class_runs_total": "counter",
    # RTK compression metrics (Phase 4)
    "noerelay_compression_total": "gauge",
    "noerelay_compression_tokens_saved_total": "gauge",
    "noerelay_compression_cache_hits_total": "gauge",
    "noerelay_compression_cache_misses_total": "gauge",
    "noerelay_compression_avg_ratio": "gauge",
    "noerelay_compression_avg_quality": "gauge",
}


def _format_labels(labels: dict[str, str] | None) -> str:
    """Format a labels dict into Prometheus label string."""
    if not labels:
        return ""
    parts = [f'{k}="{v}"' for k, v in sorted(labels.items())]
    return "{" + ",".join(parts) + "}"


def _escape_label_value(value: str) -> str:
    """Escape backslashes and double-quotes in a Prometheus label value."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _format_labels_escaped(labels: dict[str, str] | None) -> str:
    """Format a labels dict with proper escaping."""
    if not labels:
        return ""
    parts = [
        f'{k}="{_escape_label_value(str(v))}"' for k, v in sorted(labels.items())
    ]
    return "{" + ",".join(parts) + "}"


class PrometheusMetrics:
    """Prometheus-format metrics collector.

    Exposes metrics in Prometheus text format (not JSON):
    - Counters: noerelay_runs_total, noerelay_runs_accepted_total,
      noerelay_runs_escalated_total, noerelay_runs_rejected_total
    - Gauges: noerelay_active_runs, noerelay_cache_size,
      noerelay_local_models_count
    - Histograms: noerelay_request_duration_seconds,
      noerelay_tokens_per_request, noerelay_cost_per_request_usd
    - Per-model: noerelay_model_requests_total{model_id=...},
      noerelay_model_tokens_total{model_id=...},
      noerelay_model_cost_total{model_id=...}
    - Per-tenant: noerelay_tenant_spend_total{tenant_id=...}
    - Per-risk-class: noerelay_risk_class_runs_total{risk_class=...}
    """

    # Default histogram buckets (seconds for latency, count for tokens, USD for cost)
    _DURATION_BUCKETS: list[float] = [0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0]
    _TOKEN_BUCKETS: list[float] = [10.0, 50.0, 100.0, 250.0, 500.0, 1000.0, 2500.0, 5000.0, 10000.0, 50000.0]
    _COST_BUCKETS: list[float] = [0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 5.0, 10.0]

    _BUCKETS: dict[str, list[float]] = {
        "noerelay_request_duration_seconds": _DURATION_BUCKETS,
        "noerelay_tokens_per_request": _TOKEN_BUCKETS,
        "noerelay_cost_per_request_usd": _COST_BUCKETS,
    }

    def __init__(self) -> None:
        self._counters: dict[str, float] = {}
        self._gauges: dict[str, float] = {}
        self._histograms: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Counter helpers
    # ------------------------------------------------------------------

    def _counter_key(self, name: str, labels: dict[str, str] | None = None) -> str:
        """Build a stable key for a labelled counter."""
        if labels:
            return name + _format_labels_escaped(labels)
        return name

    def inc_counter(
        self,
        name: str,
        labels: dict[str, str] | None = None,
        value: float = 1.0,
    ) -> None:
        """Increment a counter by *value* (default 1)."""
        key = self._counter_key(name, labels)
        with self._lock:
            self._counters[key] = self._counters.get(key, 0.0) + value

    def get_counter(
        self, name: str, labels: dict[str, str] | None = None
    ) -> float:
        """Read a counter value (for testing)."""
        key = self._counter_key(name, labels)
        with self._lock:
            return self._counters.get(key, 0.0)

    # ------------------------------------------------------------------
    # Gauge helpers
    # ------------------------------------------------------------------

    def _gauge_key(self, name: str, labels: dict[str, str] | None = None) -> str:
        if labels:
            return name + _format_labels_escaped(labels)
        return name

    def set_gauge(
        self,
        name: str,
        value: float,
        labels: dict[str, str] | None = None,
    ) -> None:
        """Set a gauge to an absolute value."""
        key = self._gauge_key(name, labels)
        with self._lock:
            self._gauges[key] = value

    def get_gauge(
        self, name: str, labels: dict[str, str] | None = None
    ) -> float:
        """Read a gauge value (for testing)."""
        key = self._gauge_key(name, labels)
        with self._lock:
            return self._gauges.get(key, 0.0)

    # ------------------------------------------------------------------
    # Histogram helpers
    # ------------------------------------------------------------------

    def _histogram_key(self, name: str, labels: dict[str, str] | None = None) -> str:
        if labels:
            return name + _format_labels_escaped(labels)
        return name

    def observe_histogram(
        self,
        name: str,
        value: float,
        labels: dict[str, str] | None = None,
    ) -> None:
        """Observe a histogram value."""
        key = self._histogram_key(name, labels)
        with self._lock:
            if key not in self._histograms:
                self._histograms[key] = []
            self._histograms[key].append(value)

    def get_histogram_values(
        self, name: str, labels: dict[str, str] | None = None
    ) -> list[float]:
        """Read histogram observations (for testing)."""
        key = self._histogram_key(name, labels)
        with self._lock:
            return list(self._histograms.get(key, []))

    # ------------------------------------------------------------------
    # Prometheus text format
    # ------------------------------------------------------------------

    def _format_metric_line(
        self,
        name: str,
        value: float,
        labels: dict[str, str] | None = None,
        suffix: str | None = None,
    ) -> str:
        """Format a single metric line."""
        metric_name = name if suffix is None else f"{name}_{suffix}"
        label_str = _format_labels_escaped(labels)
        return f"{metric_name}{label_str} {_format_float(value)}"

    def format(self) -> str:
        """Format all metrics in Prometheus text exposition format."""
        lines: list[str] = []

        with self._lock:
            # Collect all unique metric names from counters and gauges
            metric_names: set[str] = set()

            # Gather counter names (strip label suffix)
            for key in self._counters:
                base = key
                if "{" in key:
                    base = key[: key.index("{")]
                metric_names.add(base)

            # Gather gauge names
            for key in self._gauges:
                base = key
                if "{" in key:
                    base = key[: key.index("{")]
                metric_names.add(base)

            # Gather histogram names
            for key in self._histograms:
                base = key
                if "{" in key:
                    base = key[: key.index("{")]
                metric_names.add(base)

            # Sort for deterministic output
            for name in sorted(metric_names):
                help_text = _HELP_TEXT.get(name, "")
                type_text = _TYPE_TEXT.get(name, "untyped")

                if help_text:
                    lines.append(f"# HELP {name} {help_text}")
                lines.append(f"# TYPE {name} {type_text}")

                if type_text == "counter":
                    lines.extend(self._format_counters_for_name(name))
                elif type_text == "gauge":
                    lines.extend(self._format_gauges_for_name(name))
                elif type_text == "histogram":
                    lines.extend(self._format_histograms_for_name(name))

        # Ensure output ends with a newline (Prometheus convention)
        result = "\n".join(lines)
        if not result.endswith("\n"):
            result += "\n"
        return result

    def _format_counters_for_name(self, name: str) -> list[str]:
        """Format all counter instances for a given metric name."""
        result: list[str] = []
        prefix = name + "{"
        for key, value in sorted(self._counters.items()):
            if key == name or key.startswith(prefix):
                if key == name:
                    result.append(f"{name} {_format_float(value)}")
                else:
                    # key is "name{labels}", extract labels
                    labels_str = key[len(name):]
                    result.append(f"{name}{labels_str} {_format_float(value)}")
        return result

    def _format_gauges_for_name(self, name: str) -> list[str]:
        """Format all gauge instances for a given metric name."""
        result: list[str] = []
        prefix = name + "{"
        for key, value in sorted(self._gauges.items()):
            if key == name or key.startswith(prefix):
                if key == name:
                    result.append(f"{name} {_format_float(value)}")
                else:
                    labels_str = key[len(name):]
                    result.append(f"{name}{labels_str} {_format_float(value)}")
        return result

    def _format_histograms_for_name(self, name: str) -> list[str]:
        """Format all histogram instances for a given metric name."""
        result: list[str] = []
        buckets = self._BUCKETS.get(name, [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0])
        prefix = name + "{"

        for key, values in sorted(self._histograms.items()):
            base_labels: dict[str, str] = {}

            if key != name and key.startswith(prefix):
                # Extract labels from the key
                labels_str = key[len(name):]
                base_labels = _parse_labels_string(labels_str)

            # Only include if this histogram belongs to the current metric name
            hist_name = name
            if key != name:
                hist_name = key[: key.index("{")] if "{" in key else key
            if hist_name != name:
                continue

            # Compute bucket counts
            bucket_counts = _compute_bucket_counts(values, buckets)
            total_count = len(values)
            total_sum = sum(values)

            # Emit bucket lines
            for i, bound in enumerate(buckets):
                bucket_labels = dict(base_labels)
                bucket_labels["le"] = _format_float(bound)
                result.append(
                    f"{name}_bucket{_format_labels_escaped(bucket_labels)} {bucket_counts[i]}"
                )

            # +Inf bucket
            inf_labels = dict(base_labels)
            inf_labels["le"] = "+Inf"
            result.append(
                f"{name}_bucket{_format_labels_escaped(inf_labels)} {total_count}"
            )

            # Sum and count
            result.append(
                f"{name}_sum{_format_labels_escaped(base_labels) if base_labels else ''} {_format_float(total_sum)}"
            )
            result.append(
                f"{name}_count{_format_labels_escaped(base_labels) if base_labels else ''} {total_count}"
            )

        return result

    def get_metrics_text(self) -> str:
        """Get the full Prometheus metrics text."""
        return self.format()

    # ------------------------------------------------------------------
    # Record helpers
    # ------------------------------------------------------------------

    def record_run(self, run_record: dict[str, Any]) -> None:
        """Record metrics from a completed run.

        *run_record* should be a dict with keys like:
        - status: "accepted", "escalated", "rejected"
        - model_id: upstream model used
        - risk_class: governance risk class
        - tokens: total tokens consumed
        - cost_usd: total cost in USD
        - duration_ms: request duration in milliseconds
        - tenant_id: tenant that owns the run
        """
        status = run_record.get("status", "unknown")
        model_id = run_record.get("model_id")
        risk_class = run_record.get("risk_class")
        tokens = run_record.get("tokens")
        cost_usd = run_record.get("cost_usd")
        duration_ms = run_record.get("duration_ms")
        tenant_id = run_record.get("tenant_id")

        # Increment overall runs counter
        self.inc_counter("noerelay_runs_total")

        # Status-specific counters
        if status == "accepted":
            self.inc_counter("noerelay_runs_accepted_total")
        elif status == "escalated":
            self.inc_counter("noerelay_runs_escalated_total")
        elif status == "rejected":
            self.inc_counter("noerelay_runs_rejected_total")

        # Per-model counters
        if model_id:
            self.inc_counter(
                "noerelay_model_requests_total",
                {"model_id": str(model_id)},
            )
            if tokens:
                self.inc_counter(
                    "noerelay_model_tokens_total",
                    {"model_id": str(model_id)},
                    value=float(tokens),
                )
            if cost_usd:
                self.inc_counter(
                    "noerelay_model_cost_total",
                    {"model_id": str(model_id)},
                    value=float(cost_usd),
                )

        # Per-tenant spend
        if tenant_id and cost_usd:
            self.inc_counter(
                "noerelay_tenant_spend_total",
                {"tenant_id": str(tenant_id)},
                value=float(cost_usd),
            )

        # Per-risk-class
        if risk_class:
            self.inc_counter(
                "noerelay_risk_class_runs_total",
                {"risk_class": str(risk_class)},
            )

        # Histograms
        if duration_ms is not None:
            self.observe_histogram(
                "noerelay_request_duration_seconds",
                float(duration_ms) / 1000.0,
            )
        if tokens is not None:
            self.observe_histogram(
                "noerelay_tokens_per_request",
                float(tokens),
            )
        if cost_usd is not None:
            self.observe_histogram(
                "noerelay_cost_per_request_usd",
                float(cost_usd),
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_float(value: float) -> str:
    """Format a float for Prometheus output.

    Prometheus accepts standard float notation; +Inf, -Inf, and NaN are
    represented as +Inf, -Inf, and Nan respectively.
    """
    if value == float("inf"):
        return "+Inf"
    if value == float("-inf"):
        return "-Inf"
    if value != value:  # NaN
        return "Nan"
    # Use repr for precision, but strip trailing zeros
    formatted = repr(value)
    if "." in formatted:
        formatted = formatted.rstrip("0").rstrip(".")
    if formatted == "-0":
        formatted = "0"
    return formatted


def _parse_labels_string(labels_str: str) -> dict[str, str]:
    """Parse a Prometheus labels string like '{key="value",...}' into a dict."""
    result: dict[str, str] = {}
    if not labels_str.startswith("{") or not labels_str.endswith("}"):
        return result
    inner = labels_str[1:-1]
    if not inner:
        return result
    # Simple parser: split on commas, then on first =
    for part in _split_labels(inner):
        if "=" not in part:
            continue
        k, _, v = part.partition("=")
        k = k.strip()
        v = v.strip().strip('"')
        result[k] = v
    return result


def _split_labels(inner: str) -> list[str]:
    """Split a label string on commas, respecting quoted values."""
    parts: list[str] = []
    current: list[str] = []
    in_quotes = False
    for ch in inner:
        if ch == '"':
            in_quotes = not in_quotes
            current.append(ch)
        elif ch == "," and not in_quotes:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    if current:
        parts.append("".join(current))
    return parts


def _compute_bucket_counts(
    values: list[float], buckets: list[float]
) -> list[int]:
    """Compute cumulative bucket counts for a Prometheus histogram.

    Each bucket count includes all observations <= the bucket bound.
    Since values that fall in a lower bound also fall in all higher
    bounds, the counts are naturally cumulative without extra
    accumulation.
    """
    counts = [0] * len(buckets)
    for v in values:
        for i, bound in enumerate(buckets):
            if v <= bound:
                counts[i] += 1
    return counts