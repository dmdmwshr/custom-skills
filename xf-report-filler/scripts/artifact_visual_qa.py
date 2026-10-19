from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import monthly_workflow


SCHEMA = "xf-report-visual-qa/v1"
MAX_JSON_BYTES = 1024 * 1024
WORD_TYPES = {"doc", "docx"}
EXCEL_TYPES = {"xls", "xlsx"}
SUPPORTED_TYPES = WORD_TYPES | EXCEL_TYPES
QA_RULES = monthly_workflow.load_config().get('visual_qa_rules', {})
RULE_SNAPSHOT_SUFFIXES = set(QA_RULES.get('rule_snapshot_suffixes', ['.json', '.js', '.py', '.md', '.yaml', '.yml']))


class VisualQaError(RuntimeError):
    pass


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _resolve(path_text: str) -> Path:
    if not path_text or not path_text.strip():
        raise VisualQaError("路径不能为空。")
    try:
        return Path(path_text).expanduser().resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise VisualQaError(f"路径无效：{path_text}（{exc}）") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _snapshot(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise VisualQaError(f"文件不存在或不是普通文件：{path}")
    stat = path.stat()
    return {
        "path": str(path),
        "bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": _sha256(path),
    }


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise VisualQaError(f"JSON 文件不存在：{path}")
    raw = path.read_bytes()
    if len(raw) > MAX_JSON_BYTES:
        raise VisualQaError(f"JSON 超过 {MAX_JSON_BYTES} 字节限制：{path}")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VisualQaError(f"JSON 不是有效 UTF-8 对象：{path}（{exc}）") from exc
    if not isinstance(value, dict):
        raise VisualQaError(f"JSON 顶层必须是对象：{path}")
    return value


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def create_baseline(
    *,
    artifact_text: str,
    artifact_type: str,
    source_texts: Sequence[str],
    receipt_text: str,
    rule_source_texts: Sequence[str] = (),
) -> dict[str, Any]:
    normalized_type = artifact_type.lower().lstrip(".")
    if normalized_type not in SUPPORTED_TYPES:
        raise VisualQaError(f"不支持的成品类型：{artifact_type}")
    artifact = _resolve(artifact_text)
    receipt = _resolve(receipt_text)
    if artifact.suffix.lower() != f".{normalized_type}":
        raise VisualQaError("成品扩展名与 --artifact-type 不一致。")
    if receipt.exists():
        raise VisualQaError("视觉收据已存在；不得覆盖旧基线，请使用新的收据路径。")
    if not source_texts:
        raise VisualQaError("至少需要一个 --source。")

    sources = [_resolve(value) for value in source_texts]
    rules = [_resolve(value) for value in rule_source_texts]
    if len(set(sources + rules)) != len(sources + rules):
        raise VisualQaError("--source 与 --rule-source 不能重复。")
    protected = {artifact, receipt}
    if any(source in protected for source in sources + rules):
        raise VisualQaError("采用源不能与成品或视觉收据使用同一路径。")
    if any(rule.suffix.lower() not in RULE_SNAPSHOT_SUFFIXES for rule in rules):
        raise VisualQaError('规则快照仅接受配置或规则文本；业务源表和外部Office模板必须使用 --source。')
    source_baseline = [_snapshot(source) for source in sources]
    rule_baseline = [_snapshot(rule) for rule in rules]
    if rules:
        rule_dir = receipt.parent / f'{receipt.stem}.rules'
        try:
            rule_dir.mkdir(parents=True, exist_ok=False)
        except OSError as exc:
            raise VisualQaError(f'规则快照目录已存在或无法创建，保留断点：{rule_dir}') from exc
        for index, (rule, baseline) in enumerate(zip(rules, rule_baseline), 1):
            frozen = rule_dir / f"{index:02d}-{baseline['sha256'][:12]}-{rule.name}"
            shutil.copy2(rule, frozen)
            if _sha256(frozen) != baseline['sha256'] or _sha256(rule) != baseline['sha256']:
                raise VisualQaError(f'冻结期间规则文件发生变化，保留快照及断点：{rule}')
            baseline['snapshot'] = _snapshot(frozen)

    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "status": "baseline_ready",
        "observed_at": _now_utc(),
        "artifact": {"path": str(artifact), "type": normalized_type},
        "source_baseline": source_baseline,
        "rule_baseline": rule_baseline,
        "inspection_hash_required": QA_RULES.get('require_inspection_artifact_hash', True),
        "sources_unchanged": None,
        "outcome": "qa_pending",
        "warnings": [],
        "blockers": [],
        "pending_reasons": ["尚未完成成品生成、全量渲染和视觉检查。"],
    }
    _write_json_atomic(receipt, payload)
    return payload


def _inspection_result(inspection: Mapping[str, Any], artifact_type: str) -> tuple[list[str], list[str]]:
    pending: list[str] = []
    blockers: list[str] = []
    renderer = inspection.get("renderer")
    renderer_version = inspection.get("renderer_version")
    if not isinstance(renderer, str) or not renderer.strip():
        pending.append("inspection.renderer 缺失。")
    if not isinstance(renderer_version, str) or not renderer_version.strip():
        pending.append("inspection.renderer_version 缺失。")

    total_units = inspection.get("total_units")
    checked_units = inspection.get("checked_units")
    if not isinstance(total_units, int) or isinstance(total_units, bool) or total_units < 1:
        pending.append("inspection.total_units 必须是正整数。")
        total_units = 0
    if not isinstance(checked_units, list) or any(
        not isinstance(unit, str) or not unit.strip() for unit in (checked_units or [])
    ):
        pending.append("inspection.checked_units 必须是非空字符串列表。")
        checked_units = []
    if isinstance(checked_units, list):
        normalized_units = [unit.strip() for unit in checked_units if isinstance(unit, str)]
        if len(set(normalized_units)) != len(normalized_units):
            pending.append("inspection.checked_units 存在重复项。")
        if total_units and len(normalized_units) != total_units:
            pending.append("逐页或逐工作表检查数量与 total_units 不一致。")
        if artifact_type in WORD_TYPES and total_units:
            expected = [f"page-{index}" for index in range(1, total_units + 1)]
            if normalized_units != expected:
                pending.append("Word checked_units 必须按 page-1 到 page-N 连续列出。")
        if artifact_type in EXCEL_TYPES and any(
            not (unit.startswith("sheet:") or unit.startswith("print-page:"))
            for unit in normalized_units
        ):
            pending.append("Excel checked_units 必须使用 sheet: 或 print-page: 标识。")

    checks = inspection.get("checks")
    if not isinstance(checks, Mapping) or not checks:
        pending.append("inspection.checks 缺失。")
    else:
        for name, state in checks.items():
            if not isinstance(name, str) or not name.strip():
                pending.append("inspection.checks 含空检查名。")
                continue
            if state == "failed":
                blockers.append(f"视觉检查失败：{name}")
            elif state not in {"passed", "not_applicable"}:
                pending.append(f"视觉检查状态无效：{name}")

    supplied_blockers = inspection.get("blockers", [])
    if not isinstance(supplied_blockers, list) or any(
        not isinstance(item, str) or not item.strip() for item in (supplied_blockers or [])
    ):
        pending.append("inspection.blockers 必须是字符串列表。")
    else:
        blockers.extend(item.strip() for item in supplied_blockers)
    warnings = inspection.get("warnings", [])
    if not isinstance(warnings, list) or any(
        not isinstance(item, str) or not item.strip() for item in (warnings or [])
    ):
        pending.append("inspection.warnings 必须是字符串列表。")
    return pending, blockers


def _inspection_hash_result(inspection, artifact_snapshot, required):
    hashes = [inspection[key] for key in ['artifact_sha256', 'stage_sha256'] if inspection.get(key)]
    if not hashes:
        return (['视觉检查未记录所检查成品的 artifact_sha256。'] if required else []), []
    if any(not isinstance(value, str) for value in hashes):
        return ['视觉检查成品哈希格式无效。'], []
    if isinstance(artifact_snapshot, Mapping) and artifact_snapshot.get('sha256') and any(value.lower() != artifact_snapshot['sha256'] for value in hashes):
        return [], ['当前成品与已完成视觉检查的文件哈希不同，须重新检查实际版本。']
    return [], []


def finalize_receipt(*, receipt_text: str, inspection_text: str) -> dict[str, Any]:
    receipt_path = _resolve(receipt_text)
    inspection_path = _resolve(inspection_text)
    receipt = _load_json(receipt_path)
    if receipt.get("schema") != SCHEMA:
        raise VisualQaError("视觉收据 schema 不匹配。")
    if receipt.get("status") not in {"baseline_ready", "qa_pending"}:
        raise VisualQaError("视觉收据已经终结；不得覆盖已通过或已阻断的结果。")
    artifact_record = receipt.get("artifact")
    baselines = receipt.get("source_baseline")
    if not isinstance(artifact_record, Mapping) or not isinstance(baselines, list):
        raise VisualQaError("视觉收据缺少成品或源基线。")
    artifact_type = str(artifact_record.get("type") or "")
    artifact_path = _resolve(str(artifact_record.get("path") or ""))
    inspection = _load_json(inspection_path)

    pending, blockers = _inspection_result(inspection, artifact_type)
    source_after: list[dict[str, Any]] = []
    sources_unchanged = True
    for baseline in baselines:
        if not isinstance(baseline, Mapping) or not baseline.get("path") or not baseline.get("sha256"):
            pending.append("源基线记录不完整。")
            sources_unchanged = False
            continue
        source_path = _resolve(str(baseline["path"]))
        try:
            current = _snapshot(source_path)
        except VisualQaError as exc:
            blockers.append(str(exc))
            sources_unchanged = False
            continue
        current["unchanged"] = current["sha256"] == baseline["sha256"]
        source_after.append(current)
        if not current["unchanged"]:
            sources_unchanged = False
            blockers.append(f"采用源在生成期间发生变化：{source_path}")

    rule_after = []
    for baseline in receipt.get('rule_baseline', []):
        try:
            current = _snapshot(_resolve(baseline['path']))
            frozen = _snapshot(_resolve(baseline['snapshot']['path']))
            if current['sha256'] != baseline['sha256'] or frozen['sha256'] != baseline['sha256']:
                sources_unchanged = False
                blockers.append(f"采用规则在生成期间发生变化或快照损坏：{baseline['path']}")
            rule_after.append({'original': current, 'snapshot': frozen})
        except (KeyError, VisualQaError) as exc:
            sources_unchanged = False
            blockers.append(f'采用规则基线无效：{exc}')

    artifact_snapshot: dict[str, Any] | None = None
    try:
        artifact_snapshot = _snapshot(artifact_path)
        if artifact_snapshot["bytes"] < 1:
            pending.append("成品为空文件。")
    except VisualQaError as exc:
        pending.append(str(exc))
    hash_pending, hash_blockers = _inspection_hash_result(inspection, artifact_snapshot, receipt.get('inspection_hash_required', False))
    pending.extend(hash_pending)
    blockers.extend(hash_blockers)

    supplied_warnings = inspection.get("warnings", [])
    receipt.update(
        {
            "finalized_at": _now_utc(),
            "artifact": {**dict(artifact_record), **(artifact_snapshot or {})},
            "source_after": source_after,
            "rule_after": rule_after,
            "sources_unchanged": sources_unchanged,
            "inspection": inspection,
            "warnings": list(supplied_warnings) if isinstance(supplied_warnings, list) else [],
            "blockers": sorted(set(blockers)),
            "pending_reasons": sorted(set(pending)),
        }
    )
    if blockers:
        receipt["status"] = "blocked"
        receipt["outcome"] = "blocked"
    elif pending:
        receipt["status"] = "qa_pending"
        receipt["outcome"] = "qa_pending"
    else:
        receipt["status"] = "passed"
        receipt["outcome"] = "passed"
    _write_json_atomic(receipt_path, receipt)
    return receipt


def verify_receipt(*, receipt_text: str) -> dict[str, Any]:
    receipt = _load_json(_resolve(receipt_text))
    reasons: list[str] = []
    if receipt.get("schema") != SCHEMA:
        reasons.append("schema 不匹配。")
    if receipt.get("outcome") != "passed" or receipt.get("status") != "passed":
        reasons.append("视觉收据尚未通过。")
    if receipt.get("sources_unchanged") is not True:
        reasons.append("sources_unchanged 不是 true。")

    artifact = receipt.get("artifact")
    if isinstance(artifact, Mapping) and artifact.get("path") and artifact.get("sha256"):
        try:
            current_artifact = _snapshot(_resolve(str(artifact["path"])))
            if current_artifact["sha256"] != artifact["sha256"]:
                reasons.append("成品哈希已漂移。")
        except VisualQaError as exc:
            reasons.append(str(exc))
    else:
        reasons.append("成品哈希记录缺失。")
    hash_pending, hash_blockers = _inspection_hash_result(receipt.get('inspection', {}), artifact, receipt.get('inspection_hash_required', False))
    reasons.extend(hash_pending + hash_blockers)

    for baseline in receipt.get("source_baseline", []):
        if not isinstance(baseline, Mapping) or not baseline.get("path") or not baseline.get("sha256"):
            reasons.append("源基线记录不完整。")
            continue
        try:
            current_source = _snapshot(_resolve(str(baseline["path"])))
            if current_source["sha256"] != baseline["sha256"]:
                reasons.append(f"采用源哈希已漂移：{baseline['path']}")
        except VisualQaError as exc:
            reasons.append(str(exc))
    for baseline in receipt.get('rule_baseline', []):
        try:
            frozen = _snapshot(_resolve(baseline['snapshot']['path']))
            if frozen['sha256'] != baseline['sha256']:
                reasons.append(f"采用规则快照哈希已漂移：{baseline['snapshot']['path']}")
        except (KeyError, VisualQaError) as exc:
            reasons.append(f'采用规则快照无效：{exc}')
    return {
        "schema": SCHEMA,
        "verified_at": _now_utc(),
        "outcome": "passed" if not reasons else "blocked",
        "reasons": reasons,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="记录并验证消防报告成品的视觉 QA 收据。")
    subparsers = parser.add_subparsers(dest="operation", required=True)

    baseline = subparsers.add_parser("baseline", help="生成前冻结采用源哈希。")
    baseline.add_argument("--artifact", required=True)
    baseline.add_argument("--artifact-type", required=True, choices=sorted(SUPPORTED_TYPES))
    baseline.add_argument("--source", action="append", required=True)
    baseline.add_argument('--rule-source', action='append', default=[], help='冻结实际采用的规则文本；完成后按快照回读，不受后续规则发布影响')
    baseline.add_argument("--receipt", required=True)

    finalize = subparsers.add_parser("finalize", help="生成后核验源哈希并合并全量视觉检查。")
    finalize.add_argument("--receipt", required=True)
    finalize.add_argument("--inspection", required=True)

    verify = subparsers.add_parser("verify", help="只读回读已完成收据和当前哈希。")
    verify.add_argument("--receipt", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.operation == "baseline":
            result = create_baseline(
                artifact_text=args.artifact,
                artifact_type=args.artifact_type,
                source_texts=args.source,
                receipt_text=args.receipt,
                rule_source_texts=args.rule_source,
            )
        elif args.operation == "finalize":
            result = finalize_receipt(receipt_text=args.receipt, inspection_text=args.inspection)
        else:
            result = verify_receipt(receipt_text=args.receipt)
    except VisualQaError as exc:
        print(json.dumps({"outcome": "error", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    outcome = result.get("outcome")
    if outcome == "passed" or args.operation == "baseline":
        return 0
    return 3 if outcome == "qa_pending" else 2


if __name__ == "__main__":
    raise SystemExit(main())
