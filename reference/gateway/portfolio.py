"""Candidate-action portfolio loader for the gateway.

The portfolio is a JSON array of candidate-action objects conforming to
``spec/schemas/candidate-action.schema.json``. The shipped example lives at
``examples/candidate-actions.json``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_portfolio(path: Path) -> list[dict[str, Any]]:
    """Load the candidate-action portfolio from a JSON file."""
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)