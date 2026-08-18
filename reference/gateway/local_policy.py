"""Runtime extension of the routing policy to permit local models.

The shipped routing policy (``spec/routing-policy.json``) only allows the
``openrouter`` gateway.  Rather than modifying that spec file, this module
extends a loaded policy in-memory so ``local`` becomes an allowed gateway and
local-model routing is configured.
"""

from __future__ import annotations

from typing import Any


def extend_policy_with_local(policy: dict[str, Any]) -> dict[str, Any]:
    """Extend the routing policy to allow local models.

    Adds ``local`` to ``allowed_gateways`` and configures local model routing.
    Returns a new dict; the original is not mutated.
    """
    extended = dict(policy)
    inference = dict(extended.get("inference", {}))
    inference["allowed_gateways"] = list(
        inference.get("allowed_gateways", [])
    ) + ["local"]
    inference["local"] = {
        "base_url_env": "NOERELAY_LOCAL_MODEL_URL",
        "default_base_url": "http://127.0.0.1:11434",
        "model_fallbacks_managed_by_noerelay": True,
        "automatic_model_selection_allowed": False,
        "escalate_to_cloud_on_failure": True,
    }
    extended["inference"] = inference
    return extended