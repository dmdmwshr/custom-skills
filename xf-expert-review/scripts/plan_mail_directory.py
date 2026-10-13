#!/usr/bin/env python3
"""Read-only naming plan; project membership must be verified by the caller."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys

NUMBER = re.compile(r"^(0\.)?([1-9][0-9]*)\.([1-9][0-9]*)、")
INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def component(value: str) -> str:
    if not isinstance(value, str) or not value or INVALID.search(value):
        raise ValueError("名称不能为空，也不能含 Windows 路径分隔符或非法字符")
    if value.endswith((" ", ".")) or len(value.encode("utf-16-le")) // 2 > 255:
        raise ValueError("名称过长或以空格、句点结尾；请在保留信息的前提下核实名称")
    return value


def normalized_name(value: str) -> str:
    return re.sub(r"[ \u3000]", "", value)


def numbered(name: str) -> tuple[int, int] | None:
    match = NUMBER.match(name)
    return (int(match[2]), int(match[3])) if match else None


def tail_groups(name: str, stop_suffixes: tuple[str, ...]) -> list[str]:
    groups = []
    while name and not name.endswith(stop_suffixes) and name[-1] in "）)":
        closer = name[-1]
        opener = "（" if closer == "）" else "("
        depth = 0
        for index in range(len(name) - 1, -1, -1):
            if name[index] == closer:
                depth += 1
            elif name[index] == opener:
                depth -= 1
                if depth == 0:
                    groups.insert(0, name[index + 1:-1])
                    name = name[:index]
                    break
        else:
            raise ValueError("原名尾部括号不成对，不能自动提取备注")
    return groups


def make_plan(selection: dict, existing: list[str]) -> dict:
    project = component(selection["project_name"])
    stage = component(selection["stage"])
    raw_experts = selection["experts"]
    if not isinstance(raw_experts, list) or not raw_experts:
        raise ValueError("需要本次已核实的劳务专家名单")
    experts = []
    for value in raw_experts:
        name = component(normalized_name(value))
        if re.search(r"[、,，;；（）()]", name):
            raise ValueError("专家名单必须逐人提供")
        if name not in experts:
            experts.append(name)
    current = selection.get("current_name")
    source = selection["source_name"]
    component(source)
    if current is not None and current not in existing:
        raise ValueError("当前邮件目录不存在；先核对稳定编号和最新资料索引")
    members = selection["project_members"]
    if not isinstance(members, list):
        raise ValueError("同项目成员必须是已核实目录清单")
    member_numbers = []
    for name in dict.fromkeys(members):
        if name not in existing or numbered(name) is None:
            raise ValueError("同项目成员不存在或没有可识别序号")
        member_numbers.append(numbered(name))
    current_number = numbered(current) if current else None
    families = {n[0] for n in member_numbers}
    if current_number:
        families.add(current_number[0])
    if len(families) > 1:
        raise ValueError("同项目成员分属不同项目序号；不能自动合并")
    all_numbers = [numbered(name) for name in existing if numbered(name)]
    family = next(iter(families)) if families else max((n[0] for n in all_numbers), default=0) + 1
    occupied = [n for n in all_numbers if n[0] == family]
    if any(count > 1 for count in Counter(occupied).values()):
        raise ValueError("该项目已有重复评审序号，包含已发送项；先核对")
    sequence = current_number[1] if current_number else max((n[1] for n in occupied), default=0) + 1
    prefix = "0." if current and NUMBER.match(current) and NUMBER.match(current)[1] else ""
    remarks = []
    sent = False
    for old_name in dict.fromkeys([source, current or source]):
        for text in tail_groups(old_name, (project, stage)):
            if text == "已发送":
                sent = True
            elif set(map(normalized_name, re.split(r"[、,，]", text))) == set(experts):
                continue
            elif text and text not in remarks:
                remarks.append(text)
    extra_remarks = selection.get("remarks", [])
    if not isinstance(extra_remarks, list):
        raise ValueError("补充备注必须是文本列表")
    for text in extra_remarks:
        component(text)
        if text not in remarks:
            remarks.append(text)
    suffix = "".join(f"（{text}）" for text in remarks)
    if sent:
        suffix += "（已发送）"
    target = component(f"{prefix}{family}.{sequence}、{project}-{stage}（{'、'.join(experts)}）{suffix}")
    if any(name.casefold() == target.casefold() and name != current for name in existing):
        raise ValueError("目标已存在；先认领同次评审，不能新建或覆盖")
    body = NUMBER.sub("", target, count=1).casefold()
    if any(NUMBER.sub("", name, count=1).casefold() == body and name != current for name in existing):
        raise ValueError("相同名称的评审已经存在；核对后用 current_name 复用")
    return {
        "review_id": selection.get("review_id"),
        "project_number": family,
        "review_number": sequence,
        "current_name": current,
        "target_name": target,
        "expert_count": len(experts),
        "preserved_remarks": remarks,
        "preserved_sent_mark": sent,
        "rename_needed": current != target,
        "read_only": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mail-root", required=True, type=Path)
    parser.add_argument("--selection", required=True, type=Path)
    args = parser.parse_args()
    try:
        root = args.mail_root.resolve(strict=True)
        if not root.is_dir():
            raise ValueError("待发邮箱入口不是目录")
        selection = json.loads(args.selection.read_text(encoding="utf-8-sig"))
        # Include files too, so a same-named file cannot be mistaken for a free target.
        entries = list(root.iterdir())
        directories = [p.name for p in entries if p.is_dir()]
        plan = make_plan(selection, directories)
        for entry in entries:
            if entry.name.casefold() == plan["target_name"].casefold() and not entry.is_dir():
                raise ValueError("目标名称已被文件占用")
        plan["target_path"] = str(root / plan["target_name"])
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, KeyError, TypeError, OSError) as exc:
        # Do not echo selection contents or document rows on failure.
        message = str(exc) if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError) else type(exc).__name__
        print(json.dumps({"error": message, "read_only": True}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
