"""Run the EPR-1 route-selection example."""

from __future__ import annotations

import json
from pathlib import Path

from epr.kernel import select_route


ROOT = Path(__file__).resolve().parents[1]


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


if __name__ == "__main__":
    contract = load_json(ROOT / "examples" / "high-risk-coding-contract.json")
    candidates = load_json(ROOT / "examples" / "candidate-actions.json")
    policy = load_json(ROOT / "spec" / "routing-policy.json")
    decision = select_route(contract, candidates, policy)
    print(json.dumps(decision, indent=2, sort_keys=True))
