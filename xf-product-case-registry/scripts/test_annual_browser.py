from copy import deepcopy

import pytest

from scripts.annual_browser import check_filters
from scripts.source_intake import SourceIntakeError


def observation():
    return {
        "dates": ["2026-01-01", "2026-12-31"],
        "dateFieldLabel": "创建时间",
        "otherDates": ["", ""],
        "lawEnforcementEmpty": True,
        "queryCategory": "日常监督任务",
        "inputs": [
            {"placeholder": "请选择管辖范围", "value": "全部管辖单位(含派出所)"},
            {"placeholder": "请选择状态", "value": ""},
            {"placeholder": "请选择任务检查结果", "value": ""},
        ],
    }


def test_source_query_category_cannot_be_relabelled_as_another_menu():
    check_filters(observation(), 2026, "日常监督任务")
    with pytest.raises(SourceIntakeError, match="实际查询页面不一致"):
        check_filters(observation(), 2026, "消防装备抽查任务")


@pytest.mark.parametrize(
    "patch",
    [
        {"dates": ["2026-06-20", "2026-09-20"]},
        {"otherDates": ["2026-01-01", ""]},
        {"lawEnforcementEmpty": False},
        {"dateFieldLabel": None},
    ],
)
def test_hidden_filters_cannot_claim_all_annual_cases(patch):
    with pytest.raises(SourceIntakeError):
        check_filters({**observation(), **patch}, 2026)


def test_closed_only_and_unqualified_only_filters_are_rejected():
    for index, value in [(0, "本单位"), (1, "已结案"), (2, "不合格")]:
        current = deepcopy(observation())
        current["inputs"][index]["value"] = value
        with pytest.raises(SourceIntakeError):
            check_filters(current, 2026)


class RecordingSession:
    """合成会话：记录每个脚本，按顺序返回预置结果。以真实模块方法为被测对象。"""

    def __init__(self, ab, responses):
        self.__class__ = type("RS", (ab.Session,), {"run": self._run})
        self.cwd = self.session = self.browser = None
        self.list_reader, self.identity_reader = ab.LIST_READER, ab.IDENTITY_READER
        self.scripts, self.responses = [], list(responses)

    def _run(self, code):
        self.scripts.append(code)
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def _list(units, page=1, total=None, size=10):
    total = total if total is not None else len(units)
    items = [{"rwid": None, "unitName": u, "columns": {"消防管辖": "一大队"}, "sourcePage": page,
              "sourceRow": i + 1, "sourceOrder": (page - 1) * size + i + 1} for i, u in enumerate(units)]
    return {"ready": True, "visibleOnly": True, "url": "http://x/#/q?name=日常监督任务", "pageNumber": page,
            "pageSize": size, "totalCount": total, "totalPages": max(1, -(-total // size)), "queryCategory": "日常监督任务",
            **{k: v for k, v in observation().items() if k not in ("queryCategory",)}, "items": items}


def _identity(rwid="32100934444", unit="甲公司", org="一大队"):
    return {"ready": True, "rwid": rwid, "sourceUrl": "http://x/#/xfjd/projectDetail?RWID=" + rwid,
            "fields": {"projectNo": "32002205C202600036", "单位名称": unit, "消防管辖": org}}


def test_default_dom_entries_open_only_a_unique_visible_candidate_and_leave_rwid_pending(monkeypatch):
    from scripts import annual_browser as ab

    s = RecordingSession(ab, [_list(["甲公司", "乙公司"]), {"submitted": True}])
    assert s.open_visible_case("甲公司")["submitted"] is True
    assert len(s.scripts) == 2  # 一次新观察 + 恰一次点击
    s = RecordingSession(ab, [_list(["甲公司", "甲公司"])])
    with pytest.raises(SourceIntakeError, match="VISIBLE_CANDIDATE_NOT_UNIQUE"):
        s.open_visible_case("甲公司")
    assert len(s.scripts) == 1  # 歧义时没有任何点击
    s = RecordingSession(ab, [_list(["乙公司"])])
    with pytest.raises(SourceIntakeError, match="VISIBLE_CANDIDATE_NOT_UNIQUE"):
        s.open_visible_case("甲公司")


def test_open_identity_converges_route_rwid_and_requires_unit_and_org(monkeypatch):
    from scripts import annual_browser as ab

    ok = [_list(["甲公司"]), {"submitted": True}, {"identityReady": True}, _identity(), _identity(), {"captured": True},
          _identity(), {"returned": True}, _list(["甲公司"])]
    s = RecordingSession(ab, ok)
    got = s.open_identity(None, __import__("pathlib").Path("x.png"), unit_name="甲公司", page=1)
    assert got["identity"]["rwid"] == "32100934444"
    cases = [(_identity(unit="乙公司"), "单位"), (_identity(org=""), "大队"), (_identity(rwid="1" * 11), "RWID")]
    for ident, why in cases:
        resp = [_list(["甲公司"]), {"submitted": True}, {"identityReady": True}, ident, ident]
        with pytest.raises(SourceIntakeError, match=why):
            RecordingSession(ab, resp).open_identity("32100934444", None, unit_name="甲公司", page=1)


def test_collect_list_and_identities_use_visible_candidates_and_block_ambiguity(monkeypatch, tmp_path):
    from scripts import annual_browser as ab
    from types import SimpleNamespace

    monkeypatch.setattr(ab.coverage, "execution_year", lambda: 2026)
    layout = SimpleNamespace(capture_batches=tmp_path)
    s = RecordingSession(ab, [_list(["甲公司", "乙公司", "乙公司"])])
    out = ab.collect_list(layout, s, "b1", "base", "日常监督任务")
    assert out["rwidStatus"] == "pending" and out["complete"] is True
    saved = __import__("json").loads((tmp_path / "b1" / "visible-candidates.json").read_text(encoding="utf-8"))
    assert all(i["rwid"] is None for i in saved["pages"]["1"]["items"])
    page = _list(["甲公司", "乙公司", "乙公司"])
    flow = [page,  # collect_identities 初始观察
            page, {"submitted": True}, {"identityReady": True}, _identity(), _identity(), {"captured": True},
            _identity(), {"returned": True}, page]  # 甲公司：新观察→一次点击→详情双读→截图→回列表
    s2 = RecordingSession(ab, flow)
    res = ab.collect_identities(layout, s2, "b1", limit=3)
    saved = __import__("json").loads((tmp_path / "b1" / "visible-candidates.json").read_text(encoding="utf-8"))
    assert res["blocked"] == 2 and res["identitiesSaved"] == 1 and res["pending"] == 0
    assert list(saved["resolved"].values())[0]["rwid"] == "32100934444"
    assert {v["reason"] for v in saved["blocked"].values()} == {"VISIBLE_CANDIDATE_NOT_UNIQUE"}
    assert sum("evaluate(e=>e.click())" in c for c in s2.scripts) == 1  # 歧义候选没有任何点击


def test_legacy_env_switch_cannot_restore_internal_state_paths(monkeypatch):
    """环境变量设为 1 也不会恢复内部状态读取器、旧动作链或旧采集入口：活动模块里根本没有这些代码。"""
    from pathlib import Path
    from scripts import annual_browser as ab

    for value in ("1", "true", "yes"):
        monkeypatch.setenv("XF_ALLOW_LEGACY_INTERNAL_STATE_READER", value)
        s = ab.Session(Path("."), "synthetic")
        assert s.list_reader is ab.LIST_READER and s.identity_reader is ab.IDENTITY_READER
    src = Path(ab.__file__).read_text(encoding="utf-8")
    for needle in ("__vue__", "tableFullData", "$parent", "store?.states", "LEGACY_", "legacy_internal_state",
                   "require_legacy_flow", "XF_ALLOW_LEGACY", "_locate_known_case_rwid", "_open_case_legacy",
                   "_open_identity_legacy", "_collect_list_legacy", "_collect_identities_legacy"):
        assert needle not in src, needle
    assert not hasattr(ab, "LEGACY_ENV") and not hasattr(ab, "LEGACY_LIST_READER")
    # 带环境变量时，正常入口仍是可见候选流程：open_case 没有已定位候选就拒绝，不回落到旧内部读取
    with pytest.raises(SourceIntakeError, match="没有与该 RWID 绑定的已定位可见候选"):
        ab.Session(Path("."), "synthetic").open_case("1")


def test_open_visible_case_requires_a_unit_name():
    from scripts import annual_browser as ab

    with pytest.raises(SourceIntakeError):
        ab.Session(__import__("pathlib").Path("."), "synthetic").open_visible_case("  ")


def test_cross_check_leaf_count_comes_only_from_a_proven_complete_directory():
    from scripts.package_source_case import _cross_check_leaf_count as _declared_leaf_count

    tree = [{"currentProject": True, "children": [{"children": []}, {"children": [{"children": []}]}]},
            {"currentProject": False, "children": [{"children": []}]}]
    assert _declared_leaf_count({"fields": {"文书目录": tree, "文书目录完整性": {"complete": True, "coverageProven": True}}}) == 2
    assert _declared_leaf_count({"fields": {"文书目录": tree, "文书目录完整性": {"complete": False}}}) is None
    assert _declared_leaf_count({"fields": {"文书目录": tree}}) is None
    assert _declared_leaf_count({"fields": {"文书目录": [{"currentProject": None, "children": []}], "文书目录完整性": {"complete": True, "coverageProven": True}}}) is None


class PanelBrowser:
    """合成面板：记录脚本；观察按预置序列返回，用于核对“一个脚本一个状态动作”与全选门禁。"""

    def __init__(self, can_select_all, ids=("1", "2")):
        self.log, self.ids, self.can = [], list(ids), can_select_all
        self.selected = []

    def read_full_detail(self, *args):
        self.log.append("detail")
        return {"fields": {}}

    def run(self, code):
        if "function readPackageSelection" in code or "evaluate(" in code and "expectedLeafIds" in code:
            self.log.append("observe")
            leaves = [{"id": i, "own": True} for i in self.ids]
            return {"ready": True, "leaves": leaves, "selectedLeafIds": list(self.selected),
                    "allCurrentSelected": sorted(self.selected) == sorted(self.ids), "canSelectAll": self.can}
        for name, marker in (("open", "panelOpenSubmitted"), ("panel", "panelOpen:true"), ("clear", "panelReady"),
                             ("all", "selectAllSubmitted"), ("leaf", "clickSubmitted")):
            if marker in code:
                self.log.append(name)
                if name == "all":
                    self.selected = list(self.ids)
                elif name == "clear":
                    self.selected = []
                elif name == "leaf":
                    self.selected.append(next(i for i in self.ids if f'"{i}"' in code and i not in self.selected))
                return {}
        raise AssertionError(code[:80])


def test_select_leaves_uses_one_state_action_per_script_and_select_all_only_when_proven():
    from scripts.package_source_case import _select_leaves

    ctx = {"rwid": "32100934444", "projectNo": "32002205C202600036", "unitName": "甲公司", "org": "一大队"}
    full = PanelBrowser(True)
    _select_leaves(full, ctx)
    assert full.log == ["detail", "open", "panel", "clear", "observe", "all", "observe"]
    part = PanelBrowser(False)
    _select_leaves(part, ctx)
    assert "all" not in part.log
    assert part.log == ["detail", "open", "panel", "clear", "observe", "leaf", "observe", "leaf", "observe", "observe"]
