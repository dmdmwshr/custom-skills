#!/usr/bin/env python3
"""Create a non-overwriting novel-video preproduction packet in an existing project."""

from __future__ import annotations

import argparse
import os
import stat
import sys
from pathlib import Path
from textwrap import dedent


FILE_ATTRIBUTE_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)


def is_reparse_point(path: Path) -> bool:
    """Return True for a symbolic link, junction, or other Windows reparse point."""
    try:
        result = os.lstat(path)
    except FileNotFoundError:
        return False
    attributes = getattr(result, "st_file_attributes", 0)
    return path.is_symlink() or bool(attributes & FILE_ATTRIBUTE_REPARSE_POINT)


def validate_relative_path(value: str) -> Path:
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts or not candidate.parts:
        raise ValueError("生产包目录必须是项目目录内的相对路径，且不能包含 '..'。")
    return candidate


def ensure_safe_target(project_root: Path, relative_path: Path) -> Path:
    if is_reparse_point(project_root):
        raise ValueError("项目根目录是重解析点，拒绝写入。")

    target = project_root / relative_path
    current = project_root
    for part in relative_path.parts:
        current = current / part
        if current.exists() and is_reparse_point(current):
            raise ValueError(f"目标路径包含重解析点，拒绝写入：{current}")

    root_resolved = project_root.resolve()
    target_parent = target.parent.resolve()
    try:
        target_parent.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError("目标路径越出项目根目录，拒绝写入。") from exc
    return target


def build_files(title: str, source: str) -> dict[str, str]:
    return {
        "00-README.md": dedent(
            f"""\
            # {title}｜小说视频前期生产包

            - 原始来源：{source}
            - 当前版本：v01
            - 状态：策划中；未授权启动 ComfyUI、下载模型或批量生成。

            ## 使用顺序

            1. 完成 `01-source-facts.md`，再做角色、对白与场次。
            2. 完成资产矩阵和镜头合同，确认后才生成关键帧。
            3. H3 仅使用已登记的参考资产；声音、剪辑和验收按 `06-h3-audio-plan.md` 执行。

            ## 版本边界

            本生产包必须写清“概念预告、短篇章或完整章节版”。未确认的事实使用“待确认”，不得用生成设计替代原作事实。
            """
        ),
        "01-source-facts.md": dedent(
            f"""\
            # 章节事实与改编边界

            ## 原始来源定位

            - 来源：{source}
            - 章节/片段：待填
            - 文件哈希或清单定位：待填
            - 改编版本目标：待填

            ## 事实账

            | 事实 ID | 来源定位 | 类别 | 确认内容 | 视觉/声音影响 | 确定性 |
            |---|---|---|---|---|---|
            | F-001 | 待填 | 待填 | 待填 | 待填 | 待确认 |

            ## 改编决策

            | 改编 ID | 对应事实 | 决策 | 原因 | 影响 | 审核状态 |
            |---|---|---|---|---|---|
            | ADP-001 | 待填 | 待填 | 待填 | 待填 | 待审核 |
            """
        ),
        "02-cast-dialogue.md": dedent(
            """\
            # 角色、群像与对白

            ## 角色出场表

            | 角色 ID | 原作名称/身份 | 首次出场 | 退场 | 当前状态 | 视觉需求 | 声音需求 | 证据 |
            |---|---|---|---|---|---|---|---|
            | CHR-001 | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 | F-001 |

            ## 对白与声音线索

            | 台词 ID | 场次/节拍 | 说话者 | 原文定位 | 原文功能 | 改编文本或保留策略 | 口型 | 音轨 | 关联镜头 |
            |---|---|---|---|---|---|---|---|---|
            | D-001 | 待填 | CHR-001 | 待填 | 待填 | 待填 | 待填 | 对白 | 待填 |

            ## 未命名群像规则

            原文未命名的说话者使用稳定群像 ID；不得补造姓名、家族、外貌或关系。
            """
        ),
        "03-scenes-beats.md": dedent(
            """\
            # 场次与节拍

            | 场次 ID | 时间/地点 | 人物 | 场次任务 | 节拍 ID | 节拍变化 | 关联对白/资产 |
            |---|---|---|---|---|---|---|
            | SCN-001 | 待填 | 待填 | 待填 | B-001 | 待填 | 待填 |

            ## 节拍规则

            节拍可以跨多个剪辑镜头；一个镜头也可以承载多个微小动作。不要用场次数或节拍数直接决定镜头数。
            """
        ),
        "04-shot-contracts.md": dedent(
            """\
            # 可变时长镜头合同

            | 镜头 ID | 场次/节拍 | 剪辑时长 | 时长依据 | 入/出点 | 画面与动作 | 镜头计划 | 声音/对白 | 生成策略 | 验收 |
            |---|---|---:|---|---|---|---|---|---|---|
            | SH-001 | SCN-001/B-001 | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 |

            ## 禁止默认化

            不以固定 5 秒、固定 4–7 镜头或模型单段时长代替剪辑理由。剪辑时长与生成片段长度分别记录。
            """
        ),
        "05-asset-matrix.md": dedent(
            """\
            # 资产矩阵与生产优先级

            | 资产 ID | 类别 | 用途/镜头 | 必要状态与细节 | 参考来源 | 生产方式 | 优先级 | 状态 |
            |---|---|---|---|---|---|---|---|
            | AST-001 | 待填 | 待填 | 待填 | 待填 | 待填 | P0 | 待确认 |

            ## 必须覆盖的类别

            角色、服装状态、场景空间、核心道具、群像、特效/氛围、环境声、对白声线、音效、音乐。
            """
        ),
        "06-h3-audio-plan.md": dedent(
            """\
            # MiniMax H3 参考与声音计划

            ## 工作流核实

            - 工作流：待填
            - 当前节点支持的图像参考：待核实
            - 当前节点支持的视频参考：待核实
            - 当前节点支持的音频参考：待核实
            - 图像/视频/音频引用标签：待核实

            ## 镜头参考映射

            | 镜头 ID | 图像参考 | 视频参考 | 音频参考 | 提示词变化 | 必须保持项 | 测试层级 |
            |---|---|---|---|---|---|---|
            | SH-001 | 待填 | 待填 | 待填 | 待填 | 待填 | 身份测试 |

            ## 声音分轨

            | 声音 ID | 类别 | 来源/许可 | 入点 | 出点 | 关联镜头 | 混音说明 | 状态 |
            |---|---|---|---|---|---|---|---|
            | AUD-001 | 环境 | 待填 | 待填 | 待填 | 待填 | 待填 | 待确认 |
            """
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="在已有项目中创建不会覆盖既有文件的小说视频前期生产包。"
    )
    parser.add_argument("project_dir", help="已有项目目录的绝对或相对路径")
    parser.add_argument(
        "--packet-dir",
        default="brief/preproduction",
        help="项目内的相对生产包目录，默认 brief/preproduction",
    )
    parser.add_argument("--title", default="未命名项目", help="写入生产包标题")
    parser.add_argument("--source", default="待填", help="写入原始来源说明")
    parser.add_argument("--dry-run", action="store_true", help="仅显示将创建的文件")
    args = parser.parse_args()

    project_root = Path(args.project_dir).expanduser()
    if not project_root.exists() or not project_root.is_dir():
        print("[错误] 项目目录不存在或不是目录。", file=sys.stderr)
        return 2

    try:
        packet_rel = validate_relative_path(args.packet_dir)
        project_root = project_root.resolve()
        packet_root = ensure_safe_target(project_root, packet_rel)
    except ValueError as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return 2

    files = build_files(args.title, args.source)
    destinations = [packet_root / name for name in files]
    if packet_root.exists():
        print("[错误] 生产包目录已存在；为防止覆盖，未创建任何文件。", file=sys.stderr)
        return 3
    existing = [path for path in destinations if path.exists()]
    if existing:
        print("[错误] 以下文件已存在；为防止覆盖，未创建任何文件：", file=sys.stderr)
        for path in existing:
            print(f"  - {path}", file=sys.stderr)
        return 3

    if args.dry_run:
        print("[预演] 将创建以下文件：")
        for path in destinations:
            print(f"  - {path}")
        return 0

    packet_root.mkdir(parents=True, exist_ok=False)
    try:
        for name, content in files.items():
            destination = packet_root / name
            destination.write_text(content, encoding="utf-8", newline="\n")
    except Exception as exc:  # noqa: BLE001 - print an actionable local error
        print(f"[错误] 创建生产包时失败：{exc}", file=sys.stderr)
        return 4

    print(f"[完成] 已创建生产包：{packet_root}")
    for path in destinations:
        print(f"  - {path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
