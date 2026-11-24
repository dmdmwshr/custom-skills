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
- Checkpoints are keyed by (stage, case, entry). A step whose effect is unknown is closed only by ``reconciled`` (a write
  stage also needs the original receipt as ``ref``); ``finish`` carries failed and explicitly retained items as ``issues``.
  Counts come from evidence (receipts, ledger, queue history), not from what a writer reported.
- Executions: the model session that works on a run binds itself (session id, runner receipt, runner pid)
  into that run's record, so a supervisor asks about exactly that execution and never about "the latest
  receipt" of some other project.

Standard library only.
"""

from __future__ import annotations

import json
import os
import re
import functools
import time
from contextlib import contextmanager
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
SCHEDULED_TIME = "02:00"           # 夜间定时任务的约定触发时刻（上海时间）
DELAY_NOTE_MINUTES = 30            # 实际开始晚于约定时刻超过这个分钟数，须登记原因；原因没有证据时保持 UNPROVEN
COVERAGE_ENTRIES = ("日常监督任务", "开业前检查任务", "举报投诉核查任务", "火灾事故调查核查任务", "社会单位产品抽查任务")
DISPOSITIONS = frozenset({"OBSERVED", "REGISTERED", "OUT_OF_SCOPE"})

# Fixed order. Write stages reach outside the local ledger; an interrupted one is an unknown write.
STAGES = ("discover", "acquire", "organize", "validate", "sync", "mirror", "reconcile", "report")
WRITE_STAGES = frozenset({"acquire", "sync"})
TERMINAL = frozenset({"done", "failed", "skipped", "reconciled"})
STATUSES = frozenset({"started", "unknown", "quota", *TERMINAL})
# 夜间发现的十个入口：五类任务列表 × 两个窗口（创建时间、检查日期）；discover 阶段按入口各记一条检查点。
DISCOVER_ENTRIES = tuple(f"{name}/{window}" for name in COVERAGE_ENTRIES for window in ("创建时间", "检查日期"))
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


MANUAL_DIR = "手工"
MANUAL_ID = re.compile(r"^[A-Za-z0-9]{1,12}$")


def split_run_key(key: str) -> tuple[str, str]:
    """``2026-11-19`` -> (day, "") for the regular run; ``2026-11-19@m1`` -> (day, "m1") for a same-day manual run."""
    day, _, manual = key.partition("@")
    try:
        date.fromisoformat(day)
    except ValueError as error:
        raise NightlyError("运行日期必须是 YYYY-MM-DD（手工运行为 YYYY-MM-DD@编号）") from error
    if "@" in key and not MANUAL_ID.match(manual):
        raise NightlyError("手工运行编号只能是 1–12 位字母或数字")
    return day, manual


def lane_key(day: str, manual: str = "") -> str:
    """The run key a caller means: the regular run of ``day``, or its manual lane when a manual id is given (an id already in ``day`` must agree)."""
    base, existing = split_run_key(day)
    if manual and not MANUAL_ID.match(manual):
        raise NightlyError("手工运行编号只能是 1–12 位字母或数字")
    if manual and existing and existing != manual:
        raise NightlyError(f"--date 已带手工编号 {existing}，与 --manual-run {manual} 不一致")
    manual = manual or existing
    return f"{base}@{manual}" if manual else base


def _run_dir(root: Path, day: str) -> Path:
    base, manual = split_run_key(day)
    return root / RUNS_DIR / MANUAL_DIR / f"{base}@{manual}" if manual else root / RUNS_DIR / base


def checkpoints(root: Path, day: str) -> list[dict[str, Any]]:
    path = _run_dir(root, day) / "checkpoints.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _item_key(item: dict[str, Any]) -> tuple[str, str, str]:
    """A checkpoint's identity: (stage, case, entry). Records written before ``entry`` existed have no entry."""
    return (item["stage"], item.get("case") or "", item.get("entry") or "")


def _reported(item: dict[str, Any]) -> int:
    """What the writer of a checkpoint said it changed (``changed`` in records written before 2026-11-17)."""
    return int(item.get("reportedChanged", item.get("changed")) or 0)


def _latest(items: list[dict[str, Any]]) -> dict[tuple[str, str, str], dict[str, Any]]:
    latest: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in items:
        latest[_item_key(item)] = item
    return latest


def progress(root: Path, day: str) -> dict[str, Any]:
    """Latest state per (stage, case, entry) and what follows from it."""
    latest = _latest(checkpoints(root, day))
    stage_state = {stage: (latest.get((stage, "", "")) or {}).get("status") for stage in STAGES}
    open_items = [item for item in latest.values() if item["status"] not in TERMINAL]
    unknown = [item for item in open_items
               if item["status"] == "unknown" or (item["status"] == "started" and item["stage"] in WRITE_STAGES)]
    quota = [item for item in open_items if item["status"] == "quota"]
    resumable = [item for item in open_items if item not in unknown and item not in quota]
    done_steps = sorted([key[0], key[1]] for key, item in latest.items() if key[1] and not key[2] and item["status"] in TERMINAL)
    done_entries = sorted(list(key) for key, item in latest.items() if key[2] and item["status"] in TERMINAL)
    next_stage = next((stage for stage in STAGES if stage_state[stage] not in TERMINAL), None)
    return {"stages": stage_state, "nextStage": next_stage, "unknown": unknown, "quota": quota, "resumable": resumable,
            "finishedSteps": done_steps, "finishedEntries": done_entries, "reportedChanged": sum(_reported(i) for i in latest.values())}


def with_stats(root: Path, day: str, state: dict[str, Any], *, evidence_dir: str = "") -> dict[str, Any]:
    """``state`` (from ``progress``) plus the evidence-based ``changed``/``breakdown`` of ``run_stats``, so every aggregate output
    counts the same way. ``reportedChanged`` stays what the checkpoint writers said; a self-reported change with no evidence
    is flagged as ``unverifiedReportedChanges`` rather than read as no change."""
    run = _load(_run_dir(root, day) / "run.json", {})
    stats = run_stats(root, day, evidence_dir=evidence_dir or run.get("evidenceDir", ""))
    result = {**state, "changed": stats["changed"], "breakdown": stats["breakdown"], "reportedChanged": stats["reportedChanged"]}
    if stats["reportedChanged"] > 0 and stats["changed"] == 0:
        result["unverifiedReportedChanges"] = stats["reportedChanged"]
    return result


def issues_of(root: Path, day: str) -> list[dict[str, Any]]:
    """What a finished run still carries: failed items, items kept as an explicit issue, discover entries never recorded.

    Current view = latest state per item; the failed history stays in ``checkpoints.jsonl``."""
    items = checkpoints(root, day)
    latest = _latest(items)
    found = []
    for key, item in sorted(latest.items()):
        if item["status"] == "failed" or item.get("issue"):
            found.append({"kind": "failed" if item["status"] == "failed" else "retained-issue", "stage": key[0], "case": key[1],
                          "entry": key[2], "status": item["status"], "detail": item.get("detail", ""), "ref": item.get("ref", "")})
    run = _load(_run_dir(root, day) / "run.json", {})
    if run.get("entryKeyed"):
        for entry in DISCOVER_ENTRIES:
            if (latest.get(("discover", "", entry)) or {}).get("status") not in TERMINAL:
                found.append({"kind": "entry-incomplete", "stage": "discover", "case": "", "entry": entry,
                              "status": (latest.get(("discover", "", entry)) or {}).get("status"), "detail": "入口没有终态检查点", "ref": ""})
    return found


def _issue_signature(issue: dict[str, Any]) -> tuple[str, str, str, str]:
    return (issue["kind"], issue["stage"], issue.get("case") or "", issue.get("entry") or "")


def _run_days(root: Path, *, lanes: bool = False) -> list[str]:
    """Regular run days; with ``lanes`` also the same-day manual runs (key ``YYYY-MM-DD@编号``, sorted right after their regular day)."""
    directory = root / RUNS_DIR
    days = [p.name for p in directory.iterdir() if p.is_dir() and p.name != MANUAL_DIR and (p / "run.json").is_file()] if directory.is_dir() else []
    manual = directory / MANUAL_DIR
    if lanes and manual.is_dir():
        days += [p.name for p in manual.iterdir() if p.is_dir() and (p / "run.json").is_file()]
    return sorted(days)


BEGIN_LOCK = ".begin.lock"
BEGIN_LOCK_WAIT_SECONDS = 20


@contextmanager
def _begin_lock(root: Path):
    """Short kernel-owned lock on the run entry of one data root (process exit releases it, no stale-PID guessing). Every ``begin``
    holds it from the checks until the start record is written, so two processes cannot both pass the checks."""
    directory = root / RUNS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / BEGIN_LOCK).open("a+b") as stream:
        if stream.seek(0, 2) == 0:
            stream.write(b"0")
            stream.flush()
        deadline = time.monotonic() + BEGIN_LOCK_WAIT_SECONDS
        while True:
            stream.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as error:
                if time.monotonic() >= deadline:
                    raise NightlyError("另一个夜间 begin 正在检查或写启动记录（锁 20 秒未释放）：稍后再试，不要手工清锁") from error
                time.sleep(0.05)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def _under_begin_lock(function):
    """Read-modify-write of an existing run.json under the same short lock as ``begin`` (a missing record is left to the function's own error)."""
    @functools.wraps(function)
    def wrapper(root: Path, day: str, *args: Any, **kwargs: Any):
        if not (_run_dir(root, day) / "run.json").is_file():
            return function(root, day, *args, **kwargs)
        with _begin_lock(root):
            return function(root, day, *args, **kwargs)
    return wrapper


def begin(root: Path, *, now: datetime | None = None, trigger: str = "scheduled", actor: str = "",
          evidence_dir: str = "", manual: str = "", day: str | None = None) -> dict[str, Any]:
    """Locked entry (see ``_begin``). A run that is already FINISHED only reads, so it needs no lock and leaves no lock file behind."""
    now_day = (now or now_shanghai()).astimezone(SHANGHAI).date().isoformat()
    key = lane_key(split_run_key(day or now_day)[0], manual) if manual and MANUAL_ID.match(manual) else now_day
    if (_load(_run_dir(root, key) / "run.json", {}) or {}).get("status") == "FINISHED":
        return _begin(root, now=now, trigger=trigger, actor=actor, evidence_dir=evidence_dir, manual=manual, day=day)
    with _begin_lock(root):
        return _begin(root, now=now, trigger=trigger, actor=actor, evidence_dir=evidence_dir, manual=manual, day=day)


def _earlier_unfinished(root: Path, before: str, *, skip: str = "") -> list[dict[str, Any]]:
    """Runs older than ``before`` (a run key) that were never finished, with their derived status and unknown items."""
    earlier = []
    for other in _run_days(root, lanes=True):
        if other < before and other != skip:
            state = _load(_run_dir(root, other) / "run.json", {})
            if state.get("status") != "FINISHED":
                earlier.append({"date": other, "status": state.get("status"), "derivedStatus": run_view(root, other)["derivedStatus"],
                                "unknown": progress(root, other)["unknown"]})
    return earlier


def _begin(root: Path, *, now: datetime | None = None, trigger: str = "scheduled", actor: str = "",
           evidence_dir: str = "", manual: str = "", day: str | None = None) -> dict[str, Any]:
    """Open today's run, resume it, or report that it is already finished (no write in that case).

    ``manual`` opens (or resumes) an extra same-day run next to the regular one: only when the regular run of that business day is already
    FINISHED and nothing is running or bound anywhere, in its own record directory (``夜间运行/手工/<日>@<编号>``) with its own checkpoints,
    bindings and snapshots. The regular run's files are never touched; both share the ledger, coverage log, observation queue and source files."""
    now = (now or now_shanghai()).astimezone(SHANGHAI)
    if manual:
        return _begin_manual(root, now=now, trigger=trigger, actor=actor, evidence_dir=evidence_dir, manual=manual, day=day or now.date().isoformat())
    day = now.date().isoformat()
    path = _run_dir(root, day) / "run.json"
    earlier = _earlier_unfinished(root, day)
    finished = [d for d in _run_days(root, lanes=True) if _load(_run_dir(root, d) / "run.json", {}).get("status") == "FINISHED"]
    missed: list[str] = []
    if finished:
        cursor = date.fromisoformat(split_run_key(finished[-1])[0]) + timedelta(days=1)
        while cursor.isoformat() < day:
            missed.append(cursor.isoformat())
            cursor += timedelta(days=1)
    run = _load(path, None)
    if run is not None and run.get("status") == "FINISHED":
        stats = with_stats(root, day, {}, evidence_dir=evidence_dir)         # 读取同一证据口径，不回显可能是旧口径的已存值，也不写盘
        return {"status": "ALREADY_FINISHED", "date": day, "finishedAt": run.get("finishedAt"), "wrote": False, **stats,
                "manualRunHint": "同日要另跑一轮：--manual-run <编号>（独立记录与证据目录，不改本日运行）"}
    if run is None:
        run = {"schemaVersion": "NightlyRunV1", "date": day, "status": "RUNNING", "startedAt": now.isoformat(timespec="seconds"),
               "triggers": [], "missedDates": missed, "entryKeyed": True, "schedule": _schedule(day, now, trigger)}
        outcome = "STARTED"
    else:
        outcome = "RESUME"
        run["status"] = "RUNNING"
    if evidence_dir:
        run["evidenceDir"] = evidence_dir
    run["triggers"].append({"at": now.isoformat(timespec="seconds"), "kind": trigger, "actor": actor})
    _atomic(path, run)
    carried = carry_issues(root, finished[-1]) if finished else []
    return {"status": outcome, "date": day, "missedDates": missed, "earlierUnfinished": earlier, **with_stats(root, day, progress(root, day), evidence_dir=evidence_dir),
            "weeklyCoverage": coverage_state(root, today=day), "lastFinishedDate": finished[-1] if finished else None,
            "openExecutions": open_executions(root), "carryIssues": carried,
            "rule": "先对账 unknown（回读，不重放）；到期事项按绝对到期日处理，错过的夜晚不单独补跑"}


def _begin_manual(root: Path, *, now: datetime, trigger: str, actor: str, evidence_dir: str, manual: str, day: str) -> dict[str, Any]:
    if not MANUAL_ID.match(manual):
        raise NightlyError("手工运行编号只能是 1–12 位字母或数字")
    base, existing = split_run_key(day)
    if existing:
        raise NightlyError("--date 不带手工编号；编号用 --manual-run 给")
    key = f"{base}@{manual}"
    path = _run_dir(root, key) / "run.json"
    run = _load(path, None)
    if run is not None and run.get("status") == "FINISHED":
        stats = with_stats(root, key, {}, evidence_dir=evidence_dir)
        return {"status": "ALREADY_FINISHED", "date": key, "lane": run.get("lane"), "finishedAt": run.get("finishedAt"), "wrote": False, **stats}
    regular = _load(_run_dir(root, base) / "run.json", None)
    if run is None:
        if regular is None:
            raise NightlyError(f"{base} 没有常规运行：用常规 begin，不开手工运行")
        if regular.get("status") != "FINISHED":
            raise NightlyError(f"{base} 的常规运行还没结束（{regular.get('status')}）：先续办常规运行，手工运行不叠加在进行中的运行上")
        for other in _run_days(root, lanes=True):
            state = _load(_run_dir(root, other) / "run.json", {})
            if other == base or state.get("status") == "FINISHED":
                continue
            if "@" in other:
                raise NightlyError(f"已有未结束的手工运行 {other}：先收尾或对账，手工运行不与它交叉写入")
            if [i for i in progress(root, other)["unknown"] if i["stage"] in WRITE_STAGES]:
                raise NightlyError(f"更早的运行 {other} 留有效果未知的写入：先回读对账（reconciled），手工运行不与它交叉写入")
            # 更早的运行只是没收尾、也没有未知写入或绑定执行：属历史未完成事项，由 earlierUnfinished/carry 列出，不拦手工运行
        running = open_executions(root)
        if running:
            raise NightlyError(f"还有未记终态的绑定执行（{running[0]['date']} {running[0].get('sessionId', '')}）：先 bind-end 或对账，手工运行不与它交叉写入")
        _run_dir(root, key).mkdir(parents=True, exist_ok=True)           # 在 begin 锁内：没有 run.json 的空目录是启动中途退出的空壳，直接接续；同时启动者由锁挡住
        run = {"schemaVersion": "NightlyRunV1", "date": base, "status": "RUNNING", "startedAt": now.isoformat(timespec="seconds"),
               "triggers": [], "missedDates": [], "entryKeyed": True, "schedule": _schedule(base, now, "manual"),
               "lane": {"kind": "manual", "id": manual, "businessDate": base, "regularRun": base, "regularFinishedAt": regular.get("finishedAt")}}
        outcome = "STARTED"
    else:
        active = [i for i in open_executions(root) if i["date"] == key]
        if active:
            raise NightlyError(f"手工运行 {key} 已有未记终态的绑定执行（{active[0].get('sessionId', '')}）：先 bind-end 或对账，不重复切入")
        outcome = "RESUME"
        run["status"] = "RUNNING"
    if evidence_dir:
        run["evidenceDir"] = evidence_dir
    run["triggers"].append({"at": now.isoformat(timespec="seconds"), "kind": trigger if trigger != "scheduled" else "manual", "actor": actor})
    _atomic(path, run)
    finished = [d for d in _run_days(root, lanes=True) if d < key and _load(_run_dir(root, d) / "run.json", {}).get("status") == "FINISHED"]
    return {"status": outcome, "date": key, "lane": run["lane"], "earlierUnfinished": _earlier_unfinished(root, key), **with_stats(root, key, progress(root, key), evidence_dir=evidence_dir),
            "weeklyCoverage": coverage_state(root, today=base), "lastFinishedDate": finished[-1] if finished else None,
            "openExecutions": open_executions(root), "carryIssues": carry_issues(root, finished[-1]) if finished else [],
            "rule": "手工运行是额外一轮：沿同一账本、覆盖、观察队列和原始资料，不改当天常规运行的任何记录；先对账 unknown（回读，不重放）；每周全范围核对不因手工运行重复"}


def _schedule(day: str, started: datetime, kind: str = "scheduled") -> dict[str, Any]:
    """Expected vs actual start of a run. A late start gets ``delayCause`` UNPROVEN until ``delay_cause`` records a cause with its
    evidence; a cause is never guessed from the delay itself."""
    expected = datetime.fromisoformat(f"{day}T{SCHEDULED_TIME}:00").replace(tzinfo=SHANGHAI)
    delay = round((started.astimezone(SHANGHAI) - expected).total_seconds() / 60)
    schedule: dict[str, Any] = {"expectedAt": expected.isoformat(timespec="seconds"), "actualAt": started.astimezone(SHANGHAI).isoformat(timespec="seconds"),
                                "delayMinutes": delay}
    if kind != "scheduled":
        schedule["manualStart"] = True        # 人工发起的运行没有“应在 02:00 触发”的预期，迟起不适用
    elif delay > DELAY_NOTE_MINUTES:
        schedule["delayCause"] = {"status": "UNPROVEN"}
    return schedule


def delay_cause(root: Path, day: str, *, cause: str, evidence: str, now: datetime | None = None) -> dict[str, Any]:
    """Record the proven cause of a late start with the evidence that proves it. Needs both; an earlier cause stays in ``history``."""
    path = _run_dir(root, day) / "run.json"
    run = _load(path, None)
    if run is None:
        raise NightlyError("该日期没有运行记录")
    if not cause.strip() or not evidence.strip():
        raise NightlyError("登记迟起原因需要原因（--detail）和证据位置（--ref）；没有证据时保持 UNPROVEN，不猜")
    schedule = run.get("schedule") or _schedule_for(run)
    if schedule.get("manualStart") or schedule.get("delayMinutes", 0) <= DELAY_NOTE_MINUTES:
        raise NightlyError("该次运行是人工发起或没有超过迟起阈值，无需登记迟起原因")
    previous = schedule.get("delayCause") or {"status": "UNPROVEN"}
    entry = {"status": "PROVEN", "cause": cause.strip(), "evidence": evidence.strip(),
             "at": (now or now_shanghai()).astimezone(SHANGHAI).isoformat(timespec="seconds")}
    if previous.get("status") == "PROVEN":
        entry["history"] = [*previous.get("history", []), {k: previous[k] for k in ("cause", "evidence", "at") if k in previous}]
    schedule["delayCause"] = entry
    run["schedule"] = schedule
    _atomic(path, run)
    return {"status": "RECORDED", "date": day, "schedule": schedule}


def _schedule_for(run: dict[str, Any]) -> dict[str, Any]:
    """Schedule of a record written before the field existed: derived from its own date and first start, flagged so."""
    triggers = run.get("triggers") or [{}]
    return {**_schedule(run["date"], _parse_time(run["startedAt"]), triggers[0].get("kind") or "scheduled"), "derived": True}


def run_view(root: Path, day: str) -> dict[str, Any]:
    """One run in one place: expected/actual start and delay with its cause, every execution (a second one is a takeover and says
    why the earlier one ended), and a derived state that tells a finished interruption from a run that is really active. The stored
    ``status`` is never rewritten by this view; an old run left ``RUNNING`` after its executions ended shows as INTERRUPTED_UNFINISHED."""
    run = _load(_run_dir(root, day) / "run.json", None)
    if run is None:
        return {"date": day, "derivedStatus": "NO_RUN"}
    executions = run.get("executions", [])
    open_now = [e for e in executions if not e.get("endedAt")]
    stored = run.get("status")
    if stored != "RUNNING":
        derived = stored
    elif open_now:
        derived = "ACTIVE"
    elif executions:
        derived = "INTERRUPTED_UNFINISHED"
    else:
        derived = "RUNNING_UNBOUND"
    takeovers = [{"sessionId": item["sessionId"], "boundAt": item["boundAt"], "after": {"sessionId": before["sessionId"], "endState": before.get("endState"),
                  "endedAt": before.get("endedAt"), "detail": before.get("endDetail", "")}}
                 for before, item in zip(executions, executions[1:])]
    last = executions[-1] if executions else None
    return {"date": day, "storedStatus": stored, "derivedStatus": derived, "triggers": run.get("triggers", []),
            "schedule": run.get("schedule") or (_schedule_for(run) if run.get("startedAt") else None),
            "executions": [{k: e.get(k) for k in ("sessionId", "boundAt", "endedAt", "endState")} for e in executions], "takeovers": takeovers,
            "finalState": {"stored": stored, "lastExecutionEnd": (last or {}).get("endState"), "finishedAt": run.get("finishedAt"),
                           "finishedWithIssues": run.get("finishedWithIssues")}}


def timeline(root: Path, *, since: str | None = None) -> list[dict[str, Any]]:
    return [run_view(root, day) for day in _run_days(root, lanes=True) if not since or day >= since]


def checkpoint(root: Path, day: str, *, stage: str, status: str, case: str | None = None, detail: str = "",
               ref: str = "", changed: int = 0, entry: str = "", issue: bool = False, now: datetime | None = None,
               window: tuple[str, str] | None = None) -> dict[str, Any]:
    if window is not None:
        window = (split_run_key(window[0])[0], split_run_key(window[1])[0])
        if window[0] > window[1]:
            raise NightlyError("--window 的起日不能晚于止日")
    if stage not in STAGES or status not in STATUSES:
        raise NightlyError(f"stage 必须是 {'/'.join(STAGES)}；status 必须是 {'/'.join(sorted(STATUSES))}")
    path = _run_dir(root, day) / "run.json"
    run = _load(path, None)
    if run is None:
        raise NightlyError("该日期没有运行记录，先 begin")
    if run.get("status") == "FINISHED":
        raise NightlyError("该日期的运行已结束，不能再追加检查点")
    all_latest = _latest(checkpoints(root, day))
    key = (stage, case or "", entry or "")
    latest = all_latest.get(key)
    if latest is not None and status == "started" and (
            latest["status"] == "unknown" or (latest["status"] == "started" and stage in WRITE_STAGES)):
        raise NightlyError("该步骤上次的效果未知，先回读对账并记 reconciled，不能直接重新开始")
    if latest is not None and latest["status"] == "unknown" and status != "unknown":
        if status == "reconciled":
            if not detail.strip():
                raise NightlyError("reconciled 需要 --detail 写明回读到的实际效果")
            if stage in WRITE_STAGES and not ref.strip():
                raise NightlyError(f"{stage} 是写阶段：reconciled 还需要 --ref 指向原回执，并以真实回读闭合")
        elif status == "skipped" and issue and detail.strip() and stage not in WRITE_STAGES:
            pass                                       # 已证实的来源问题，作为显式保留事项记录
        else:     # quota 也不行：它会让该项的最新状态不再是 unknown，等于间接覆盖
            raise NightlyError(f"该步骤的效果未知：不能记为 {status}，只能回读对账后记 reconciled"
                               + ("（写阶段需 --ref 原回执）" if stage in WRITE_STAGES else "；已证实的非写阶段来源问题可用 skipped --issue --detail 保留"))
    if status == "done" and not key[1] and not key[2]:
        expected = set(DISCOVER_ENTRIES) if stage == "discover" and run.get("entryKeyed") else set()
        pending = [k for k, i in all_latest.items() if k[0] == stage and (k[1] or k[2]) and i["status"] not in TERMINAL]
        failed = [k for k, i in all_latest.items() if k[0] == stage and (k[1] or k[2]) and i["status"] == "failed"]
        if failed:
            names = "、".join((k[1] or k[2]) for k in failed[:4])
            raise NightlyError(f"{stage} 有 {len(failed)} 项失败（{names}）：整阶段不能记 done；"
                               "先重试成功，或对已证实的来源问题记 skipped --issue --detail，或整阶段记 failed 并带出")
        pending += [(stage, "", e) for e in sorted(expected) if (all_latest.get((stage, "", e)) or {}).get("status") not in TERMINAL]
        if pending:
            names = "、".join((k[1] or k[2]) for k in pending[:4])
            raise NightlyError(f"{stage} 还有 {len(pending)} 项没到终态（{names}）：整阶段不能记 done")
    item = {"at": (now or now_shanghai()).astimezone(SHANGHAI).isoformat(timespec="seconds"), "stage": stage, "status": status}
    if case:
        item["case"] = case
    if entry:
        item["entry"] = entry
    if detail:
        item["detail"] = detail
    if ref:
        item["ref"] = ref
    if changed:
        item["reportedChanged"] = changed
    if issue:
        item["issue"] = True
    if window is not None:
        item["window"] = list(window)      # 发现入口本次实际读取的日期窗口：入口水位只按完整成功的窗口推进
    with (_run_dir(root, day) / "checkpoints.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    dirty = False
    if entry and not run.get("entryKeyed"):
        run["entryKeyed"] = dirty = True
    if status == "quota" and run.get("status") != "PAUSED_QUOTA":
        run["status"] = "PAUSED_QUOTA"
        dirty = True
    if dirty:
        _atomic(path, run)
    return item


# 到期案按 scan-plan 的动作需要走完的最早阶段；discover 只是认出案卷身份，不算任何到期阶段
DUE_STAGE = {"ACQUIRE_FIRST": "acquire", "COLLECT": "acquire", "CLASSIFY": "organize", "LOCAL_VERIFY": "validate"}
PROTECTED_WORDS = ("保护", "锁", "批次", "待整理标记", "人工放行")


def _due_state(items: list[dict[str, Any]], required: list[str]) -> dict[str, Any]:
    """One due case's end mark from the stages it actually still had to pass. ``items`` are the latest checkpoints of the case.

    A stage is finished by a ``done``/``reconciled`` mark at that stage or a later one (a case that got further passed it), or closed by an
    explained ``skipped --issue``/``failed`` mark at that stage or an earlier one (the case cannot go further). Discover marks never count."""
    order = {stage: index for index, stage in enumerate(STAGES)}
    work = [i for i in items if i["stage"] != "discover"]
    if any(i["status"] == "unknown" or (i["status"] == "started" and i["stage"] in WRITE_STAGES) for i in work):
        return {"closed": False, "kind": "unknown", "open": required, "detail": "效果未知的写入，先回读对账"}
    open_stages: list[str] = []
    closure: dict[str, Any] | None = None
    for stage in required:
        reached = any(i["status"] in ("done", "reconciled") and order[i["stage"]] >= order[stage] for i in work)
        closed_by = next((i for i in work if i["status"] in ("skipped", "failed") and (i.get("detail") or "").strip()
                          and (i["status"] == "failed" or i.get("issue")) and order[i["stage"]] <= order[stage]), None)
        if reached:
            continue
        if closed_by is not None:
            closure = closure or closed_by
            continue
        open_stages.append(stage)
    if open_stages:
        return {"closed": False, "kind": "open", "open": open_stages, "detail": ""}
    if closure is None:
        return {"closed": True, "kind": "completed", "open": [], "detail": ""}
    detail = _clean(closure.get("detail"), 80)
    return {"closed": True, "kind": "protected" if any(word in detail for word in PROTECTED_WORDS) else "needs-evidence", "open": [], "detail": detail}


def due_cases(root: Path, day: str, *, evidence_dir: str = "") -> dict[str, Any]:
    """The cases tonight's own ``source scan-plan`` (``<证据目录>/scan-plan.json``) listed as to-do (``pending`` and ``packageAcquireFirst``)
    and which of them still lack an end mark for the stages their own action needs. A discover ``done`` only identifies a case; a case listed
    in several queues is one entry with all its queues and the union of required stages; an unknown-effect write on the case is reconciled first
    (it is never closed here). No scan-plan file (a narrow manual run, a night that never planned) means nothing is due here; no new ledger is
    created, the checkpoints are the traceable end marks: ``states`` carries per case its queues, required stages and a compact
    completed / needs-evidence / protected / open verdict."""
    run = _load(_run_dir(root, day) / "run.json", {}) or {}
    folder = Path(_native(evidence_dir or run.get("evidenceDir", "") or ""))
    plan_path = folder / "scan-plan.json"
    if not str(folder) or not plan_path.is_file():
        return {"planned": False, "cases": [], "unmarked": [], "states": []}
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"planned": False, "cases": [], "unmarked": [], "states": [], "unreadable": str(plan_path)}
    queues: dict[str, dict[str, Any]] = {}
    for key in ("pending", "packageAcquireFirst"):
        for item in plan.get(key, []):
            if isinstance(item, dict) and item.get("projectNo"):
                entry = queues.setdefault(str(item["projectNo"]), {"queues": [], "required": set()})
                action = str(item.get("action") or "")
                entry["queues"].append(f"{key}/{action}" if action else key)
                entry["required"].add(DUE_STAGE.get(action, "acquire"))
    latest = _latest(checkpoints(root, day))
    states = []
    for project in sorted(queues):
        required = sorted(queues[project]["required"], key=STAGES.index)
        state = _due_state([i for k, i in latest.items() if k[1] == project and not k[2]], required)
        states.append({"case": project, "queues": sorted(set(queues[project]["queues"])), "required": required, **state})
    return {"planned": True, "cases": sorted(queues), "unmarked": [row["case"] for row in states if not row["closed"]], "states": states}


DISCOVER_FLOOR = "2026-01-01"           # 既有真实初查范围起点：发现窗口不早于它
DISCOVER_OVERLAP_DAYS = 3               # 每次从最近一次完整覆盖往前多读 3 天
DISCOVER_BOOTSTRAP_DAYS = 14            # 从没有任何完整成功记录的入口：往前读 14 天（原行为）
DISCOVER_LEGACY_LAG_DAYS = 3            # 没有记窗口的旧完整检查点：保守按“只覆盖到运行日前 3 天”处理


def discover_starts(root: Path, day: str) -> dict[str, Any]:
    """Per entry (five task lists x 创建时间/检查日期) the start of the window to read on run ``day``.

    The basis is the entry's own most recent COMPLETE read: a ``discover`` checkpoint with status ``done`` (the read reached the last page and
    matched the page total; zero rows with a fully read page counts). A failed, skipped (502, source unavailable) or incomplete entry never
    advances; successes roll on. Windows are chained in time order: a window only extends the covered range when it starts at or before the day
    after the range already covered, so a narrow manual run with its own start cannot move the mark across an unread gap. Checkpoints written before
    windows were recorded count as covering through three days before their run day (a conservative lookback). A COMPLETE weekly full-range
    check (inspection-date entries) covers its whole range. The start is the covered-through day minus the overlap, never before 2026-01-01; the
    unread span can be any length, the next window simply reaches back to it. Nothing is written."""
    base_day = split_run_key(day)[0]
    current = date.fromisoformat(base_day)
    floor = date.fromisoformat(DISCOVER_FLOOR)
    runs = [key for key in _run_days(root, lanes=True) if key <= day]
    through: dict[str, date] = {}
    basis: dict[str, str] = {}
    unread: dict[str, date] = {}      # 还没有任何完整覆盖的入口：已证实没读完整的最早起点（失败/跳过/没读完的窗口起点）
    gaps: dict[str, list[list[str]]] = {entry: [] for entry in DISCOVER_ENTRIES}
    checks = sorted(coverage_checks(root), key=lambda c: c.get("at", ""))
    events: list[tuple[str, str, str, dict[str, Any]]] = []
    for key in runs:
        for item in _latest(checkpoints(root, key)).values():
            if item["stage"] == "discover" and not item.get("case") and item.get("entry") in DISCOVER_ENTRIES:
                events.append((item["at"], "checkpoint", key, item))
    for check in checks:
        if check.get("dateField") == "检查日期" and str(check.get("scopeFrom", "")) <= DISCOVER_FLOOR:
            events.append((check.get("at", ""), "weekly", check.get("date", ""), check))
    for at, kind, key, item in sorted(events, key=lambda e: (e[0], e[1])):
        if kind == "weekly":
            for name, entry_state in (item.get("entries") or {}).items():
                entry = f"{name}/检查日期"
                if entry in gaps and entry_state.get("complete"):
                    end = min(date.fromisoformat(item["scopeTo"]), date.fromisoformat(key))
                    if end > through.get(entry, date.min):
                        through[entry], basis[entry] = end, "weekly-coverage"
            continue
        entry = item["entry"]
        run_day = date.fromisoformat(split_run_key(key)[0])
        if item["status"] != "done":
            if entry not in through:      # 没读完整：该运行本该从 min(窗口起点, 运行日前 14 天) 读起，这段至今没被证实读过
                first = min(date.fromisoformat(item["window"][0]) if item.get("window") else run_day, run_day - timedelta(days=DISCOVER_BOOTSTRAP_DAYS))
                unread[entry] = min(unread.get(entry, first), max(floor, first))
            continue
        if item.get("window"):
            start, end = (date.fromisoformat(value) for value in item["window"])
            if entry in through:
                reaches = start <= through[entry] + timedelta(days=1)
            else:      # 第一次完整覆盖：窗口必须从常规起点（运行日前 14 天）或该入口已证实未读的最早起点（取更早者）读起；手工或常规的显式窄窗留下检查点，但不替代未读跨度
                reaches = start <= max(floor, min(unread.get(entry, run_day), run_day - timedelta(days=DISCOVER_BOOTSTRAP_DAYS)))
            if not reaches:
                gaps[entry].append([start.isoformat(), end.isoformat()])      # 与应覆盖的范围之间有未读缺口：这次读取不推进水位
                if entry not in through:      # 没有任何覆盖的入口：缩窄的这次读取也证明其本该读的起点至今未读
                    first = max(floor, min(start, run_day - timedelta(days=DISCOVER_BOOTSTRAP_DAYS)))
                    unread[entry] = min(unread.get(entry, first), first)
                continue
            if end > through.get(entry, date.min):
                through[entry], basis[entry] = end, "checkpoint-window"
        else:
            end = run_day - timedelta(days=DISCOVER_LEGACY_LAG_DAYS)
            if end > through.get(entry, date.min):
                through[entry], basis[entry] = end, "legacy-checkpoint"
    starts: dict[str, Any] = {}
    for entry in DISCOVER_ENTRIES:
        if entry in through:
            start = max(floor, through[entry] - timedelta(days=DISCOVER_OVERLAP_DAYS))
            starts[entry] = {"start": start.isoformat(), "end": base_day, "coveredThrough": through[entry].isoformat(), "basis": basis[entry],
                             "unreadDays": max(0, (current - through[entry]).days), "gapWindows": gaps[entry]}
        else:      # 没有任何完整成功记录：从已证实未读的最早起点读起（首次失败早于 14 天也补回，不从次日截断），否则按原行为往前 14 天
            first = min(unread.get(entry, current), current - timedelta(days=DISCOVER_BOOTSTRAP_DAYS))
            start = max(floor, first)
            starts[entry] = {"start": start.isoformat(), "end": base_day, "coveredThrough": None, "basis": "uncovered-failure" if entry in unread and unread[entry] < current - timedelta(days=DISCOVER_BOOTSTRAP_DAYS) else "bootstrap",
                             "unreadDays": None, "gapWindows": gaps[entry]}
    return {"date": day, "entries": starts}


def finish(root: Path, day: str, *, summary: str = "", evidence_dir: str = "", refresh_stats: bool = False,
           now: datetime | None = None) -> dict[str, Any]:
    path = _run_dir(root, day) / "run.json"
    run = _load(path, None)
    if run is None:
        raise NightlyError("该日期没有运行记录")
    if run.get("status") == "FINISHED":
        if not refresh_stats:
            return {"status": "ALREADY_FINISHED", "date": day, "wrote": False}
        # 已收尾的运行只允许按证据重算统计并留下修订记录；状态、收尾时间和检查点不动
        stats = run_stats(root, day, evidence_dir=evidence_dir or run.get("evidenceDir", ""), now=now)
        run.setdefault("statsRevisions", []).append({"at": (now or now_shanghai()).astimezone(SHANGHAI).isoformat(timespec="seconds"),
                                                     "previous": {"changed": run.get("changed"), "breakdown": run.get("breakdown")},
                                                     "changed": stats["changed"], "breakdown": stats["breakdown"]})
        run.update(changed=stats["changed"], breakdown=stats["breakdown"], reportedChanged=stats["reportedChanged"])
        _atomic(path, run)
        return {"status": "STATS_REFRESHED", "date": day, "changed": stats["changed"], "breakdown": stats["breakdown"], "wrote": True}
    state = progress(root, day)
    base, manual = split_run_key(day)
    weekly = coverage_state(root, today=base)
    weekly_due = weekly["due"] and not manual      # 手工运行是额外一轮：每周全范围核对由常规运行负责，不因手工运行重复，也不因它到期而拦住收尾
    if state["unknown"] or state["quota"] or state["resumable"] or state["nextStage"] is not None:
        return {"status": "NOT_FINISHED", "date": day, "wrote": False, **with_stats(root, day, state, evidence_dir=evidence_dir)}
    due = due_cases(root, day, evidence_dir=evidence_dir)
    if due["unmarked"]:
        return {"status": "NOT_FINISHED", "date": day, "wrote": False, **with_stats(root, day, state, evidence_dir=evidence_dir), "dueCasesUnmarked": due["unmarked"],
                "dueCaseStates": [row for row in due["states"] if not row["closed"]],
                "rule": "今晚 scan-plan 列出的到期案，按各自动作还没走完的阶段逐案终标（案卷发现 done 只算认出身份）：能办的继续办并在对应阶段记 done；缺据、已保护或来源未出的在对应阶段记 skipped --issue --detail（写原因与依据）；效果未知的先回读记 reconciled"}
    if weekly_due and not weekly["attemptedToday"]:
        return {"status": "NOT_FINISHED", "date": day, "wrote": False, **with_stats(root, day, state, evidence_dir=evidence_dir), "coverageDue": weekly,
                "rule": "全范围检查日期核对已到期且今晚未做：先做并用 nightly coverage 登记（来源不可用也要登记这次尝试）"}
    stats = run_stats(root, day, evidence_dir=evidence_dir or run.get("evidenceDir", ""), now=now)
    issues = issues_of(root, day)
    not_attempted = stats["cloud"].get("openNotAttempted", [])
    if not_attempted:
        issues.append({"kind": "cloud-not-attempted", "stage": "sync", "case": "", "entry": "", "status": "skipped",
                       "detail": "原批停止后尚未发送的目标，按稳定键核对并生成新计划", "items": not_attempted})
    finished_before = [d for d in _run_days(root, lanes=True) if d < day and _load(_run_dir(root, d) / "run.json", {}).get("status") == "FINISHED"]
    own = [_carry_item(u, day) for u in stats["cloud"].get("unknown", [])]
    carried_open = [i for issue in (carry_issues(root, finished_before[-1]) if finished_before else []) if issue["kind"] == "cloud-unknown" for i in issue.get("items", [])]
    items: list[dict[str, Any]] = []
    seen_keys: set[tuple[Any, ...]] = set()
    for item in [*carried_open, *own]:       # 未决跨夜持续到证据闭合：前几夜仍未闭合的项与本夜新的未知合并；同一规格同一对象只留一项，不同对象各留各的
        key = _unknown_key(item)
        if key not in seen_keys:
            seen_keys.add(key)
            items.append(item)
    if items:      # 云写入证据里有未闭合的未知项：带出到下一轮，不静默写成 0
        issues.append({"kind": "cloud-unknown", "stage": "sync", "case": "", "entry": "", "status": "unknown",
                       "detail": f"云写入有 {len(items)} 项没有回执与账本共同证明（读不出的回执或账本非 OK，含前几夜未闭合的）：先回读对账，不重放；每项带原业务日和回执路径", "ref": "",
                       "items": items})      # 持久列表不截断；展示可缩略，不影响后续动作
    previous = {_issue_signature(i) for i in (_load(_run_dir(root, finished_before[-1]) / "run.json", {}).get("issues", []) if finished_before else [])}
    new_issues = [i for i in issues if _issue_signature(i) not in previous]
    prior_keys = {_unknown_key(i) for issue in (_load(_run_dir(root, finished_before[-1]) / "run.json", {}).get("issues", []) if finished_before else [])
                  if issue.get("kind") == "cloud-unknown" for i in issue.get("items", [])}
    for issue in issues:      # 同类事项已存在时，又出现的不同对象的未知仍算新增(按对象身份，不按事项类别)；同一对象重复出现保持安静
        if issue.get("kind") == "cloud-unknown" and issue not in new_issues and any(_unknown_key(i) not in prior_keys for i in issue.get("items", [])):
            new_issues.append(issue)
    unverified = stats["reportedChanged"] > 0 and stats["changed"] == 0      # 执行者自报有变化，却没有任何凭证：不当作“无变化”
    quiet = stats["changed"] == 0 and not weekly_due and not new_issues and not unverified
    run.update(status="FINISHED", finishedAt=(now or now_shanghai()).astimezone(SHANGHAI).isoformat(timespec="seconds"),
               changed=stats["changed"], quiet=quiet, summary=summary, breakdown=stats["breakdown"], issues=issues,
               finishedWithIssues=bool(issues), reportedChanged=state_reported(root, day))
    result = {"status": "FINISHED", "date": day, "changed": stats["changed"], "quiet": quiet, "breakdown": stats["breakdown"],
              "issues": issues, "finishedWithIssues": bool(issues)}
    if new_issues:
        result["newIssues"] = new_issues
    if unverified:
        result["unverifiedReportedChanges"] = stats["reportedChanged"]
    if weekly_due:
        run["weeklyCoverage"] = result["weeklyCoverage"] = weekly      # 今晚试过但未完整：如实带出，下一轮仍到期
    _atomic(path, run)
    return result


def _carry_item(unknown: dict[str, Any], day: str) -> dict[str, Any]:
    """One open cloud unknown as carried to later nights: the original business day, the exact receipt path (two nights can hold
    the same file name), and whatever run/intent/object the evidence proves."""
    item = {k: unknown.get(k) for k in ("receipt", "receiptPath", "runId", "table", "stableKey", "status", "intentIds") if unknown.get(k)}
    item["originDate"] = day
    return item


def _unknown_key(item: dict[str, Any]) -> tuple[Any, ...]:
    """Stable identity of one open unknown: its exact receipt path, original business day, run and target (stable key / table / intents).
    Two records of one receipt differ by target; an unreadable receipt with no target is identified by the receipt alone."""
    return (item.get("receiptPath") or item.get("receipt") or "", item.get("originDate") or "", item.get("runId") or "", item.get("table") or "",
            item.get("stableKey") or "", tuple(item.get("intentIds") or ()))


def _item_closed(root: Path, item: dict[str, Any], cache: dict[str, Any] | None = None) -> bool:
    """Late evidence for exactly this item: its own intents are all closed by a verified final/reconciliation, or its unreadable
    receipt now classifies as resolved / known-zero-action from the same specification's evidence. Never guessed; never unlocks anything."""
    cache = cache if cache is not None else {}
    day = str(item.get("originDate") or "")
    if day not in cache:      # 同一业务日的未决共用一次账本读取，不为每一项重读整份账本
        origin = _load(_run_dir(root, day) / "run.json", {})
        if not origin.get("startedAt"):
            cache[day] = None
        else:
            since = _parse_time(origin["startedAt"])
            until = _parse_time(origin["finishedAt"]) if origin.get("finishedAt") else None
            cache[day] = {"since": since, "until": until, "resolutions": _ledger_resolutions(root, since=since), "ledger": None}
    shared = cache[day]
    if shared is None:
        return False
    resolutions = shared["resolutions"]
    receipt = item.get("receiptPath")
    if receipt and Path(_native(receipt)).is_file():
        try:
            json.loads(Path(_native(receipt)).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            if shared["ledger"] is None:
                shared["ledger"] = _ledger_by_run(root, since=shared["since"], until=shared["until"])
            klass = _classify_unreadable_receipt(Path(_native(receipt)), shared["ledger"], resolutions)["class"]
            return klass != "UNKNOWN"
    intents = item.get("intentIds") or []
    return bool(intents) and all((resolutions.get(i) or {}).get("clear") for i in intents)


def carry_issues(root: Path, last_day: str) -> list[dict[str, Any]]:
    """What the last finished night still carries, re-checked against evidence that arrived later. Only the carried items are read
    again (not the whole history); an item that closed is removed alone; the original records are never rewritten."""
    run = _load(_run_dir(root, last_day) / "run.json", {})
    out: list[dict[str, Any]] = []
    cache: dict[str, Any] = {}
    for issue in run.get("issues", []):
        if issue.get("kind") != "cloud-unknown":
            out.append(issue)
            continue
        items = list(issue.get("items") or [])
        if not any(i.get("receiptPath") for i in items) and run.get("evidenceDir") and run.get("startedAt"):
            # 旧版记录携证不足：按原证据目录和同一对账口径只读重算，不猜、不放行
            cloud = cloud_stats(root, run["evidenceDir"], since=_parse_time(run["startedAt"]),
                                until=_parse_time(run["finishedAt"]) if run.get("finishedAt") else None)
            items = [_carry_item(u, last_day) for u in cloud.get("unknown", [])]
        remaining = [i for i in items if not _item_closed(root, i, cache)]
        if remaining:
            out.append({**issue, "items": remaining, "detail": f"云写入有 {len(remaining)} 项没有回执与账本共同证明：先回读对账，不重放（每项带原业务日与回执路径）"})
    return out


def state_reported(root: Path, day: str) -> int:
    """Sum of what checkpoint writers said they changed. Not evidence: kept beside the evidence-based counts."""
    return sum(_reported(item) for item in _latest(checkpoints(root, day)).values())


def status(root: Path, day: str | None = None, *, evidence_dir: str = "") -> dict[str, Any]:
    days = _run_days(root)
    day = day or (days[-1] if days else None)
    if day is None:
        return {"status": "NO_RUN", "runs": 0}
    run = _load(_run_dir(root, day) / "run.json", None)
    if run is None:
        return {"status": "NO_RUN", "date": day, "runs": len(days)}
    stats = run_stats(root, day, evidence_dir=evidence_dir or run.get("evidenceDir", ""))
    result = {"date": day, "run": run, "runs": len(days), **progress(root, day), "changed": stats["changed"],
              "breakdown": stats["breakdown"], "reportedChanged": stats["reportedChanged"],
              "weeklyCoverage": coverage_state(root, today=now_shanghai().date().isoformat()), "openExecutions": open_executions(root)}
    result["stats"] = stats
    result["view"] = run_view(root, day)
    if stats["reportedChanged"] > 0 and stats["changed"] == 0:
        result["unverifiedReportedChanges"] = stats["reportedChanged"]
    result["issues"] = issues_of(root, day)
    return result


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
    day, manual = split_run_key(day)
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
    if manual:
        item["lane"] = f"{day}@{manual}"      # 手工运行做的覆盖核对照常计入同一份每周覆盖记录（一次一行，只追加），标明来自哪次手工运行
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


@_under_begin_lock
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
    live = [item for item in executions if not item.get("endedAt")]
    if live and "@" in day:        # 同一手工运行只允许一个业务写入执行；常规夜的续办绑定保持原行为
        raise NightlyError(f"手工运行 {day} 已有未记终态的绑定执行（会话 {live[0]['sessionId']}）：只有一个写入执行可绑定；先对账并 bind-end，再续办绑定")
    item = {"sessionId": session_id, "receipt": receipt, "runnerPid": runner_pid, "runner": runner,
            "boundAt": (now or now_shanghai()).astimezone(SHANGHAI).isoformat(timespec="seconds")}
    if note:
        item["note"] = note
    executions.append(item)
    _atomic(path, run)
    return {"status": "BOUND", "date": day, "execution": item}


@_under_begin_lock
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


@_under_begin_lock
def record_snapshot(root: Path, day: str, *, tag: str, run_key: str, files: dict[str, dict[str, str]],
                    now: datetime | None = None, contract_sha: str = "") -> dict[str, Any]:
    """Record where this execution's independent ``pre`` / ``post`` mirror snapshots are (file and sha per table).
    The first record for a (tag, run key) stays; a later call only reports it."""
    if tag not in ("pre", "post"):
        raise NightlyError("tag 必须是 pre/post")
    path = _run_dir(root, day) / "run.json"
    run = _load(path, None)
    if run is None:
        return {"status": "NO_RUN_RECORD", "date": day, "wrote": False}
    recorded = run.setdefault("mirrorSnapshots", {}).setdefault(tag, {})
    if run_key in recorded:
        return {"status": "ALREADY_RECORDED", "date": day, "wrote": False, "snapshot": recorded[run_key]}
    recorded[run_key] = {"at": (now or now_shanghai()).astimezone(SHANGHAI).isoformat(timespec="seconds"), "tables": files}
    if contract_sha:
        recorded[run_key]["contractSha256"] = contract_sha      # 写前登记带合同身份：写入前检查据此核对
    _atomic(path, run)
    return {"status": "RECORDED", "date": day, "snapshot": recorded[run_key]}


def execution_key(item: dict[str, Any]) -> str:
    """The run key of one bound execution, as sync.sh derives it: ``e<绑定时刻>-<会话前 8 位>``."""
    return "e" + str(item["boundAt"]).replace("-", "").replace(":", "").replace("T", "")[:14] + "-" + str(item["sessionId"])[:8]


def write_evidence(root: Path, day: str, run_key: str) -> dict[str, Any]:
    """Cloud write evidence of this execution since it was bound: ledger intents/finals (and the durable pending area) at or after the
    bind time. Used to refuse taking a write-before snapshot after writes may already have started."""
    run = _load(_run_dir(root, day) / "run.json", {}) or {}
    bound = [item for item in run.get("executions", []) if execution_key(item) == run_key]
    since = _parse_time(bound[-1]["boundAt"]) if bound else _parse_time(run["startedAt"]) if run.get("startedAt") else None
    if since is None:
        return {"since": None, "events": 0, "known": False, "intents": []}
    events = _ledger_events(root, since=since, kinds=("intent", "final"))
    return {"since": since.isoformat(timespec="seconds"), "events": len(events), "known": bool(bound),
            "intents": sorted({str(e.get("intentId")) for e in events})[:10]}


def record_pre_incomplete(root: Path, day: str, *, run_key: str, files: dict[str, str], reason: str, evidence: dict[str, Any],
                          now: datetime | None = None) -> dict[str, Any]:
    """Keep the evidence that this execution has an incomplete write-before snapshot it can no longer complete safely (writes already
    evidenced). The first record per run key stays; a new execution key is the existing safe takeover."""
    path = _run_dir(root, day) / "run.json"
    with _begin_lock(root):
        run = _load(path, None)
        if run is None:
            return {"status": "NO_RUN_RECORD", "date": day, "wrote": False}
        recorded = run.setdefault("mirrorSnapshots", {}).setdefault("preIncomplete", {})
        if run_key in recorded:
            return {"status": "ALREADY_RECORDED", "date": day, "wrote": False, "record": recorded[run_key]}
        recorded[run_key] = {"at": (now or now_shanghai()).astimezone(SHANGHAI).isoformat(timespec="seconds"), "files": files, "reason": reason, "writeEvidence": evidence}
        _atomic(path, run)
        return {"status": "RECORDED", "date": day, "record": recorded[run_key]}


def execution_status(run: dict[str, Any] | None, lane: str, run_key: str) -> dict[str, Any]:
    """Is ``run_key`` the latest bound, still unfinished execution of this lane? Pure check on the run record.

    A key from an older execution (a newer one took over, or this one was ended) and a key of another lane are refused, so an old
    explicit RUN_KEY can never lead a change branch into planning."""
    reasons: list[str] = []
    executions = (run or {}).get("executions") or []
    run_lane = (run or {}).get("lane") or ""
    if isinstance(run_lane, dict):          # 真实手工运行记录里的 lane 是 {kind, id, businessDate…}；演练夹具直接写 YYYY-MM-DD@编号
        run_lane = f"{run_lane.get('businessDate') or (run or {}).get('date')}@{run_lane.get('id')}" if run_lane.get("id") else ""
    if run is None:
        reasons.append("没有该日期/手工运行的运行记录")
    elif ("@" in lane) != bool(run_lane) or (run_lane and run_lane != lane):
        reasons.append("运行记录的运行通道与本次不一致")
    latest = executions[-1] if executions else None
    if run is not None and run.get("status") in ("FINISHED", "PAUSED_QUOTA"):      # 运行已收尾或因额度暂停：哪怕执行没记退出，也不再发出云写入
        reasons.append(f"运行状态已是 {run.get('status')}，不再发出云写入")
    if run is not None and latest is None:
        reasons.append("该运行没有绑定的执行")
    if latest is not None:
        if execution_key(latest) != run_key:
            reasons.append("运行键不是本通道最近绑定的执行（已有新执行接管，或属于别的通道）")
        elif latest.get("endedAt"):
            reasons.append("该执行已记终态，不得再写")
    return {"lane": lane, "runKey": run_key, "latest": execution_key(latest) if latest else None, "ok": not reasons, "reasons": reasons}


def _planned(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else len(value) if isinstance(value, (list, dict)) else 0


def receipt_wrote(path: Path) -> bool:
    """Does one saved period / exception / stamp receipt show (or leave open) a cloud write? Unreadable or unfinished counts as written."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    if not isinstance(data, dict):
        return True
    if data.get("executed") is False:
        return False
    plan = data.get("plan") if isinstance(data.get("plan"), dict) else {}
    stamp = data.get("stamp") if isinstance(data.get("stamp"), dict) else {}
    apply = data.get("apply") if isinstance(data.get("apply"), dict) else {}
    counts = [_planned(plan.get("creates")), _planned(plan.get("updates")), _planned(stamp.get("stamped")), _planned(data.get("created")),
              _planned(data.get("updated")), *[_planned(v) for v in apply.values() if not isinstance(v, str)]]
    return data.get("status") != "OK" or any(counts)


MONTHLY_LEDGER = "月度统计账"
EXCEPTION_LEDGER = "异常登记账"
WRITE_RECEIPTS = ("period", "exceptions", "stamp")


def writes_after(business_root: Path, night_run: Path, since: float) -> list[str]:
    """Every sign of a cloud write made after ``since`` (epoch seconds): saved receipts of ordinary writes, period / exception / stamp
    receipts that wrote or are unclear, and the three write ledgers. Plan-only calls (marked ``plan-only``) are not writes."""
    found: list[str] = []
    for directory in sorted((Path(night_run) / "sync").glob("*")):
        if not directory.is_dir() or (directory / "plan-only").exists():
            continue
        for path in directory.glob("exec*/exec-*.*"):
            if path.stat().st_mtime > since:
                found.append(f"{directory.name}：普通写入回执 {path.name}")
        for name in WRITE_RECEIPTS:
            parts = [directory / f"{name}.{ext}" for ext in ("json", "stdout", "err")]
            fresh = [p for p in parts if p.exists() and p.stat().st_mtime > since]
            if fresh and (not parts[0].is_file() or receipt_wrote(parts[0])):
                found.append(f"{directory.name}：{name} 有写入或结果不明")
    root = Path(business_root) / RUNTIME_DIR
    floor = float(int(since))                  # 账本时刻只到秒：同一秒内的事件按“之后”处理（宁可多要一次复算）
    if _ledger_events(root, since=datetime.fromtimestamp(floor, SHANGHAI), kinds=("intent", "final")):
        found.append("飞书写入账本在复算之后有写入事件")
    for name in (MONTHLY_LEDGER, EXCEPTION_LEDGER):
        for path in (root / name).glob("*.jsonl") if (root / name).is_dir() else []:
            if path.stat().st_mtime < floor:
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    found.append(f"{name} 有无法解析的行")
                    continue
                if event.get("event") in ("intent", "result") and _parse_time(event["at"]).timestamp() >= floor:
                    found.append(f"{name} 在复算之后有 {event['event']} 事件")
                    break
    return found


def recount_status(business_root: Path, night_run: Path, *, run_key: str, contract_sha: str) -> dict[str, Any]:
    """May the monthly stamp rely on the newest recount of this night? Only if it is parseable, zero-diff, from this execution and contract,
    and nothing wrote to the cloud (period, exceptions, ordinary writes, successful or unclear) after it finished."""
    reasons: list[str] = []
    sync_dir = Path(night_run) / "sync"
    recounts = [p for p in sync_dir.glob("recount-*") if p.is_dir()]
    latest = max(recounts, key=lambda p: p.stat().st_mtime) if recounts else None
    since = 0.0
    if latest is None:
        reasons.append("本晚没有独立复算")
    else:
        try:
            data = json.loads((latest / "recount.json").read_text(encoding="utf-8"))
            meta = json.loads((latest / "recount.meta.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data, meta = None, None
            reasons.append("最近一次复算没有可解析的结果")
        if data is not None:
            if data.get("status") != "OK" or data.get("diffs"):
                reasons.append("最近一次复算有差异或状态不是 OK")
            if meta.get("runKey") != run_key or meta.get("contractSha256") != contract_sha or meta.get("exit") != 0:
                reasons.append("最近一次复算不是本次执行、当前合同或未以 0 差异结束")
            started = meta.get("startedAtEpoch")      # 复算开始时刻：读取期间（开始之后）出现的任何写入也使它失效，不只看它结束之后
            finished = max((latest / name).stat().st_mtime for name in ("recount.json", "recount.meta.json"))
            since = min(float(started), finished) if isinstance(started, (int, float)) else finished
            reasons += [f"复算之后：{item}" for item in writes_after(business_root, night_run, since)]
    return {"recount": latest.name if latest else None, "ok": not reasons, "reasons": reasons}


def open_executions(root: Path) -> list[dict[str, Any]]:
    """Bound executions with no recorded end, over all runs: what a supervisor checks before dispatching again."""
    found = []
    for day in _run_days(root, lanes=True):      # 常规运行与同日手工运行一并看；手工运行的 date 是运行键 YYYY-MM-DD@编号，可直接用于 --date
        run = _load(_run_dir(root, day) / "run.json", {})
        found += [{"date": day, "runStatus": run.get("status"), **item} for item in run.get("executions", []) if not item.get("endedAt")]
    return found


# ---------------------------------------------------------------------------------------------
# Evidence-based counts: what was really changed tonight
# ---------------------------------------------------------------------------------------------

CLOUD_WRITE = {"CREATED_VERIFIED": "create", "WRITTEN": "update"}
CLOUD_UNKNOWN = frozenset({"UNKNOWN", "UNKNOWN_SCAN", "FAILED_OR_UNKNOWN", "RUNNING", "PARTIAL"})
CLOUD_NOT_ATTEMPTED = frozenset({"NOT_ATTEMPTED", "NOT_ATTEMPTED_AFTER_STOP", "LEDGER_UNAVAILABLE_NOT_SENT"})
LEDGER_DIR = "飞书写入账本"


def _parse_time(text: str) -> datetime:
    value = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    return value.replace(tzinfo=SHANGHAI) if value.tzinfo is None else value.astimezone(SHANGHAI)


def _native(path: str) -> str:
    """The same folder as this interpreter sees it: the nightly tools hand over WSL paths (``/mnt/d/...``) while the
    published copy may run under Windows Python (``D:/...``), and the other way round."""
    text = str(path or "")
    if os.name == "nt" and text.startswith("/mnt/") and len(text) > 6 and text[6] == "/":
        return f"{text[5].upper()}:/{text[7:]}"
    if os.name != "nt" and len(text) > 2 and text[1] == ":" and text[2] in "/\\":
        return f"/mnt/{text[0].lower()}/{text[3:].replace(chr(92), '/')}"
    return text


def _same_path_key(path: str) -> str:
    """Comparable form of a recorded path: the same folder whether the ledger recorded it as /mnt/d/... or D:\\..., with / separators and no
    trailing slash (case-folded only where the filesystem is case-insensitive)."""
    text = _native(str(path or "")).replace("\\", "/").rstrip("/")
    return text.lower() if os.name == "nt" else text


def _receipt_files(evidence_dir: str) -> list[Path]:
    evidence_dir = _native(evidence_dir)
    base = Path(evidence_dir) / "sync"
    return sorted(base.glob("*/exec*/exec-*.json")) if evidence_dir and base.is_dir() else []


def _ledger_events(root: Path, *, since: datetime, kinds: tuple[str, ...] = ("final",)) -> list[dict[str, Any]]:
    """Events of the cloud write ledger written at or after ``since`` (files not touched since are skipped)."""
    found = []
    seen: set[str] = set()
    # 目标文件与耐久暂存区(pending/)同属一份账：服务已答复、账本暂时写不进的事件在暂存区，同样计入
    for sub in ("targets", "pending"):
        directory = root / LEDGER_DIR / sub
        if not directory.is_dir():
            continue
        for path in directory.glob("*.jsonl"):
            if datetime.fromtimestamp(path.stat().st_mtime, SHANGHAI) < since:
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                if not any(f'"{kind}"' in line for kind in kinds) or line in seen:
                    continue
                seen.add(line)
                event = json.loads(line)
                if event.get("event") in kinds and _parse_time(event["at"]) >= since:
                    found.append(event)
    return found


def _ledger_by_run(root: Path, *, since: datetime, until: datetime | None = None) -> dict[str, dict[str, dict[str, Any]]]:
    """runId -> intentId -> {record, kind, final}: what the unique write ledger says each intent of a run ended as.
    ``until`` is the night's own close: intents written after it (manual work later that day) are not that night's writes."""
    runs: dict[str, dict[str, dict[str, Any]]] = {}
    events = [e for e in _ledger_events(root, since=since, kinds=("intent", "bound", "final")) if until is None or _parse_time(e["at"]) <= until]
    for event in sorted(events, key=lambda e: e["at"]):
        intent = runs.setdefault(str(event.get("runId")), {}).setdefault(event["intentId"], {"record": None, "kind": "update", "final": None})
        if event["event"] == "intent":
            intent["record"] = (event.get("target") or {}).get("record") or intent["record"]
            intent["table"] = (event.get("target") or {}).get("table") or ""
            intent["kind"] = event.get("kind") or "update"
            intent["logDir"] = event.get("logDir") or ""
            intent["fields"] = event.get("fields") if isinstance(event.get("fields"), dict) else intent.get("fields", {})
            stable = event.get("stableKey")
            intent["stable"] = str((stable.get("value") if isinstance(stable, dict) else stable) or "")
        elif event["event"] == "bound":
            intent["record"] = event.get("recordId") or intent["record"]
        else:
            intent["final"] = event.get("status")
    return runs


LEDGER_CLEAR_FINALS = frozenset({"OK", "UNCHANGED", "NOT_ATTEMPTED"})
LEDGER_CLEAR_RECONCILED = frozenset({"APPLIED", "NOT_APPLIED_PROVEN"})
LEDGER_WROTE = frozenset({"OK", "APPLIED"})     # 其余闭合标签（未尝试、未改变、证明未应用）只闭合“已知零动作”，不是变更


def _ledger_resolutions(root: Path, *, since: datetime) -> dict[str, dict[str, Any]]:
    """intentId -> how the unique ledger last closed it, whichever run wrote that event (a reconcile run has its own runId):
    ``clear`` only for a verified final (OK/UNCHANGED/NOT_ATTEMPTED) or a reconciled APPLIED/NOT_APPLIED_PROVEN; a human release is
    clear for the gate but is not a verified result, so it is reported apart."""
    found: dict[str, dict[str, Any]] = {}
    for event in sorted(_ledger_events(root, since=since, kinds=("final", "reconciled", "human_release")), key=lambda e: e["at"]):
        kind = event["event"]
        label = event.get("status") if kind == "final" else event.get("outcome") if kind == "reconciled" else "HUMAN_RELEASED"
        verified = (kind == "final" and label in LEDGER_CLEAR_FINALS) or (kind == "reconciled" and label in LEDGER_CLEAR_RECONCILED)
        entry = {"label": str(label), "clear": verified, "humanRelease": kind == "human_release", "at": event["at"]}
        if kind == "reconciled" and label == "APPLIED":      # 对账证明已写入时，行 ID 来自它的证明/唯一匹配（原创建当时没有 bound 事件）
            observed = event.get("observed") or {}
            ids = {p.get("recordId") for p in (event.get("terminalProof") or observed.get("verifiedProof") or []) if isinstance(p, dict) and p.get("recordId")}
            ids |= set(observed.get("matches") or [])
            if len(ids) == 1:
                entry["recordId"] = next(iter(ids))
            # 更新意图的字段级证明：对账读回时这些字段都在且全部等于目标值才记 APPLIED；原意图随事件保存，窗口外的意图事件也能用
            entry["observedFields"] = sorted(fid for fid, value in (observed.get("fields") or {}).items() if value != "__absent__")
            original = event.get("originalIntent") or {}
            if isinstance(original.get("fields"), dict):
                entry["originalFields"] = original["fields"]
        found[event["intentId"]] = entry
    return found


def _proven_update_fields(info: dict[str, Any], state: dict[str, Any]) -> list[str] | None:
    """The record fields one update intent is proven to have written, or None when the evidence does not name them. A writer final OK read
    every target field back; a reconcile APPLIED needs every intent field observed and equal to its target. A field whose old value was
    already the target did not change and is not counted. Never inferred from a count or from the neighbouring spec."""
    fields = info.get("fields") or state.get("originalFields") or {}
    changed = sorted(fid for fid, f in fields.items() if not (isinstance(f, dict) and f.get("old") is not None and f.get("old") == f.get("wire")))
    if not changed or not info.get("stable") or not info.get("table"):
        return None
    if state["label"] == "OK" or (state["label"] == "APPLIED" and set(changed) <= set(state.get("observedFields") or ())):
        return changed
    return None


def _classify_unreadable_receipt(path: Path, ledger: dict[str, dict[str, dict[str, Any]]], resolutions: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """A receipt file that is empty or not JSON (the writer died, or exited with an error and printed nothing). Its meaning comes
    from the other evidence of the same folder, never from the missing file: the error text beside it and the intents the unique
    ledger holds for that folder's log directories. ``REFUSED_NO_CALL``: refused before any request, known not written.
    ``RESOLVED_BY_LEDGER``: every intent in that folder is closed by a verified final/reconciliation. Anything else is unknown."""
    folder = _same_path_key(str(path.parents[1]))
    spec_name = path.stem[len("exec-"):] if path.stem.startswith("exec-") else path.stem
    try:
        error_text = path.with_suffix(".err").read_text(encoding="utf-8", errors="replace")
    except OSError:
        error_text = ""
    # 只认“同一规格”：回执 exec-<名>.json 对应本批 logs*/<名> 日志目录；同一批里相邻规格的意图不能替它作证
    intents = []
    for run_id, items in ledger.items():
        for intent_id, info in items.items():
            log_dir = _same_path_key(info.get("logDir") or "")
            parent, _, leaf = log_dir.rpartition("/")
            if (leaf == (spec_name.lower() if os.name == "nt" else spec_name) and parent.rpartition("/")[0] == folder) \
                    or log_dir == f"{folder}/logs-{spec_name.lower() if os.name == 'nt' else spec_name}":
                intents.append((run_id, intent_id, info))
    result: dict[str, Any] = {"receipt": str(path), "size": path.stat().st_size if path.exists() else None, "intents": len(intents)}
    if not intents and "未发起任何外部调用" in error_text:
        return {**result, "class": "REFUSED_NO_CALL"}
    states = {intent_id: resolutions.get(intent_id) for _, intent_id, _ in intents}
    if intents and all(state and state["clear"] for state in states.values()):
        wrote = [(run_id, iid, info) for run_id, iid, info in intents if states[iid]["label"] in LEDGER_WROTE]
        base = {**result, "runs": sorted({run_id for run_id, _, _ in intents}), "closedBy": sorted({state["label"] for state in states.values() if state})}
        if not wrote:      # 全部是未尝试/未改变/证明未应用：闭合了未知，但没有任何变更可计
            return {**base, "class": "CLOSED_NO_WRITE", "cases": []}
        creates, updates, unproven = [], [], []
        for run_id, iid, info in wrote:     # 已证实的创建：原意图＋表＋(bound 的或对账证明的)行 ID；缺行 ID 证据的不计行
            record = info.get("record") or states[iid].get("recordId")
            if info.get("kind") == "create" and record and info.get("stable"):
                creates.append({"runId": run_id, "intentId": iid, "table": info.get("table") or "", "stable": info["stable"], "record": record})
            elif info.get("kind") != "create":      # 已证实的更新：原意图＋表＋稳定键＋逐字段证明；字段说不清的只列出，不计操作也不计字段
                proven = _proven_update_fields(info, states[iid])
                if proven:
                    updates.append({"runId": run_id, "intentId": iid, "table": info["table"], "stable": info["stable"], "fields": proven})
                else:
                    unproven.append({"runId": run_id, "intentId": iid, "table": info.get("table") or "", "stable": info.get("stable") or ""})
        return {**base, "class": "RESOLVED_BY_LEDGER", "creates": creates, "updates": updates, "updatesWithoutFieldProof": unproven,
                "cases": sorted({info["stable"].split("|")[0] for _, _, info in wrote if info.get("stable")})}
    return {**result, "class": "UNKNOWN", "intentIds": sorted(iid for _, iid, _ in intents),
            "ledgerStates": sorted({(state or {}).get("label", "NO_FINAL") for state in states.values()}) or ["NO_INTENT"]}


def _durable_create_report(path: Path, ledger: dict[str, dict[str, dict[str, Any]]]) -> dict[str, Any] | None:
    """Match a V3 writer's saved report to its own spec, run and ledger when the command output was interrupted."""
    folder = path.parents[1]
    name = path.stem.removeprefix("exec-")
    specs = list(folder.glob(f"specs*/{name}.json"))
    reports = list(folder.glob(f"logs*/{name}/report.json"))
    if len(specs) != 1 or len(reports) != 1:
        return None
    try:
        spec = json.loads(specs[0].read_text(encoding="utf-8"))
        report = json.loads(reports[0].read_text(encoding="utf-8"))
        if spec["schemaVersion"] != "FeishuSourceSyncSpecV3" or report["schema"] != "FeishuSourceSyncReportV3":
            return None
        log_dir = _same_path_key(str(reports[0].parent))
        if _same_path_key(spec["logDir"]) != log_dir or spec["contract"]["sha256"] != report["contractSha256"]:
            return None
        if report["mode"] != "execute" or not report["finishedAt"] or report["status"] not in {"OK", "FAILED", "PARTIAL"}:
            return None
        intents = ledger.get(report["runId"], {})
        if not any(_same_path_key(item.get("logDir") or "") == log_dir for item in intents.values()):
            return None
        planned = {(item["tableId"], item["stableKey"]["value"]) for item in spec["creates"]}
        recorded = {(row["tableId"], row["stableKey"]["value"]) for row in report["records"]}
        if recorded != planned or len(recorded) != len(report["records"]):
            return None
        return report
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def _reconciled_receipt_record(run_ledger: dict[str, dict[str, Any]] | None, resolutions: dict[str, dict[str, Any]],
                               record: dict[str, Any], stable: str) -> tuple[str, str, str] | None:
    """One receipt record that the writer could not close (unknown/failed): if the SAME run's ledger holds exactly one intent for that
    record (by record id or stable key) and a verified closure exists (final OK, or a reconcile APPLIED / NOT_APPLIED_PROVEN), return
    (label, action, intentId). Adjacent specs, a missing intent or several candidates give None - never guessed."""
    matched = [(iid, i) for iid, i in (run_ledger or {}).items()
               if (record.get("recordId") and i["record"] == record.get("recordId")) or (stable and i.get("stable") == str(stable))]
    if len(matched) != 1:
        return None
    intent_id, info = matched[0]
    state = resolutions.get(intent_id)
    if not state or not state["clear"]:
        return None
    return state["label"], "create" if info["kind"] == "create" else "update", intent_id


def cloud_stats(root: Path, evidence_dir: str, *, since: datetime, until: datetime | None = None) -> dict[str, Any]:
    """Cloud writes counted once per (intent, table, record, action). Receipts name the runs and records; the unique write
    ledger gives each intent and how it ended (``final``). A write counts as done only if its receipt says OK **and** the
    ledger's final for that intent is OK; anything else is unknown and listed apart. The same receipt copied to another
    folder, or a record listed twice, is one write; two different intents on one record are two writes. Fields are counted
    from the receipt's ``writtenFields`` (cells that really changed) of updates; created rows are counted as rows only."""
    evidence_dir = _native(evidence_dir)
    if not evidence_dir:
        return {"source": "none", "note": "没有回执目录：云写入数不统计，只看检查点与账本"}
    files = _receipt_files(evidence_dir)
    ledger = _ledger_by_run(root, since=since, until=until)
    resolutions = _ledger_resolutions(root, since=since)      # 对账可以晚于该夜：它闭合的是该夜的未知，不是新增变更
    seen: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    fields: set[tuple[str, str, str]] = set()
    unknown: list[dict[str, Any]] = []
    unreadable: list[dict[str, Any]] = []
    run_ids: set[str] = set()
    duplicates = unconfirmed = receipts_ok = 0
    held: dict[str, int] = {}
    held_keys: list[dict[str, Any]] = []
    not_attempted: dict[tuple[str, str, str], dict[str, Any]] = {}
    durable_receipts = 0
    cases: set[str] = set()
    for path in files:
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(report, dict) or not isinstance(report.get("records"), list):
                raise ValueError("回执缺少记录清单")
        except (OSError, ValueError):
            report = _durable_create_report(path, ledger)
            if report is None:
                unreadable.append(_classify_unreadable_receipt(path, ledger, resolutions))
                continue
            durable_receipts += 1
        run_id = str(report.get("runId") or "")
        run_ids.add(run_id)
        run_ledger = ledger.get(run_id)
        report_ok = report.get("status") == "OK"
        receipts_ok += report_ok
        for record in report.get("records", []):
            state = record.get("status")
            stable = (record.get("stableKey") or {}).get("value") or record.get("recordId") or ""
            table = record.get("tableId") or ""
            reconciled = None
            if state in CLOUD_UNKNOWN or state == "FAILED":    # 同一规格原回执里的未知，若账本已逐意图对账闭合：已写入则按原夜计一次，证明未写入则不计
                reconciled = _reconciled_receipt_record(run_ledger, resolutions, record, stable)
            if reconciled is not None:
                label, action, intent_id = reconciled
                if label in LEDGER_WROTE:
                    key = (intent_id, table, stable, action)
                    if key in seen:
                        duplicates += 1
                    else:
                        seen[key] = {"receipt": path.name, "runId": run_id, "verdict": "reconciled"}
                        cases.add(str(stable).split("|")[0])
                        if action == "update":
                            for field_id in (record.get("writtenFields") if record.get("writtenFields") is not None else (record.get("fieldDetail") or {})):
                                fields.add((table, stable, field_id))
                else:
                    held[f"RECONCILED_{label}"] = held.get(f"RECONCILED_{label}", 0) + 1
                continue
            if state not in CLOUD_WRITE:
                if state in CLOUD_UNKNOWN or state in ("FAILED", "VIOLATION", "DUPLICATE_KEYS_BLOCK"):
                    unknown.append({"receipt": path.name, "receiptPath": str(path), "runId": run_id, "table": table, "stableKey": stable, "status": state,
                                    "intentIds": sorted(iid for iid, i in (run_ledger or {}).items() if i.get("stable") == str(stable) or i["record"] == record.get("recordId"))})
                else:      # 被挡、未改变、已存在等：没有写入，也不是未知；分别留存，不并入成功
                    held[str(state)] = held.get(str(state), 0) + 1
                    if state in CLOUD_NOT_ATTEMPTED:
                        not_attempted[(run_id, table, str(stable))] = {"receipt": path.name, "table": table, "stableKey": stable, "status": state}
                    if len(held_keys) < 20:
                        held_keys.append({"receipt": path.name, "table": table, "stableKey": stable, "status": state})
                continue
            action = CLOUD_WRITE[state]
            matched = [(iid, i) for iid, i in (run_ledger or {}).items()
                       if i["record"] == record.get("recordId") and (i["kind"] == "create") == (action == "create")]
            intent_id = record.get("intentId") or (matched[0][0] if len(matched) == 1 else run_id)
            key = (str(intent_id), table, stable, action)
            if key in seen:
                duplicates += 1
                if action == "update":
                    for field_id in (record.get("writtenFields") if record.get("writtenFields") is not None else (record.get("fieldDetail") or {})):
                        fields.add((table, stable, field_id))    # 重复列出里的新格仍计入（同一格只算一次）
                continue
            # 成功按逐意图证据判定，不看整批报告的 status（一批里可以既有已核实的写入，又有被挡/失败/未知的条目）：
            #   有账本：该记录的意图都有 final=OK；没有账本：新建要记录自身为 CREATED_VERIFIED（创建时已回读核实），
            #   更新的 WRITTEN 只有整批回读通过（报告 OK）才算核实。
            verdict = "ledger-ok"
            if run_ledger is None:
                verdict = "receipt-only" if (action == "create" or report_ok) else "ledger-not-ok"
            elif not matched or any(i["final"] != "OK" for _, i in matched):
                # 回执写 WRITTEN 而写入进程终态不是 OK（如读回时公式自然重算被判违规）：账本之后逐意图对账为已应用的，按原夜计一次
                reconciled_ids = [iid for iid, _ in matched if (resolutions.get(iid) or {}).get("clear") and resolutions[iid]["label"] in LEDGER_WROTE]
                verdict = "reconciled" if matched and len(reconciled_ids) == len(matched) else "ledger-not-ok"
            if verdict == "ledger-not-ok":
                unknown.append({"receipt": path.name, "receiptPath": str(path), "runId": run_id, "table": table, "stableKey": stable, "status": state, "reportStatus": report.get("status"),
                                "intentIds": sorted(iid for iid, _ in matched),
                                "ledgerFinal": sorted({i["final"] or "NONE" for _, i in matched}) or ["NO_INTENT"]})
                continue
            seen[key] = {"receipt": path.name, "runId": run_id, "verdict": verdict}
            unconfirmed += verdict == "receipt-only"
            cases.add(str(stable).split("|")[0])
            if action == "update":
                for field_id in (record.get("writtenFields") if record.get("writtenFields") is not None else (record.get("fieldDetail") or {})):
                    fields.add((table, stable, field_id))
    explained_runs: set[str] = set()
    for item in unreadable:        # 读不出的回执按同目录证据分层：零调用拒绝、账本已核实对账，其余记为未知，不再静默忽略
        if item["class"] == "RESOLVED_BY_LEDGER":
            cases.update(item["cases"])
            explained_runs.update(item["runs"])
            for made in item.get("creates", []):      # 同一意图只计一次：可读回执已计过的(同意图、表、稳定键)不再重复
                key = (made["intentId"], made["table"], made["stable"], "create")
                if key in seen:
                    duplicates += 1
                else:
                    seen[key] = {"receipt": Path(item["receipt"]).name, "runId": made["runId"], "verdict": "ledger-resolved", "record": made["record"]}
            for made in item.get("updates", []):      # 空回执后的原更新：同一意图只计一次，字段按（表、稳定键、字段）去重；可读回执已计的不重复
                key = (made["intentId"], made["table"], made["stable"], "update")
                if key in seen:
                    duplicates += 1
                else:
                    seen[key] = {"receipt": Path(item["receipt"]).name, "runId": made["runId"], "verdict": "ledger-resolved"}
                for field_id in made["fields"]:
                    fields.add((made["table"], made["stable"], field_id))
        elif item["class"] == "CLOSED_NO_WRITE":
            explained_runs.update(item["runs"])
        elif item["class"] == "UNKNOWN":
            unknown.append({"receipt": Path(item["receipt"]).name, "receiptPath": item["receipt"], "table": "", "stableKey": "", "status": "UNREADABLE_RECEIPT",
                            "intentIds": item.get("intentIds", []), "ledgerFinal": item["ledgerStates"]})
    created = {(k[1], k[2]) for k in seen if k[3] == "create"}
    updates = [k for k in seen if k[3] == "update"]
    created_with_ledger = {(table, str(stable)) for (_, table, stable, action), evidence in seen.items()
                           if action == "create" and evidence["verdict"] != "receipt-only"}
    open_not_attempted = [item for item in not_attempted.values()
                          if (item["table"], str(item["stableKey"])) not in created_with_ledger]
    ok_runs = {r for r, intents in ledger.items() if any(i["final"] == "OK" for i in intents.values())}
    run_ids.discard("")
    cross = {"ledgerFinalOk": sum(1 for intents in ledger.values() for i in intents.values() if i["final"] == "OK"),
             "receiptRunsWithoutLedgerOk": sorted(run_ids - ok_runs), "ledgerOkRunsWithoutReceipt": sorted(ok_runs - run_ids - explained_runs),
             "explainedByUnreadableReceipt": sorted(explained_runs & ok_runs)}
    cross["result"] = "MATCH" if not cross["receiptRunsWithoutLedgerOk"] and not cross["ledgerOkRunsWithoutReceipt"] else "MISMATCH"
    return {"source": "receipts+ledger", "receiptFiles": len(files), "receiptsOk": receipts_ok, "createdRows": len(created),
            "updatedOperations": len(updates), "uniqueFields": len(fields), "duplicatesIgnored": duplicates, "unknown": unknown,
            "unreadable": unreadable, "receiptsRecoveredFromReport": durable_receipts,
            "notAttempted": list(not_attempted.values()), "openNotAttempted": open_not_attempted, "receiptsRefusedNoCall": sum(i["class"] == "REFUSED_NO_CALL" for i in unreadable),
            "receiptsResolvedByLedger": sum(i["class"] == "RESOLVED_BY_LEDGER" for i in unreadable),
            "updatesWithoutFieldProof": sum(len(i.get("updatesWithoutFieldProof", [])) for i in unreadable),
            "receiptsClosedNoWrite": sum(i["class"] == "CLOSED_NO_WRITE" for i in unreadable), "receiptOnlyUnconfirmed": unconfirmed, "heldOrNotWritten": held, "heldSample": held_keys, "ledgerCrossCheck": cross, "cases": sorted(cases),
            "note": "新建行只计行数，不逐格计字段；更新字段取回执 writtenFields（真正改变的格）；回执为空时更新字段取账本原意图加终态/对账的逐字段证明，说不清字段的不计"}


def _observation_today(root: Path, day: str) -> dict[str, Any]:
    day, manual = split_run_key(day)
    counts = {"added": 0, "kept": 0, "enterScope": 0, "outOfScope": 0}
    names = {"ADDED": "added", "KEPT": "kept", "ENTER_SCOPE": "enterScope", "OUT_OF_SCOPE": "outOfScope"}
    rwids: set[str] = set()
    for rwid, item in _queue(root)["items"].items():
        for event in item.get("history", []):
            if event.get("on") == day and (event.get("lane") or "") == manual and event.get("outcome") in names:
                counts[names[event["outcome"]]] += 1
                if event["outcome"] != "KEPT":
                    rwids.add(item.get("projectNo") or "观察:" + rwid)      # 同一任务已按项目编号计过的不重复
    return {**counts, "rwids": sorted(rwids)}


def _collected_cases(evidence_dir: str, since: datetime) -> tuple[set[str], str]:
    """Cases whose own package-attached result (``source/r53-new-results.jsonl``) was written in this run."""
    path = Path(_native(evidence_dir)) / "source" / "r53-new-results.jsonl" if evidence_dir else None
    if path is None or not path.is_file():
        return set(), "没有采集结果文件"
    found = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if item.get("outcome") == "NEW_PACKAGE_ATTACHED" and item.get("projectNo") and _parse_time(item["at"]) >= since:
            found.add(item["projectNo"])
    return found, "采集结果文件 NEW_PACKAGE_ATTACHED"


def _archived_cases(root: Path, since: datetime, until: datetime | None = None) -> tuple[set[str], str]:
    """Cases the waterline shows as completed (archived) from ``since`` to the run's own close ``until`` (a case archived by hand after
    the night closed belongs to that later work, not to the night): the case ledger's own completion record."""
    path = Path(root).parent / "案卷水位记录.json"
    if not path.is_file():
        return set(), "没有案卷水位记录"
    cases = json.loads(path.read_text(encoding="utf-8")).get("cases", {})
    return {p for p, c in cases.items() if c.get("state") == "COMPLETED" and c.get("completedAt")
            and since <= _parse_time(c["completedAt"]) and (until is None or _parse_time(c["completedAt"]) <= until)}, \
        "案卷水位记录 completedAt" + ("（截至该夜收尾时间）" if until else "")


def run_stats(root: Path, day: str, *, evidence_dir: str = "", now: datetime | None = None) -> dict[str, Any]:
    """What this run changed, from its own evidence. ``changed`` = distinct cases/tasks changed. Collection, archiving, cloud
    writes and queue transitions are each counted from their own evidence (package-attached results, the waterline's completion
    record, receipts+write ledger, queue history); what a writer reported with ``--changed`` is kept only as
    ``reportedChanged``. The coverage is stated so a number is never read wider."""
    run = _load(_run_dir(root, day) / "run.json", {})
    since = _parse_time(run["startedAt"]) if run.get("startedAt") else (now or now_shanghai())
    closed_at = _parse_time(run["finishedAt"]) if run.get("finishedAt") else None
    cloud = cloud_stats(root, evidence_dir, since=since, until=closed_at)
    observation = _observation_today(root, day)
    collected, collected_basis = _collected_cases(evidence_dir, since)
    archived, archived_basis = _archived_cases(root, since, closed_at)
    changed_set = set(collected) | set(archived) | set(cloud.get("cases", [])) | set(observation["rwids"])
    unknown_writes = [i for i in progress(root, day)["unknown"] if i["stage"] in WRITE_STAGES]
    breakdown = {"collectedCases": len(collected), "archivedCases": len(archived),
                 "observation": {k: v for k, v in observation.items() if k != "rwids"},
                 "cloudCreatedRows": cloud.get("createdRows"), "cloudUpdatedOperations": cloud.get("updatedOperations"),
                 "cloudUniqueFields": cloud.get("uniqueFields"),
                 "cloudUnknown": len(cloud.get("unknown", [])) if cloud.get("source") == "receipts+ledger" else 0,
                 "cloudNotAttempted": len(cloud.get("notAttempted", [])),
                 "cloudReceiptsRecoveredFromReport": cloud.get("receiptsRecoveredFromReport", 0),
                 "cloudReceiptsUnreadable": len(cloud.get("unreadable", [])) + cloud.get("receiptsRecoveredFromReport", 0),
                 "cloudRefusedNoCall": cloud.get("receiptsRefusedNoCall", 0),
                 "cloudResolvedByLedger": cloud.get("receiptsResolvedByLedger", 0),
                 "ledgerCrossCheck": (cloud.get("ledgerCrossCheck") or {}).get("result", "NOT_CHECKED"),
                 "unknownWriteCheckpoints": len(unknown_writes)}
    return {"changed": len(changed_set), "reportedChanged": state_reported(root, day), "breakdown": breakdown, "cloud": cloud,
            "collectedCases": sorted(collected), "archivedCases": sorted(archived),
            "coverage": f"采集：{collected_basis}；归档：{archived_basis}；云写入：" + ("回执+唯一账逐意图" if cloud.get("source") == "receipts+ledger" else "无回执目录")
                        + "；观察：队列当日历史"}


# ---------------------------------------------------------------------------------------------
# Structured report of one run (one receipt, counted once)
# ---------------------------------------------------------------------------------------------

_PATH_LIKE = re.compile(r"(?:[A-Za-z]:[\\/]|/mnt/|/srv/|/home/|\\\\)[^\s，。；、）)\]\"']*")
CASE_NO = re.compile(r"\d{8}C\d{9}")


STAGE_NAMES = {"discover": "发现", "acquire": "取包接件", "organize": "整理", "validate": "核验", "sync": "飞书同步", "mirror": "镜像", "reconcile": "对账", "report": "汇报"}
KIND_NAMES = {"failed": "失败", "retained-issue": "保留事项", "entry-incomplete": "发现入口未完成", "cloud-unknown": "云写入效果未知", "cloud-not-attempted": "有未发送目标"}
# 汇报只用受控的业务原因：检查点原文（可能含原材料名、本地路径、个人信息）只用来归类，绝不原样进入正文
SAFE_REASONS = (
    (("保护", "锁", "批次", "待整理标记", "放行"), "受保护事项，不清锁、不人工放行"),
    (("效果未知", "未知", "回读", "对账"), "写入效果未知，先回读对账，不重放"),
    (("额度", "quota"), "额度不足，保留断点待恢复"),
    (("502", "不可用", "超时", "未读完整", "断线", "没读完整"), "来源暂时不可用或未读完整，下一轮补读"),
    (("原包", "来源未", "未提供", "缺件", "缺据", "缺材料", "材料待核", "材料缺", "尚未提供", "待核"), "来源材料暂缺或待核，待补齐后办理"),
    (("读不出", "无法辨认", "识别", "目视", "record-unparsed", "record-read-error"), "电子记录或材料读不出，保持待核实"),
)


def _clean(text: Any, limit: int = 200) -> str:
    """One line of detail with file paths removed. Internal use only (matching words); the report body never echoes checkpoint text."""
    line = _PATH_LIKE.sub("<路径>", str(text or "")).replace("\n", " ")
    return line if len(line) <= limit else line[:limit] + "…"


def _safe_reason(detail: Any, status: str = "") -> str:
    text = str(detail or "")
    for words, reason in SAFE_REASONS:
        if any(word in text for word in words):
            return reason
    return {"failed": "处理失败，原因见运行检查点", "skipped": "已跳过并保留，原因见运行检查点"}.get(status, "待办未走完，原因见运行检查点")


def _case_label(index: int, project: str, names: dict[str, dict[str, str]]) -> str:
    info = names.get(project) or {}
    who = "｜".join(part for part in (info.get("brigade"), info.get("unit")) if part) or "大队/单位待核"
    return f"第 {index} 案 {who}（项目编号 {project}）"


def report(root: Path, day: str, *, evidence_dir: str = "", waterline: Path | None = None) -> dict[str, Any]:
    """Reusable structured report body of one run, computed from the run's own receipt and evidence (``run_stats``): the number of cases first,
    then every case as ``第 N 案 大队｜单位（项目编号 …）`` (N is the position in this report, never the project number), then the separate
    counts (cases, new cloud rows, update operations, unique field cells, unknown items, observation) and the open problems.
    Cases that are still pending (an open problem or an unfinished due case) are listed after the changed ones with the same label, one line per
    project number, and counted apart (``pendingCases``); they never raise the changed-case count. Checkpoint text, file names and local paths
    never appear: the reasons are a fixed business vocabulary chosen from the stage and status. Old records are left as they are."""
    run = _load(_run_dir(root, day) / "run.json", None)
    if run is None:
        raise NightlyError("该日期没有运行记录")
    evidence = evidence_dir or run.get("evidenceDir", "")
    stats = run_stats(root, day, evidence_dir=evidence)
    cloud_cases = set(stats["cloud"].get("cases", []))
    cases = sorted(project for project in {*stats["collectedCases"], *stats["archivedCases"], *cloud_cases} if CASE_NO.fullmatch(str(project)))
    names: dict[str, dict[str, str]] = {}
    water = waterline if waterline is not None else root.parent / "案卷水位记录.json"
    if water.is_file():
        try:
            for project, record in (json.loads(water.read_text(encoding="utf-8")).get("cases") or {}).items():
                names[project] = {"brigade": str(record.get("brigadeName") or ""), "unit": str(record.get("unitName") or "")}
        except (OSError, ValueError):
            pass
    issues = carry_issues(root, day) if run.get("status") == "FINISHED" else issues_of(root, day)
    waiting: dict[str, list[str]] = {}
    for issue in issues or []:
        project = str(issue.get("case") or "")
        if CASE_NO.fullmatch(project) and project not in cases:
            waiting.setdefault(project, []).append(_safe_reason(issue.get("detail"), str(issue.get("status") or "")))
    for state in due_cases(root, day, evidence_dir=evidence)["states"]:
        if state["kind"] != "completed" and state["case"] not in cases:
            reason = "写入效果未知，先回读对账，不重放" if state["kind"] == "unknown" else _safe_reason(state.get("detail"), "skipped" if state["kind"] != "open" else "")
            waiting.setdefault(state["case"], []).append(reason)
    pending = sorted(waiting)
    labelled = {project: index for index, project in enumerate([*cases, *pending], 1)}
    numbered = [{"n": index, "projectNo": project, "text": _case_label(index, project, names),
                 "collected": project in stats["collectedCases"], "archived": project in stats["archivedCases"], "cloudWritten": project in cloud_cases}
                for index, project in enumerate(cases, 1)]
    waiting_rows = [{"n": labelled[project], "projectNo": project, "text": f"{_case_label(labelled[project], project, names)}——待核：" + "；".join(dict.fromkeys(waiting[project])),
                     "reasons": list(dict.fromkeys(waiting[project]))} for project in pending]
    breakdown = stats["breakdown"]
    problems = []
    for index, issue in enumerate(issues or [], 1):
        project = issue.get("case") or ""
        where = _case_label(labelled[project], project, names) if project in labelled else (f"项目编号 {project}" if project else "")
        if issue.get("kind") == "cloud-unknown":
            reason = f"云写入有 {len(issue.get('items') or [])} 项没有回执与账本共同证明，先回读对账，不重放"
        elif issue.get("kind") == "cloud-not-attempted":
            reason = f"原批有 {len(issue.get('items') or [])} 项未发出请求，按稳定键回读后生成新计划"
        elif issue.get("kind") == "entry-incomplete":
            reason = f"{issue.get('entry') or '入口'}没有终态检查点，下一轮补读"
        else:
            reason = _safe_reason(issue.get("detail"), str(issue.get("status") or ""))
        stage = STAGE_NAMES.get(str(issue.get("stage") or ""), str(issue.get("stage") or ""))
        problems.append({"n": index, "kind": issue.get("kind"), "stage": issue.get("stage"), "case": project, "status": issue.get("status"), "reason": reason,
                         "text": "；".join(part for part in (where, f"{stage}阶段{KIND_NAMES.get(str(issue.get('kind')), str(issue.get('kind') or ''))}", reason) if part)})
    counts = {"cases": len(cases), "pendingCases": len(pending), "cloudCreatedRows": breakdown.get("cloudCreatedRows"), "cloudUpdatedOperations": breakdown.get("cloudUpdatedOperations"),
              "cloudUniqueFields": breakdown.get("cloudUniqueFields"), "cloudUnknown": breakdown.get("cloudUnknown"),
              "cloudNotAttempted": breakdown.get("cloudNotAttempted"),
              "unknownWriteCheckpoints": breakdown.get("unknownWriteCheckpoints"), "observation": breakdown.get("observation"),
              "changedCasesAndTasks": stats["changed"]}
    lines = [f"运行 {day}：{run.get('status')}。本轮新建或更新 {len(cases)} 案。" if cases else f"运行 {day}：{run.get('status')}。本轮没有新建或更新的案卷（0 案）。",
             *[item["text"] for item in numbered]]
    if waiting_rows:
        lines += [f"另有待核案 {len(waiting_rows)} 案（不计入上面的新建或更新案数）：", *[item["text"] for item in waiting_rows]]
    lines.append("分项（各是各的计数，不互相代替）：案数 %s；云端新建行 %s；更新操作 %s；唯一字段格 %s；云写入结果未知 %s；本轮未发出请求 %s；效果未知的写入检查点 %s；观察队列 %s。" % (
        len(cases), counts["cloudCreatedRows"], counts["cloudUpdatedOperations"], counts["cloudUniqueFields"], counts["cloudUnknown"],
        counts["cloudNotAttempted"], counts["unknownWriteCheckpoints"], json.dumps(counts["observation"], ensure_ascii=False)))
    lines += [f"问题 {p['n']}：{p['text']}" for p in problems] or ["问题：无。"]
    return {"schema": "NightReportV1", "date": day, "runStatus": run.get("status"), "caseCount": len(cases), "cases": numbered, "pendingCases": waiting_rows, "counts": counts,
            "problems": problems, "text": "\n".join(lines), "coverage": stats["coverage"], "namesFrom": "案卷水位记录" if names else "无"}


# ---------------------------------------------------------------------------------------------
# Supervision progress: how long a running execution has made no progress
# ---------------------------------------------------------------------------------------------

DEFAULT_SILENCE_MINUTES = 30
MIN_SILENCE_MINUTES = 30
MAX_SILENCE_MINUTES = 60
SILENCE_SAMPLES = 5


def _pid_alive(pid: int, runner: str = "claude-exec") -> bool | None:
    """None when this interpreter cannot look (no /proc, e.g. Windows Python): the supervisor then passes what it saw."""
    if not Path("/proc").is_dir():
        return None
    try:
        os.kill(int(pid), 0)
        return runner in Path(f"/proc/{int(pid)}/cmdline").read_bytes().decode("utf-8", "replace")
    except (OSError, ValueError):
        return False


def progress_times(root: Path, day: str, *, evidence_dir: str, start: datetime, end: datetime | None = None) -> list[datetime]:
    """When this run made progress inside ``[start, end]``: checkpoints, receipts/specs written under the evidence folder,
    cloud-ledger final events. Neither the runner process being alive nor the model session's own events count."""
    stop = end or datetime.max.replace(tzinfo=SHANGHAI)
    found = [_parse_time(i["at"]) for i in checkpoints(root, day)]
    evidence_dir = _native(evidence_dir)
    if evidence_dir and Path(evidence_dir).is_dir():
        for folder, _dirs, names in os.walk(evidence_dir):
            for name in names:
                if name.endswith((".json", ".jsonl")):
                    found.append(datetime.fromtimestamp((Path(folder) / name).stat().st_mtime, SHANGHAI))
    found += [_parse_time(e["at"]) for e in _ledger_events(root, since=start)]
    return sorted(t for t in found if start <= t <= stop)


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, -(-95 * len(ordered) // 100) - 1)]


def silence_threshold(root: Path, current_start: datetime) -> dict[str, Any]:
    """p95 of the gaps between progress events over the last ``SILENCE_SAMPLES`` ended executions before this one,
    clamped to 30–60 minutes; 30 while fewer samples exist. The silence of the running execution never enters it."""
    samples: list[tuple[datetime, list[float]]] = []
    for day in _run_days(root):
        run = _load(_run_dir(root, day) / "run.json", {})
        for execution in run.get("executions", []):
            if execution.get("endState") != "ENDED" or not execution.get("endedAt"):
                continue
            began = _parse_time(execution["boundAt"])
            if began >= current_start:
                continue
            times = progress_times(root, day, evidence_dir=run.get("evidenceDir", ""), start=began, end=_parse_time(execution["endedAt"]))
            points = [began, *times]
            gaps = [(b - a).total_seconds() / 60 for a, b in zip(points, points[1:])]
            if gaps:
                samples.append((began, gaps))
    samples.sort(key=lambda item: item[0])
    recent = samples[-SILENCE_SAMPLES:]
    if len(recent) < SILENCE_SAMPLES:
        return {"minutes": DEFAULT_SILENCE_MINUTES, "samples": len(recent), "basis": "样本不足 5 次，用默认 30 分钟"}
    value = _p95([gap for _, gaps in recent for gap in gaps])
    return {"minutes": min(MAX_SILENCE_MINUTES, max(MIN_SILENCE_MINUTES, round(value))), "samples": len(recent),
            "basis": f"最近 5 次成功执行的进展间隔 p95={value:.1f} 分钟，夹在 30–60 之间"}


def _session_activity(session_id: str, session_root: Path | None) -> datetime | None:
    base = session_root or Path.home() / ".claude" / "projects"
    hits = list(base.glob(f"*/{session_id}.jsonl")) if session_id and base.is_dir() else []
    return datetime.fromtimestamp(max(p.stat().st_mtime for p in hits), SHANGHAI) if hits else None


def progress_info(root: Path, day: str, now: datetime | None = None, *, alive: Any = _pid_alive,
                  session_root: Path | None = None) -> dict[str, Any]:
    """Read-only supervision view of a run's current execution. Over the threshold it only sets ``needsInspection``;
    nothing is killed or dispatched from here."""
    now = (now or now_shanghai()).astimezone(SHANGHAI)
    run = _load(_run_dir(root, day) / "run.json", None)
    if run is None:
        raise NightlyError("该日期没有运行记录")
    executions = run.get("executions", [])
    current = next((e for e in reversed(executions) if not e.get("endedAt")), None)
    if current is None:
        return {"date": day, "execution": None, "runnerAlive": False, "needsInspection": False,
                "note": "没有未结束的绑定执行"}
    began = _parse_time(current["boundAt"])
    times = progress_times(root, day, evidence_dir=run.get("evidenceDir", ""), start=began, end=now)
    last = times[-1] if times else None
    threshold = silence_threshold(root, began)
    silent = (now - (last or began)).total_seconds() / 60
    activity = _session_activity(current["sessionId"], session_root)
    return {"date": day, "execution": {"sessionId": current["sessionId"], "receipt": current["receipt"], "runnerPid": current["runnerPid"]},
            "runnerAlive": alive(current["runnerPid"], current.get("runner", "claude-exec")),
            "elapsedMinutes": round((now - began).total_seconds() / 60, 1),
            "lastProgressAt": last.isoformat(timespec="seconds") if last else None,
            "lastActivityAt": activity.isoformat(timespec="seconds") if activity else None,
            "silentMinutes": round(silent, 1), "thresholdMinutes": threshold["minutes"], "threshold": threshold,
            "needsInspection": silent > threshold["minutes"]}


# ---------------------------------------------------------------------------------------------
# Observation queue: tasks seen at the source that are not in the active range yet
# ---------------------------------------------------------------------------------------------


def _queue_path(root: Path) -> Path:
    return root / QUEUE_DIR / "观察队列.json"


def _queue(root: Path) -> dict[str, Any]:
    return _load(_queue_path(root), {"schemaVersion": "ObservationQueueV1", "items": {}})


IDENTITY_FIELDS = ("projectNo", "unitName", "brigade")


def _norm_identity(value: Any) -> str:
    return "".join(str(value or "").split())


def observe(root: Path, items: list[dict[str, Any]], *, observed_on: str, entry: str, days: int = FOLLOW_UP_DAYS, lane: str = "") -> dict[str, Any]:
    """Record what one source look saw. ``items``: ``{rwid, state, projectNo?, unitName?, brigade?, inspectionDate?, productEvidence?, awaitingProduct?}``.

    A queued task (any waiting state) leaves the queue as ``ENTER_SCOPE`` / ``OUT_OF_SCOPE`` only when all four identities
    (项目编号, RWID, 单位, 大队), the inspection date and the product evidence are present and agree with what the queue
    already holds; otherwise it stays, due again ``days`` later, and the history says what is missing. Tasks are linked by
    RWID only: the same project number or unit under another RWID is another task. Nothing is overwritten: every look
    appends to ``history``. A task not in the queue that already shows an inspection date goes straight to the normal source
    entry (which does its own four-identity check) and is not queued.
    """
    try:
        seen = date.fromisoformat(observed_on)
    except ValueError as error:
        raise NightlyError("observed_on 必须是 YYYY-MM-DD") from error
    queue = _queue(root)
    result: dict[str, Any] = {"added": 0, "kept": 0, "enterScope": [], "outOfScope": [], "blocked": [], "identityConflicts": [], "alreadyClosed": []}
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
        awaiting = bool(inspected) and (bool(item.get("awaitingProduct")) or item.get("productEvidence") is False)
        event: dict[str, Any] = {"on": observed_on, "entry": entry, "state": state}
        if lane:
            event["lane"] = lane      # 手工运行的观察事件带编号：当天常规运行的观察计数与统计不被它改变
        for field in ("inspectionDate", "productEvidence", "awaitingProduct"):
            if item.get(field) not in (None, ""):
                event[field] = item[field]
        incoming = {k: item[k] for k in IDENTITY_FIELDS if item.get(k)}
        if current is None:
            if inspected and inspected < SCOPE_FROM:
                result["outOfScope"].append(rwid)
                continue
            if inspected and not awaiting:
                result["enterScope"].append(rwid)      # 已有检查日期且在范围内：直接走正常来源登记，不入观察队列
                continue
            queue["items"][rwid] = current = {"rwid": rwid, "firstSeenOn": observed_on, "entry": entry, "looks": 0, "status": "WAITING",
                                              "history": []}
            event["outcome"] = "ADDED"
            result["added"] += 1
        elif current["status"] != "WAITING":
            result["alreadyClosed"].append(rwid)
            continue
        else:
            event["outcome"] = "KEPT"
            result["kept"] += 1
        conflicts = {k: {"queue": current[k], "now": v} for k, v in incoming.items()
                     if current.get(k) and _norm_identity(current[k]) != _norm_identity(v)}
        if conflicts:
            event.update(outcome="IDENTITY_CONFLICT", conflicts=conflicts)
            result["identityConflicts"].append({"rwid": rwid, "conflicts": conflicts})
        for key, value in incoming.items():
            current.setdefault(key, value)               # 已有的身份项不被覆盖
        known = {k: current.get(k) for k in IDENTITY_FIELDS}
        missing = [name for name, value in (("项目编号", known["projectNo"]), ("单位", known["unitName"]), ("大队", known["brigade"]),
                                             ("检查日期", inspected), ("产品证据", item.get("productEvidence") is True and not awaiting))
                   if not value]
        close = None
        if inspected and not conflicts:
            if missing:
                event["closeBlocked"] = missing
                result["blocked"].append({"rwid": rwid, "missing": missing})
            else:
                close = "OUT_OF_SCOPE" if inspected < SCOPE_FROM else "ENTER_SCOPE"
        current.setdefault("history", [])
        current["looks"] = current.get("looks", 0) + 1
        current.update(state=state, lastSeenOn=observed_on)
        if inspected:
            current["inspectionDate"] = inspected
        if close:
            event["outcome"] = close
            current.update(status=close, closedOn=observed_on, awaitingProduct=False)
            current.pop("nextDueOn", None)
            result["enterScope" if close == "ENTER_SCOPE" else "outOfScope"].append(rwid)
        else:
            current.update(status="WAITING", awaitingProduct=awaiting, nextDueOn=(seen + timedelta(days=days)).isoformat())
        current["history"].append(event)
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
