"""Recheck rounds of one case manifest (2026-11-17, a32).

``recheckInspection`` is round 1 and keeps its old keys (``<项目编号>|复查`` / ``<项目编号>|复查|<序号>``). Round 2 and later live in the
optional ``laterRecheckInspections`` array, each item a normal recheck inspection plus an integer ``round`` (2, 3, ...). Their keys carry the
round: ``<项目编号>|复查2`` / ``<项目编号>|复查2|<序号>``, which is the shape ``feishu_stats`` already reads (the cloud 检查序次 field holds the
round). A manifest without the array reads exactly as before. A round is dated by its own ``inspectionDate``; the latest round decides.

Standard library only.
"""

from __future__ import annotations

from typing import Any

LATER_KEY = "laterRecheckInspections"
FIRST_KEY = "recheckInspection"


def stage_label(round_no: int) -> str:
    """Display stage inside keys: 复查 for round 1 (old keys), 复查2/复查3 for later rounds."""
    return "复查" if round_no == 1 else f"复查{round_no}"


def rounds(manifest: dict[str, Any] | None) -> list[tuple[int, str, Any]]:
    """[(round, path in manifest, inspection)] ascending by round; round 1 first when present. Never invents a round."""
    manifest = manifest or {}
    out: list[tuple[int, str, Any]] = []
    if manifest.get(FIRST_KEY) is not None:
        out.append((1, FIRST_KEY, manifest[FIRST_KEY]))
    later = manifest.get(LATER_KEY)
    if isinstance(later, list):
        found = []
        for index, item in enumerate(later):
            number = item.get("round") if isinstance(item, dict) else None
            if type(number) is int and number >= 2:
                found.append((number, f"{LATER_KEY}[{index}]", item))
        out.extend(sorted(found, key=lambda entry: entry[0]))
    return out


def round_products(manifest: dict[str, Any] | None) -> list[tuple[int, dict[str, Any]]]:
    """Every recheck product (dict) with its round, rounds ascending, products in manifest order."""
    return [(number, p) for number, _path, block in rounds(manifest) if isinstance(block, dict)
            for p in block.get("products") or [] if isinstance(p, dict)]


def check(manifest: dict[str, Any] | None) -> list[str]:
    """Structural problems of the later rounds (round numbers contiguous from 2, round 1 present, dates not going backwards)."""
    manifest = manifest or {}
    later = manifest.get(LATER_KEY)
    if later is None:
        return []
    if not isinstance(later, list):
        return [f"{LATER_KEY} 必须是数组"]
    problems: list[str] = []
    numbers: list[int] = []
    for index, item in enumerate(later):
        number = item.get("round") if isinstance(item, dict) else None
        if type(number) is not int or number < 2:
            problems.append(f"{LATER_KEY}[{index}].round 必须是不小于 2 的整数（第 1 轮是 {FIRST_KEY}）")
        else:
            numbers.append(number)
    if len(set(numbers)) != len(numbers):
        problems.append(f"{LATER_KEY} 的 round 不得重复")
    if numbers and manifest.get(FIRST_KEY) is None:
        problems.append(f"存在第 2 轮及以后复查但缺少第 1 轮 {FIRST_KEY}")
    if numbers and sorted(set(numbers)) != list(range(2, 2 + len(set(numbers)))):
        problems.append(f"{LATER_KEY} 的轮次必须从 2 起连续，不留空轮")
    previous = ""
    for number, _path, block in rounds(manifest):
        day = str((block or {}).get("inspectionDate") or "") if isinstance(block, dict) else ""
        if day and previous and day < previous:
            problems.append(f"第 {number} 轮复查日 {day} 早于上一轮 {previous}")
        previous = day or previous
    return problems
