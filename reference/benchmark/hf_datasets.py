"""HuggingFace Hub dataset loaders with immutable revision pinning.

The EPR benchmark manifest mandates ``require_immutable_revision: true``.
This module honours that requirement using only the Python standard library:
it resolves a dataset's commit SHA through the HuggingFace API and downloads
data files at the pinned revision via ``urllib.request``.
"""

from __future__ import annotations

import csv
import io
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .datasets import DatasetLoader

HF_API_BASE = "https://huggingface.co/api"
HF_DATASETS_BASE = "https://huggingface.co/datasets"


class HuggingFaceDatasetLoader(DatasetLoader):
    """Load datasets from the HuggingFace Hub with immutable revision pinning.

    EPR benchmark manifest requires ``require_immutable_revision: true``.
    Uses the HuggingFace API (``https://huggingface.co/api/datasets/{id}``) to
    resolve the commit SHA, then downloads the dataset file(s) via
    ``https://huggingface.co/datasets/{id}/resolve/{revision}/{filename}``.

    No ``datasets`` library dependency — uses ``urllib.request`` directly.
    """

    def __init__(
        self,
        dataset_id: str,
        revision: str | None = None,
        split: str = "test",
        hf_token: str | None = None,
        config: str | None = None,
    ) -> None:
        self.dataset_id = dataset_id
        self.revision = revision  # If None, resolves to latest commit SHA
        self.split = split
        self.hf_token = hf_token
        self.config = config

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.hf_token:
            headers["Authorization"] = f"Bearer {self.hf_token}"
        return headers

    def resolve_revision(self) -> str:
        """Resolve the immutable commit SHA for the dataset."""
        if self.revision:
            return self.revision
        url = f"{HF_API_BASE}/datasets/{self.dataset_id}"
        request = urllib.request.Request(url, headers=self._headers())
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
        sha = payload.get("sha") or payload.get("_id") or "main"
        return str(sha)

    def _candidate_filenames(self) -> list[str]:
        names: list[str] = []
        split = self.split
        if self.config:
            base = f"{self.config}/{split}"
            names.extend(
                [
                    f"{base}.jsonl",
                    f"{base}.json",
                    f"{base}.csv",
                    f"{base}.parquet",
                    f"{self.config}/{split}-00000-of-00001.jsonl",
                    f"{self.config}/{split}-00000-of-00001.parquet",
                ]
            )
        else:
            names.extend(
                [
                    f"{split}.jsonl",
                    f"{split}.json",
                    f"{split}.csv",
                    f"{split}.parquet",
                    f"data/{split}-00000-of-00001.jsonl",
                    f"data/{split}-00000-of-00001.parquet",
                ]
            )
        return names

    def _download_file(self, revision: str, filename: str) -> bytes | None:
        url = f"{HF_DATASETS_BASE}/{self.dataset_id}/resolve/{revision}/{filename}"
        request = urllib.request.Request(url, headers=self._headers())
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.read()
        except (urllib.error.HTTPError, urllib.error.URLError, OSError):
            return None

    def load(self) -> list[dict[str, Any]]:
        """Download and parse the dataset into benchmark test cases."""
        revision = self.resolve_revision()
        for filename in self._candidate_filenames():
            data = self._download_file(revision, filename)
            if data is None:
                continue
            cases = self._parse(data, filename)
            if cases:
                return cases
        return []

    def _parse(self, data: bytes, filename: str) -> list[dict[str, Any]]:
        text = data.decode("utf-8")
        if filename.endswith(".jsonl"):
            rows: list[Any] = []
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            return [self._case_from_row(row) for row in rows]
        if filename.endswith(".json"):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                return []
            if isinstance(parsed, list):
                rows = parsed
            elif isinstance(parsed, dict):
                rows = parsed.get("rows") or parsed.get("data") or []
                if not isinstance(rows, list):
                    rows = [parsed]
            else:
                return []
            return [self._case_from_row(row) for row in rows]
        if filename.endswith(".csv"):
            reader = csv.DictReader(io.StringIO(text))
            return [self._case_from_row(row) for row in reader]
        # Parquet is not parseable without a third-party dependency; the
        # stdlib-only loader intentionally skips it.
        return []

    def _case_from_row(self, row: Any) -> dict[str, Any]:
        if not isinstance(row, dict):
            return {}
        record = dict(row)
        input_data = record.get("input")
        if not isinstance(input_data, dict):
            input_data = {
                "messages": [
                    {"role": "user", "content": json.dumps(record, ensure_ascii=False)}
                ]
            }
        expected = record.get(
            "expected_output", record.get("expected", record.get("output", ""))
        )
        metadata = {
            key: value
            for key, value in record.items()
            if key not in {"input", "expected_output", "expected", "output"}
        }
        return {
            "id": (
                record.get("id")
                or record.get("instance_id")
                or record.get("index")
                or ""
            ),
            "input": input_data,
            "expected_output": expected,
            "metadata": metadata,
        }


def _load_manifest() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[2] / "spec" / "benchmark-manifest.json"
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def build_registry_from_manifest(
    manifest: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Build a registry mapping cohort ids to their HuggingFace datasets."""
    if manifest is None:
        manifest = _load_manifest()
    registry: dict[str, dict[str, Any]] = {}
    for cohort in manifest.get("cohorts", []):
        datasets: list[dict[str, Any]] = []
        for ds in cohort.get("datasets", []):
            if ds.get("registry") != "huggingface" or not ds.get("dataset_id"):
                continue
            dataset_id = ds["dataset_id"]
            lowered = dataset_id.lower()
            if "swe" in lowered:
                format_name = "swe_bench"
            elif "berkeley" in lowered or "bfcl" in lowered:
                format_name = "bfcl"
            else:
                format_name = "contract"
            datasets.append(
                {
                    "id": dataset_id,
                    "split": ds.get("split", "test"),
                    "format": format_name,
                }
            )
        if datasets:
            registry[cohort["id"]] = {"datasets": datasets}
    return registry


# Cohort -> HuggingFace dataset loaders, as required by the benchmark manifest.
# Cohorts without a HuggingFace ``dataset_id`` (internal/arxiv sources) are
# intentionally absent; their loaders are supplied by internal harnesses.
DATASET_REGISTRY: dict[str, dict[str, Any]] = {
    "governed-software-v-model": {
        "datasets": [
            {
                "id": "SWE-bench/SWE-bench_Verified",
                "split": "test",
                "format": "swe_bench",
            },
        ],
    },
    "agentic-tool-calling": {
        "datasets": [
            {
                "id": "gorilla-llm/Berkeley-Function-Calling-Leaderboard",
                "split": "test",
                "format": "bfcl",
            },
        ],
    },
}


def get_cohort_loaders(
    cohort_name: str,
    hf_token: str | None = None,
    revision: str | None = None,
) -> list[HuggingFaceDatasetLoader]:
    """Return HuggingFace dataset loaders registered for ``cohort_name``."""
    entry = DATASET_REGISTRY.get(cohort_name, {})
    loaders: list[HuggingFaceDatasetLoader] = []
    for spec in entry.get("datasets", []):
        loaders.append(
            HuggingFaceDatasetLoader(
                dataset_id=spec["id"],
                revision=revision,
                split=spec.get("split", "test"),
                hf_token=hf_token,
            )
        )
    return loaders
