"""One entry point for the monthly bulletin: plan the month, run the stages, drive the visual QA.

Subcommands
  plan  --year Y --month M   read-only: which mode applies today (pending before the 25th), which inputs exist,
                             what blocks, and the exact next commands for this machine.
  run   --stage organize|grade|root [--apply]   call the existing stage script with the right paths (dry-run by default).
  qa    baseline|template|finalize|verify       freeze the real run inputs before generating, then check the finished files.

Nothing here writes Feishu, schedules a task, logs in anywhere or decides a visual check: ``qa template`` writes every
check as ``unknown`` and ``qa finalize`` refuses to pass until a person (or model) who looked at the rendered pages
has changed them. Run ``plan`` first; it prints what to do next.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import feishu_case_source as source
import host_paths
import monthly_workflow as workflow

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
SHANGHAI = timezone(timedelta(hours=8))
STAGES = {
    "organize": "monthly_file_organizer.py",
    "grade": "monthly_grade_register.py",
    "root": "monthly_bulletin_root.py",
}
BRIGADE_SHORT = ["江阴", "宜兴", "梁溪", "锡山", "惠山", "滨湖", "新吴", "经开"]


def today_shanghai():
    return datetime.now(SHANGHAI).date()


def score_month_of(year, month):
    return (year - 1, 12) if month == 1 else (year, month - 1)


def window_for(year, month):
    """Statistics window of a bulletin month: the 26th of the previous month through the 25th of this month."""
    start_year, start_month = score_month_of(year, month)
    return date(start_year, start_month, 26), date(year, month, 25)


def python_command():
    return sys.executable if os.name == "nt" else "python3"


def interactive_session():
    """True on a Windows user desktop session, False in session 0 (SSH/service), None where it cannot be told."""
    if os.name != "nt":
        return None
    import ctypes

    session = ctypes.c_ulong()
    if not ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session)):
        return None
    return session.value != 0


def host_path(template, year, month=None):
    return host_paths.host_path(template, year, month or 0)


def quote_powershell(command):
    return "& " + " ".join("'" + str(item).replace("'", "''") + "'" for item in command)


def quote_command(command):
    """One copy-pasteable line for this platform: PowerShell call syntax on Windows, shlex quoting elsewhere."""
    if os.name == "nt":
        return quote_powershell(command)
    return shlex.join(str(item) for item in command)


def locate(year, month, args):
    config = workflow.load_config()
    run_cfg = config["monthly_run"]
    score_year, score_month = score_month_of(year, month)
    bulletin_dir = Path(args.bulletin_dir) if getattr(args, "bulletin_dir", None) else host_path(run_cfg["bulletin_root"], year) / run_cfg["bulletin_dir_pattern"].format(yy=str(year)[-2:], month=month, year=year)
    score_dir = bulletin_dir / workflow.score_dir_name(score_month, config)
    snapshot = Path(args.snapshot) if getattr(args, "snapshot", None) else workflow.case_source_path(config)
    roster = Path(args.roster) if getattr(args, "roster", None) else host_path(config["data_sources"]["staff_roster"]["path"], year)
    if getattr(args, "overrides", None):
        overrides = Path(args.overrides)
    else:
        found = host_path(run_cfg["overrides_dir"], year) / run_cfg["overrides_pattern"].format(year=year, month=month)
        overrides = found if found.is_file() else None
    return {"config": config, "bulletin_dir": bulletin_dir, "score_dir": score_dir, "score_year": score_year,
            "score_month": score_month, "snapshot": snapshot, "roster": roster, "overrides": overrides}


def read_info(snapshot):
    path = Path(snapshot) / source.INFO_FILE if snapshot else None
    if not path or not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except ValueError:
        return {"unreadable": True}


def plan(year, month, args, today=None):
    where = locate(year, month, args)
    today = today or (date.fromisoformat(args.today) if getattr(args, "today", None) else today_shanghai())
    start, end = window_for(year, month)
    window_ended = today >= end
    blockers, warnings, checks = [], [], []

    def check(name, path, required=True):
        exists = path is not None and Path(path).exists()
        checks.append({"name": name, "path": str(path) if path else None, "status": "ok" if exists else "missing"})
        if not exists and required:
            blockers.append({"code": "input_missing", "message": f"{name}不存在", "path": str(path) if path else ""})
        return exists

    check("通报月份目录", where["bulletin_dir"])
    check("成绩月份巡查目录", where["score_dir"])
    roster_ok = check("大队执法人员总表", where["roster"])
    snapshot_ok = bool(where["snapshot"]) and Path(where["snapshot"]).is_dir()
    checks.append({"name": "飞书统计快照", "path": str(where["snapshot"]) if where["snapshot"] else None, "status": "ok" if snapshot_ok else "missing"})
    if not snapshot_ok:
        blockers.append({"code": "snapshot_missing", "message": "没有飞书统计快照：按 next 里的 snapshot 命令导出（WSL 直接运行，Windows 经 wsl.exe），或用 --snapshot 指向已导出目录；根层两表和 R02 核对依赖它，成绩月份材料生成不依赖它。"})
    info = read_info(where["snapshot"]) if snapshot_ok else None
    exported_day = None
    if snapshot_ok and info is None:
        warnings.append({"code": "snapshot_info_missing", "message": "快照没有 snapshot.info.json：导出时间和完整性未声明，只能当下限。"})
    elif info and info.get("exportedAt"):
        try:
            exported_day = date.fromisoformat(str(info["exportedAt"])[:10])
        except ValueError:
            warnings.append({"code": "snapshot_exported_at_invalid", "message": "snapshot.info.json 的 exportedAt 无法解析"})
    if where["overrides"]:
        checks.append({"name": "本月显式覆盖文件", "path": str(where["overrides"]), "status": "ok"})
    else:
        warnings.append({"code": "overrides_absent", "message": "没有本月覆盖文件（单位名/条款更正）；只有用户本月明确授权的更正才放在这里，没有则忽略。"})

    mode = "pending"
    final_blockers = []
    if not window_ended:
        final_note = f"今天 {today} 早于统计窗口终点 {end}：根层两表只能待补，final-audit 不可用。"
    else:
        mode = "final_candidate"
        final_note = f"今天 {today} 已到窗口终点 {end}：可以定稿，但须同时满足下面几条。"
        if exported_day is None or exported_day < end:
            final_blockers.append("快照导出时间早于窗口终点：先重新导出（snapshot 命令），再核对。")
        if not (info and info.get("finalDataConfirmed") is True):
            final_blockers.append("用户尚未确认本月数据已最终汇总（snapshot.info.json 的 finalDataConfirmed 须由人确认后改为 true）；不假设飞书与监督系统同步时效。")
        if info and info.get("latestCheckDate"):
            warnings.append({"code": "latest_check_date", "message": f"快照中最新检查日期 {info['latestCheckDate'][:10]}，资料最近更新 {info.get('latestRecordUpdate')}；（可选）需要交叉核对时，可用 Ego 浏览器只读查看监督系统统计页；不是使用飞书快照的前提。"})
    py = python_command()
    me = str(SCRIPTS / "monthly_run.py")
    base = [py, me]
    ym = ["--year", str(year), "--month", str(month)]
    needs_export = not snapshot_ok or (window_ended and (exported_day is None or exported_day < end))
    task_dir = host_paths.host_path(f"D:\\ws\\05-temp\\{today:%Y%m%d}_月通报_{year}{month:02d}")
    planned_snapshot = task_dir / f"snapshot-{today:%Y%m%d}"
    chosen = planned_snapshot if needs_export else where["snapshot"]
    # Every later step carries the context chosen for this run, so the printed commands can be pasted as they are.
    context = ["--bulletin-dir", str(where["bulletin_dir"]), "--snapshot", str(chosen), "--roster", str(where["roster"])]
    if where["overrides"]:
        context += ["--overrides", str(where["overrides"])]
    qa_dir = task_dir / "qa"
    on_windows = os.name == "nt"
    nxt = []

    def step(name, command, note=None, where_to_run="Windows", windows_only=False):
        item = {"step": name, "where": where_to_run}
        if windows_only and not on_windows:
            windows_python = where["config"]["monthly_run"]["windows_python"]
            windows_command = [windows_python, *[host_paths.win_path(part) for part in command[1:]]]
            item["command"] = None
            item["windowsCommand"] = windows_command
            item["shell"] = quote_powershell(windows_command)
            item["note"] = "需要 Word/Excel：在 Windows 桌面 PowerShell 粘贴 shell 这一行（脚本经 \\\\wsl.localhost 读取源仓，路径已转成 Windows 形式）。" + (f" {note}" if note else "")
        else:
            item["command"] = command
            item["shell"] = quote_command(command)
            if note:
                item["note"] = note
        nxt.append(item)

    if needs_export:
        exporter = SCRIPTS / "feishu_snapshot_export.py"
        export_args = ["--year", str(year), "--month", str(month), "--out-dir", host_paths.wsl_path(planned_snapshot)]
        if on_windows:
            command = ["wsl.exe", "-e", "python3", host_paths.wsl_path(exporter), *export_args]
            note = "Windows 没有 lark-cli，经 wsl.exe 调用 WSL 里已授权的官方 CLI（只读，不登录）；输出目录必须是新的，后面各步已使用该目录作 --snapshot。"
        else:
            command = ["python3", str(exporter), *export_args]
            note = "只读导出四表；输出目录必须是新的，后面各步已使用该目录作 --snapshot。"
        step("snapshot", command, note, where_to_run="WSL（Windows 经 wsl.exe）")
    step("organize-dry-run", [*base, "run", "--stage", "organize", *ym, *context], windows_only=True)
    step("organize-apply", [*base, "run", "--stage", "organize", *ym, *context, "--apply"], windows_only=True)
    step("qa-baseline", [*base, "qa", "baseline", *ym, *context, "--qa-dir", str(qa_dir)], "必须在生成之前；冻结实际使用的脚本、配置、模板、名册、快照、覆盖文件。", windows_only=True)
    step("grade-apply", [*base, "run", "--stage", "grade", *ym, *context, "--apply"], "写 Word/Excel，需在用户交互桌面会话。", windows_only=True)
    step("root-apply", [*base, "run", "--stage", "root", *ym, *context, "--apply"], f"模式：{'pending（待补）' if mode == 'pending' else 'pending；定稿另用 root --mode final-audit'}", windows_only=True)
    nxt.append({"step": "qa-render-and-look", "note": "导出 PDF/PNG 逐页看图，把 inspection 里的 unknown 改成 passed/not_applicable/failed，再 qa finalize、qa verify（同一 --qa-dir）。"})
    return {
        "year": year, "month": month, "scoreYear": where["score_year"], "scoreMonth": where["score_month"],
        "today": str(today), "window": [str(start), str(end)], "mode": mode, "note": final_note,
        "finalBlockers": final_blockers, "platform": "windows" if os.name == "nt" else "wsl/linux",
        "interactiveOfficeSession": interactive_session(),
        "paths": {"bulletin_dir": str(where["bulletin_dir"]), "score_dir": str(where["score_dir"]), "snapshot": str(where["snapshot"]) if where["snapshot"] else None, "snapshotForNextSteps": str(chosen), "qa_dir": str(qa_dir),
                  "roster": str(where["roster"]), "overrides": str(where["overrides"]) if where["overrides"] else None},
        "checks": checks, "snapshot": info, "blockers": blockers, "warnings": warnings, "next": nxt, "ok": not blockers,
    }


def stage_command(stage, year, month, args, apply):
    where = locate(year, month, args)
    script = SCRIPTS / STAGES[stage]
    py = python_command()
    score_year, score_month = where["score_year"], where["score_month"]
    if stage == "organize":
        cmd = [py, "-B", str(script), "--bulletin-dir", str(where["bulletin_dir"]), "--bulletin-year", str(year), "--bulletin-month", str(month),
               "--score-year", str(score_year), "--score-month", str(score_month), "--apply" if apply else "--dry-run"]
    elif stage == "grade":
        cmd = [py, "-B", str(script), "--month-dir", str(where["score_dir"]), "--year", str(score_year), "--month", str(score_month),
               "--bulletin-year", str(year), "--bulletin-month", str(month), "--no-history-update"]
        if args.force:
            cmd.append("--force")
        if where["overrides"]:
            cmd += ["--overrides", str(where["overrides"])]
        if not apply:
            cmd.append("--dry-run")
    else:
        cmd = [py, "-B", str(script), "--bulletin-dir", str(where["bulletin_dir"]), "--year", str(year), "--month", str(month),
               "--score-dir", str(where["score_dir"]), "--score-year", str(score_year), "--score-month", str(score_month),
               "--mode", args.mode, "--roster", str(where["roster"]), "--apply" if apply else "--dry-run"]
        if where["snapshot"]:
            cmd += ["--case-data", str(where["snapshot"])]
        if where["overrides"]:
            cmd += ["--overrides", str(where["overrides"])]
    return cmd


def run_stage(year, month, args):
    apply = bool(args.apply)
    if args.stage == "root" and args.mode == "final-audit":
        result = plan(year, month, args)
        if result["mode"] != "final_candidate" or result["finalBlockers"]:
            raise SystemExit(json.dumps({"ok": False, "error": "final-audit 被拒绝", "reasons": result["finalBlockers"] or [result["note"]]}, ensure_ascii=False, indent=2))
    if apply and args.stage in {"grade", "root"} and interactive_session() is False:
        raise SystemExit(json.dumps({"ok": False, "error": "当前是 Windows 非交互会话（SSH/服务会话 0）：Word/Excel 无法打开文件。请在用户桌面终端运行，或用一次性交互任务运行同一命令。"}, ensure_ascii=False, indent=2))
    cmd = stage_command(args.stage, year, month, args, apply)
    print("执行：" + " ".join(cmd), flush=True)
    return subprocess.run(cmd).returncode


# ---------------------------------------------------------------- visual QA helpers

def artifact_table(year, month, args):
    where = locate(year, month, args)
    sm, sy = where["score_month"], where["score_year"]
    score, bulletin = where["score_dir"], where["bulletin_dir"]
    pending = where["config"]["naming"]["pending_prefix"]
    items = []
    for short in BRIGADE_SHORT:
        items.append((f"archive-{short}", score / f"{sy}年{sm}月消防产品监督成绩" / f"{short}大队（{sm}月产品监督档案）.doc", "doc", "T02"))
    items += [
        ("summary", score / f"{sy}年{sm}月消防产品监督成绩" / "产品监督成绩总表.xlsx", "xlsx", None),
        ("personal", score / f"{sy}年{sm}月个人执法统计表.xlsx", "xlsx", "T04"),
        ("case", score / f"{sy}年{sm}月消防监督管理系统消防执法质量（个案成绩）.xls", "xls", "T06"),
        ("report", score / f"{sy}年{sm}月通报.doc", "doc", "T07"),
    ]
    for key, name, kind, tid in (("root-R01", f"{year}年{month}月科室月考核情况记录表.xlsx", "xlsx", "T11"), ("root-R02", f"{year}年{month}月消防产品监督统计表.xls", "xls", "T09")):
        normal, waiting = bulletin / name, bulletin / f"{pending}{name}"
        items.append((key, waiting if waiting.exists() or not normal.exists() else normal, kind, tid))
    return items


def common_sources(year, month, args):
    where = locate(year, month, args)
    score, sm = where["score_dir"], where["score_month"]
    found = sorted(score.glob("*产品巡查底册*.docx")) + sorted(score.glob("*基础信息考评截图*.xls"))
    network = workflow.find_monitor_directory(score, where["score_year"], sm, where["config"])
    if network is not None:
        found += sorted(Path(network).glob("联网监测统计表.xls"))
    sources = [p for p in found]
    if where["roster"].is_file():
        sources.append(where["roster"])
    if where["snapshot"] and Path(where["snapshot"]).is_dir():
        sources += [Path(where["snapshot"]) / name for name in list(source.SNAPSHOT_FILES.values())]
    return sources


def frozen_rules(year, month, args):
    where = locate(year, month, args)
    rules = sorted(p for p in SCRIPTS.glob("*.py") if not p.name.startswith("test_"))
    rules += sorted((SKILL_DIR / "resources").glob("*.json")) + sorted((SKILL_DIR / "resources" / "history").glob("*.json"))
    if where["overrides"]:
        rules.append(Path(where["overrides"]))
    info = Path(where["snapshot"]) / source.INFO_FILE if where["snapshot"] else None
    if info and info.is_file():
        rules.append(info)
    return rules


def skill_tree_hash():
    digest = hashlib.sha256()
    for path in sorted(p for p in SKILL_DIR.rglob("*") if p.is_file() and "__pycache__" not in p.parts and ".git" not in p.parts):
        digest.update(path.relative_to(SKILL_DIR).as_posix().encode())
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def qa_baseline(year, month, args):
    qa_dir = Path(args.qa_dir)
    qa_dir.mkdir(parents=True, exist_ok=True)
    config = workflow.load_config()
    external = {item["id"]: Path(st["adopted_path"]) for item, st in zip(workflow.templates(config), workflow.template_statuses(config)) if st.get("adopted_path")}
    builtin = sorted((SKILL_DIR / "resources" / "monthly_templates").glob("*.xls*")) + [SKILL_DIR / "resources" / "空表.doc"]
    rules = [str(p) for p in frozen_rules(year, month, args)]
    only = set(args.only.split(",")) if args.only else None
    context = {"skillDir": str(SKILL_DIR), "treeSha256": skill_tree_hash(), "year": year, "month": month, "createdAt": datetime.now(SHANGHAI).isoformat(timespec="seconds"), "receipts": {}}
    context_path = qa_dir / "run-context.json"
    if context_path.exists():
        raise SystemExit(f"{context_path} 已存在：基线只能建一次，请换新的 --qa-dir（旧收据保留）。")
    qa = SCRIPTS / "artifact_visual_qa.py"
    for name, path, kind, template_id in artifact_table(year, month, args):
        if only and name not in only:
            continue
        receipt = qa_dir / f"{name}.receipt.json"
        srcs = [str(p) for p in common_sources(year, month, args)]
        if template_id and template_id in external:
            srcs.append(str(external[template_id]))
        srcs += [str(p) for p in builtin if p.is_file()]
        cmd = [python_command(), "-B", str(qa), "baseline", "--artifact", str(path), "--artifact-type", kind, "--receipt", str(receipt)]
        for item in dict.fromkeys(srcs):
            cmd += ["--source", item]
        for item in rules:
            cmd += ["--rule-source", item]
        done = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        context["receipts"][name] = {"artifact": str(path), "type": kind, "receipt": str(receipt), "exit": done.returncode, "error": done.stderr[-300:] if done.returncode else ""}
        print(f"{name}: baseline exit={done.returncode}" + (f" {done.stderr.strip()[-200:]}" if done.returncode else ""))
    context_path.write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if all(v["exit"] == 0 for v in context["receipts"].values()) else 2


CHECKS_WORD = ["all_pages_rendered_and_inspected", "page_order_and_no_unexpected_blank_page", "no_clipping_or_overlap",
               "title_and_heading_hierarchy", "fonts_and_indent"]
CHECKS_EXCEL = ["every_visible_sheet_checked", "values_and_formulas_no_error_cells", "merged_cells_rows_columns",
                "no_forbidden_public_content", "print_area_scale_and_pagination"]


def qa_template(args):
    context = json.loads((Path(args.qa_dir) / "run-context.json").read_text(encoding="utf-8"))
    for name, item in context["receipts"].items():
        path = Path(args.qa_dir) / f"{name}.inspection.template.json"
        if path.exists():
            continue
        kind = item["type"]
        sha = hashlib.sha256(Path(item["artifact"]).read_bytes()).hexdigest() if Path(item["artifact"]).is_file() else None
        template = {"artifact_sha256": sha, "renderer": "<填写：如 Word/Excel COM 导出 PDF + PyMuPDF PNG>", "renderer_version": "<填写>",
                    "total_units": 0, "checked_units": ["<Word 填 page-1..page-N；Excel 填 print-page:Sheet:N 或 sheet:名称>"],
                    "checks": {check: "unknown" for check in (CHECKS_WORD if kind in {"doc", "docx"} else CHECKS_EXCEL)},
                    "blockers": [], "warnings": [],
                    "instructions": "每项必须看过渲染页面后改为 passed / not_applicable / failed；unknown 会使 finalize 保持未通过。完成后另存为 <名称>.inspection.json 并删除 instructions。"}
        path.write_text(json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")
    print("已写入 inspection 模板（全部 unknown）")
    return 0


def qa_finalize_or_verify(args, mode):
    context = json.loads((Path(args.qa_dir) / "run-context.json").read_text(encoding="utf-8"))
    qa = SCRIPTS / "artifact_visual_qa.py"
    if mode == "verify" and skill_tree_hash() != context["treeSha256"]:
        print("警告：技能目录内容与基线时不同（运行代码已变化），收据不再代表实际执行版本。")
    status = 0
    for name, item in context["receipts"].items():
        receipt = Path(item["receipt"])
        cmd = [python_command(), "-B", str(qa), mode, "--receipt", str(receipt)]
        if mode == "finalize":
            inspection = Path(args.qa_dir) / f"{name}.inspection.json"
            if not inspection.is_file():
                print(f"{name}: 没有 {inspection.name}（还没看图或未填写）")
                status = 2
                continue
            cmd += ["--inspection", str(inspection)]
        done = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        print(f"{name}: {mode} exit={done.returncode} {done.stdout.strip().splitlines()[-1][:120] if done.stdout.strip() else ''}")
        status = status or (0 if done.returncode == 0 else 2)
    return status


def main():
    parser = argparse.ArgumentParser(description="月度通报统一入口：plan / run / qa。先运行 plan。")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("--year", type=int, required=True, help="通报年份")
        p.add_argument("--month", type=int, required=True, help="通报月份（成绩月份为上一个月）")
        p.add_argument("--bulletin-dir", help="通报月份目录；缺省按配置推导")
        p.add_argument("--snapshot", help="飞书统计快照目录；缺省读环境变量/本机配置")
        p.add_argument("--roster", help="大队执法人员总表")
        p.add_argument("--overrides", help="本月覆盖文件；缺省自动发现执行记录/覆盖/YYYYMM.overrides.json")

    p_plan = sub.add_parser("plan", help="只读：判定模式、检查输入、打印下一步")
    common(p_plan)
    p_plan.add_argument("--today", help="仅测试用：YYYY-MM-DD")
    p_plan.add_argument("--json", action="store_true")
    p_run = sub.add_parser("run", help="运行一个阶段；默认 dry-run")
    common(p_run)
    p_run.add_argument("--stage", choices=sorted(STAGES), required=True)
    p_run.add_argument("--apply", action="store_true")
    p_run.add_argument("--force", action="store_true", help="grade 阶段覆盖已生成文件")
    p_run.add_argument("--mode", choices=["pending", "final-audit"], default="pending", help="root 阶段模式")
    p_run.add_argument("--today", help=argparse.SUPPRESS)
    p_qa = sub.add_parser("qa", help="视觉 QA：baseline（生成前）/template/finalize/verify")
    p_qa.add_argument("action", choices=["baseline", "template", "finalize", "verify"])
    common(p_qa)
    p_qa.add_argument("--qa-dir", required=True)
    p_qa.add_argument("--only", help="只处理这些成品名，逗号分隔，如 archive-宜兴,archive-新吴,report,root-R01,root-R02")
    args = parser.parse_args()
    if args.command == "plan":
        result = plan(args.year, args.month, args)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"模式：{result['mode']}｜{result['note']}")
            for item in result["checks"]:
                print(f"  [{item['status']}] {item['name']}：{item['path']}")
            for item in result["blockers"]:
                print(f"  阻塞：{item['message']}")
            for item in result["warnings"]:
                print(f"  提示：{item['message']}")
            for reason in result["finalBlockers"]:
                print(f"  定稿前须：{reason}")
            for item in result["next"]:
                print("  下一步 " + item["step"] + f"［{item.get('where', '')}］：" + (item.get("shell") or "") + (f"（{item['note']}）" if item.get("note") else ""))
        raise SystemExit(0 if result["ok"] else 2)
    if args.command == "run":
        raise SystemExit(run_stage(args.year, args.month, args))
    if args.action == "baseline":
        raise SystemExit(qa_baseline(args.year, args.month, args))
    if args.action == "template":
        raise SystemExit(qa_template(args))
    raise SystemExit(qa_finalize_or_verify(args, args.action))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
