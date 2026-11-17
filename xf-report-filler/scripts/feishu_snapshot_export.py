"""Export the four Feishu case tables as a read-only NDJSON snapshot with provenance (run in WSL).

Only ``lark-cli base +record-list`` is ever invoked (a read command); nothing is written to Feishu and
no login or token is handled here. The CLI runs in a private temporary directory on the local
Linux filesystem (it cannot chmod under /mnt/*), pages are followed with the CLI's own
``has_more``/``next_offset`` manifest fields, and the result is copied into a NEW target directory
after it passed ``feishu_case_source.load_snapshot``.

``snapshot.info.json`` states what the export is and is not: when it was taken, the 26th-25th
statistics window it serves, the newest check date and record-update time it contains, and that
a snapshot taken before the window end is only a lower bound. It never claims a fixed Feishu-to-system lag.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import feishu_case_source as source
import host_paths
import monthly_workflow as workflow

SHANGHAI = timezone(timedelta(hours=8))
MAX_PAGES = 50
RETRIES = 2
RETRY_PAUSE = 3
READ_ONLY_VERBS = {("base", "+record-list")}


class ExportError(RuntimeError):
    """The export cannot be trusted; nothing is published to the target directory."""


def today_shanghai():
    return datetime.now(SHANGHAI).date()


def window_for(year, month):
    start_year, start_month = (year - 1, 12) if month == 1 else (year, month - 1)
    return date(start_year, start_month, 26), date(year, month, 25)


def assert_read_only(argv):
    """Refuse anything but the whitelisted read verb (guards against future edits and odd arguments)."""
    words = [item for item in argv[1:] if item in {"base", "+record-list"}]
    if tuple(words) not in READ_ONLY_VERBS:
        raise ExportError(f"只允许只读命令 base +record-list，拒绝：{' '.join(argv[:6])}")
    forbidden = {item for item in argv[1:] if item.startswith("+") and item != "+record-list"} | ({"auth", "login"} & set(argv))
    if forbidden:
        raise ExportError(f"命令含写入或登录动词，拒绝：{sorted(forbidden)}")


def record_list_argv(lark_cli, profile, base_token, table_id, limit, offset, output, fields=()):
    argv = [lark_cli, "--profile", profile, "base", "+record-list", "--as", "user",
            "--base-token", base_token, "--table-id", table_id,
            "--limit", str(limit), "--format", "ndjson", "--output", output]
    for field in fields:  # projection: the roster is exported without names
        argv += ["--field-id", field]
    if offset:
        argv += ["--offset", str(offset)]
    assert_read_only(argv)
    return argv


def _login_shell_lookup():
    """`wsl.exe -e python3 ...` starts without the login profile, so PATH may lack the nvm-installed CLI; ask a login shell."""
    if os.name == "nt":
        return None
    try:
        done = subprocess.run(["bash", "-lc", "command -v lark-cli"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    lines = done.stdout.strip().splitlines()
    return lines[-1] if done.returncode == 0 and lines else None


def find_lark_cli(explicit=None):
    candidate = explicit or os.environ.get("XF_LARK_CLI") or shutil.which("lark-cli")
    if not candidate and not explicit:
        candidate = _login_shell_lookup()
    if not candidate or not Path(candidate).is_file():
        raise ExportError(
            "找不到 lark-cli。导出必须在已安装并授权官方 lark-cli 的 WSL 里运行：在 WSL 终端执行 "
            "`python3 <技能目录>/scripts/feishu_snapshot_export.py --year Y --month M --out-dir <新目录>`；"
            "在 Windows 里用 `wsl.exe -e python3 <WSL 路径的技能目录>/scripts/feishu_snapshot_export.py ...`（monthly_run.py plan 会打印完整命令）。"
            "也可用 --lark-cli 或环境变量 XF_LARK_CLI 指定路径。本脚本不登录、不改授权。"
        )
    return candidate


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _count_lines(path):
    with Path(path).open(encoding="utf-8") as stream:
        return sum(1 for line in stream if line.strip())


def export_table(role, table_id, cfg, lark_cli, work, run, limit, fields=()):
    """Fetch every page of one table; returns (records_path, manifest_dict)."""
    pages, offset, last_rev = [], 0, None
    while True:
        if len(pages) >= MAX_PAGES:
            raise ExportError(f"{role} 超过 {MAX_PAGES} 页仍未取完，停止")
        name = f"{role}-p{len(pages) + 1:02d}.ndjson"
        argv = record_list_argv(lark_cli, cfg["profile"], cfg["base_token"], table_id, limit, offset, f"./{name}", fields)
        result = run(argv, work)
        for _ in range(RETRIES):  # a read-only page can be fetched again; transient network errors (EOF, timeout) are common
            if result.returncode == 0:
                break
            time.sleep(RETRY_PAUSE)
            result = run(argv, work)
        if result.returncode != 0:
            raise ExportError(f"{role} 第 {len(pages) + 1} 页导出失败（lark-cli 退出码 {result.returncode}）：{(result.stderr or result.stdout or '')[-400:]}")
        manifest_path = work / name.replace(".ndjson", ".manifest.json")
        records_path = work / name
        if not manifest_path.is_file() or not records_path.is_file():
            raise ExportError(f"{role} 第 {len(pages) + 1} 页没有生成记录或清单文件")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("table_id") != table_id or manifest.get("base_token") != cfg["base_token"]:
            raise ExportError(f"{role} 清单的 Base/表 ID 与请求不符")
        lines = _count_lines(records_path)
        if manifest.get("records_count") != lines:
            raise ExportError(f"{role} 第 {len(pages) + 1} 页清单记录数 {manifest.get('records_count')} 与文件行数 {lines} 不符")
        if last_rev is not None and manifest.get("rev") != last_rev:
            raise ExportError(f"{role} 导出期间表被修改（rev {last_rev} → {manifest.get('rev')}），请重新导出")
        last_rev = manifest.get("rev")
        pages.append({"offset": offset, "records": lines, "rev": last_rev, "file": name, "has_more": bool(manifest.get("has_more"))})
        if not manifest.get("has_more"):
            break
        next_offset = manifest.get("next_offset")
        if next_offset != offset + lines or lines == 0:
            raise ExportError(f"{role} 分页续取偏移不连续：offset={offset} records={lines} next_offset={next_offset}")
        offset = next_offset
    final = work / f"{role}.ndjson"
    with final.open("w", encoding="utf-8", newline="\n") as out:
        for page in pages:
            text = (work / page["file"]).read_text(encoding="utf-8")
            out.write(text if text.endswith("\n") or not text else text + "\n")
    total = sum(page["records"] for page in pages)
    (work / f"{role}.manifest.json").write_text(
        json.dumps(
            {"manifest_version": "v1", "format": "ndjson", "base_token": cfg["base_token"], "table_id": table_id, "rev": last_rev,
             "records_count": total, "has_more": False, "page_count": len(pages), "pages": pages,
             "note": "由 feishu_snapshot_export.py 合并各页清单；列统计见各页清单，不保留。"},
            ensure_ascii=False, indent=2),
        encoding="utf-8")
    return final, {"table_id": table_id, "records": total, "rev": last_rev, "pages": len(pages)}


def _max_day(rows, field):
    values = [row.get(field) for row in rows if isinstance(row.get(field), str) and len(row.get(field)) >= 10]
    return max(values)[:19] if values else None


def build_info(summary, year, month, started, finished, today, contract):
    start, end = window_for(year, month)
    after = today >= end
    snapshot = summary["_snapshot"]
    info = {
        "source": f"飞书案卷库(Base {summary['base_token']}) 四表 lark-cli base +record-list 只读全量导出，profile {summary['profile']}，user 身份；未写飞书",
        "exportedAt": finished.isoformat(timespec="seconds"),
        "window": [str(start), str(end)],
        "windowStatus": "at_or_after_window_end" if after else "before_window_end",
        "completeness": (
            "四表 has_more=false，行数 " + "、".join(f"{r} {summary['tables'][r]['records']}" for r in source.SNAPSHOT_FILES)
            + "（以 *.manifest.json 为准）；"
            + (f"导出日 {today} 已到统计窗口终点 {end}，仍需大队/用户确认本月数据已最终汇总后才可定稿，飞书与监督系统是否同步未核。"
               if after else f"导出日 {today} 早于统计窗口终点 {end}，{today} 之后的检查不在内，结果只是截至导出日的下限，只能待补。")
        ),
        "latestCheckDate": _max_day(snapshot["tables"]["checks"], "检查日期"),
        "latestRecordUpdate": _max_day(snapshot["tables"]["cases"], "资料更新时间"),
        "finalDataConfirmed": False,
        "refreshedBy": "feishu_snapshot_export.py",
        "tables": summary["tables"],
        "note": "快照为一次性只读副本，不是长期事实；水上大队等表外大队不补 0；用户确认数据最终后才可把 finalDataConfirmed 改为 true（人工）。",
        "exportStartedAt": started.isoformat(timespec="seconds"),
        "contract": contract,
    }
    return info


def check_contract(cfg, contract_dir_override=None):
    directory = Path(contract_dir_override) if contract_dir_override else _contract_dir(cfg)
    if not directory or not directory.is_dir():
        return {"checked": False, "note": "未找到字段合同目录，表 ID 未与合同核对"}
    files = sorted(directory.glob(cfg.get("contract_glob", "字段合同-*.json")))
    if not files:
        return {"checked": False, "note": "字段合同目录没有合同文件"}
    latest = files[-1]
    text = latest.read_text(encoding="utf-8-sig")
    missing = [tid for tid in cfg["tables"].values() if tid not in text]
    base_ok = cfg["base_token"] in text
    return {"checked": True, "file": latest.name, "sha256": _sha256(latest).upper(), "baseMatches": base_ok, "missingTableIds": missing}


def _contract_dir(cfg):
    value = cfg.get("contract_dir")
    if not value:
        return None
    return host_paths.host_path(value)


def run_export(year, month, out_dir, cfg, lark_cli=None, runner=None, contract_dir=None, page_limit=None, today=None, keep_work=False):
    out_dir = Path(out_dir)
    if out_dir.exists():
        raise ExportError(f"目标目录已存在，不覆盖旧快照：{out_dir}（请换一个新目录名）")
    if not out_dir.parent.is_dir():
        raise ExportError(f"目标目录的上级不存在：{out_dir.parent}")
    lark = find_lark_cli(lark_cli) if runner is None else (lark_cli or "lark-cli")
    run = runner or (lambda argv, cwd: subprocess.run(argv, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace"))
    contract = check_contract(cfg, contract_dir)
    work = Path(tempfile.mkdtemp(prefix="xf-snapshot-"))
    try:
        os.chmod(work, 0o700)
    except OSError:
        pass
    started = datetime.now(SHANGHAI)
    try:
        tables = {}
        for role in source.SNAPSHOT_FILES:
            _, table = export_table(role, cfg["tables"][role], cfg, lark, work, run, page_limit or cfg.get("page_limit", 2000))
            tables[role] = table
        roster_cfg = cfg["tables"].get("staff_roster")
        if roster_cfg:  # 执法监督人员名录：只取稳定ID、大队、有效期；姓名不进快照
            _, tables["staff_roster"] = export_table("staff_roster", roster_cfg, cfg, lark, work, run, page_limit or cfg.get("page_limit", 2000), tuple(cfg["roster_fields"]))
        stage = work / "final"
        stage.mkdir()
        for role in list(source.SNAPSHOT_FILES) + (["staff_roster"] if roster_cfg else []):
            shutil.copy2(work / f"{role}.ndjson", stage / f"{role}.ndjson")
            shutil.copy2(work / f"{role}.manifest.json", stage / f"{role}.manifest.json")
        try:
            snapshot = source.load_snapshot(stage)
        except source.SnapshotError as error:
            raise ExportError(f"导出的快照未通过结构校验：{error}") from error
        finished = datetime.now(SHANGHAI)
        summary = {"base_token": cfg["base_token"], "profile": cfg["profile"], "tables": tables, "_snapshot": snapshot}
        info = build_info(summary, year, month, started, finished, today or today_shanghai(), contract)
        (stage / source.INFO_FILE).write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        publish(stage, out_dir)
        return {"ok": True, "out_dir": str(out_dir), "info": info,
                "files": {name: _sha256(out_dir / name).upper() for name in sorted(p.name for p in out_dir.iterdir())}}
    finally:
        if not keep_work:
            shutil.rmtree(work, ignore_errors=True)


def publish(stage, out_dir):
    """Copy the validated snapshot into the NEW target (plain file copies: /mnt/* drives reject chmod/utime) and verify it.

    A partial target is removed again, so a failed publish leaves nothing that could be mistaken for a snapshot.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir()
    try:
        for item in sorted(Path(stage).iterdir()):
            shutil.copyfile(item, out_dir / item.name)
        for item in sorted(Path(stage).iterdir()):
            if _sha256(item) != _sha256(out_dir / item.name):
                raise ExportError(f"复制后哈希不一致：{item.name}")
        source.load_snapshot(out_dir)
    except BaseException:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise


def main():
    parser = argparse.ArgumentParser(description="只读导出飞书案卷四表为统计快照（WSL 运行，写入新目录，不写飞书）。")
    parser.add_argument("--year", type=int, required=True, help="通报年份")
    parser.add_argument("--month", type=int, required=True, help="通报月份（统计窗口为上月26日至本月25日）")
    parser.add_argument("--out-dir", required=True, help="新建的输出目录；已存在则拒绝")
    parser.add_argument("--lark-cli", help="lark-cli 路径；缺省用 XF_LARK_CLI 或 PATH")
    parser.add_argument("--page-limit", type=int, help="每页条数（上限 2000），仅用于验证分页")
    parser.add_argument("--contract-dir", help="字段合同目录；缺省读配置")
    args = parser.parse_args()
    cfg = workflow.load_config()["data_sources"]["case_snapshot"]["export"]
    try:
        result = run_export(args.year, args.month, args.out_dir, cfg, args.lark_cli, contract_dir=args.contract_dir, page_limit=args.page_limit)
    except ExportError as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False, indent=2))
        raise SystemExit(2)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
