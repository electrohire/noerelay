"""Distinct state vocabularies for the NoeRelay gateway.

EPR-CON-004: requirements, facts, decisions, assumptions, observations,
predictions, preferences, and artifacts MUST use distinct state vocabularies.
Each vocabulary has a distinct ``kind`` field in the state store.

This module is dependency-free (stdlib only).
"""

from __future__ import annotations

from typing import Any


class StateVocabulary:
    """Enforce the eight distinct state vocabularies (EPR-CON-004).

    A state item carries a ``kind`` field whose value is one of the vocabulary
    constants below.  :meth:`classify` maps a natural-language statement onto
    its vocabulary, and :meth:`validate` rejects items whose ``kind`` is
    unknown or whose vocabulary-required fields are absent.
    """

    REQUIREMENT = "requirement"
    FACT = "fact"
    DECISION = "decision"
    ASSUMPTION = "assumption"
    OBSERVATION = "observation"
    PREDICTION = "prediction"
    PREFERENCE = "preference"
    ARTIFACT = "artifact"

    ALL_VOCABULARIES = frozenset(
        {
            REQUIREMENT,
            FACT,
            DECISION,
            ASSUMPTION,
            OBSERVATION,
            PREDICTION,
            PREFERENCE,
            ARTIFACT,
        }
    )

    # Four-valued fact adjudication statuses (EPR-EPI-001).
    FACT_STATUSES = frozenset({"supported", "refuted", "unknown", "conflicted"})

    # Keyword signals used by :meth:`classify`.  Order matters: earlier
    # vocabularies win when a statement carries multiple signals.
    _SIGNALS: tuple[tuple[str, tuple[str, ...]], ...] = (
        (REQUIREMENT, ("shall", "must", "should")),
        (PREFERENCE, ("prefer", "would like", "want", "desire")),
        (DECISION, ("approve", "approved", "reject", "rejected", "decide", "decision")),
        (ASSUMPTION, ("assume", "assumption", "presume", "unverified premise")),
        (PREDICTION, ("predict", "prediction", "forecast", "likely", "expected to")),
        (OBSERVATION, ("observe", "observation", "measured", "sensor", "tool output")),
        (ARTIFACT, ("artifact", "produced", "build", "hash", "file")),
    )

    @staticmethod
    def classify(statement: str) -> str:
        """Classify *statement* into the most specific vocabulary.

        ``requirement`` (shall/must/should) is checked first; the remaining
        vocabularies are matched by keyword signals; anything else defaults to
        ``fact`` (adjudicated claims with a four-valued status).
        """
        text = str(statement).lower()
        for vocabulary, signals in StateVocabulary._SIGNALS:
            if any(signal in text for signal in signals):
                return vocabulary
        return StateVocabulary.FACT

    @staticmethod
    def validate(state_item: dict[str, Any]) -> list[str]:
        """Return validation errors for *state_item* (empty when valid).

        A valid state item carries a known ``kind`` and the fields its
        vocabulary requires.  This is dependency-free deterministic validation
        (no JSON-schema runtime).
        """
        errors: list[str] = []
        if not isinstance(state_item, dict):
            return ["state_item_must_be_an_object"]

        kind = state_item.get("kind")
        if kind not in StateVocabulary.ALL_VOCABULARIES:
            errors.append(f"invalid_kind:{kind}")
            return errors

        if kind == StateVocabulary.REQUIREMENT:
            if not str(state_item.get("statement", "")).strip():
                errors.append("requirement_missing_statement")
        elif kind == StateVocabulary.FACT:
            if state_item.get("status") not in StateVocabulary.FACT_STATUSES:
                errors.append("fact_missing_four_valued_status")
        elif kind == StateVocabulary.DECISION:
            if state_item.get("status") not in {"approved", "rejected"}:
                errors.append("decision_missing_status")
            if not str(state_item.get("rationale", "")).strip():
                errors.append("decision_missing_rationale")
        elif kind == StateVocabulary.ASSUMPTION:
            if not str(state_item.get("statement", "")).strip():
                errors.append("assumption_missing_statement")
        elif kind == StateVocabulary.OBSERVATION:
            if not str(state_item.get("content_hash", "")).strip():
                errors.append("observation_missing_content_hash")
        elif kind == StateVocabulary.PREDICTION:
            if not str(state_item.get("statement", "")).strip():
                errors.append("prediction_missing_statement")
        elif kind == StateVocabulary.PREFERENCE:
            if not str(state_item.get("statement", "")).strip():
                errors.append("preference_missing_statement")
        elif kind == StateVocabulary.ARTIFACT:
            if not str(state_item.get("content_hash", "")).strip() and not str(
                state_item.get("artifact_id", "")
            ).strip():
                errors.append("artifact_missing_hash")

        return errors
