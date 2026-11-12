"""Nightly run record: repeat trigger, resume, interrupted write, quota pause, missed night, observation queue."""

from __future__ import annotations

from datetime import datetime

import pytest

import scripts.nightly as nightly

T = lambda text: datetime.fromisoformat(text).replace(tzinfo=nightly.SHANGHAI)  # noqa: E731


def _all_done(root, day, skip=()):
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
