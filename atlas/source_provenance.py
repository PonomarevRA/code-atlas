"""Compare bounded source facts already read by a request, without filesystem access."""

import re


def _hash(value) -> str | None:
    """Accept a full SHA-256 only, never a fingerprint prefix or an unrelated identifier."""
    return value.lower() if isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{64}", value) else None


def _captured_hash(node: dict) -> tuple[str | None, bool]:
    """Use only snapshot source facts; conflicting captured hashes cannot prove a comparison."""
    hashes = set()
    for key in ("commentSource", "complexity", "health", "callableFacts", "fieldFacts", "attributeFacts"):
        facts = node.get(key)
        if not isinstance(facts, dict) or facts.get("sourceKind") not in {"snapshot-captured-source", "snapshot-captured-ast"}:
            continue
        value = _hash(facts.get("sourceHash"))
        if value:
            hashes.add(value)
    return (next(iter(hashes)), False) if len(hashes) == 1 else (None, len(hashes) > 1)


def source_provenance(graph: dict, node: dict, description: dict, snapshot_id: str) -> dict:
    """Compare captured file-byte hashes to the safe reader's already-read bytes.

    Captured-only declarations never imply a current file read. Missing or conflicting
    captured facts remain unknown, and neither graph nor source objects are mutated.
    """
    metadata = graph.get("metadata") or {}
    captured, conflict = _captured_hash(node)
    source_kind = description.get("sourceKind", "working-tree")
    current = _hash(description.get("sourceByteHash")) if source_kind == "working-tree" else None
    read_at = description.get("sourceReadAt") if current else None
    status, reason = "unknown", "captured-hash-missing"
    if source_kind != "working-tree":
        reason = "current-source-not-read"
    elif conflict:
        reason = "captured-hash-conflict"
    elif not current:
        reason = "current-source-unavailable"
    elif captured:
        status = "match" if captured == current else "changed"
        reason = "file-byte-hashes-equal" if status == "match" else "file-byte-hashes-differ"
    date = metadata.get("snapshotCreatedAt") or metadata.get("graphUpdatedAt") or metadata.get("createdAt")
    return {"snapshotId": snapshot_id, "snapshotCreatedAt": date if isinstance(date, str) else None,
            "capturedSourceHash": captured, "currentSourceHash": current, "currentReadAt": read_at,
            "status": status, "reason": reason, "sourceKind": source_kind,
            "file": description.get("file") or node.get("file") or node.get("source_file") or ""}
