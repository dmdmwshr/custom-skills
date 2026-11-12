"""Offline tests for whitespace-tolerant case lookup (in-memory cloud)."""

from __future__ import annotations

import json

import pytest

import scripts.name_search as names
from scripts.test_feishu_plans import BASE, CASE, KEY, UNIT, Cloud, _field, _sha

ROWS = [
    {"项目编号": "32002205C202600008", "单位名称": "无锡 蓝盾物业管理有限公司"},
    {"项目编号": "32002207C202600007", "单位名称": "锡山区鼎力台球室（个体工商户 ）"},
    {"项目编号": "32002208C202600108", "单位名称": "无锡蓝盾物业管理有限公司"},
    {"项目编号": "32002208C202600109", "单位名称": "无锡　蓝盾物业管理有限公司第二分公司"},
]


def test_key_drops_spaces_and_width_but_the_stored_name_is_returned_untouched():
    assert names.search_key(" 无锡　蓝盾 （A） ") == "无锡蓝盾(a)" and names.search_key(None) == ""
    assert names.same_name("锡山区鼎力台球室(个体工商户)", "锡山区鼎力台球室（个体工商户 ）")
    assert not names.same_name("", "") and not names.same_name("甲公司", "甲公司分公司")
    found = names.find(ROWS, "无锡蓝盾物业管理有限公司")
    assert [(m["项目编号"][-3:], m["match"]) for m in found["matches"]] == [
        ("108", "名称逐字相同"), ("008", "忽略空格和全半角后相同"), ("109", "忽略空格和全半角后包含")]
    assert found["matches"][1]["单位名称"] == "无锡 蓝盾物业管理有限公司"                       # 原文带空格，原样给出


def test_names_that_collide_after_normalizing_are_listed_side_by_side_never_merged():
    found = names.find(ROWS, "无锡 蓝盾物业管理有限公司")
    assert found["count"] == 3 and found["matches"][0]["项目编号"] == "32002205C202600008"
    assert found["sameKeyDifferentText"] == [["无锡 蓝盾物业管理有限公司", "无锡蓝盾物业管理有限公司"]]
    assert {m["项目编号"] for m in found["matches"][:2]} == {"32002205C202600008", "32002208C202600108"}   # 两案都在，各自编号


def test_project_number_and_partial_names_also_locate_and_an_empty_query_is_refused():
    assert names.find(ROWS, "32002207c202600007")["matches"][0]["match"] == "项目编号相同"
    assert [m["项目编号"][-3:] for m in names.find(ROWS, "鼎力台球室(个体工商户)")["matches"]] == ["007"]
    assert names.find(ROWS, "不存在的单位")["count"] == 0
    with pytest.raises(ValueError):
        names.find(ROWS, " 　 ")


def test_find_cases_reads_the_case_table_once_and_writes_nothing(tmp_path):
    contract = tmp_path / "contract.json"
    contract.write_text(json.dumps({"baseId": BASE, "tables": {"案卷主表": {"tableId": CASE, "fields": [
        _field(KEY, "项目编号", "text", "source-sync-whitelist"), _field(UNIT, "单位名称", "text", "source-sync-whitelist")]}}}, ensure_ascii=False), encoding="utf-8")
    cloud = Cloud({(CASE, f"recA{i:011d}"): {KEY: row["项目编号"], UNIT: row["单位名称"]} for i, row in enumerate(ROWS)})
    report = names.find_cases(contract_path=contract, contract_sha=_sha(contract), target={"baseToken": BASE}, transport=cloud, query="蓝盾物业")
    assert report["rows"] == 4 and report["count"] == 3 and not cloud.writes
    assert report["matches"][0]["recordId"] == "recA00000000000" and report["matches"][0]["单位名称"] == "无锡 蓝盾物业管理有限公司"
