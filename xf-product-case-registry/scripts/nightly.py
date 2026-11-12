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
    if state["unknown"] or state["quota"] or state["resumable"] or state["nextStage"] is not None:
        return {"status": "NOT_FINISHED", "date": day, "wrote": False, **state}
    run.update(status="FINISHED", finishedAt=(now or now_shanghai()).astimezone(SHANGHAI).isoformat(timespec="seconds"),
               changed=state["changed"], quiet=state["changed"] == 0, summary=summary)
    _atomic(path, run)
    return {"status": "FINISHED", "date": day, "changed": state["changed"], "quiet": run["quiet"]}


def status(root: Path, day: str | None = None) -> dict[str, Any]:
    days = _run_days(root)
    day = day or (days[-1] if days else None)
    if day is None:
        return {"status": "NO_RUN", "runs": 0}
    run = _load(_run_dir(root, day) / "run.json", None)
    if run is None:
        return {"status": "NO_RUN", "date": day, "runs": len(days)}
    return {"date": day, "run": run, "runs": len(days), **progress(root, day)}


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
