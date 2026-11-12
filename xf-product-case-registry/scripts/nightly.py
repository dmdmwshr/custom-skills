"""Nightly run record, checkpoints and the follow-up queue (2026-11-12 mechanism).

The scheduled 02:00 run and a manual run use the same entry. Everything lives under the business
root's fixed runtime folder ``运行记录``; nothing here reads the network or touches case material.

- One run per Asia/Shanghai date. A second trigger on a finished date is a no-op; on an unfinished date
  it resumes from the saved checkpoints.
- A checkpoint is appended per stage and per case. A write stage that was started and has no terminal
  checkpoint is "interrupted": its effect is unknown and must be reconciled (read back), never replayed.
- A missed night needs no special handling: due dates are absolute, so the next run processes everything
  that is due. Earlier unfinished runs are listed so their unknowns are reconciled first.
- The observation queue holds tasks seen at the source that are not in scope yet (not inspected, no
  inspection date). They are looked at again every ``FOLLOW_UP_DAYS`` days, however long they wait, and
  enter the active range only once the inspection date is confirmed.
- Source coverage: every night looks at two windows per entry (tasks created recently, and tasks whose
  inspection date is recent whatever their creation date). Every ``COVERAGE_DAYS`` days the whole range is
  read again by inspection date and compared with the ledger; each check is one line in a fixed log. A
  check that is due and was not even attempted blocks ``finish``; an attempted but incomplete one stays
  due, so a missed or failed weekly check is made up by the next run.
- Executions: the model session that works on a run binds itself (session id, runner receipt, runner pid)
  into that run's record, so a supervisor asks about exactly that execution and never about "the latest
  receipt" of some other project.

Standard library only.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
RUNTIME_DIR = "运行记录"
RUNS_DIR = "夜间运行"
QUEUE_DIR = "跟进队列"
FOLLOW_UP_DAYS = 7
SCOPE_FROM = "2026-01-01"
COVERAGE_LOG = "来源覆盖核对.jsonl"
COVERAGE_DAYS = 7
COVERAGE_FIELD = "检查日期"
# 任务列表的五个入口；消防装备抽查入口来源侧不可用，作为已知限制随每次核对如实记录，不计入“完整”。
COVERAGE_ENTRIES = ("日常监督任务", "开业前检查任务", "举报投诉核查任务", "火灾事故调查核查任务", "社会单位产品抽查任务")
DISPOSITIONS = frozenset({"OBSERVED", "REGISTERED", "OUT_OF_SCOPE"})

# Fixed order. Write stages reach outside the local ledger; an interrupted one is an unknown write.
STAGES = ("discover", "acquire", "organize", "validate", "sync", "mirror", "reconcile", "report")
WRITE_STAGES = frozenset({"acquire", "sync"})
TERMINAL = frozenset({"done", "failed", "skipped", "reconciled"})
STATUSES = frozenset({"started", "unknown", "quota", *TERMINAL})
WAITING = ("NOT_INSPECTED", "IN_PROGRESS", "SAMPLE_PENDING", "RECHECK_PENDING", "SOURCE_GAP")


class NightlyError(ValueError):
    pass


def now_shanghai() -> datetime:
    return datetime.now(SHANGHAI)


def runtime_root(business_root: Path) -> Path:
    return Path(business_root) / RUNTIME_DIR


def _atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=1) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _load(path: Path, default: Any) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default


def _run_dir(root: Path, day: str) -> Path:
    try:
        date.fromisoformat(day)
    except ValueError as error:
        raise NightlyError("运行日期必须是 YYYY-MM-DD") from error
    return root / RUNS_DIR / day


def checkpoints(root: Path, day: str) -> list[dict[str, Any]]:
    path = _run_dir(root, day) / "checkpoints.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def progress(root: Path, day: str) -> dict[str, Any]:
    """Latest state per (stage, case) and what follows from it."""
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for item in checkpoints(root, day):
        latest[(item["stage"], item.get("case") or "")] = item
    stage_state = {stage: (latest.get((stage, "")) or {}).get("status") for stage in STAGES}
    open_items = [item for item in latest.values() if item["status"] not in TERMINAL]
    unknown = [item for item in open_items
               if item["status"] == "unknown" or (item["status"] == "started" and item["stage"] in WRITE_STAGES)]
    quota = [item for item in open_items if item["status"] == "quota"]
    resumable = [item for item in open_items if item not in unknown and item not in quota]
    done_steps = sorted([key[0], key[1]] for key, item in latest.items() if key[1] and item["status"] in TERMINAL)
    next_stage = next((stage for stage in STAGES if stage_state[stage] not in TERMINAL), None)
    changed = sum(int(item.get("changed") or 0) for item in latest.values())
    return {"stages": stage_state, "nextStage": next_stage, "unknown": unknown, "quota": quota, "resumable": resumable,
            "finishedSteps": done_steps, "changed": changed}


def _run_days(root: Path) -> list[str]:
    directory = root / RUNS_DIR
    return sorted(p.name for p in directory.iterdir() if p.is_dir() and (p / "run.json").is_file()) if directory.is_dir() else []


def begin(root: Path, *, now: datetime | None = None, trigger: str = "scheduled", actor: str = "") -> dict[str, Any]:
    """Open today's run, resume it, or report that it is already finished (no write in that case)."""
    now = (now or now_shanghai()).astimezone(SHANGHAI)
    day = now.date().isoformat()
    path = _run_dir(root, day) / "run.json"
    earlier = []
    for other in _run_days(root):
        if other < day:
            state = _load(root / RUNS_DIR / other / "run.json", {})
            if state.get("status") != "FINISHED":
                earlier.append({"date": other, "status": state.get("status"), "unknown": progress(root, other)["unknown"]})
    finished = [d for d in _run_days(root) if _load(root / RUNS_DIR / d / "run.json", {}).get("status") == "FINISHED"]
    missed: list[str] = []
    if finished:
        cursor = date.fromisoformat(finished[-1]) + timedelta(days=1)
        while cursor.isoformat() < day:
            missed.append(cursor.isoformat())
            cursor += timedelta(days=1)
    run = _load(path, None)
    if run is not None and run.get("status") == "FINISHED":
        return {"status": "ALREADY_FINISHED", "date": day, "finishedAt": run.get("finishedAt"), "wrote": False}
    if run is None:
        run = {"schemaVersion": "NightlyRunV1", "date": day, "status": "RUNNING", "startedAt": now.isoformat(timespec="seconds"),
               "triggers": [], "missedDates": missed}
        outcome = "STARTED"
    else:
        outcome = "RESUME"
        run["status"] = "RUNNING"
    run["triggers"].append({"at": now.isoformat(timespec="seconds"), "kind": trigger, "actor": actor})
    _atomic(path, run)
    return {"status": outcome, "date": day, "missedDates": missed, "earlierUnfinished": earlier, **progress(root, day),
            "weeklyCoverage": coverage_state(root, today=day), "lastFinishedDate": finished[-1] if finished else None,
            "openExecutions": open_executions(root),
            "rule": "先对账 unknown（回读，不重放）；到期事项按绝对到期日处理，错过的夜晚不单独补跑"}


def checkpoint(root: Path, day: str, *, stage: str, status: str, case: str | None = None, detail: str = "",
               ref: str = "", changed: int = 0, now: datetime | None = None) -> dict[str, Any]:
    if stage not in STAGES or status not in STATUSES:
        raise NightlyError(f"stage 必须是 {'/'.join(STAGES)}；status 必须是 {'/'.join(sorted(STATUSES))}")
    path = _run_dir(root, day) / "run.json"
    run = _load(path, None)
    if run is None:
        raise NightlyError("该日期没有运行记录，先 begin")
    if run.get("status") == "FINISHED":
        raise NightlyError("该日期的运行已结束，不能再追加检查点")
    latest = {(i["stage"], i.get("case") or ""): i for i in checkpoints(root, day)}.get((stage, case or ""))
    if latest is not None and status == "started" and (
            latest["status"] == "unknown" or (latest["status"] == "started" and stage in WRITE_STAGES)):
        raise NightlyError("该步骤上次的效果未知，先回读对账并记 reconciled，不能直接重新开始")
    item = {"at": (now or now_shanghai()).astimezone(SHANGHAI).isoformat(timespec="seconds"), "stage": stage, "status": status}
    if case:
        item["case"] = case
    if detail:
        item["detail"] = detail
    if ref:
        item["ref"] = ref
    if changed:
        item["changed"] = changed
    with (_run_dir(root, day) / "checkpoints.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    if status == "quota" and run.get("status") != "PAUSED_QUOTA":
        run["status"] = "PAUSED_QUOTA"
        _atomic(path, run)
    return item


def finish(root: Path, day: str, *, summary: str = "", now: datetime | None = None) -> dict[str, Any]:
    path = _run_dir(root, day) / "run.json"
    run = _load(path, None)
    if run is None:
        raise NightlyError("该日期没有运行记录")
    if run.get("status") == "FINISHED":
        return {"status": "ALREADY_FINISHED", "date": day, "wrote": False}
    state = progress(root, day)
    weekly = coverage_state(root, today=day)
    if state["unknown"] or state["quota"] or state["resumable"] or state["nextStage"] is not None:
        return {"status": "NOT_FINISHED", "date": day, "wrote": False, **state}
    if weekly["due"] and not weekly["attemptedToday"]:
        return {"status": "NOT_FINISHED", "date": day, "wrote": False, **state, "coverageDue": weekly,
                "rule": "全范围检查日期核对已到期且今晚未做：先做并用 nightly coverage 登记（来源不可用也要登记这次尝试）"}
    quiet = state["changed"] == 0 and not weekly["due"]
    run.update(status="FINISHED", finishedAt=(now or now_shanghai()).astimezone(SHANGHAI).isoformat(timespec="seconds"),
               changed=state["changed"], quiet=quiet, summary=summary)
    result = {"status": "FINISHED", "date": day, "changed": state["changed"], "quiet": quiet}
    if weekly["due"]:
        run["weeklyCoverage"] = result["weeklyCoverage"] = weekly      # 今晚试过但未完整：如实带出，下一轮仍到期
    _atomic(path, run)
    return result


def status(root: Path, day: str | None = None) -> dict[str, Any]:
    days = _run_days(root)
    day = day or (days[-1] if days else None)
    if day is None:
        return {"status": "NO_RUN", "runs": 0}
    run = _load(_run_dir(root, day) / "run.json", None)
    if run is None:
        return {"status": "NO_RUN", "date": day, "runs": len(days)}
    return {"date": day, "run": run, "runs": len(days), **progress(root, day),
            "weeklyCoverage": coverage_state(root, today=now_shanghai().date().isoformat()), "openExecutions": open_executions(root)}


# ---------------------------------------------------------------------------------------------
# Source coverage: the weekly full-range check by inspection date
# ---------------------------------------------------------------------------------------------


def coverage_checks(root: Path) -> list[dict[str, Any]]:
    path = root / RUNS_DIR / COVERAGE_LOG
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def coverage(root: Path, report: dict[str, Any], *, day: str, now: datetime | None = None) -> dict[str, Any]:
    """Record one full-range check. ``report``: ``{dateField, scopeFrom, scopeTo, entries: {name: {total, rows, complete,
    observedAt}}, unregistered: [{entry, unitName, inspectionDate, disposition, ...}], unavailable?, ref?, sha256?}``.

    The check is ``COMPLETE`` only if it filtered on the inspection date, covers the whole range up to the
    day before the run, read every entry to the last page with the row count equal to the page total, and
    every task missing from the ledger has been disposed of (observed, registered or out of scope).
    """
    seen = date.fromisoformat(day)
    entries = report.get("entries")
    if not isinstance(entries, dict):
        raise NightlyError("覆盖核对需要 entries")
    reasons: list[str] = []
    if report.get("dateField") != COVERAGE_FIELD:
        reasons.append(f"筛选字段不是{COVERAGE_FIELD}：{report.get('dateField')}")
    start, end = str(report.get("scopeFrom") or ""), str(report.get("scopeTo") or "")
    try:
        if date.fromisoformat(start) > date.fromisoformat(SCOPE_FROM):
            reasons.append(f"范围起点晚于 {SCOPE_FROM}")
        if date.fromisoformat(end) < seen - timedelta(days=1):
            reasons.append("范围终点早于运行日前一天")
    except ValueError as error:
        raise NightlyError("scopeFrom/scopeTo 必须是 YYYY-MM-DD") from error
    for name in COVERAGE_ENTRIES:
        entry = entries.get(name)
        if not isinstance(entry, dict):
            reasons.append(f"缺入口：{name}")
        elif entry.get("complete") is not True or entry.get("rows") != entry.get("total") or not isinstance(entry.get("total"), int):
            reasons.append(f"入口未读完整：{name}（共 {entry.get('total')} 条，读到 {entry.get('rows')} 行）")
    unregistered = report.get("unregistered") or []
    open_rows = [row for row in unregistered if row.get("disposition") not in DISPOSITIONS]
    if open_rows:
        reasons.append(f"{len(open_rows)} 个未登记任务还没有处置")
    item = {"at": (now or now_shanghai()).astimezone(SHANGHAI).isoformat(timespec="seconds"), "date": day,
            "outcome": "COMPLETE" if not reasons else "INCOMPLETE", "reasons": reasons, "dateField": report.get("dateField"),
            "scopeFrom": start, "scopeTo": end,
            "entries": {name: {k: entry.get(k) for k in ("total", "rows", "complete", "observedAt", "error") if entry.get(k) is not None}
                        for name, entry in entries.items() if isinstance(entry, dict)},
            "rowsTotal": sum(int(e.get("rows") or 0) for e in entries.values() if isinstance(e, dict)),
            "unregistered": unregistered, "unavailable": report.get("unavailable") or [], "ref": report.get("ref") or "",
            "sha256": report.get("sha256") or ""}
    path = root / RUNS_DIR / COVERAGE_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    return {**item, "weeklyCoverage": coverage_state(root, today=day)}


def coverage_state(root: Path, *, today: str) -> dict[str, Any]:
    """When the last complete full-range check was and whether the next one is due. Never done counts as due."""
    checks = [c for c in coverage_checks(root) if c["date"] <= today]
    complete = sorted(c["date"] for c in checks if c["outcome"] == "COMPLETE")
    last = complete[-1] if complete else None
    due_on = (date.fromisoformat(last) + timedelta(days=COVERAGE_DAYS)).isoformat() if last else today
    attempts = [c for c in checks if c["date"] == today]
    return {"lastCompleteOn": last, "dueOn": due_on, "due": due_on <= today, "attemptedToday": bool(attempts),
            "todayOutcome": attempts[-1]["outcome"] if attempts else None, "todayReasons": attempts[-1]["reasons"] if attempts else [],
            "everyDays": COVERAGE_DAYS}


# ---------------------------------------------------------------------------------------------
# Executions bound to a run
# ---------------------------------------------------------------------------------------------


def bind(root: Path, day: str, *, session_id: str, receipt: str, runner_pid: int, runner: str = "claude-exec", note: str = "",
         now: datetime | None = None) -> dict[str, Any]:
    """Bind the executing model session to this run. Same session and receipt again is a no-op; a resumed
    session comes with a new receipt and is a new execution."""
    path = _run_dir(root, day) / "run.json"
    run = _load(path, None)
    if run is None:
        raise NightlyError("该日期没有运行记录，先 begin")
    if not session_id.strip() or not receipt.strip() or runner_pid <= 0:
        raise NightlyError("绑定需要会话标识、回执路径和派发进程号；取不到时如实报告，不填猜测值")
    executions = run.setdefault("executions", [])
    for item in executions:
        if item["sessionId"] == session_id and item["receipt"] == receipt:
            return {"status": "ALREADY_BOUND", "date": day, "execution": item, "wrote": False}
    item = {"sessionId": session_id, "receipt": receipt, "runnerPid": runner_pid, "runner": runner,
            "boundAt": (now or now_shanghai()).astimezone(SHANGHAI).isoformat(timespec="seconds")}
    if note:
        item["note"] = note
    executions.append(item)
    _atomic(path, run)
    return {"status": "BOUND", "date": day, "execution": item}


def bind_end(root: Path, day: str, *, receipt: str, state: str, detail: str = "", now: datetime | None = None) -> dict[str, Any]:
    """Record the observed end of one bound execution: ``ENDED`` (receipt is a complete result), ``ENDED_ERROR``
    or ``LOST`` (runner gone without a readable receipt; its effects were reconciled by hand)."""
    if state not in ("ENDED", "ENDED_ERROR", "LOST"):
        raise NightlyError("state 必须是 ENDED/ENDED_ERROR/LOST")
    path = _run_dir(root, day) / "run.json"
    run = _load(path, None)
    match = [item for item in (run or {}).get("executions", []) if item["receipt"] == receipt]
    if not match:
        raise NightlyError("该运行没有绑定这份回执")
    item = match[-1]
    if item.get("endedAt"):
        return {"status": "ALREADY_ENDED", "date": day, "execution": item, "wrote": False}
    item.update(endedAt=(now or now_shanghai()).astimezone(SHANGHAI).isoformat(timespec="seconds"), endState=state)
    if detail:
        item["endDetail"] = detail
    _atomic(path, run)
    return {"status": "RECORDED", "date": day, "execution": item}


def open_executions(root: Path) -> list[dict[str, Any]]:
    """Bound executions with no recorded end, over all runs: what a supervisor checks before dispatching again."""
    found = []
    for day in _run_days(root):
        run = _load(root / RUNS_DIR / day / "run.json", {})
        found += [{"date": day, "runStatus": run.get("status"), **item} for item in run.get("executions", []) if not item.get("endedAt")]
    return found


# ---------------------------------------------------------------------------------------------
# Observation queue: tasks seen at the source that are not in the active range yet
# ---------------------------------------------------------------------------------------------


def _queue_path(root: Path) -> Path:
    return root / QUEUE_DIR / "观察队列.json"


def _queue(root: Path) -> dict[str, Any]:
    return _load(_queue_path(root), {"schemaVersion": "ObservationQueueV1", "items": {}})


def observe(root: Path, items: list[dict[str, Any]], *, observed_on: str, entry: str, days: int = FOLLOW_UP_DAYS) -> dict[str, Any]:
    """Record what one source look saw. ``items``: ``{rwid, state, projectNo?, unitName?, brigade?, inspectionDate?, awaitingProduct?}``.

    A task with a confirmed inspection date in range leaves the queue as ``ENTER_SCOPE`` (the caller then
    registers it through the normal source entry); one confirmed out of range leaves as ``OUT_OF_SCOPE``.
    Anything else stays and is due again ``days`` later, counted from this look.
    """
    try:
        seen = date.fromisoformat(observed_on)
    except ValueError as error:
        raise NightlyError("observed_on 必须是 YYYY-MM-DD") from error
    queue = _queue(root)
    result = {"added": 0, "kept": 0, "enterScope": [], "outOfScope": []}
    for item in items:
        rwid, state = str(item.get("rwid") or "").strip(), item.get("state")
        if not rwid or state not in WAITING:
            raise NightlyError(f"每项需要 rwid 与 state({'/'.join(WAITING)})")
        inspected = item.get("inspectionDate")
        current = queue["items"].get(rwid)
        if inspected:
            date.fromisoformat(inspected)
            if state == "NOT_INSPECTED":
                raise NightlyError("有检查日期的任务不是未检查")
        if inspected and inspected < SCOPE_FROM:
            if current is not None:
                current.update(status="OUT_OF_SCOPE", closedOn=observed_on, inspectionDate=inspected)
            result["outOfScope"].append(rwid)
            continue
        awaiting = bool(inspected) and bool(item.get("awaitingProduct"))   # 已检查但详情尚无产品行：未入总账，继续观察
        if inspected and not awaiting and current is not None and current["status"] == "WAITING" \
                and (current["state"] == "NOT_INSPECTED" or current.get("awaitingProduct")):
            current.update(status="ENTER_SCOPE", closedOn=observed_on, inspectionDate=inspected, state=state, awaitingProduct=False)
            result["enterScope"].append(rwid)
            continue
        if inspected and not awaiting and current is None:
            result["enterScope"].append(rwid)      # 已有检查日期且在范围内：直接走正常来源登记，不入观察队列
            continue
        if current is None:
            queue["items"][rwid] = current = {"rwid": rwid, "firstSeenOn": observed_on, "entry": entry, "looks": 0, "status": "WAITING"}
            result["added"] += 1
        else:
            result["kept"] += 1
        current.update({k: item[k] for k in ("projectNo", "unitName", "brigade") if item.get(k)})
        if awaiting:
            current.update(awaitingProduct=True, inspectionDate=inspected)
        current.update(state=state, status="WAITING", lastSeenOn=observed_on, looks=current["looks"] + 1,
                       nextDueOn=(seen + timedelta(days=days)).isoformat())
    _atomic(_queue_path(root), queue)
    return result


def due(root: Path, *, today: str) -> list[dict[str, Any]]:
    """Waiting tasks whose next look is due. The interval never grows with the wait."""
    return sorted((i for i in _queue(root)["items"].values() if i["status"] == "WAITING" and i["nextDueOn"] <= today),
                  key=lambda i: (i["nextDueOn"], i["rwid"]))


def queue_summary(root: Path, *, today: str) -> dict[str, Any]:
    items = list(_queue(root)["items"].values())
    waiting = [i for i in items if i["status"] == "WAITING"]
    return {"waiting": len(waiting), "due": len(due(root, today=today)),
            "byState": {s: sum(i["state"] == s for i in waiting) for s in WAITING if any(i["state"] == s for i in waiting)},
            "enteredScope": sum(i["status"] == "ENTER_SCOPE" for i in items), "outOfScope": sum(i["status"] == "OUT_OF_SCOPE" for i in items)}
