import json

import pytest

from scripts import source_intake as source
from scripts.test_workspace_source import (
    FIXED_NOW,
    PROJECT_A,
    _begin_and_stabilize,
    _record,
)
from scripts.test_workspace_source import (
    layout as layout,
)


def capture(layout, tmp_path):
    _begin_and_stabilize(layout, [_record("revisit")])
    image = tmp_path / "first.png"
    image.write_bytes(b"original screenshot")
    detail = {"项目编号": PROJECT_A, "单位名称": "测试单位", "检查结论": "不合格"}
    result = source.add_detail(
        layout,
        "fixture-batch",
        "revisit",
        detail,
        "https://source.example/#/detail?RWID=revisit",
        image,
        captured_at=FIXED_NOW,
    )
    return detail, result["records"]["revisit"]["detail"]


def test_same_detail_new_screenshot_preserves_original_and_deduplicates(layout, tmp_path):
    detail, prior = capture(layout, tmp_path)
    image = tmp_path / "second.png"
    image.write_bytes(b"same business page with a later clock")
    for _ in range(2):
        result = source.add_detail(
            layout,
            "fixture-batch",
            "revisit",
            detail,
            "https://source.example/#/detail?RWID=revisit",
            image,
            captured_at="2099-08-21T11:00:00+08:00",
        )
    current = result["records"]["revisit"]["detail"]
    assert result["conflicts"] == []
    assert current["fingerprint"] == prior["fingerprint"]
    assert current["screenshot"] == prior["screenshot"]
    assert len(current["screenshotObservations"]) == 1
    assert (
        layout.root / current["screenshot"]["relativePath"]
    ).read_bytes() == b"original screenshot"


def test_new_screenshot_cannot_hide_changed_business_detail(layout, tmp_path):
    detail, _ = capture(layout, tmp_path)
    image = tmp_path / "second.png"
    image.write_bytes(b"changed page")
    with pytest.raises(source.SourceIntakeError, match="详情发生变化"):
        source.add_detail(
            layout,
            "fixture-batch",
            "revisit",
            {**detail, "检查结论": "合格"},
            "https://source.example/#/detail?RWID=revisit",
            image,
        )


def test_new_screenshot_cannot_hide_damaged_original(layout, tmp_path):
    detail, prior = capture(layout, tmp_path)
    (layout.root / prior["screenshot"]["relativePath"]).write_bytes(b"changed original")
    image = tmp_path / "second.png"
    image.write_bytes(b"new capture")
    with pytest.raises(source.SourceIntakeError, match="本地证据无法对账"):
        source.add_detail(
            layout,
            "fixture-batch",
            "revisit",
            detail,
            "https://source.example/#/detail?RWID=revisit",
            image,
        )


def test_retry_resolves_only_previous_screenshot_conflict(layout, tmp_path):
    detail, _ = capture(layout, tmp_path)
    path = layout.batch_dir("fixture-batch") / "browser-capture.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    conflict = {"type": "DETAIL_SCREENSHOT_CONFLICT", "rwid": "revisit", "projectNo": PROJECT_A}
    unrelated = {"type": "PROJECT_PACKAGE_CONFLICT", "projectNo": PROJECT_A}
    state["conflicts"] = [conflict, unrelated]
    state["records"]["revisit"]["package"] = {"sha256": "sha256:" + "b" * 64}
    path.write_text(json.dumps(state), encoding="utf-8")
    image = tmp_path / "second.png"
    image.write_bytes(b"new capture")
    result = source.add_detail(
        layout,
        "fixture-batch",
        "revisit",
        detail,
        "https://source.example/#/detail?RWID=revisit",
        image,
    )
    assert result["conflicts"] == [unrelated]
    assert result["resolvedConflicts"][0]["rwid"] == "revisit"
    assert result["records"]["revisit"]["package"] == state["records"]["revisit"]["package"]
