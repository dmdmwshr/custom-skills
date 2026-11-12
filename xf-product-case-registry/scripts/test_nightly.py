"""Nightly run record: repeat trigger, resume, interrupted write, quota pause, missed night, observation queue."""

from __future__ import annotations

from datetime import datetime

import pytest

import scripts.nightly as nightly

T = lambda text: datetime.fromisoformat(text).replace(tzinfo=nightly.SHANGHAI)  # noqa: E731


def _full(day, **change):
    report = {"dateField": "检查日期", "scopeFrom": "2026-01-01", "scopeTo": day, "unregistered": [],
              "entries": {name: {"total": 3, "rows": 3, "complete": True, "observedAt": day + "T02:10:00+08:00"} for name in nightly.COVERAGE_ENTRIES}}
    report.update(change)
    return report


def _all_done(root, day, skip=()):
    if nightly.coverage_state(root, today=day)["due"]:
        nightly.coverage(root, _full(day), day=day)
    for stage in nightly.STAGES:
        if stage not in skip:
            nightly.checkpoint(root, day, stage=stage, status="done")


def test_a_second_trigger_on_a_finished_night_writes_nothing(tmp_path):
    first = nightly.begin(tmp_path, now=T("2026-11-13T02:00:00"), trigger="scheduled")
    assert first["status"] == "STARTED" and first["nextStage"] == "discover"
    _all_done(tmp_path, "2026-11-13")
    done = nightly.finish(tmp_path, "2026-11-13")
    assert done == {"status": "FINISHED", "date": "2026-11-13", "changed": 0, "quiet": True}       # 无变化保持安静
    before = sorted((p.name, p.read_bytes()) for p in (tmp_path / "夜间运行" / "2026-11-13").iterdir())
    again = nightly.begin(tmp_path, now=T("2026-11-13T09:30:00"), trigger="manual")
    assert again["status"] == "ALREADY_FINISHED" and again["wrote"] is False
    assert before == sorted((p.name, p.read_bytes()) for p in (tmp_path / "夜间运行" / "2026-11-13").iterdir())
    with pytest.raises(nightly.NightlyError, match="已结束"):
        nightly.checkpoint(tmp_path, "2026-11-13", stage="sync", status="started")


def test_an_interrupted_run_resumes_per_case_and_an_interrupted_write_must_be_reconciled_not_replayed(tmp_path):
    nightly.begin(tmp_path, now=T("2026-11-13T02:00:00"))
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
    nightly.checkpoint(tmp_path, "2026-11-13", stage="acquire", status="reconciled", case="C-2", detail="回读：包未生成")
    nightly.checkpoint(tmp_path, "2026-11-13", stage="acquire", status="started", case="C-2")     # 对账后才可重新开始
    nightly.checkpoint(tmp_path, "2026-11-13", stage="acquire", status="done", case="C-2", changed=1)
    nightly.checkpoint(tmp_path, "2026-11-13", stage="organize", status="done", case="C-1")
    _all_done(tmp_path, "2026-11-13", skip=("discover",))
    assert nightly.finish(tmp_path, "2026-11-13") == {"status": "FINISHED", "date": "2026-11-13", "changed": 2, "quiet": False}


def test_quota_pauses_the_run_and_the_next_trigger_continues_and_lists_the_missed_night(tmp_path):
    nightly.begin(tmp_path, now=T("2026-11-13T02:00:00"))
    _all_done(tmp_path, "2026-11-13")
    nightly.finish(tmp_path, "2026-11-13")
    nightly.begin(tmp_path, now=T("2026-11-15T02:00:00"))                                          # 11-14 的定时错过
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
    task = {"rwid": "R1", "state": "NOT_INSPECTED", "unitName": "甲", "brigade": "梁溪大队"}
    assert nightly.observe(tmp_path, [task, {"rwid": "R2", "state": "NOT_INSPECTED"}], observed_on="2026-03-01", entry="新建任务")["added"] == 2
    assert nightly.due(tmp_path, today="2026-03-07") == []
    assert [i["rwid"] for i in nightly.due(tmp_path, today="2026-03-08")] == ["R1", "R2"]
    for look in ("2026-03-08", "2026-06-01", "2026-10-30"):                                        # 等待再久，间隔仍是 7 天
        nightly.observe(tmp_path, [task], observed_on=look, entry="新建任务")
    assert nightly._queue(tmp_path)["items"]["R1"]["nextDueOn"] == "2026-11-06" and nightly._queue(tmp_path)["items"]["R1"]["looks"] == 4
    assert [i["rwid"] for i in nightly.due(tmp_path, today="2026-11-12")] == ["R2", "R1"]           # 错过的到期事项下次全部处理
    moved = nightly.observe(tmp_path, [{"rwid": "R1", "state": "SAMPLE_PENDING", "inspectionDate": "2026-11-10"},
                                       {"rwid": "R2", "state": "IN_PROGRESS", "inspectionDate": "2025-12-30"},
                                       {"rwid": "R3", "state": "IN_PROGRESS", "inspectionDate": "2026-11-11"}], observed_on="2026-11-12", entry="检查日期范围")
    assert moved["enterScope"] == ["R1", "R3"] and moved["outOfScope"] == ["R2"]
    assert nightly.due(tmp_path, today="2026-12-31") == []
    assert nightly.queue_summary(tmp_path, today="2026-11-12") == {"waiting": 0, "due": 0, "byState": {}, "enteredScope": 1, "outOfScope": 1}
    with pytest.raises(nightly.NightlyError, match="不是未检查"):
        nightly.observe(tmp_path, [{"rwid": "R9", "state": "NOT_INSPECTED", "inspectionDate": "2026-11-11"}], observed_on="2026-11-12", entry="x")


def test_inspected_task_without_product_rows_keeps_waiting(tmp_path):
    item = {"rwid": "R7", "state": "IN_PROGRESS", "inspectionDate": "2026-11-12", "awaitingProduct": True}
    first = nightly.observe(tmp_path, [item], observed_on="2026-11-12", entry="日常监督任务")
    assert first["added"] == 1 and first["enterScope"] == []
    assert [i["rwid"] for i in nightly.due(tmp_path, today="2026-11-19")] == ["R7"]
    assert nightly.observe(tmp_path, [item], observed_on="2026-11-19", entry="日常监督任务")["kept"] == 1
    done = nightly.observe(tmp_path, [{"rwid": "R7", "state": "IN_PROGRESS", "inspectionDate": "2026-11-12"}], observed_on="2026-11-26", entry="日常监督任务")
    assert done["enterScope"] == ["R7"] and nightly.due(tmp_path, today="2026-12-30") == []


def test_weekly_full_range_check_is_recorded_blocks_finish_when_skipped_and_is_made_up_after_a_miss_or_failure(tmp_path):
    first = nightly.begin(tmp_path, now=T("2026-11-13T02:00:00"))
    assert first["weeklyCoverage"] == {"lastCompleteOn": None, "dueOn": "2026-11-13", "due": True, "attemptedToday": False,
                                       "todayOutcome": None, "todayReasons": [], "everyDays": 7}            # 从未核对过即到期
    for stage in nightly.STAGES:
        nightly.checkpoint(tmp_path, "2026-11-13", stage=stage, status="done")
    blocked = nightly.finish(tmp_path, "2026-11-13")
    assert blocked["status"] == "NOT_FINISHED" and blocked["coverageDue"]["due"] is True                       # 到期没做不能收尾
    done = nightly.coverage(tmp_path, _full("2026-11-13", ref="weekly.json", sha256="ab"), day="2026-11-13")
    assert done["outcome"] == "COMPLETE" and done["rowsTotal"] == 15 and done["weeklyCoverage"]["dueOn"] == "2026-11-20"
    assert nightly.finish(tmp_path, "2026-11-13") == {"status": "FINISHED", "date": "2026-11-13", "changed": 0, "quiet": True}
    for day in ("2026-11-14", "2026-11-19"):                                                                   # 未到期的夜晚不要求
        nightly.begin(tmp_path, now=T(day + "T02:00:00"))
        _all_done(tmp_path, day)
        assert nightly.finish(tmp_path, day)["quiet"] is True
    assert len(nightly.coverage_checks(tmp_path)) == 1
    late = nightly.begin(tmp_path, now=T("2026-11-23T02:00:00"))                                              # 11-20 到期那晚没跑
    assert late["weeklyCoverage"]["due"] is True and late["weeklyCoverage"]["dueOn"] == "2026-11-20"
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
