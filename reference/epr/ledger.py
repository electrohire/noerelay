"""Hash-linked append-only ledger helpers."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any


def _canonical_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _event_hash(event: dict[str, Any]) -> str:
    material = deepcopy(event)
    material.pop("event_hash", None)
    return "sha256:" + hashlib.sha256(_canonical_bytes(material)).hexdigest()


def append_event(events: list[dict[str, Any]], event: dict[str, Any]) -> dict[str, Any]:
    """Append a canonical event and return the appended copy."""

    appended = deepcopy(event)
    appended["sequence"] = len(events)
    appended["previous_event_hash"] = events[-1]["event_hash"] if events else "GENESIS"
    appended["event_hash"] = _event_hash(appended)
    events.append(appended)
    return appended


def verify_chain(events: list[dict[str, Any]]) -> tuple[bool, str]:
    """Verify sequence, previous-hash links, and event content hashes."""

    previous = "GENESIS"
    for index, event in enumerate(events):
        if event.get("sequence") != index:
            return False, f"sequence mismatch at index {index}"
        if event.get("previous_event_hash") != previous:
            return False, f"previous hash mismatch at index {index}"
        if event.get("event_hash") != _event_hash(event):
            return False, f"content hash mismatch at index {index}"
        previous = event["event_hash"]
    return True, "ok"
