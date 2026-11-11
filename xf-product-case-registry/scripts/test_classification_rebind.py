"""source rebind-retained-observation：分类证据被同案后续采集追加改写后的显式新绑定。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import scripts.workspace_state as ws
from scripts.test_local_amendment import no_network  # noqa: F401  (autouse fixture)
from scripts.test_local_completion import PROJECT, Case

RWID = "32100805558"
BOUND_AT = "2026-08-27T15:27:19+08:00"
STALE = "sha256:" + "d3" * 32
QUALIFIED_FIELDS = {
    "检查情况": "已结案[合格]",
    "检查产品信息": [
        {
            "产品质量现场检查情况": "未发现不合格现象",
            "市场准入检查情况": "未发现不合格现象",
            "检查结果": "合格",
        }
    ],
}


@pytest.fixture
def case(tmp_path: Path) -> Case:
    return Case(tmp_path)


def out(capsys: pytest.CaptureFixture[str]) -> dict:
    lines = capsys.readouterr().out.strip().splitlines()
    return json.loads(lines[-1]) if lines else {}


def extended_evidence(
    case: Case,
    *,
    retained_at: str = BOUND_AT,
    retained_fields: dict | None = None,
    package: str | None = None,
    keep_previous: bool = True,
    observed_at: str = BOUND_AT,
) -> Path:
    previous = {
        "capturedAt": retained_at,
        "fingerprint": "sha256:" + "a1" * 32,
        "fields": QUALIFIED_FIELDS if retained_fields is None else retained_fields,
        "projectInspectionStages": ["INITIAL"],
        "projectNo": PROJECT,
        "tags": ["初查", "合格", "现场检查"],
        "screenshot": {"sha256": "sha256:" + "b2" * 32},
    }
    current = {
        "projectNo": PROJECT,
        "capturedAt": "2026-11-08T21:13:25+08:00",
        "fingerprint": "sha256:" + "c3" * 32,
        "fields": QUALIFIED_FIELDS,
        "projectInspectionStages": [],
        "tags": ["合格", "现场检查"],
        "package": {"sha256": package or case.zip_sha},
        "previousObservations": [previous] if keep_previous else [],
    }
    evidence = {
        "schemaVersion": "SourceEvidenceV1",
        "projectNo": PROJECT,
        "batchIds": ["annual-1", "later-2"],
        "updatedAt": "2026-11-08T21:13:25+08:00",
        "records": {RWID: current},
    }
    path = case.pending / "source-evidence.json"
    path.write_text(json.dumps(evidence, ensure_ascii=False), encoding="utf-8")
    ws.upsert_case(
        case.layout,
        PROJECT,
        source={
            "rwid": RWID,
            "classification": {
                "initialResult": "QUALIFIED",
                "initialInspectionDate": None,
                "statisticsYear": None,
                "reinspectionPending": False,
                "evidencePath": path.relative_to(case.layout.root).as_posix(),
                "evidenceSha256": STALE,
                "observedAt": observed_at,
            },
        },
    )
    return path


ARGS = ("source", "rebind-retained-observation", "--project", PROJECT)


def test_extended_evidence_with_retained_observation_rebinds_then_archives(
    case: Case, capsys: pytest.CaptureFixture[str]
) -> None:
    path = extended_evidence(case)
    case.organize()
    capsys.readouterr()
    assert case.run("local-verify", "--project", PROJECT) == 2  # archive refuses the stale digest
    assert "分类证据摘要不一致" in capsys.readouterr().err
    assert case.run(*ARGS, "--reason", "后续采集追加，早期观察保留") == 0
    assert out(capsys)["status"] == "REBOUND_RETAINED_OBSERVATION"
    classification = case.record()["source"]["classification"]
    assert classification["evidenceSha256"] == STALE  # the recorded digest is never rewritten
    replacement = classification["evidenceReplacement"]
    assert replacement["supersededEvidenceState"] == "DIGEST_MISMATCH"
    assert replacement["retainedObservation"]["capturedAt"] == BOUND_AT
    assert replacement["currentObservation"]["projectInspectionStages"] == []
    assert (
        replacement["evidenceSha256"] == "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    )
    assert case.run(*ARGS, "--reason", "后续采集追加，早期观察保留") == 0
    assert out(capsys)["status"] == "REUSED"
    assert case.run("local-verify", "--project", PROJECT) == 0
    assert out(capsys)["status"] == "COMPLETED"
    archived = case.record()["source"]["classification"]
    assert archived["evidenceSha256"] == STALE
    assert archived["evidenceReplacement"]["status"] == "SUPERSEDED_OBSERVATION_RETAINED"
    moved = case.layout.root / archived["evidencePath"]
    assert moved.is_file()
    assert (
        "sha256:" + hashlib.sha256(moved.read_bytes()).hexdigest()
        == archived["evidenceReplacement"]["evidenceSha256"]
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"retained_at": "2026-08-28T00:00:00+08:00"},
        {"keep_previous": False},
        {"package": "sha256:" + "ee" * 32},
        {"retained_fields": {"检查情况": "已结案[不合格]"}},
        {"observed_at": "2026-08-30T00:00:00+08:00"},
    ],
    ids=["other-time", "no-trail", "other-zip", "not-reproducible", "unbound-time"],
)
def test_rebind_refused_without_a_complete_change_trail(
    case: Case, capsys: pytest.CaptureFixture[str], kwargs: dict
) -> None:
    extended_evidence(case, **kwargs)
    case.organize()
    capsys.readouterr()
    assert case.run(*ARGS, "--reason", "x") == 2
    assert "evidenceReplacement" not in case.record()["source"]["classification"]


def test_rebind_refused_when_evidence_is_intact_or_blank_reason(
    case: Case, capsys: pytest.CaptureFixture[str]
) -> None:
    path = extended_evidence(case)
    sha = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    ws.upsert_case(
        case.layout,
        PROJECT,
        source={"classification": {"evidenceSha256": sha}},
    )
    assert case.run(*ARGS, "--reason", "x") == 2
    assert "无需重新绑定" in capsys.readouterr().err
    assert case.run(*ARGS, "--reason", " ") == 2
