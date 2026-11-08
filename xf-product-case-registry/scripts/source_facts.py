"""Read-only validation of layered source observations; never mutates the ledger."""
from __future__ import annotations

from datetime import datetime
import hashlib
from pathlib import Path
import re
from typing import Any

LEVELS = {"PROJECT", "TASK", "INSPECTION", "PRODUCT"}
METHODS = {"VISIBLE_DOM", "SOURCE_DOCUMENT", "LEGACY_VISIBLE_EXPORT"}
RAW_FIELDS = {"statusRaw", "handlersRaw", "mainHandlerMarkRaw", "inspectionMethodRaw"}
PROJECT_NO = re.compile(r"^\d{8}[A-Z]\d{9}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def resolve_station(assertions: list[dict[str, Any]]) -> dict[str, Any]:
    """Combine evidenced tri-state assertions, retaining contradictory evidence."""
    values = {(item["scope"], item["value"]) for item in assertions}
    positive = any(value == "YES" for _, value in values)
    negative = ("COMBINED", "NO") in values or {
        ("PETROL", "NO"), ("GAS", "NO")
    }.issubset(values)
    contradicted = any(
        (scope, "YES") in values and (scope, "NO") in values
        for scope in ("PETROL", "GAS", "COMBINED")
    ) or (positive and ("COMBINED", "NO") in values) or (
        ("COMBINED", "YES") in values and negative
    )
    return {
        "value": "YES" if positive else "NO" if negative else "UNKNOWN",
        "needsReview": contradicted,
        "assertionCount": len(assertions),
    }


def validate_snapshot(snapshot: Any, evidence_root: Path | None = None) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(snapshot, dict):
        return {"status": "error", "errors": ["snapshot must be an object"], "inputModified": False}
    if snapshot.get("formatVersion") != "SourceFactsSnapshotV1":
        errors.append("formatVersion must be SourceFactsSnapshotV1")
    observations = snapshot.get("observations")
    if not isinstance(observations, list) or not observations:
        errors.append("observations must be a nonempty array")
        observations = []
    seen: set[str] = set()
    station: dict[str, list[dict[str, Any]]] = {}
    for number, item in enumerate(observations, 1):
        label = f"observation {number}"
        if not isinstance(item, dict):
            errors.append(f"{label} must be an object")
            continue
        observation_id = item.get("observationId")
        if not isinstance(observation_id, str) or not observation_id.strip() or observation_id in seen:
            errors.append(f"{label} requires a unique observationId")
        else:
            seen.add(observation_id)
        project = item.get("projectNo", "")
        if not isinstance(project, str) or not PROJECT_NO.fullmatch(project):
            errors.append(f"{label} projectNo is invalid")
        if not isinstance(item.get("rwid"), str) or not item["rwid"].strip():
            errors.append(f"{label} rwid is required")
        level = item.get("level")
        if not isinstance(level, str) or level not in LEVELS:
            errors.append(f"{label} level is invalid")
        required = []
        if isinstance(level, str) and level in {"TASK", "INSPECTION", "PRODUCT"}:
            required += ["taskKind", "taskId"]
        if isinstance(level, str) and level in {"INSPECTION", "PRODUCT"}:
            required += ["inspectionRef"]
        if level == "PRODUCT":
            required += ["productRef"]
        for key in required:
            if not isinstance(item.get(key), str) or not item[key].strip():
                errors.append(f"{label} {key} is required at {level} level")
        provenance = item.get("provenance")
        if not isinstance(provenance, dict):
            errors.append(f"{label} provenance is required")
            continue
        method = provenance.get("method")
        if not isinstance(method, str) or method not in METHODS:
            errors.append(f"{label} provenance method is invalid")
        if not isinstance(provenance.get("entry"), str) or not provenance["entry"].strip():
            errors.append(f"{label} visible source entry is required")
        observed = provenance.get("observedAt")
        if observed is None and method == "LEGACY_VISIBLE_EXPORT":
            warnings.append(f"{label} historical observation time unknown; current source not established")
        else:
            try:
                if datetime.fromisoformat(observed).tzinfo is None:
                    raise ValueError("timezone missing")
            except (TypeError, ValueError):
                errors.append(f"{label} observedAt must have an explicit timezone")
        refs = provenance.get("evidence")
        if not isinstance(refs, list) or not refs:
            errors.append(f"{label} evidence is required")
            refs = []
        for ref in refs:
            if not isinstance(ref, dict):
                errors.append(f"{label} evidence must be an object")
                continue
            relative = ref.get("relativePath")
            digest = ref.get("sha256")
            if not isinstance(relative, str) or not relative or not isinstance(digest, str) or not SHA256.fullmatch(digest):
                errors.append(f"{label} evidence path and SHA-256 are required")
                continue
            path = Path(relative)
            if path.is_absolute() or ".." in path.parts or "\\" in relative or ":" in relative:
                errors.append(f"{label} evidence path must stay within the evidence root")
                continue
            if evidence_root is not None:
                root = evidence_root.resolve()
                resolved = (root / path).resolve()
                if not resolved.is_relative_to(root) or not resolved.is_file():
                    errors.append(f"{label} evidence file is missing or outside the root")
                elif hashlib.sha256(resolved.read_bytes()).hexdigest() != digest:
                    errors.append(f"{label} evidence SHA-256 differs")
        facts = item.get("facts")
        if not isinstance(facts, dict) or set(facts) - RAW_FIELDS or not facts:
            errors.append(f"{label} facts must contain supported raw fields")
        elif any(not isinstance(value, str) or not value.strip() for value in facts.values()):
            errors.append(f"{label} observed raw values must be nonempty strings; omit unknown fields")
        assertions = item.get("stationAssertions", [])
        if not isinstance(assertions, list):
            errors.append(f"{label} stationAssertions must be an array")
            continue
        evidence_names = {
            ref.get("relativePath") for ref in refs
            if isinstance(ref, dict) and isinstance(ref.get("relativePath"), str)
        }
        for assertion in assertions:
            if not isinstance(assertion, dict):
                errors.append(f"{label} station assertion must be an object")
                continue
            scope, value, evidence_ref = (
                assertion.get("scope"), assertion.get("value"), assertion.get("evidenceRef")
            )
            valid_scope = isinstance(scope, str) and scope in {"PETROL", "GAS", "COMBINED"}
            valid_value = isinstance(value, str) and value in {"YES", "NO", "UNKNOWN"}
            valid_evidence = isinstance(evidence_ref, str) and evidence_ref in evidence_names
            if not (valid_scope and valid_value and valid_evidence):
                errors.append(f"{label} station assertion requires a valid scope, tri-state and bound evidenceRef")
            elif isinstance(project, str) and PROJECT_NO.fullmatch(project):
                station.setdefault(project, []).append(assertion)
    return {
        "status": "error" if errors else "ok",
        "observationCount": len(observations),
        "errors": errors,
        "warnings": warnings,
        "stationDecisions": {project: resolve_station(items) for project, items in station.items()},
        "inputModified": False,
        "sourceTruthVerified": False,
        "cloudWritten": False,
    }


STORE_FILE = "source-facts.json"
STORE_VERSION = "SourceFactsStoreV1"


class SourceFactsConflict(ValueError):
    """An observation id is already stored with different content, or crosses projects."""


def empty_store() -> dict[str, Any]:
    """Three separate layers. Only sourceFacts is ever written from a source snapshot;
    manualVerification and internalManagement are preserved byte-for-byte by every merge."""
    return {
        "storeVersion": STORE_VERSION,
        "sourceFacts": {"observations": []},
        "manualVerification": {},
        "internalManagement": {},
    }


def merge_snapshot(store: dict[str, Any] | None, snapshot: dict[str, Any], project: str,
                   ingested_at: str) -> tuple[dict[str, Any], dict[str, int]]:
    """Append-only merge of validated observations for one project.

    Same observationId with identical content is reused; different content, or an observation
    of another project, is refused so a later task state never overwrites an earlier one.
    """
    current = store if store is not None else empty_store()
    if current.get("storeVersion") != STORE_VERSION or not isinstance(
        current.get("sourceFacts", {}).get("observations"), list
    ):
        raise SourceFactsConflict("existing source-facts store is not SourceFactsStoreV1")
    known = {item["observationId"]: item for item in current["sourceFacts"]["observations"]}
    added: list[dict[str, Any]] = []
    reused = 0
    for item in snapshot["observations"]:
        if item["projectNo"] != project:
            raise SourceFactsConflict(
                f"observation {item['observationId']} belongs to {item['projectNo']}, not {project}"
            )
        if item["observationId"] in known:
            if known[item["observationId"]].get("observation") != item:
                raise SourceFactsConflict(f"observationId {item['observationId']} already stored differently")
            reused += 1
            continue
        added.append({"observationId": item["observationId"], "ingestedAt": ingested_at, "observation": item})
    merged = {
        **current,
        "sourceFacts": {"observations": [*current["sourceFacts"]["observations"], *added]},
    }
    return merged, {"added": len(added), "reused": reused}


def station_activity(store: dict[str, Any] | None) -> dict[str, Any]:
    """Unified station field: YES/NO/UNKNOWN from evidenced assertions only; history is kept."""
    observations = ((store or {}).get("sourceFacts") or {}).get("observations") or []
    assertions: list[dict[str, Any]] = []
    history: list[dict[str, Any]] = []
    for entry in observations:
        item = entry.get("observation") or {}
        for assertion in item.get("stationAssertions") or []:
            assertions.append(assertion)
            history.append({
                "observationId": entry.get("observationId"),
                "observedAt": (item.get("provenance") or {}).get("observedAt"),
                "scope": assertion.get("scope"),
                "value": assertion.get("value"),
                "evidenceRef": assertion.get("evidenceRef"),
            })
    return {**resolve_station(assertions), "history": history}
