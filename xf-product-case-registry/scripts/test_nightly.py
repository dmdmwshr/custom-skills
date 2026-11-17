"""Nightly run record: repeat trigger, resume, interrupted write, quota pause, missed night, observation queue."""

from __future__ import annotations

import json
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
    assert ["acquire", "C-9"] in state["finishedSteps"] and state["changed"] == 1


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
