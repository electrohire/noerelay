"""Deterministic epistemic state transitions."""


def adjudicate_fact(support_lcb: float, refutation_lcb: float, threshold: float) -> str:
    """Return the four-valued state for a factual claim.

    Support and refutation remain independent. This prevents contradictory evidence
    from being averaged into a deceptively neutral score.
    """

    for name, value in {
        "support_lcb": support_lcb,
        "refutation_lcb": refutation_lcb,
        "threshold": threshold,
    }.items():
        if not 0 <= value <= 1:
            raise ValueError(f"{name} must be between 0 and 1")

    supported = support_lcb >= threshold
    refuted = refutation_lcb >= threshold
    if supported and refuted:
        return "conflicted"
    if supported:
        return "supported"
    if refuted:
        return "refuted"
    return "unknown"
