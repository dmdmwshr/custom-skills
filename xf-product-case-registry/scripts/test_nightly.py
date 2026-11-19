"""Nightly run record: repeat trigger, resume, interrupted write, quota pause, missed night, observation queue."""

from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timedelta

import pytest

import scripts.nightly as nightly

T = lambda text: datetime.fromisoformat(text).replace(tzinfo=nightly.SHANGHAI)  # noqa: E731


def _full(day, **change):
    report = {"dateField": "检查日期", "scopeFrom": "2026-01-01", "scopeTo": day, "unregistered": [],
              "entries": {name: {"total": 3, "rows": 3, "complete": True, "observedAt": day + "T02:10:00+08:00"} for name in nightly.COVERAGE_ENTRIES}}
    report.update(change)
    return report


def _entries(root, day):
    for entry in nightly.DISCOVER_ENTRIES:
        nightly.checkpoint(root, day, stage="discover", status="done", entry=entry)


def _all_done(root, day, skip=()):
    if nightly.coverage_state(root, today=day)["due"]:
        nightly.coverage(root, _full(day), day=day)
    for stage in nightly.STAGES:
        if stage == "discover" and stage not in skip:
            _entries(root, day)
        if stage not in skip:
            nightly.checkpoint(root, day, stage=stage, status="done")


def test_a_second_trigger_on_a_finished_night_writes_nothing(tmp_path):
    first = nightly.begin(tmp_path, now=T("2026-11-13T02:00:00"), trigger="scheduled")
    assert first["status"] == "STARTED" and first["nextStage"] == "discover"
    _all_done(tmp_path, "2026-11-13")
    done = nightly.finish(tmp_path, "2026-11-13")
    assert (done["status"], done["changed"], done["quiet"], done["issues"], done["finishedWithIssues"]) == ("FINISHED", 0, True, [], False)   # 无变化保持安静
    before = sorted((p.name, p.read_bytes()) for p in (tmp_path / "夜间运行" / "2026-11-13").iterdir())
    again = nightly.begin(tmp_path, now=T("2026-11-13T09:30:00"), trigger="manual")
    assert again["status"] == "ALREADY_FINISHED" and again["wrote"] is False
    assert before == sorted((p.name, p.read_bytes()) for p in (tmp_path / "夜间运行" / "2026-11-13").iterdir())
    with pytest.raises(nightly.NightlyError, match="已结束"):
        nightly.checkpoint(tmp_path, "2026-11-13", stage="sync", status="started")


def test_an_interrupted_run_resumes_per_case_and_an_interrupted_write_must_be_reconciled_not_replayed(tmp_path):
    nightly.begin(tmp_path, now=T("2026-11-13T02:00:00"))
    _entries(tmp_path, "2026-11-13")
    nightly.checkpoint(tmp_path, "2026-11-13", stage="discover", status="done")
    nightly.checkpoint(tmp_path, "2026-11-13", stage="acquire", status="done", case="C-1", changed=1)
    nightly.checkpoint(tmp_path, "2026-11-13", stage="acquire", status="started", case="C-2")     # 进程在此中断
    nightly.checkpoint(tmp_path, "2026-11-13", stage="organize", status="started", case="C-1")    # 本地步骤中断可直接续
    resumed = nightly.begin(tmp_path, now=T("2026-11-13T02:40:00"), trigger="manual")
    assert resumed["status"] == "RESUME" and resumed["nextStage"] == "acquire" and resumed["finishedSteps"] == [["acquire", "C-1"]]
    assert [(i["stage"], i["case"]) for i in resumed["unknown"]] == [("acquire", "C-2")]
    assert [(i["stage"], i["case"]) for i in resumed["resumable"]] == [("organize", "C-1")]
    with pytest.raises(nightly.NightlyError, match="先回读对账"):
        nightly.checkpoint(tmp_path, "2026-11-13", stage="acquire", status="started", case="C-2")
    assert nightly.finish(tmp_path, "2026-11-13")["status"] == "NOT_FINISHED"
    nightly.checkpoint(tmp_path, "2026-11-13", stage="acquire", status="unknown", case="C-2", detail="进程中断，效果未知")
    with pytest.raises(nightly.NightlyError, match="需要 --ref"):
        nightly.checkpoint(tmp_path, "2026-11-13", stage="acquire", status="reconciled", case="C-2", detail="回读：包未生成")
    nightly.checkpoint(tmp_path, "2026-11-13", stage="acquire", status="reconciled", case="C-2", detail="回读：包未生成", ref="/runs/orig-receipt.json")
    nightly.checkpoint(tmp_path, "2026-11-13", stage="acquire", status="started", case="C-2")     # 对账后才可重新开始
    nightly.checkpoint(tmp_path, "2026-11-13", stage="acquire", status="done", case="C-2", changed=1)
    nightly.checkpoint(tmp_path, "2026-11-13", stage="organize", status="done", case="C-1")
    _all_done(tmp_path, "2026-11-13", skip=("discover",))
    done = nightly.finish(tmp_path, "2026-11-13")
    assert (done["status"], done["changed"], done["quiet"]) == ("FINISHED", 0, False)       # 自报 2 处变化但没有任何凭证：不算变更数，也不当作无变化
    assert done["unverifiedReportedChanges"] == 2


def test_quota_pauses_the_run_and_the_next_trigger_continues_and_lists_the_missed_night(tmp_path):
    nightly.begin(tmp_path, now=T("2026-11-13T02:00:00"))
    _all_done(tmp_path, "2026-11-13")
    nightly.finish(tmp_path, "2026-11-13")
    nightly.begin(tmp_path, now=T("2026-11-15T02:00:00"))                                          # 11-14 的定时错过
    _entries(tmp_path, "2026-11-15")
    nightly.checkpoint(tmp_path, "2026-11-15", stage="discover", status="done")
    nightly.checkpoint(tmp_path, "2026-11-15", stage="acquire", status="quota", detail="模型额度用尽")
    assert nightly.status(tmp_path)["run"]["status"] == "PAUSED_QUOTA"
    assert nightly.finish(tmp_path, "2026-11-15")["status"] == "NOT_FINISHED"
    nxt = nightly.begin(tmp_path, now=T("2026-11-16T02:00:00"))
    assert nxt["status"] == "STARTED" and nxt["missedDates"] == ["2026-11-14", "2026-11-15"]
    assert nxt["earlierUnfinished"][0]["date"] == "2026-11-15" and nxt["earlierUnfinished"][0]["status"] == "PAUSED_QUOTA"
    back = nightly.begin(tmp_path, now=T("2026-11-15T23:00:00"))
    assert back["status"] == "RESUME" and back["nextStage"] == "acquire" and back["quota"][0]["detail"] == "模型额度用尽"


def test_observation_queue_keeps_the_seven_day_rhythm_and_enters_scope_only_on_a_confirmed_inspection_date(tmp_path):
    task = {"rwid": "R1", "state": "NOT_INSPECTED", "projectNo": "P1", "unitName": "甲", "brigade": "梁溪大队"}
    assert nightly.observe(tmp_path, [task, {"rwid": "R2", "state": "NOT_INSPECTED"}], observed_on="2026-03-01", entry="新建任务")["added"] == 2
    assert nightly.due(tmp_path, today="2026-03-07") == []
    assert [i["rwid"] for i in nightly.due(tmp_path, today="2026-03-08")] == ["R1", "R2"]
    for look in ("2026-03-08", "2026-06-01", "2026-10-30"):                                        # 等待再久，间隔仍是 7 天
        nightly.observe(tmp_path, [task], observed_on=look, entry="新建任务")
    assert nightly._queue(tmp_path)["items"]["R1"]["nextDueOn"] == "2026-11-06" and nightly._queue(tmp_path)["items"]["R1"]["looks"] == 4
    assert [i["rwid"] for i in nightly.due(tmp_path, today="2026-11-12")] == ["R2", "R1"]           # 错过的到期事项下次全部处理
    moved = nightly.observe(tmp_path, [{"rwid": "R1", "state": "SAMPLE_PENDING", "inspectionDate": "2026-11-10", "productEvidence": True},
                                       {"rwid": "R2", "state": "IN_PROGRESS", "inspectionDate": "2025-12-30", "projectNo": "P2", "unitName": "乙",
                                        "brigade": "锡山大队", "productEvidence": True},
                                       {"rwid": "R3", "state": "IN_PROGRESS", "inspectionDate": "2026-11-11"}], observed_on="2026-11-12", entry="检查日期范围")
    assert sorted(moved["enterScope"]) == ["R1", "R3"] and moved["outOfScope"] == ["R2"]
    assert nightly.due(tmp_path, today="2026-12-31") == []
    assert nightly.queue_summary(tmp_path, today="2026-11-12") == {"waiting": 0, "due": 0, "byState": {}, "enteredScope": 1, "outOfScope": 1}
    with pytest.raises(nightly.NightlyError, match="不是未检查"):
        nightly.observe(tmp_path, [{"rwid": "R9", "state": "NOT_INSPECTED", "inspectionDate": "2026-11-11"}], observed_on="2026-11-12", entry="x")


def test_inspected_task_without_product_rows_keeps_waiting(tmp_path):
    item = {"rwid": "R7", "state": "IN_PROGRESS", "inspectionDate": "2026-11-12", "awaitingProduct": True,
            "projectNo": "P7", "unitName": "丁", "brigade": "新吴大队"}
    first = nightly.observe(tmp_path, [item], observed_on="2026-11-12", entry="日常监督任务")
    assert first["added"] == 1 and first["enterScope"] == []
    assert [i["rwid"] for i in nightly.due(tmp_path, today="2026-11-19")] == ["R7"]
    assert nightly.observe(tmp_path, [item], observed_on="2026-11-19", entry="日常监督任务")["kept"] == 1
    done = nightly.observe(tmp_path, [{"rwid": "R7", "state": "IN_PROGRESS", "inspectionDate": "2026-11-12", "productEvidence": True}],
                           observed_on="2026-11-26", entry="日常监督任务")
    assert done["enterScope"] == ["R7"] and nightly.due(tmp_path, today="2026-12-30") == []


def test_weekly_full_range_check_is_recorded_blocks_finish_when_skipped_and_is_made_up_after_a_miss_or_failure(tmp_path):
    first = nightly.begin(tmp_path, now=T("2026-11-13T02:00:00"))
    assert first["weeklyCoverage"] == {"lastCompleteOn": None, "dueOn": "2026-11-13", "due": True, "attemptedToday": False,
                                       "todayOutcome": None, "todayReasons": [], "everyDays": 7}            # 从未核对过即到期
    _entries(tmp_path, "2026-11-13")
    for stage in nightly.STAGES:
        nightly.checkpoint(tmp_path, "2026-11-13", stage=stage, status="done")
    blocked = nightly.finish(tmp_path, "2026-11-13")
    assert blocked["status"] == "NOT_FINISHED" and blocked["coverageDue"]["due"] is True                       # 到期没做不能收尾
    done = nightly.coverage(tmp_path, _full("2026-11-13", ref="weekly.json", sha256="ab"), day="2026-11-13")
    assert done["outcome"] == "COMPLETE" and done["rowsTotal"] == 15 and done["weeklyCoverage"]["dueOn"] == "2026-11-20"
    first_done = nightly.finish(tmp_path, "2026-11-13")
    assert (first_done["status"], first_done["changed"], first_done["quiet"]) == ("FINISHED", 0, True)
    for day in ("2026-11-14", "2026-11-19"):                                                                   # 未到期的夜晚不要求
        nightly.begin(tmp_path, now=T(day + "T02:00:00"))
        _all_done(tmp_path, day)
        assert nightly.finish(tmp_path, day)["quiet"] is True
    assert len(nightly.coverage_checks(tmp_path)) == 1
    late = nightly.begin(tmp_path, now=T("2026-11-23T02:00:00"))                                              # 11-20 到期那晚没跑
    assert late["weeklyCoverage"]["due"] is True and late["weeklyCoverage"]["dueOn"] == "2026-11-20"
    _entries(tmp_path, "2026-11-23")
    for stage in nightly.STAGES:
        nightly.checkpoint(tmp_path, "2026-11-23", stage=stage, status="done")
    assert nightly.finish(tmp_path, "2026-11-23")["status"] == "NOT_FINISHED"
    entries = _full("2026-11-23")["entries"]
    entries["开业前检查任务"] = {"total": 227, "rows": 150, "complete": False, "error": "PAGE_NOT_ADVANCED"}
    failed = nightly.coverage(tmp_path, _full("2026-11-23", entries=entries), day="2026-11-23")
    assert failed["outcome"] == "INCOMPLETE" and failed["reasons"] == ["入口未读完整：开业前检查任务（共 227 条，读到 150 行）"]
    ended = nightly.finish(tmp_path, "2026-11-23")                                                             # 试过但没读完：可收尾，不安静
    assert ended["status"] == "FINISHED" and ended["quiet"] is False and ended["weeklyCoverage"]["todayOutcome"] == "INCOMPLETE"
    again = nightly.begin(tmp_path, now=T("2026-11-24T02:00:00"))
    assert again["weeklyCoverage"]["due"] is True and again["weeklyCoverage"]["lastCompleteOn"] == "2026-11-13"  # 下一晚补做
    nightly.coverage(tmp_path, _full("2026-11-24"), day="2026-11-24")
    assert nightly.coverage_state(tmp_path, today="2026-11-24")["dueOn"] == "2026-12-01"


@pytest.mark.parametrize("change, reason", [
    ({"dateField": "创建时间"}, "筛选字段不是检查日期：创建时间"),
    ({"scopeFrom": "2026-08-14"}, "范围起点晚于 2026-01-01"),
    ({"scopeTo": "2026-11-01"}, "范围终点早于运行日前一天"),
    ({"unregistered": [{"entry": "日常监督任务", "unitName": "甲", "inspectionDate": "2026-03-02", "disposition": ""}]}, "1 个未登记任务还没有处置"),
])
def test_a_check_that_does_not_prove_coverage_is_not_complete(tmp_path, change, reason):
    result = nightly.coverage(tmp_path, _full("2026-11-13", **change), day="2026-11-13")
    assert result["outcome"] == "INCOMPLETE" and result["reasons"] == [reason] and result["weeklyCoverage"]["due"] is True
    entries = _full("2026-11-13")["entries"]
    del entries["社会单位产品抽查任务"]
    assert nightly.coverage(tmp_path, _full("2026-11-13", entries=entries), day="2026-11-13")["reasons"] == ["缺入口：社会单位产品抽查任务"]
    kept = nightly.coverage(tmp_path, _full("2026-11-13", unregistered=[{"unitName": "甲", "disposition": "OBSERVED", "rwid": "R1"}]), day="2026-11-13")
    assert kept["outcome"] == "COMPLETE" and kept["unregistered"][0]["rwid"] == "R1"


def test_the_executing_session_binds_itself_to_the_run_and_only_that_execution_is_asked_about(tmp_path):
    with pytest.raises(nightly.NightlyError, match="先 begin"):
        nightly.bind(tmp_path, "2026-11-13", session_id="s-1", receipt="/runs/a-11.json", runner_pid=11)
    assert nightly.begin(tmp_path, now=T("2026-11-13T02:00:00"))["openExecutions"] == []
    with pytest.raises(nightly.NightlyError, match="不填猜测值"):
        nightly.bind(tmp_path, "2026-11-13", session_id="", receipt="/runs/a-11.json", runner_pid=11)
    bound = nightly.bind(tmp_path, "2026-11-13", session_id="s-1", receipt="/runs/a-11.json", runner_pid=11, now=T("2026-11-13T02:01:00"))
    assert bound["status"] == "BOUND" and bound["execution"]["runner"] == "claude-exec"
    assert nightly.bind(tmp_path, "2026-11-13", session_id="s-1", receipt="/runs/a-11.json", runner_pid=11)["status"] == "ALREADY_BOUND"
    assert [(e["date"], e["receipt"]) for e in nightly.open_executions(tmp_path)] == [("2026-11-13", "/runs/a-11.json")]
    again = nightly.begin(tmp_path, now=T("2026-11-14T02:00:00"))                               # 次晚：监督者看到的是这次绑定，不是别处的最新回执
    assert [(e["date"], e["sessionId"], e["runnerPid"]) for e in again["openExecutions"]] == [("2026-11-13", "s-1", 11)]
    with pytest.raises(nightly.NightlyError, match="没有绑定这份回执"):
        nightly.bind_end(tmp_path, "2026-11-13", receipt="/runs/other-project.json", state="ENDED")
    assert nightly.bind_end(tmp_path, "2026-11-13", receipt="/runs/a-11.json", state="ENDED", now=T("2026-11-14T02:00:30"))["status"] == "RECORDED"
    assert nightly.bind_end(tmp_path, "2026-11-13", receipt="/runs/a-11.json", state="LOST")["status"] == "ALREADY_ENDED"
    resumed = nightly.bind(tmp_path, "2026-11-13", session_id="s-1", receipt="/runs/b-12.json", runner_pid=12)   # 同一会话续接是新的一次执行
    assert resumed["status"] == "BOUND" and [e["receipt"] for e in nightly.open_executions(tmp_path)] == ["/runs/b-12.json"]


# ---------------------------------------------------------------------------------------------
# 2026-11-17 夜间流程优化（a27）：入口键、unknown 防覆盖、整阶段收口、问题带出、统计去重、监督进展、观察队列迁移
# ---------------------------------------------------------------------------------------------

DAY = "2026-11-17"


def _run(tmp_path, day=DAY):
    nightly.begin(tmp_path, now=T(day + "T02:00:00"))
    return day


def test_checkpoints_are_keyed_by_entry_and_old_records_without_entry_still_read(tmp_path):
    day = _run(tmp_path)
    first, second = nightly.DISCOVER_ENTRIES[:2]
    nightly.checkpoint(tmp_path, day, stage="discover", status="done", entry=first)
    nightly.checkpoint(tmp_path, day, stage="discover", status="failed", entry=second, detail="入口切换失败")
    state = nightly.progress(tmp_path, day)
    assert [first] == [e[2] for e in state["finishedEntries"] if e[2] == first] and second in [e[2] for e in state["finishedEntries"]]
    nightly.checkpoint(tmp_path, day, stage="discover", status="started", entry=second)         # 一个入口重来不影响另一个入口
    assert [e[2] for e in nightly.progress(tmp_path, day)["finishedEntries"]] == [first]
    old = {"at": "2026-11-17T02:05:00+08:00", "stage": "acquire", "status": "done", "case": "C-9", "changed": 2}   # 旧格式：无 entry、changed
    with (tmp_path / "夜间运行" / day / "checkpoints.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(old, ensure_ascii=False) + "\n")
    state = nightly.progress(tmp_path, day)
    assert ["acquire", "C-9"] in state["finishedSteps"] and state["reportedChanged"] == 2 and "changed" not in state     # 进度只带自报值，变更数由证据出口给


def test_a_stage_cannot_be_closed_while_its_cases_or_entries_are_open_and_discover_needs_all_ten_entries(tmp_path):
    day = _run(tmp_path)
    nightly.checkpoint(tmp_path, day, stage="acquire", status="started", case="C-1")
    with pytest.raises(nightly.NightlyError, match="整阶段不能记 done"):
        nightly.checkpoint(tmp_path, day, stage="acquire", status="done")
    nightly.checkpoint(tmp_path, day, stage="acquire", status="done", case="C-1")
    nightly.checkpoint(tmp_path, day, stage="acquire", status="done")
    with pytest.raises(nightly.NightlyError, match="整阶段不能记 done"):                          # 十个入口缺一个都不行
        nightly.checkpoint(tmp_path, day, stage="discover", status="done")
    for entry in nightly.DISCOVER_ENTRIES[:-1]:
        nightly.checkpoint(tmp_path, day, stage="discover", status="done", entry=entry)
    with pytest.raises(nightly.NightlyError, match="整阶段不能记 done"):
        nightly.checkpoint(tmp_path, day, stage="discover", status="done")
    nightly.checkpoint(tmp_path, day, stage="discover", status="done", entry=nightly.DISCOVER_ENTRIES[-1])
    assert nightly.checkpoint(tmp_path, day, stage="discover", status="done")["status"] == "done"


@pytest.mark.parametrize("stage", ["acquire", "sync", "organize", "report"])
@pytest.mark.parametrize("status", ["done", "failed", "skipped", "started"])
def test_an_unknown_step_is_never_overwritten_by_done_failed_skipped_or_started(tmp_path, stage, status):
    day = _run(tmp_path)
    nightly.checkpoint(tmp_path, day, stage=stage, status="unknown", case="C-1", detail="中断，效果未知")
    with pytest.raises(nightly.NightlyError, match="效果未知"):
        nightly.checkpoint(tmp_path, day, stage=stage, status=status, case="C-1", detail="x", ref="r")
    assert nightly.progress(tmp_path, day)["unknown"][0]["case"] == "C-1"


def test_unknown_closes_only_by_reconciled_and_a_write_stage_needs_the_original_receipt(tmp_path):
    day = _run(tmp_path)
    nightly.checkpoint(tmp_path, day, stage="sync", status="unknown", case="C-1")
    with pytest.raises(nightly.NightlyError, match="--ref"):
        nightly.checkpoint(tmp_path, day, stage="sync", status="reconciled", case="C-1", detail="回读一致")
    with pytest.raises(nightly.NightlyError, match="--detail"):
        nightly.checkpoint(tmp_path, day, stage="sync", status="reconciled", case="C-1", ref="/r/exec.json")
    with pytest.raises(nightly.NightlyError, match="效果未知"):                                   # 写阶段不能用保留事项覆盖云写入未知
        nightly.checkpoint(tmp_path, day, stage="sync", status="skipped", case="C-1", detail="混包", issue=True)
    done = nightly.checkpoint(tmp_path, day, stage="sync", status="reconciled", case="C-1", detail="回读：云端已有且一致", ref="/r/exec.json")
    assert done["ref"] == "/r/exec.json" and nightly.progress(tmp_path, day)["unknown"] == []
    nightly.checkpoint(tmp_path, day, stage="organize", status="unknown", case="C-090", detail="混包")
    kept = nightly.checkpoint(tmp_path, day, stage="organize", status="skipped", case="C-090", detail="来源包混入外案，已核实", issue=True)
    assert kept["issue"] is True and nightly.progress(tmp_path, day)["unknown"] == []


def _complete(tmp_path, day, failed=()):
    _entries(tmp_path, day)
    for stage in nightly.STAGES:
        nightly.checkpoint(tmp_path, day, stage=stage, status="failed" if stage in failed else "done")


def test_finish_carries_issues_without_hiding_them_and_only_new_issues_break_the_quiet(tmp_path):
    day = _run(tmp_path)
    nightly.coverage(tmp_path, _full(day), day=day)
    nightly.checkpoint(tmp_path, day, stage="organize", status="failed", case="C-5", detail="拆分失败")
    nightly.checkpoint(tmp_path, day, stage="organize", status="skipped", case="C-090", detail="混包", issue=True)
    _complete(tmp_path, day, failed=("organize",))                       # 有失败子项：整阶段只能记 failed，不能记 done
    done = nightly.finish(tmp_path, day)
    assert done["status"] == "FINISHED" and done["finishedWithIssues"] is True and done["quiet"] is False
    assert sorted(i["kind"] for i in done["issues"]) == ["failed", "failed", "retained-issue"] and len(done["newIssues"]) == 3   # 子项失败、整阶段失败、保留事项
    history = [json.loads(l) for l in (tmp_path / "夜间运行" / day / "checkpoints.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [h["status"] for h in history if h.get("case") == "C-5"] == ["failed"]
    nxt = nightly.begin(tmp_path, now=T("2026-11-18T02:00:00"))
    assert len(nxt["carryIssues"]) == 3
    nightly.checkpoint(tmp_path, "2026-11-18", stage="organize", status="failed", case="C-5", detail="拆分失败")      # 同一问题仍在：不算新问题
    nightly.checkpoint(tmp_path, "2026-11-18", stage="organize", status="skipped", case="C-090", detail="混包", issue=True)
    nightly.coverage(tmp_path, _full("2026-11-18"), day="2026-11-18") if nightly.coverage_state(tmp_path, today="2026-11-18")["due"] else None
    _complete(tmp_path, "2026-11-18", failed=("organize",))
    again = nightly.finish(tmp_path, "2026-11-18")
    assert again["finishedWithIssues"] is True and again["quiet"] is True and "newIssues" not in again


def test_a_discover_entry_never_recorded_is_an_issue_and_unknown_still_blocks_finish(tmp_path):
    day = _run(tmp_path)
    nightly.coverage(tmp_path, _full(day), day=day)
    nightly.checkpoint(tmp_path, day, stage="discover", status="failed", entry=nightly.DISCOVER_ENTRIES[0], detail="社会单位入口切换失败")
    for entry in nightly.DISCOVER_ENTRIES[1:]:
        nightly.checkpoint(tmp_path, day, stage="discover", status="done", entry=entry)
    with pytest.raises(nightly.NightlyError, match="整阶段不能记 done"):                      # 有入口失败：整阶段不能记 done，只能记 failed
        nightly.checkpoint(tmp_path, day, stage="discover", status="done")
    nightly.checkpoint(tmp_path, day, stage="discover", status="failed", detail="十个入口中 1 个没读完整")
    for stage in nightly.STAGES[1:]:
        nightly.checkpoint(tmp_path, day, stage=stage, status="done")
    nightly.checkpoint(tmp_path, day, stage="sync", status="unknown", case="C-1")
    assert nightly.finish(tmp_path, day)["status"] == "NOT_FINISHED"
    nightly.checkpoint(tmp_path, day, stage="sync", status="reconciled", case="C-1", detail="回读", ref="/r/a.json")
    done = nightly.finish(tmp_path, day)
    assert done["status"] == "FINISHED" and [(i["kind"], i["entry"]) for i in done["issues"]] == [("failed", ""), ("failed", nightly.DISCOVER_ENTRIES[0])]


def _receipt(base, step, folder, name, records, *, run_id, status="OK"):
    path = base / "sync" / step / folder
    path.mkdir(parents=True, exist_ok=True)
    (path / name).write_text(json.dumps({"runId": run_id, "status": status, "records": records}, ensure_ascii=False), encoding="utf-8")


def _ledger(root, run_id, record, *, intent=None, kind=None, final="OK", at="2026-11-16T18:40:00Z", table="tM"):
    """One intent of the unique write ledger (intent [+ bound for creates] + final)."""
    folder = root / "飞书写入账本" / "targets"
    folder.mkdir(parents=True, exist_ok=True)
    intent = intent or f"i-{run_id}-{record}"
    events = [{"event": "intent", "runId": run_id, "intentId": intent, "at": at, "target": {"table": table, "record": record}, **({"kind": kind} if kind else {})}]
    if kind == "create":
        events.append({"event": "bound", "runId": run_id, "intentId": intent, "at": at, "recordId": record})
    if final:
        events.append({"event": "final", "runId": run_id, "intentId": intent, "at": at, "status": final})
    with (folder / f"{intent}.jsonl").open("a", encoding="utf-8") as stream:
        for event in events:
            stream.write(json.dumps(event) + "\n")


def test_cloud_writes_are_counted_once_per_intent_from_receipts_and_ledger_unknown_apart_and_reported_changed_is_not_the_count(tmp_path):
    day = _run(tmp_path)
    evidence = tmp_path / "evidence"
    created = [{"tableId": "tM", "stableKey": {"value": "P-1"}, "status": "CREATED_VERIFIED", "recordId": "r1"},
               {"tableId": "tM", "stableKey": {"value": "P-2"}, "status": "CREATED_VERIFIED", "recordId": "r2"}]
    _receipt(evidence, "main-023551", "exec-main", "exec-create-01.json", created, run_id="run-a")
    _receipt(evidence, "renamed-folder", "exec-main", "exec-create-01.json", created, run_id="run-a")   # 同一回执复制到另一个目录（改名也不影响）
    update = [{"tableId": "tM", "stableKey": {"value": "P-1"}, "status": "WRITTEN", "recordId": "r1", "writtenFields": ["f1", "f2"]},
              {"tableId": "tM", "stableKey": {"value": "P-1"}, "status": "WRITTEN", "recordId": "r1", "writtenFields": ["f2", "f3"]}]   # 同一记录重复列出
    _receipt(evidence, "calibrate-023959", "exec", "exec-fp-01.json", update, run_id="run-b")
    _receipt(evidence, "qty-025233", "exec-qty", "exec-fp-01.json",
             [{"tableId": "tQ", "stableKey": {"value": "P-3|初查|1"}, "status": "UNKNOWN", "recordId": "r3"}], run_id="run-c", status="UNKNOWN")
    _ledger(tmp_path, "run-a", "r1", kind="create")
    _ledger(tmp_path, "run-a", "r2", kind="create")
    _ledger(tmp_path, "run-b", "r1")
    nightly.checkpoint(tmp_path, day, stage="sync", status="done", case="P-1", changed=9)
    stats = nightly.run_stats(tmp_path, day, evidence_dir=str(evidence))
    b = stats["breakdown"]
    assert (b["cloudCreatedRows"], b["cloudUpdatedOperations"], b["cloudUniqueFields"], b["cloudUnknown"]) == (2, 1, 3, 1)
    assert stats["cloud"]["duplicatesIgnored"] == 3 and stats["cloud"]["ledgerCrossCheck"]["result"] == "MISMATCH"   # run-c 有回执无账本 OK
    assert stats["cloud"]["ledgerCrossCheck"]["receiptRunsWithoutLedgerOk"] == ["run-c"]
    assert stats["changed"] == 2 and stats["reportedChanged"] == 9          # 实际变化 P-1、P-2 两案；自报数只并排保留
    _ledger(tmp_path, "run-c", "r3", intent="i-c")
    _ledger(tmp_path, "run-x", "rx", intent="i-x", at="2026-11-16T18:55:00Z")
    cross = nightly.run_stats(tmp_path, day, evidence_dir=str(evidence))["cloud"]["ledgerCrossCheck"]
    assert cross["ledgerOkRunsWithoutReceipt"] == ["run-x"] and cross["result"] == "MISMATCH"


def test_two_different_intents_on_one_record_are_two_writes_and_only_written_cells_are_counted(tmp_path):
    day = _run(tmp_path)
    evidence = tmp_path / "evidence"
    for number in (1, 2):
        record = {"status": "WRITTEN", "tableId": "table", "recordId": "record", "stableKey": {"value": "PROJECT"},
                  "fieldDetail": {"changed": {"old": number - 1, "new": number}, "unchanged": {"old": "same", "new": "same"}}, "writtenFields": ["changed"]}
        _receipt(evidence, f"labels-{number}", "exec", "exec-test.json", [record], run_id=f"run-{number}")
        _ledger(tmp_path, f"run-{number}", "record", table="table")
    cloud = nightly.cloud_stats(tmp_path, str(evidence), since=T("2026-11-16T10:00:00"))
    assert cloud["updatedOperations"] == 2 and cloud["uniqueFields"] == 1 and cloud["receiptOnlyUnconfirmed"] == 0
    stats = nightly.run_stats(tmp_path, day, evidence_dir=str(evidence))
    assert stats["breakdown"]["cloudUpdatedOperations"] == 2


def test_a_write_the_ledger_did_not_confirm_is_unknown_even_if_the_receipt_says_written(tmp_path):
    _run(tmp_path)
    evidence = tmp_path / "evidence"
    record = {"status": "WRITTEN", "tableId": "tM", "recordId": "r1", "stableKey": {"value": "P-1"}, "writtenFields": ["f1"]}
    _receipt(evidence, "calibrate-1", "exec", "exec-fp.json", [record], run_id="run-u", status="UNKNOWN")
    _ledger(tmp_path, "run-u", "r1", final="READBACK_FAILED")
    cloud = nightly.cloud_stats(tmp_path, str(evidence), since=T("2026-11-16T10:00:00"))
    assert cloud["updatedOperations"] == 0 and cloud["uniqueFields"] == 0
    assert [(u["status"], u["ledgerFinal"]) for u in cloud["unknown"]] == [("WRITTEN", ["READBACK_FAILED"])]
    _receipt(evidence, "calibrate-2", "exec", "exec-fp.json", [{**record, "recordId": "r2", "stableKey": {"value": "P-2"}}], run_id="run-v")
    _ledger(tmp_path, "run-v", "r2", final=None)                          # 有意图没有终态 = 没有确认
    cloud = nightly.cloud_stats(tmp_path, str(evidence), since=T("2026-11-16T10:00:00"))
    assert cloud["updatedOperations"] == 0 and len(cloud["unknown"]) == 2


def test_collection_and_archiving_are_counted_from_their_own_evidence_not_from_a_reported_number(tmp_path):
    business = tmp_path
    tmp_path = business / "运行记录"           # 水位记录在业务根，运行记录在其下
    day = _run(tmp_path)
    evidence = business / "evidence"
    nightly.checkpoint(tmp_path, day, stage="acquire", status="done", case="C-1", changed=17)
    assert nightly.run_stats(tmp_path, day, evidence_dir=str(evidence))["breakdown"]["collectedCases"] == 0       # 只有自报，没有采集凭证
    (evidence / "source").mkdir(parents=True)
    (evidence / "source" / "r53-new-results.jsonl").write_text("\n".join(json.dumps(x) for x in (
        {"projectNo": "C-1", "outcome": "NEW_PACKAGE_ATTACHED", "at": "2026-11-17T02:20:00+08:00"},
        {"projectNo": "C-2", "outcome": "FAILED_AT_OPEN", "at": "2026-11-17T02:21:00+08:00"},
        {"projectNo": "C-9", "outcome": "NEW_PACKAGE_ATTACHED", "at": "2026-11-10T02:20:00+08:00"})), encoding="utf-8")   # 别的夜晚的不算
    (business / "案卷水位记录.json").write_text(json.dumps({"cases": {
        "C-1": {"state": "COMPLETED", "completedAt": "2026-11-16T18:32:43Z"}, "C-5": {"state": "COMPLETED", "completedAt": "2026-08-25T02:44:39Z"},
        "C-6": {"state": "PENDING_ORGANIZATION"}}}), encoding="utf-8")
    stats = nightly.run_stats(tmp_path, day, evidence_dir=str(evidence))
    assert stats["breakdown"]["collectedCases"] == 1 and stats["breakdown"]["archivedCases"] == 1 and stats["changed"] == 1
    assert stats["collectedCases"] == ["C-1"] and stats["archivedCases"] == ["C-1"] and stats["reportedChanged"] == 17


def test_unknown_cannot_be_hidden_by_a_quota_record_and_a_failed_child_blocks_stage_done(tmp_path):
    day = _run(tmp_path)
    nightly.checkpoint(tmp_path, day, stage="sync", status="unknown", case="C-1", detail="请求结果未知", ref="/r/original.json")
    with pytest.raises(nightly.NightlyError, match="效果未知"):
        nightly.checkpoint(tmp_path, day, stage="sync", status="quota", case="C-1")
    assert nightly.progress(tmp_path, day)["unknown"][0]["case"] == "C-1"
    nightly.checkpoint(tmp_path, day, stage="acquire", status="failed", case="C-2", detail="下载确实失败")
    with pytest.raises(nightly.NightlyError, match="整阶段不能记 done"):
        nightly.checkpoint(tmp_path, day, stage="acquire", status="done")
    nightly.checkpoint(tmp_path, day, stage="acquire", status="skipped", case="C-2", detail="来源包缺件，已证实", issue=True)   # 已证实的来源问题可显式保留
    assert nightly.checkpoint(tmp_path, day, stage="acquire", status="done")["status"] == "done"


def test_stats_without_a_receipt_folder_say_so_instead_of_counting_zero(tmp_path):
    day = _run(tmp_path)
    stats = nightly.run_stats(tmp_path, day)
    assert stats["cloud"]["source"] == "none" and stats["breakdown"]["cloudCreatedRows"] is None and stats["changed"] == 0


def _bound_run(tmp_path, day, pid=11):
    nightly.begin(tmp_path, now=T(day + "T02:00:00"))
    nightly.bind(tmp_path, day, session_id="s-" + day, receipt=f"/runs/{day}.json", runner_pid=pid, now=T(day + "T02:01:00"))


def test_progress_view_counts_checkpoints_and_receipts_not_the_process_or_session(tmp_path):
    day = DAY
    _bound_run(tmp_path, day)
    sessions = tmp_path / "sessions" / "p"
    sessions.mkdir(parents=True)
    log = sessions / ("s-" + day + ".jsonl")
    log.write_text("{}", encoding="utf-8")
    nightly.checkpoint(tmp_path, day, stage="acquire", status="done", case="C-1", now=T(day + "T02:30:00"))
    now = T(day + "T03:20:00")
    info = nightly.progress_info(tmp_path, day, now, alive=lambda pid, runner: True, session_root=tmp_path / "sessions")
    assert info["runnerAlive"] is True and info["thresholdMinutes"] == 30 and info["elapsedMinutes"] == 79.0
    assert info["lastProgressAt"] == "2026-11-17T02:30:00+08:00" and info["silentMinutes"] == 50.0 and info["needsInspection"] is True
    assert info["lastActivityAt"] is not None                      # 会话活动只展示，不当作进展
    quiet = nightly.progress_info(tmp_path, day, T(day + "T02:50:00"), alive=lambda pid, runner: False, session_root=tmp_path / "sessions")
    assert quiet["runnerAlive"] is False and quiet["silentMinutes"] == 20.0 and quiet["needsInspection"] is False   # 进程存活与否不改变进展判断


def test_receipt_files_written_under_the_evidence_folder_count_as_progress(tmp_path):
    day = DAY
    _bound_run(tmp_path, day)
    evidence = tmp_path / "evidence"
    nightly.begin(tmp_path, now=T(day + "T02:05:00"), evidence_dir=str(evidence))
    _receipt(evidence, "main-1", "exec-main", "exec-create-01.json", [], run_id="r")
    stamp = (T(day + "T02:40:00")).timestamp()
    import os
    os.utime(evidence / "sync" / "main-1" / "exec-main" / "exec-create-01.json", (stamp, stamp))
    info = nightly.progress_info(tmp_path, day, T(day + "T02:55:00"), alive=lambda pid, runner: True)
    assert info["lastProgressAt"] == "2026-11-17T02:40:00+08:00" and info["silentMinutes"] == 15.0


def _ended(tmp_path, day, gap_minutes, count=3):
    nightly.begin(tmp_path, now=T(day + "T02:00:00"))
    nightly.bind(tmp_path, day, session_id="s-" + day, receipt=f"/runs/{day}.json", runner_pid=1, now=T(day + "T02:00:00"))
    for n in range(1, count + 1):
        nightly.checkpoint(tmp_path, day, stage="organize", status="done", case=f"C-{n}", now=T(day + "T02:00:00") + timedelta(minutes=gap_minutes * n))
    nightly.bind_end(tmp_path, day, receipt=f"/runs/{day}.json", state="ENDED", now=T(day + "T05:00:00"))


@pytest.mark.parametrize("gap, expected", [(10, 30), (45, 45), (90, 60)])
def test_silence_threshold_is_the_p95_of_recent_progress_gaps_clamped_to_30_to_60(tmp_path, gap, expected):
    for n in range(5):
        _ended(tmp_path, f"2026-11-1{n}", gap)
    _bound_run(tmp_path, "2026-11-17", pid=11)
    info = nightly.progress_info(tmp_path, "2026-11-17", T("2026-11-17T09:00:00"), alive=lambda pid, runner: False)
    assert info["thresholdMinutes"] == expected and info["threshold"]["samples"] == 5


def test_threshold_is_30_with_fewer_than_five_samples_and_the_current_silence_never_raises_it(tmp_path):
    for n in range(4):
        _ended(tmp_path, f"2026-11-1{n}", 50)
    _bound_run(tmp_path, "2026-11-17")
    early = nightly.progress_info(tmp_path, "2026-11-17", T("2026-11-17T03:00:00"), alive=lambda p, r: True)
    late = nightly.progress_info(tmp_path, "2026-11-17", T("2026-11-17T12:00:00"), alive=lambda p, r: True)
    assert early["thresholdMinutes"] == late["thresholdMinutes"] == 30 and early["threshold"]["samples"] == 4
    assert late["needsInspection"] is True and nightly.open_executions(tmp_path)[0]["date"] == "2026-11-17"   # 只标记，不结束也不改状态
    assert nightly.status(tmp_path, "2026-11-17")["run"]["status"] == "RUNNING"


def test_observation_leaves_the_queue_from_any_waiting_state_only_when_identities_date_and_product_evidence_close(tmp_path):
    base = {"projectNo": "P-1", "unitName": "八方汇", "brigade": "梁溪大队"}
    nightly.observe(tmp_path, [{"rwid": "R-1", "state": "SAMPLE_PENDING", **base}], observed_on="2026-11-10", entry="社会单位产品抽查任务")
    blocked = nightly.observe(tmp_path, [{"rwid": "R-1", "state": "SAMPLE_PENDING", "inspectionDate": "2026-11-12", "unitName": "八方汇"}],
                              observed_on="2026-11-17", entry="社会单位产品抽查任务")
    assert blocked["enterScope"] == [] and blocked["blocked"][0]["missing"] == ["产品证据"]      # 缺产品证据，别的身份项来自队列已有记录
    assert nightly.due(tmp_path, today="2026-11-24")[0]["rwid"] == "R-1"
    closed = nightly.observe(tmp_path, [{"rwid": "R-1", "state": "SAMPLE_PENDING", "inspectionDate": "2026-11-12", "productEvidence": True}],
                             observed_on="2026-11-18", entry="x")
    assert closed["enterScope"] == ["R-1"]
    item = nightly._queue(tmp_path)["items"]["R-1"]
    assert item["status"] == "ENTER_SCOPE" and [h["outcome"] for h in item["history"]] == ["ADDED", "KEPT", "ENTER_SCOPE"]
    assert item["history"][1]["closeBlocked"] == ["产品证据"] and item["history"][0]["state"] == "SAMPLE_PENDING"
    for state in ("IN_PROGRESS", "RECHECK_PENDING", "SOURCE_GAP"):                                   # 其余等待状态同样可迁移
        rwid = "R-" + state
        nightly.observe(tmp_path, [{"rwid": rwid, "state": state, **base}], observed_on="2026-11-10", entry="x")
        out = nightly.observe(tmp_path, [{"rwid": rwid, "state": state, "inspectionDate": "2025-12-31", "productEvidence": True}],
                              observed_on="2026-11-17", entry="x")
        assert out["outOfScope"] == [rwid]


def test_observation_is_linked_by_rwid_only_and_a_conflicting_identity_never_overwrites(tmp_path):
    old = {"rwid": "32100990204", "state": "SAMPLE_PENDING", "projectNo": "32002000C202600001", "unitName": "八方汇", "brigade": "梁溪大队"}
    nightly.observe(tmp_path, [old], observed_on="2026-11-10", entry="社会单位产品抽查任务")
    new = {"rwid": "32100923137", "state": "IN_PROGRESS", "projectNo": "32113513C202600029", "unitName": "八方汇", "brigade": "梁溪大队",
           "inspectionDate": "2026-11-16", "productEvidence": True}
    result = nightly.observe(tmp_path, [new], observed_on="2026-11-17", entry="日常监督任务")
    assert result["enterScope"] == ["32100923137"] or result["added"] == 0      # 新 RWID 有检查日期且证据齐：直接走正常登记，不并入旧观察项
    queue = nightly._queue(tmp_path)["items"]
    assert queue["32100990204"]["status"] == "WAITING" and queue["32100990204"]["projectNo"] == "32002000C202600001"
    clash = {"rwid": "32100990204", "state": "SAMPLE_PENDING", "projectNo": "32113513C202600029", "unitName": "八方汇", "brigade": "梁溪大队",
             "inspectionDate": "2026-11-16", "productEvidence": True}
    out = nightly.observe(tmp_path, [clash], observed_on="2026-11-17", entry="x")
    item = nightly._queue(tmp_path)["items"]["32100990204"]
    assert out["enterScope"] == [] and out["identityConflicts"][0]["rwid"] == "32100990204"
    assert item["status"] == "WAITING" and item["projectNo"] == "32002000C202600001" and item["history"][-1]["outcome"] == "IDENTITY_CONFLICT"


def test_evidence_paths_are_read_in_the_form_the_interpreter_understands_and_unknown_liveness_is_reported_as_none(tmp_path, monkeypatch):
    monkeypatch.setattr(nightly.os, "name", "nt")
    assert nightly._native("/mnt/d/ws/05-temp/x") == "D:/ws/05-temp/x" and nightly._native("D:/ws/y") == "D:/ws/y"
    monkeypatch.setattr(nightly.os, "name", "posix")
    assert nightly._native("D:/ws/05-temp/x") == "/mnt/d/ws/05-temp/x" and nightly._native("C:\\a\\b") == "/mnt/c/a/b"
    day = DAY
    _bound_run(tmp_path, day)
    info = nightly.progress_info(tmp_path, day, T(day + "T02:10:00"), alive=lambda pid, runner: None)
    assert info["runnerAlive"] is None and info["needsInspection"] is False


def test_a_task_that_is_both_a_case_and_a_queue_transition_counts_once_and_a_finished_run_can_refresh_its_stats(tmp_path):
    root = tmp_path / "运行记录"
    day = _run(root)
    nightly.coverage(root, _full(day), day=day)
    nightly.observe(root, [{"rwid": "R-1", "state": "IN_PROGRESS", "projectNo": "P-1", "unitName": "甲", "brigade": "梁溪"}], observed_on="2026-11-10", entry="x")
    nightly.observe(root, [{"rwid": "R-1", "state": "IN_PROGRESS", "projectNo": "P-1", "inspectionDate": "2026-11-17", "productEvidence": True}],
                    observed_on=day, entry="x")
    evidence = tmp_path / "evidence"
    (evidence / "source").mkdir(parents=True)
    (evidence / "source" / "r53-new-results.jsonl").write_text(json.dumps({"projectNo": "P-1", "outcome": "NEW_PACKAGE_ATTACHED", "at": day + "T03:00:00+08:00"}) + "\n", encoding="utf-8")
    _complete(root, day)
    done = nightly.finish(root, day, evidence_dir=str(evidence))
    assert done["changed"] == 1 and done["breakdown"]["collectedCases"] == 1 and done["breakdown"]["observation"]["enterScope"] == 1
    assert nightly.finish(root, day)["status"] == "ALREADY_FINISHED"
    run_file = root / "夜间运行" / day / "run.json"
    run = json.loads(run_file.read_text(encoding="utf-8")); run["changed"] = 2; run["breakdown"] = {"old": True}
    run_file.write_text(json.dumps(run), encoding="utf-8")                                           # 模拟旧版本算错的记录
    refreshed = nightly.finish(root, day, evidence_dir=str(evidence), refresh_stats=True)
    after = json.loads(run_file.read_text(encoding="utf-8"))
    assert refreshed["status"] == "STATS_REFRESHED" and after["changed"] == 1 and after["status"] == "FINISHED"
    assert after["statsRevisions"][0]["previous"] == {"changed": 2, "breakdown": {"old": True}} and after["finishedAt"] == run["finishedAt"]


def test_a_mixed_batch_counts_each_intent_by_its_own_evidence_not_by_the_batch_status(tmp_path):
    since = T("2026-11-16T10:00:00")
    evidence = tmp_path / "evidence"
    # 整批创建报告 PARTIAL：一条已核实创建，一条被挡（不是写入、不是未知）
    _receipt(evidence, "main-1", "exec-main", "exec-create.json", [
        {"status": "CREATED_VERIFIED", "tableId": "T", "recordId": "R1", "stableKey": {"value": "P1"}},
        {"status": "BLOCKED_ENTITY", "tableId": "T", "stableKey": {"value": "P2"}}], run_id="run-create", status="PARTIAL")
    _ledger(tmp_path, "run-create", "R1", kind="create", table="T")
    # 整批更新报告 FAILED：一条意图 OK，一条回读违规；另一条 WRITTEN 在账本里没有意图
    _receipt(evidence, "fp-1", "exec", "exec-fp.json", [
        {"status": "WRITTEN", "tableId": "T", "recordId": "U1", "stableKey": {"value": "P3"}, "writtenFields": ["a"]},
        {"status": "WRITTEN", "tableId": "T", "recordId": "U2", "stableKey": {"value": "P4"}, "writtenFields": ["b"]},
        {"status": "WRITTEN", "tableId": "T", "recordId": "U3", "stableKey": {"value": "P5"}, "writtenFields": ["c"]}], run_id="run-update", status="FAILED")
    _ledger(tmp_path, "run-update", "U1")
    _ledger(tmp_path, "run-update", "U2", final="VIOLATION")
    cloud = nightly.cloud_stats(tmp_path, str(evidence), since=since)
    assert (cloud["createdRows"], cloud["updatedOperations"], cloud["uniqueFields"]) == (1, 1, 1)
    assert cloud["heldOrNotWritten"] == {"BLOCKED_ENTITY": 1} and cloud["heldSample"][0]["stableKey"] == "P2"
    assert sorted((u["stableKey"], u["ledgerFinal"][0]) for u in cloud["unknown"]) == [("P4", "VIOLATION"), ("P5", "NO_INTENT")]
    assert cloud["ledgerCrossCheck"]["result"] == "MATCH" and cloud["cases"] == ["P1", "P3"]


def test_without_a_ledger_a_verified_creation_counts_but_an_update_needs_a_passed_batch(tmp_path):
    since = T("2026-11-16T10:00:00")
    evidence = tmp_path / "evidence"
    _receipt(evidence, "main-1", "exec", "exec-create.json", [
        {"status": "CREATED_VERIFIED", "tableId": "T", "recordId": "R1", "stableKey": {"value": "P1"}}], run_id="r-c", status="PARTIAL")
    _receipt(evidence, "fp-1", "exec", "exec-fp.json", [
        {"status": "WRITTEN", "tableId": "T", "recordId": "U1", "stableKey": {"value": "P3"}, "writtenFields": ["a"]}], run_id="r-u", status="UNKNOWN")
    cloud = nightly.cloud_stats(tmp_path, str(evidence), since=since)
    assert cloud["createdRows"] == 1 and cloud["receiptOnlyUnconfirmed"] == 1 and cloud["updatedOperations"] == 0
    assert [u["stableKey"] for u in cloud["unknown"]] == ["P3"]


def _evidence_with(root, day, cases):
    """A run evidence dir whose package-attached results name ``cases``; returns its path."""
    evidence = root / "evidence"
    (evidence / "source").mkdir(parents=True, exist_ok=True)
    (evidence / "source" / "r53-new-results.jsonl").write_text("".join(
        json.dumps({"projectNo": case, "outcome": "NEW_PACKAGE_ATTACHED", "at": day + "T03:00:00+08:00"}) + "\n" for case in cases), encoding="utf-8")
    return str(evidence)


def test_every_aggregate_output_counts_changed_from_evidence_and_keeps_the_self_reported_number_apart(tmp_path):
    root = tmp_path / "运行记录"
    day = _run(root)
    nightly.coverage(root, _full(day), day=day)
    evidence = _evidence_with(root, day, ["C-1", "C-2", "C-3"])
    nightly.checkpoint(root, day, stage="acquire", status="done", case="C-1", changed=1)       # 自报 2 处，凭证 3 案
    nightly.checkpoint(root, day, stage="sync", status="done", case="C-2", changed=1)
    status = nightly.status(root, day, evidence_dir=evidence)
    assert status["changed"] == status["stats"]["changed"] == 3 and status["reportedChanged"] == 2
    assert status["breakdown"]["collectedCases"] == 3 and "unverifiedReportedChanges" not in status
    resumed = nightly.begin(root, now=T(day + "T03:30:00"), evidence_dir=evidence)             # 同一晚再次触发
    assert resumed["status"] == "RESUME" and resumed["changed"] == 3 and resumed["reportedChanged"] == 2
    assert resumed["breakdown"] == status["breakdown"]
    unfinished = nightly.finish(root, day, evidence_dir=evidence)
    assert unfinished["status"] == "NOT_FINISHED" and unfinished["changed"] == 3 and unfinished["reportedChanged"] == 2
    _complete(root, day)
    done = nightly.finish(root, day, evidence_dir=evidence)
    assert done["status"] == "FINISHED" and done["changed"] == 3
    again = nightly.begin(root, now=T(day + "T04:00:00"))
    assert again["status"] == "ALREADY_FINISHED" and again["changed"] == 3 and again["reportedChanged"] == 2 and again["breakdown"]["collectedCases"] == 3
    final = nightly.status(root, day)                                                          # 不再传证据目录：用运行记录里的
    assert final["changed"] == final["stats"]["changed"] == final["run"]["changed"] == 3


def test_a_self_reported_change_without_evidence_is_flagged_not_counted_in_status_and_begin_and_old_records_still_read(tmp_path):
    root = tmp_path / "运行记录"
    day = _run(root)
    nightly.checkpoint(root, day, stage="acquire", status="done", case="C-1", changed=4)
    status = nightly.status(root, day)
    assert status["changed"] == status["stats"]["changed"] == 0 and status["reportedChanged"] == 4 and status["unverifiedReportedChanges"] == 4
    resumed = nightly.begin(root, now=T(day + "T03:00:00"))
    assert resumed["changed"] == 0 and resumed["unverifiedReportedChanges"] == 4
    old = {"at": day + "T02:05:00+08:00", "stage": "acquire", "status": "done", "case": "C-9", "changed": 2}   # 旧格式：只有 changed
    with (root / "夜间运行" / day / "checkpoints.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(old, ensure_ascii=False) + "\n")
    assert nightly.status(root, day)["reportedChanged"] == 6
    evidence = _evidence_with(root, day, ["C-9"])
    assert nightly.status(root, day, evidence_dir=evidence)["changed"] == 1


def test_unknown_writes_stay_visible_in_status_and_begin_next_to_the_evidence_based_count(tmp_path):
    root = tmp_path / "运行记录"
    day = _run(root)
    nightly.checkpoint(root, day, stage="sync", status="started", case="C-1")
    for result in (nightly.status(root, day), nightly.begin(root, now=T(day + "T03:00:00"))):
        assert [u["case"] for u in result["unknown"]] == ["C-1"] and result["breakdown"]["unknownWriteCheckpoints"] == 1 and result["changed"] == 0


@pytest.mark.parametrize("saved", [{"changed": 17}, {}], ids=["legacy-reported-count", "legacy-missing-count"])
def test_begin_on_a_finished_legacy_run_reports_the_evidence_count_without_writing_anything(tmp_path, saved):
    root = tmp_path / "运行记录"
    day = DAY
    run_dir = root / "夜间运行" / day
    run_dir.mkdir(parents=True)
    run_file = run_dir / "run.json"
    run_file.write_text(json.dumps({"schemaVersion": "NightlyRunV1", "date": day, "status": "FINISHED", "startedAt": day + "T02:00:00+08:00",
                                    "finishedAt": day + "T03:00:00+08:00", **saved}), encoding="utf-8")        # 旧记录：自报数或没有统计，没有任何凭证
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    again = nightly.begin(root, now=T(day + "T10:00:00"))
    status = nightly.status(root, day)
    assert again["status"] == "ALREADY_FINISHED" and again["wrote"] is False
    assert again["changed"] == status["changed"] == 0 and again["breakdown"] == status["breakdown"] and again["breakdown"]["collectedCases"] == 0
    assert again["reportedChanged"] == status["reportedChanged"] == 0 and "unverifiedReportedChanges" not in again
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before          # 旧的 changed 和统计原样保留，目录里没有新文件
    assert json.loads(run_file.read_text(encoding="utf-8")).get("changed") == saved.get("changed")


def test_begin_on_a_finished_run_flags_self_reported_changes_that_have_no_evidence(tmp_path):
    root = tmp_path / "运行记录"
    day = _run(root)
    nightly.coverage(root, _full(day), day=day)
    nightly.checkpoint(root, day, stage="acquire", status="done", case="C-1", changed=4)
    _complete(root, day)
    assert nightly.finish(root, day)["status"] == "FINISHED"
    again = nightly.begin(root, now=T(day + "T04:00:00"))
    assert again["status"] == "ALREADY_FINISHED" and again["changed"] == 0 and again["reportedChanged"] == 4 and again["unverifiedReportedChanges"] == 4


# ------------------------------------------------------------------ a33：读不出的回执分层、触发/接管视图

def _unreadable(base, step, folder, name, err=""):
    path = base / "sync" / step / folder
    path.mkdir(parents=True, exist_ok=True)
    (path / name).write_text("", encoding="utf-8")
    if err:
        (path / name).with_suffix(".err").write_text(err, encoding="utf-8")


def _ledger_line(root, intent, events):
    folder = root / "飞书写入账本" / "targets"
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / f"{intent}.jsonl").open("a", encoding="utf-8") as stream:
        for event in events:
            stream.write(json.dumps({"intentId": intent, "at": "2026-11-16T18:40:00Z", **event}) + "\n")


def test_a_receipt_that_cannot_be_read_is_classified_from_its_folder_evidence_and_never_silently_zero(tmp_path):
    day = _run(tmp_path)
    evidence = tmp_path / "evidence"
    _unreadable(evidence, "calibrate-1", "exec", "exec-fp-01.json", "ERROR: 飞书来源同步已拒绝：目标存在未对账……已拒绝(未发起任何外部调用)")
    log = str(evidence / "sync" / "main-1" / "logs-main" / "create-01")
    _unreadable(evidence, "main-1", "exec-main", "exec-create-01.json", "Traceback ... PermissionError")
    _ledger_line(tmp_path, "i-create", [{"event": "intent", "runId": "run-a", "kind": "create", "logDir": log, "target": {"table": "tM"}, "stableKey": {"value": "P-1"}},
                                         {"event": "reconciled", "runId": "run-reconcile", "outcome": "APPLIED"}])      # 另一次对账运行关闭它
    _unreadable(evidence, "main-2", "exec-main", "exec-fill-01.json", "Traceback ...")
    _ledger_line(tmp_path, "i-open", [{"event": "intent", "runId": "run-b", "logDir": str(evidence / "sync" / "main-2" / "logs-main" / "fill-01"), "target": {"table": "tM", "record": "r9"}, "stableKey": {"value": "P-9"}}])
    stats = nightly.run_stats(tmp_path, day, evidence_dir=str(evidence))
    cloud, b = stats["cloud"], stats["breakdown"]
    assert {Path(i["receipt"]).name: i["class"] for i in cloud["unreadable"]} == {
        "exec-fp-01.json": "REFUSED_NO_CALL", "exec-create-01.json": "RESOLVED_BY_LEDGER", "exec-fill-01.json": "UNKNOWN"}
    assert (b["cloudReceiptsUnreadable"], b["cloudRefusedNoCall"], b["cloudResolvedByLedger"], b["cloudUnknown"]) == (3, 1, 1, 1)
    assert cloud["unknown"][0]["status"] == "UNREADABLE_RECEIPT" and cloud["unknown"][0]["ledgerFinal"] == ["NO_FINAL"]
    assert "P-1" in cloud["cases"] and "P-9" not in cloud["cases"]
    # 未知的云写入随收尾带出到下一轮，而不是写成 0
    _all_done(tmp_path, day)
    finished = nightly.finish(tmp_path, day, evidence_dir=str(evidence))
    assert finished["status"] == "FINISHED" and finished["finishedWithIssues"] and not finished["quiet"]
    assert [i["kind"] for i in finished["issues"] if i["kind"] == "cloud-unknown"] == ["cloud-unknown"]


def test_a_late_scheduled_start_needs_a_proven_cause_and_a_manual_start_has_no_expected_time(tmp_path):
    nightly.begin(tmp_path, now=T("2026-11-18T08:12:17"), trigger="scheduled")
    nightly.begin(tmp_path, now=T("2026-11-19T19:00:00"), trigger="manual")
    late = nightly.run_view(tmp_path, "2026-11-18")["schedule"]
    assert (late["expectedAt"], late["delayMinutes"], late["delayCause"]) == ("2026-11-18T02:00:00+08:00", 372, {"status": "UNPROVEN"})
    manual = nightly.run_view(tmp_path, "2026-11-19")["schedule"]
    assert manual["manualStart"] is True and "delayCause" not in manual
    with pytest.raises(nightly.NightlyError, match="证据"):
        nightly.delay_cause(tmp_path, "2026-11-18", cause="自动化延迟", evidence="")
    with pytest.raises(nightly.NightlyError, match="人工发起"):
        nightly.delay_cause(tmp_path, "2026-11-19", cause="x", evidence="y")
    nightly.delay_cause(tmp_path, "2026-11-18", cause="自动化 08:10 才触发", evidence="/r/automation.log", now=T("2026-11-18T10:00:00"))
    again = nightly.delay_cause(tmp_path, "2026-11-18", cause="改因", evidence="/r/other", now=T("2026-11-18T11:00:00"))["schedule"]["delayCause"]
    assert again["cause"] == "改因" and again["history"][0]["cause"] == "自动化 08:10 才触发"


def test_a_run_left_running_after_its_executions_ended_is_shown_as_interrupted_and_a_second_execution_is_a_takeover(tmp_path):
    day = "2026-11-13"
    nightly.begin(tmp_path, now=T(day + "T01:56:37"), trigger="scheduled")
    assert nightly.run_view(tmp_path, day)["derivedStatus"] == "RUNNING_UNBOUND"
    nightly.bind(tmp_path, day, session_id="s1", receipt="/r/1.json", runner_pid=11, now=T(day + "T01:56:40"))
    assert nightly.run_view(tmp_path, day)["derivedStatus"] == "ACTIVE"
    nightly.bind_end(tmp_path, day, receipt="/r/1.json", state="LOST", detail="进程消失", now=T(day + "T09:14:18"))
    view = nightly.run_view(tmp_path, day)
    assert view["storedStatus"] == "RUNNING" and view["derivedStatus"] == "INTERRUPTED_UNFINISHED" and view["takeovers"] == []
    assert nightly.begin(tmp_path, now=T("2026-11-14T02:05:00"), trigger="scheduled")["earlierUnfinished"][0]["derivedStatus"] == "INTERRUPTED_UNFINISHED"
    nightly.bind(tmp_path, day, session_id="s2", receipt="/r/2.json", runner_pid=12, now=T(day + "T09:20:00"))
    view = nightly.run_view(tmp_path, day)
    assert view["derivedStatus"] == "ACTIVE" and view["takeovers"][0]["after"] == {"sessionId": "s1", "endState": "LOST", "endedAt": T(day + "T09:14:18").isoformat(timespec="seconds"), "detail": "进程消失"}
    assert [v["date"] for v in nightly.timeline(tmp_path, since="2026-11-14")] == ["2026-11-14"]


# ------------------------------------------------------------------ a34：汇总边界（收尾后的人工归档、零动作闭合、同规格匹配、原夜对账闭合）

def test_a_case_archived_by_hand_after_the_night_closed_is_not_counted_in_that_night(tmp_path):
    root = tmp_path / "运行记录"
    day = "2026-11-18"
    run = root / nightly.RUNS_DIR / day
    run.mkdir(parents=True)
    (run / "run.json").write_text(json.dumps({"schemaVersion": "NightlyRunV1", "date": day, "status": "FINISHED",
                                              "startedAt": day + "T02:00:00+08:00", "finishedAt": day + "T06:00:00+08:00"}), encoding="utf-8")
    (tmp_path / "案卷水位记录.json").write_text(json.dumps({"cases": {
        "A": {"state": "COMPLETED", "completedAt": day + "T05:55:00+08:00"}, "B": {"state": "COMPLETED", "completedAt": day + "T06:05:00+08:00"}}}), encoding="utf-8")
    assert nightly.run_stats(root, day)["archivedCases"] == ["A"]


def test_zero_action_closures_are_neither_changes_nor_unknown(tmp_path):
    evidence = tmp_path / "evidence"
    for name, label_event in (("a", {"event": "final", "status": "NOT_ATTEMPTED"}), ("b", {"event": "final", "status": "UNCHANGED"}),
                              ("c", {"event": "reconciled", "outcome": "NOT_APPLIED_PROVEN"})):
        _unreadable(evidence, f"s-{name}", "exec-main", "exec-main.json")
        _ledger_line(tmp_path, f"i-{name}", [{"event": "intent", "runId": f"r-{name}", "logDir": str(evidence / "sync" / f"s-{name}" / "logs-main"),
                                              "target": {"table": "t", "record": "r"}, "stableKey": {"value": f"P-{name}"}}, label_event])
    cloud = nightly.cloud_stats(tmp_path, str(evidence), since=T("2026-11-16T00:00:00"))
    assert cloud["cases"] == [] and cloud["unknown"] == [] and cloud["receiptsClosedNoWrite"] == 3
    assert {i["class"] for i in cloud["unreadable"]} == {"CLOSED_NO_WRITE"}


def test_an_unreadable_receipt_is_matched_only_to_the_intent_of_its_own_specification(tmp_path):
    evidence = tmp_path / "evidence"
    base = evidence / "sync" / "main-x"
    _unreadable(evidence, "main-x", "exec-main", "exec-create-01.json")
    _unreadable(evidence, "main-x", "exec-main", "exec-fill-01.json")
    _ledger_line(tmp_path, "i-1", [{"event": "intent", "runId": "r1", "kind": "create", "logDir": str(base / "logs-main" / "create-01"),
                                    "target": {"table": "t"}, "stableKey": {"value": "P-1"}}, {"event": "final", "status": "OK"}])
    cloud = nightly.cloud_stats(tmp_path, str(evidence), since=T("2026-11-16T00:00:00"))
    assert {Path(i["receipt"]).name: i["class"] for i in cloud["unreadable"]} == {"exec-create-01.json": "RESOLVED_BY_LEDGER", "exec-fill-01.json": "UNKNOWN"}
    assert cloud["unknown"][0]["receipt"] == "exec-fill-01.json"


def test_an_original_create_closed_by_a_later_reconcile_counts_once_in_its_own_night_and_the_reconcile_is_not_a_second_change(tmp_path):
    evidence = tmp_path / "evidence"
    _receipt(evidence, "main-1", "exec-main", "exec-create-01.json", [{"status": "UNKNOWN", "tableId": "tM", "stableKey": {"value": "P-132"}}], run_id="run-a", status="UNKNOWN")
    _receipt(evidence, "main-1", "exec-main", "exec-create-02.json", [{"status": "UNKNOWN", "tableId": "tM", "stableKey": {"value": "P-133"}}], run_id="run-b", status="UNKNOWN")
    _ledger_line(tmp_path, "i-132", [{"event": "intent", "runId": "run-a", "kind": "create", "target": {"table": "tM"}, "stableKey": {"value": "P-132"}},
                                     {"event": "reconciled", "runId": "run-rec", "outcome": "APPLIED"}])
    _ledger_line(tmp_path, "i-133", [{"event": "intent", "runId": "run-b", "kind": "create", "target": {"table": "tM"}, "stableKey": {"value": "P-133"}},
                                     {"event": "reconciled", "runId": "run-rec", "outcome": "NOT_APPLIED_PROVEN"}])
    cloud = nightly.cloud_stats(tmp_path, str(evidence), since=T("2026-11-16T00:00:00"))
    assert cloud["cases"] == ["P-132"] and cloud["createdRows"] == 1 and cloud["unknown"] == [] and cloud["heldOrNotWritten"] == {"RECONCILED_NOT_APPLIED_PROVEN": 1}


def test_an_open_unknown_carries_its_receipt_and_intent_so_the_next_night_can_check_exactly_that_item(tmp_path):
    day = _run(tmp_path)
    evidence = tmp_path / "evidence"
    _receipt(evidence, "main-1", "exec-main", "exec-create-01.json", [{"status": "UNKNOWN", "tableId": "tM", "stableKey": {"value": "P-9"}}], run_id="run-z", status="UNKNOWN")
    _ledger_line(tmp_path, "i-9", [{"event": "intent", "runId": "run-z", "kind": "create", "target": {"table": "tM"}, "stableKey": {"value": "P-9"}}])
    _all_done(tmp_path, day)
    finished = nightly.finish(tmp_path, day, evidence_dir=str(evidence))
    item = next(i for i in finished["issues"] if i["kind"] == "cloud-unknown")["items"][0]
    assert item["receipt"] == "exec-create-01.json" and item["runId"] == "run-z" and item["intentIds"] == ["i-9"] and item["stableKey"] == "P-9"


def test_a_ledger_event_waiting_in_the_durable_spool_counts_like_one_in_the_ledger(tmp_path):
    evidence = tmp_path / "evidence"
    _unreadable(evidence, "main-s", "exec-main", "exec-main.json")
    _ledger_line(tmp_path, "i-s", [{"event": "intent", "runId": "r-s", "logDir": str(evidence / "sync" / "main-s" / "logs-main"),
                                    "target": {"table": "t", "record": "r"}, "stableKey": {"value": "P-S"}}])
    spool = tmp_path / "飞书写入账本" / "pending"
    spool.mkdir(parents=True)
    (spool / "i-s.jsonl").write_text(json.dumps({"intentId": "i-s", "at": "2026-11-16T18:41:00Z", "event": "final", "runId": "r-s", "status": "OK"}) + "\n", encoding="utf-8")
    cloud = nightly.cloud_stats(tmp_path, str(evidence), since=T("2026-11-16T00:00:00"))
    assert cloud["cases"] == ["P-S"] and cloud["unknown"] == []


def test_ledger_writes_made_after_the_night_closed_are_not_that_nights_writes_and_do_not_break_the_cross_check(tmp_path):
    root = tmp_path / "运行记录"
    day = "2026-11-18"
    run = root / nightly.RUNS_DIR / day
    run.mkdir(parents=True)
    (run / "run.json").write_text(json.dumps({"schemaVersion": "NightlyRunV1", "date": day, "status": "FINISHED",
                                              "startedAt": day + "T02:00:00+08:00", "finishedAt": day + "T06:00:00+08:00"}), encoding="utf-8")
    evidence = tmp_path / "evidence"
    _receipt(evidence, "main-1", "exec-main", "exec-fill-01.json", [{"status": "WRITTEN", "tableId": "tM", "recordId": "rec1", "stableKey": {"value": "P-N"}, "writtenFields": ["f1"]}], run_id="run-night")
    _ledger(root, "run-night", "rec1", at="2026-11-17T21:00:00Z")           # 05:00 北京时间，夜里
    _ledger(root, "run-later", "rec2", at="2026-11-18T03:00:00Z")           # 11:00 北京时间，收尾以后的手工同步
    stats = nightly.run_stats(root, day, evidence_dir=str(evidence))
    assert stats["cloud"]["ledgerCrossCheck"]["result"] == "MATCH" and stats["cloud"]["cases"] == ["P-N"]


def test_a_ledger_log_directory_recorded_as_a_windows_path_matches_the_same_folder_as_the_wsl_path():
    assert nightly._same_path_key("D:\\ws\\x\\sync\\main-1\\logs-main\\create-01\\") == nightly._same_path_key("/mnt/d/ws/x/sync/main-1/logs-main/create-01")
    assert nightly._same_path_key("/mnt/d/ws/x/") != nightly._same_path_key("/mnt/d/ws/y")


def test_a_create_behind_an_empty_receipt_counts_once_as_a_created_row_when_the_ledger_proves_it(tmp_path):
    evidence = tmp_path / "evidence"
    for name in ("a", "b", "c"):
        _unreadable(evidence, f"s-{name}", "exec-main", "exec-create-01.json")
    def intent(name, extra, closing):
        _ledger_line(tmp_path, f"i-{name}", [{"event": "intent", "runId": f"r-{name}", "kind": "create", "target": {"table": "tM"}, "stableKey": {"value": f"P-{name}"},
                                              "logDir": str(evidence / "sync" / f"s-{name}" / "logs-main" / "create-01")}, *extra, closing])
    intent("a", [{"event": "bound", "runId": "r-a", "recordId": "recA"}], {"event": "final", "runId": "r-a", "status": "OK"})                      # 132：bound＋final OK
    intent("b", [], {"event": "reconciled", "runId": "r-rec", "outcome": "APPLIED", "observed": {"matches": ["recB"]}})                           # 133：对账证明＋唯一行
    intent("c", [], {"event": "reconciled", "runId": "r-rec", "outcome": "APPLIED"})                                                                # 没有行 ID 证据：不计行
    cloud = nightly.cloud_stats(tmp_path, str(evidence), since=T("2026-11-16T00:00:00"))
    assert cloud["createdRows"] == 2 and cloud["cases"] == ["P-a", "P-b", "P-c"] and cloud["unknown"] == [] and cloud["updatedOperations"] == 0


def _fields(**cells):
    return {fid: {"name": fid, "type": "select", "old": old, "wire": [new]} for fid, (old, new) in cells.items()}


def test_an_original_update_behind_an_empty_receipt_counts_only_with_field_level_proof_once_and_beside_a_create(tmp_path):
    evidence = tmp_path / "evidence"
    since = T("2026-11-16T00:00:00")
    def update(name, fields, closing, *, stable=None, table="tM", kind=None, record=None):
        _unreadable(evidence, f"s-{name}", "exec-main", "exec-fill-01.json")
        _ledger_line(tmp_path, f"i-{name}", [{"event": "intent", "runId": f"r-{name}", "kind": kind or "update", "fields": fields, "target": {"table": table, "record": record or f"rec-{name}"},
                                              "stableKey": {"value": stable or f"P-{name}"}, "logDir": str(evidence / "sync" / f"s-{name}" / "logs-main" / "fill-01")}, *closing])
    update("a", _fields(f1=(None, "x"), f2=(None, "y")), [{"event": "final", "runId": "r-a", "status": "OK"}])                                    # 写入进程自己的终态 OK：两格
    update("b", _fields(f1=(None, "x"), f2=(None, "y"), f3=(["z"], "z")),
           [{"event": "final", "runId": "r-b", "status": "VIOLATION"},
            {"event": "reconciled", "runId": "r-rec", "outcome": "APPLIED", "observed": {"fields": {"f1": ["x"], "f2": ["y"], "f3": ["z"]}}}])      # 对账证明：f3 旧值已是目标，不算改变
    update("c", _fields(f1=(None, "x"), f2=(None, "y")),
           [{"event": "reconciled", "runId": "r-rec", "outcome": "APPLIED", "observed": {"fields": {"f1": ["x"]}}}])                              # 对账没读到 f2：字段说不清，不计
    update("d", _fields(f1=(None, "x")), [{"event": "reconciled", "runId": "r-rec", "outcome": "NOT_APPLIED_PROVEN"}])                             # 证明未应用：不计
    update("e", _fields(f1=(None, "x")), [])                                                                                                       # 没有终态：仍是未知
    update("f", _fields(f1=(None, "x"), f9=(None, "q")), [{"event": "final", "runId": "r-f", "status": "OK"}], stable="P-a", record="rec-a")        # 同一记录的另一意图：另计一次操作，同一格只算一次
    _unreadable(evidence, "s-g", "exec-main", "exec-create-01.json")                                                                              # 同夜的创建照旧只计行
    _ledger_line(tmp_path, "i-g", [{"event": "intent", "runId": "r-g", "kind": "create", "target": {"table": "tM"}, "stableKey": {"value": "P-g"}, "logDir": str(evidence / "sync" / "s-g" / "logs-main" / "create-01")},
                                   {"event": "bound", "runId": "r-g", "recordId": "rec-g"}, {"event": "final", "runId": "r-g", "status": "OK"}])
    cloud = nightly.cloud_stats(tmp_path, str(evidence), since=since)
    assert (cloud["createdRows"], cloud["updatedOperations"], cloud["uniqueFields"]) == (1, 3, 5)      # a:2 + b:2 + f:{f1,f9}→新增f9 ，f1 同一格不重复
    assert cloud["updatesWithoutFieldProof"] == 1 and cloud["receiptsClosedNoWrite"] == 1
    assert [u["intentIds"] for u in cloud["unknown"]] == [["i-e"]]
    assert {Path(i["receipt"]).parents[1].name: i["class"] for i in cloud["unreadable"]}["s-e"] == "UNKNOWN"


def test_an_original_update_proved_after_the_night_counts_in_the_original_night_and_never_twice_with_a_readable_receipt(tmp_path):
    evidence = tmp_path / "evidence"
    day = "2026-11-16"
    fill = evidence / "sync" / "s-1" / "logs-main" / "fill-01"
    _unreadable(evidence, "s-1", "exec-main", "exec-fill-01.json")
    _ledger_line(tmp_path, "i-1", [{"event": "intent", "runId": "r-1", "kind": "update", "at": "2026-11-15T20:00:00Z", "fields": _fields(f1=(None, "x")), "target": {"table": "tM", "record": "rec-1"},
                                    "stableKey": {"value": "P-1"}, "logDir": str(fill)},
                                   {"event": "final", "runId": "r-1", "at": "2026-11-15T20:01:00Z", "status": "UNKNOWN"},
                                   {"event": "reconciled", "runId": "r-rec", "at": "2026-11-16T10:00:00Z", "outcome": "APPLIED", "observed": {"fields": {"f1": ["x"]}}}])   # 夜后对账
    since, until = T(f"{day}T02:00:00"), T(f"{day}T05:00:00")
    cloud = nightly.cloud_stats(tmp_path, str(evidence), since=since, until=until)
    assert (cloud["updatedOperations"], cloud["uniqueFields"], cloud["unknown"]) == (1, 1, [])
    # 同一意图另有一份可读回执（复制到别处）：同意图只计一次，不同的格并入
    _receipt(evidence, "copy", "exec-main", "exec-fill-01.json", [{"tableId": "tM", "stableKey": {"value": "P-1"}, "status": "WRITTEN", "recordId": "rec-1", "writtenFields": ["f1", "f2"],
                                                               "intentId": "i-1"}], run_id="r-1")
    again = nightly.cloud_stats(tmp_path, str(evidence), since=since, until=until)
    assert again["updatedOperations"] == 1 and again["duplicatesIgnored"] >= 1 and again["uniqueFields"] == 2
    # 旧夜的账本与回执一字节不动：重算只读
    assert not list(tmp_path.rglob("*.tmp"))


# ------------------------------------------------------------------ a35：未决跨夜可定位并持续到证据闭合

def _night(root, day, *, evidence=None):
    at = lambda hhmm: datetime.fromisoformat(f"{day}T{hhmm}+08:00")  # noqa: E731
    nightly.begin(root, now=at("02:00:00"), evidence_dir=evidence or "")
    if nightly.coverage_state(root, today=day)["due"]:
        nightly.coverage(root, _full(day), day=day, now=at("02:15:00"))
    for entry in nightly.DISCOVER_ENTRIES:
        nightly.checkpoint(root, day, stage="discover", entry=entry, status="done", now=at("02:15:00"))
    for step in nightly.STAGES:
        nightly.checkpoint(root, day, stage=step, status="done", now=at("02:25:00"))
    return nightly.finish(root, day, now=at("02:30:00"))


def test_an_unresolved_receipt_is_carried_with_its_exact_path_across_healthy_nights_and_late_evidence_closes_only_that_item(tmp_path):
    root = tmp_path / "运行记录"
    evidence = tmp_path / "第一晚证据"
    paths = [evidence / "sync" / name / "exec-main" / "exec-fill-01.json" for name in ("main-a", "main-b")]       # 同名回执在不同目录
    for receipt in paths:
        receipt.parent.mkdir(parents=True)
        receipt.touch()
    first = _night(root, "2026-11-15", evidence=str(evidence))
    saved = (root / nightly.RUNS_DIR / "2026-11-15" / "run.json").read_bytes()
    refs = lambda issues: {i["receiptPath"] for issue in issues if issue["kind"] == "cloud-unknown" for i in issue["items"]}  # noqa: E731
    assert first["finishedWithIssues"] and refs(first["issues"]) == {str(p) for p in paths}
    assert {i["originDate"] for issue in first["issues"] for i in issue.get("items", [])} == {"2026-11-15"}
    second = _night(root, "2026-11-16")            # 第二晚正常收尾，没有新证据目录
    assert second["status"] == "FINISHED" and refs(second["issues"]) == {str(p) for p in paths}
    third = nightly.begin(root, now=datetime.fromisoformat("2026-11-17T02:00:00+08:00"))
    assert refs(third["carryIssues"]) == {str(p) for p in paths}                    # 第三晚仍看得到，且分得清同名路径
    paths[0].with_suffix(".err").write_text("拒绝：未发起任何外部调用\n", encoding="utf-8")        # 后到的证据只对应其中一项
    assert refs(nightly.carry_issues(root, "2026-11-16")) == {str(paths[1])}
    assert (root / nightly.RUNS_DIR / "2026-11-15" / "run.json").read_bytes() == saved       # 历史原始记录字节不变


def test_a_legacy_saved_cloud_unknown_without_receipt_paths_is_rederived_from_its_own_evidence_directory(tmp_path):
    root = tmp_path / "运行记录"
    evidence = tmp_path / "证据"
    receipt = evidence / "sync" / "main-a" / "exec-main" / "exec-fill-01.json"
    receipt.parent.mkdir(parents=True)
    receipt.touch()
    run = nightly._run_dir(root, "2026-11-15")
    run.mkdir(parents=True)
    (run / "run.json").write_text(json.dumps({"schemaVersion": "NightlyRunV1", "date": "2026-11-15", "status": "FINISHED", "startedAt": "2026-11-15T02:00:00+08:00",
                                              "finishedAt": "2026-11-15T03:00:00+08:00", "evidenceDir": str(evidence),
                                              "issues": [{"kind": "cloud-unknown", "stage": "sync", "case": "", "entry": "", "status": "unknown", "detail": "旧版", "ref": ""}]}), encoding="utf-8")
    carried = nightly.carry_issues(root, "2026-11-15")
    assert [i["receiptPath"] for i in carried[0]["items"]] == [str(receipt)] and carried[0]["items"][0]["originDate"] == "2026-11-15"


# ------------------------------------------------------------------ a36：多目标未决结转、超出展示数不丢、新增不同对象不静默

def _batch(path, run_id, stables):
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"runId": run_id, "status": "UNKNOWN", "records": [
        {"recordId": f"rec-{s}", "tableId": "t", "stableKey": {"value": s}, "status": "UNKNOWN"} for s in stables]}), encoding="utf-8")


def test_one_receipt_with_several_unknown_targets_keeps_every_target_beyond_any_display_limit_and_a_new_object_is_not_quiet(tmp_path):
    root = tmp_path / "运行记录"
    first_ev = tmp_path / "第一夜"
    _batch(first_ev / "sync/main-a/exec-main/exec-fill-01.json", "A", ["s-1", "s-2"])
    for n in range(61):
        _unreadable(first_ev, f"main-o{n}", "exec-main", "exec-fill-01.json")
    closed = _night(root, "2026-11-15", evidence=str(first_ev))
    items = lambda issues: [i for issue in issues if issue["kind"] == "cloud-unknown" for i in issue["items"]]  # noqa: E731
    assert len(items(closed["issues"])) == 63 and closed["breakdown"]["cloudUnknown"] == 63
    assert sorted(i["stableKey"] for i in items(closed["issues"]) if i.get("stableKey")) == ["s-1", "s-2"]
    second_ev = tmp_path / "第二夜"
    _batch(second_ev / "sync/main-a/exec-main/exec-fill-01.json", "B", ["s-new"])
    second = _night(root, "2026-11-16", evidence=str(second_ev))
    assert len(items(second["issues"])) == 64 and not second["quiet"] and second["newIssues"]      # 同类已有，又出现不同对象：照常通知
    third = _night(root, "2026-11-17")
    assert len(items(third["issues"])) == 64 and "newIssues" not in third                           # 同一对象重复出现：安静


def test_late_proof_for_one_of_several_targets_of_the_same_receipt_removes_only_that_target(tmp_path):
    root = tmp_path / "运行记录"
    ev = tmp_path / "证据"
    _batch(ev / "sync/main-a/exec-main/exec-fill-01.json", "A", ["s-1", "s-2"])
    _ledger_line(root, "i-1", [{"event": "intent", "runId": "A", "kind": "update", "target": {"table": "t", "record": "rec-s-1"}, "stableKey": {"value": "s-1"}},
                               {"event": "final", "runId": "A", "status": "OK"}])
    _ledger_line(root, "i-2", [{"event": "intent", "runId": "A", "kind": "update", "target": {"table": "t", "record": "rec-s-2"}, "stableKey": {"value": "s-2"}}])
    closed = _night(root, "2026-11-15", evidence=str(ev))
    keys = [i["stableKey"] for issue in closed["issues"] if issue["kind"] == "cloud-unknown" for i in issue["items"]]
    assert "s-2" in keys
    carried = [i["stableKey"] for issue in nightly.carry_issues(root, "2026-11-15") if issue["kind"] == "cloud-unknown" for i in issue["items"]]
    assert "s-2" in carried


# ------------------------------------------------------------------ a39：同日手工运行（额外一轮，与当天常规运行分开）

def _lane_done(root, key):
    for stage in nightly.STAGES:
        if stage == "discover":
            _entries(root, key)
        nightly.checkpoint(root, key, stage=stage, status="done")


def _bytes(root, day):
    folder = nightly._run_dir(root, day)
    return {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()}


def test_a_finished_regular_night_gets_an_extra_manual_run_in_its_own_record_and_the_regular_files_stay_byte_identical(tmp_path):
    root, day, key = tmp_path / "运行记录", "2026-11-19", "2026-11-19@m1"
    _night(root, day)
    regular = _bytes(root, day)
    stats_before = nightly.run_stats(root, day, evidence_dir="")
    assert nightly.begin(root, now=T("2026-11-19T10:00:00"), trigger="manual")["status"] == "ALREADY_FINISHED"      # 默认入口不变：同日续办/已结束
    started = nightly.begin(root, now=T("2026-11-19T10:00:00"), trigger="manual", manual="m1", evidence_dir=str(tmp_path / "手工证据"))
    assert started["status"] == "STARTED" and started["date"] == key and started["lane"]["regularRun"] == day
    assert nightly._run_dir(root, key) == root / nightly.RUNS_DIR / "手工" / key and nightly._run_days(root) == [day]      # 常规日列表不混入手工运行
    assert nightly._run_days(root, lanes=True) == [day, key]
    assert (nightly._load(nightly._run_dir(root, key) / "run.json", {})["schedule"].get("manualStart")) is True
    nightly.bind(root, key, session_id="s-m1", receipt="r-m1.json", runner_pid=11)
    assert [e["date"] for e in nightly.open_executions(root)] == [key]                      # 默认 supervisor 的 openExecutions 看得见手工运行
    _lane_done(root, key)                                                                    # 没有重做当日已验的每周覆盖，也能收尾
    nightly.bind_end(root, key, receipt="r-m1.json", state="ENDED")
    finished = nightly.finish(root, key, evidence_dir=str(tmp_path / "手工证据"))
    assert finished["status"] == "FINISHED" and nightly.open_executions(root) == []
    assert _bytes(root, day) == regular                                                      # 原日 run.json、checkpoints 字节不变
    assert nightly.run_stats(root, day, evidence_dir="") == stats_before                      # 常规历史统计不受手工写入影响
    again = nightly.begin(root, now=T("2026-11-19T11:00:00"), manual="m1")
    assert again["status"] == "ALREADY_FINISHED" and again["wrote"] is False                 # 同编号重复触发幂等
    assert nightly.timeline(root, since=day)[-1]["date"] == key


def test_a_manual_run_is_serialized_against_unfinished_runs_open_executions_and_a_taken_id(tmp_path):
    root, day = tmp_path / "运行记录", "2026-11-19"
    nightly.begin(root, now=T(day + "T02:00:00"), trigger="scheduled")
    with pytest.raises(nightly.NightlyError, match="还没结束"):
        nightly.begin(root, now=T(day + "T03:00:00"), manual="m1")                               # 常规运行进行中：不叠加
    _all_done(root, day)
    nightly.bind(root, day, session_id="s", receipt="r.json", runner_pid=5)
    nightly.finish(root, day)
    with pytest.raises(nightly.NightlyError, match="未记终态的绑定执行"):
        nightly.begin(root, now=T(day + "T10:00:00"), manual="m1")                               # 常规执行没记终态：不交叉
    nightly.bind_end(root, day, receipt="r.json", state="ENDED")
    nightly.begin(root, now=T(day + "T10:00:00"), manual="m1")
    with pytest.raises(nightly.NightlyError, match="已有未结束的手工运行"):
        nightly.begin(root, now=T(day + "T10:05:00"), manual="m2")                               # 另一条手工运行未结束
    with pytest.raises(nightly.NightlyError, match="编号"):
        nightly.begin(root, now=T(day + "T10:05:00"), manual="bad/id")
    resumed = nightly.begin(root, now=T(day + "T10:10:00"), manual="m1")
    assert resumed["status"] == "RESUME"
    (nightly._run_dir(root, day + "@m3")).mkdir(parents=True)                                    # 编号被另一次启动占住
    with pytest.raises(nightly.NightlyError, match="占用|未结束"):
        nightly.begin(root, now=T(day + "T10:20:00"), manual="m3")
    with pytest.raises(nightly.NightlyError, match="没有常规运行"):
        nightly.begin(root, now=T("2026-11-20T10:00:00"), manual="m1")


def test_manual_unknowns_and_observations_are_carried_to_the_next_night_but_never_counted_into_the_regular_night(tmp_path):
    root, day, key = tmp_path / "运行记录", "2026-11-19", "2026-11-19@m1"
    _night(root, day)
    nightly.observe(root, [{"rwid": "1", "state": "NOT_INSPECTED"}], observed_on=day, entry="日常监督任务")        # 常规运行的观察
    regular_obs = nightly._observation_today(root, day)
    evidence = tmp_path / "手工证据"
    nightly.begin(root, now=T(day + "T10:00:00"), manual="m1", evidence_dir=str(evidence))
    nightly.observe(root, [{"rwid": "2", "state": "NOT_INSPECTED"}], observed_on=day, entry="日常监督任务", lane="m1")
    assert nightly._observation_today(root, key)["added"] == 1 and nightly._observation_today(root, day) == regular_obs
    _unreadable(evidence, "main-x", "exec-main", "exec-fill-01.json", "Traceback")                  # 手工运行里留下一个读不出的回执：未知
    _lane_done(root, key)
    closed = nightly.finish(root, key, evidence_dir=str(evidence))
    assert closed["status"] == "FINISHED" and closed["finishedWithIssues"] and any(i["kind"] == "cloud-unknown" for i in closed["issues"])
    nxt = nightly.begin(root, now=T("2026-11-20T02:00:00"))
    carried = [i for issue in nxt["carryIssues"] if issue["kind"] == "cloud-unknown" for i in issue["items"]]
    assert carried and carried[0]["originDate"] == key                                              # 手工未知沿到期核对进入下一轮，带手工运行键


def test_manual_coverage_is_recorded_once_in_the_shared_log_with_its_lane_and_old_records_without_lanes_still_read(tmp_path):
    root, day = tmp_path / "运行记录", "2026-11-19"
    old = nightly._run_dir(root, "2026-11-18")
    old.mkdir(parents=True)
    (old / "run.json").write_text(json.dumps({"schemaVersion": "NightlyRunV1", "date": "2026-11-18", "status": "FINISHED", "startedAt": "2026-11-18T02:00:00+08:00",
                                              "finishedAt": "2026-11-18T03:00:00+08:00", "entryKeyed": True}), encoding="utf-8")      # 没有 lane 字段、没有手工目录的旧记录
    assert nightly._run_days(root, lanes=True) == ["2026-11-18"] and nightly.timeline(root)[0]["derivedStatus"] == "FINISHED"
    _night(root, day)
    nightly.begin(root, now=T(day + "T10:00:00"), manual="m1")
    result = nightly.coverage(root, _full(day), day=day + "@m1")
    assert result["lane"] == day + "@m1" and result["date"] == day and nightly.coverage_state(root, today=day)["lastCompleteOn"] == day


def test_an_old_unfinished_day_without_unknown_writes_does_not_block_a_manual_run_but_an_unknown_write_does(tmp_path):
    root, day = tmp_path / "运行记录", "2026-11-19"
    nightly.begin(root, now=T("2026-11-13T02:00:00"), trigger="scheduled")                   # 更早一夜没收尾（历史遗留，没有未知写入）
    _night(root, day)
    assert nightly.begin(root, now=T(day + "T10:00:00"), manual="m1")["status"] == "STARTED"
    nightly.checkpoint(root, "2026-11-13", stage="sync", status="started")                    # 写阶段停在 started＝效果未知
    with pytest.raises(nightly.NightlyError, match="效果未知的写入"):
        nightly.begin(root, now=T(day + "T11:00:00"), manual="m2")
