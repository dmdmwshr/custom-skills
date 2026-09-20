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
