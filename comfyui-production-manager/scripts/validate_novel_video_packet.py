#!/usr/bin/env python3
"""Validate the minimum structure of a novel-video preproduction packet."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


REQUIRED_FILES: dict[str, tuple[str, ...]] = {
    "00-README.md": ("原始来源", "使用顺序", "版本边界"),
    "01-source-facts.md": ("原始来源定位", "事实账", "改编决策"),
    "02-cast-dialogue.md": ("角色出场表", "对白与声音线索", "未命名群像规则"),
    "03-scenes-beats.md": ("场次", "节拍"),
    "04-shot-contracts.md": ("镜头", "剪辑时长", "禁止默认化"),
    "05-asset-matrix.md": ("资产矩阵", "必须覆盖的类别"),
    "06-h3-audio-plan.md": ("工作流核实", "镜头参考映射", "声音分轨"),
}


def examine_packet(packet_root: Path, strict: bool) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    shot_text = ""

    for filename, markers in REQUIRED_FILES.items():
        path = packet_root / filename
        if not path.is_file():
            errors.append(f"缺少文件：{filename}")
            continue
        content = path.read_text(encoding="utf-8")
        for marker in markers:
            if marker not in content:
                errors.append(f"{filename} 缺少必要栏目：{marker}")
        if "待填" in content or "TODO" in content:
            message = f"{filename} 仍含模板占位项。"
            if strict:
                errors.append(message)
            else:
                warnings.append(message)
        if filename == "04-shot-contracts.md":
            shot_text = content

    durations = []
    for line in shot_text.splitlines():
        if line.lstrip().startswith("| SH-"):
            durations.extend(
                float(item)
                for item in re.findall(r"(\d+(?:\.\d+)?)\s*(?:秒|s)", line)
            )
    if len(durations) >= 3 and len(set(durations)) == 1:
        warnings.append("镜头表中至少三个可识别时长完全一致；请确认不是固定时长模板。")
    elif len(durations) >= 5 and len(set(durations)) <= 2:
        warnings.append("镜头时长变化很少；请逐镜头补充叙事时长依据。")

    source_path = packet_root / "01-source-facts.md"
    if source_path.is_file():
        content = source_path.read_text(encoding="utf-8")
        if not re.search(r"(?:L\d+|第.+?[章节]|source_line)", content):
            warnings.append("事实账没有发现明显的章节或行号定位；请确保原作事实可回溯。")

    return errors, warnings


def main() -> int:
    parser = argparse.ArgumentParser(description="检查小说视频前期生产包的最低结构。")
    parser.add_argument("packet_dir", help="生产包目录")
    parser.add_argument("--strict", action="store_true", help="将待填/待确认项视为错误")
    args = parser.parse_args()

    packet_root = Path(args.packet_dir).expanduser()
    if not packet_root.is_dir():
        print("[错误] 生产包目录不存在或不是目录。", file=sys.stderr)
        return 2

    errors, warnings = examine_packet(packet_root, args.strict)
    for message in warnings:
        print(f"[提示] {message}")
    for message in errors:
        print(f"[错误] {message}", file=sys.stderr)

    if errors:
        return 1
    print("[通过] 生产包具备最低结构。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
