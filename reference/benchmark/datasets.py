"""Benchmark dataset loaders.

The loader interface is intentionally minimal so new backends (for example a
HuggingFace datasets adapter) can be added without touching the runner.
"""

from __future__ import annotations

import json
from typing import Any


class DatasetLoader:
    """Abstract base for dataset loaders."""

    def load(self) -> list[dict[str, Any]]:
        """Return a list of test cases.

        Each case has: ``input``, ``expected_output``, ``metadata``.
        """
        raise NotImplementedError


class JsonlDatasetLoader(DatasetLoader):
    """Load benchmark cases from a JSONL file.

    Blank lines and lines starting with ``#`` are ignored.
    """

    def __init__(self, path: str) -> None:
        self.path = path

    def load(self) -> list[dict[str, Any]]:
        cases: list[dict[str, Any]] = []
        with open(self.path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                case = json.loads(line)
                cases.append(case)
        return cases


class InlineDatasetLoader(DatasetLoader):
    """Load benchmark cases from an inline list."""

    def __init__(self, cases: list[dict[str, Any]]) -> None:
        self.cases = cases

    def load(self) -> list[dict[str, Any]]:
        return self.cases
