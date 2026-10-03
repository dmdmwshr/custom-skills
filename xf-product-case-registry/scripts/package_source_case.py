"""Resume one known case's native ZIP delivery without replaying package clicks."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from contextlib import suppress
from pathlib import Path
from urllib.parse import urlsplit

if __package__:
    from . import annual_browser as browser_api
    from . import capture_source_case as capture
    from . import source_intake as source
    from . import workspace_state as ws
else:
    import annual_browser as browser_api
    import capture_source_case as capture
    import source_intake as source
    import workspace_state as ws


def _read_request(layout, path: Path, context: dict) -> dict:
    request = ws._read_progress_json(path, layout.root, "单案下载请求")
    if any(request.get(key) != context[key] for key in ("batchId", "rwid", "projectNo")):
        raise source.SourceIntakeError("原下载请求不属于当前案卷，拒绝跨案复用")
    baseline = Path(request["baselinePath"])
    checkpoint = Path(request["checkpointPath"])
    ws._validated_workspace_path(baseline, layout.root, "原下载基线", must_exist=True)
    ws._validated_workspace_path(checkpoint, layout.root, "原打包动作", must_exist=False)
    source._validate_bound_download_baseline(
        layout, context["batchId"], context["rwid"], context["projectNo"], baseline,
    )
    return request


def _await_request(layout, config, context: dict, request: dict, timeout: int) -> dict:
    result = source.await_download(
        layout, context["batchId"], context["rwid"],
        download_baseline=request["baselinePath"], download_dir=config.download_dir,
        allowed_download_dir=config.download_dir, attach=True, timeout_seconds=timeout,
    )
    return {key: value for key, value in result.items() if key != "capture"}


def _select_leaves(browser, context: dict) -> list[dict]:
    rwid = context["rwid"]
    browser.read_full_detail(rwid, context["projectNo"], context["unitName"])
    module = Path(__file__).with_name("playwright_cli_download.cjs").read_text(encoding="utf-8")
    reader = module.split("// PACKAGE_SELECTION_READER_START\n", 1)[1].split(
        "// PACKAGE_SELECTION_READER_END", 1
    )[0]
    browser.run(r'''async page => {
      const start=page.locator('button:visible').filter({hasText:/^开始打包$/});
      if(await start.count()===0)await page.locator('button:visible')
        .filter({hasText:/^打\s*包$/}).press('Enter');
      await start.waitFor({state:'visible',timeout:10000});
      const clear=page.locator('button:visible').filter({hasText:/^取消所选数据$/});
      if(await clear.count()!==1)throw new Error('PACKAGE_CLEAR_CONTROL_NOT_UNIQUE');
      await clear.press('Enter');return {panelReady:true};}''')

    def observe(expected_ids=None):
        request = {"rwid": rwid, "expectedLeafIds": expected_ids}
        selection = browser.run(
            "async page => {return await page.evaluate(" + reader + ","
            + json.dumps(request) + ");}"
        )
        if not selection.get("ready"):
            error = source.SourceIntakeError(
                "本案完整选择状态尚未核实：" + selection.get("reason", "SELECTION_MISMATCH")
            )
            error.observation = selection
            raise error
        return selection

    before = observe()
    if before["selectedLeafIds"]:
        raise source.SourceIntakeError("取消所选数据后仍有文书被选中，停止打包")
    ids = [leaf["id"] for leaf in before["leaves"]]
    # DOM check events update the website normally. Do not call 全选 or write Vue state.
    browser.run(
        "async page => {const ids=" + json.dumps(ids) + ";for(const id of ids){"
        "if(await page.evaluate(()=>document.visibilityState)==='hidden')"
        "throw new Error('SOURCE_DOCUMENT_HIDDEN');"
        "const label=page.locator('label.el-checkbox:visible').filter({has:"
        "page.locator('input[type=\"checkbox\"][value=\"'+id+'\"]')});"
        "if(await label.count()!==1)throw new Error('CURRENT_LEAF_CHECKBOX_NOT_UNIQUE');"
        "const box=label.locator('input[type=\"checkbox\"][value=\"'+id+'\"]');"
        "if(!(await box.isChecked()))await label.evaluate(e=>e.click());"
        "if(!(await box.isChecked()))throw new Error('CURRENT_LEAF_NOT_SELECTED');"
        "}return {selected:true};}"
    )
    after = observe(ids)
    if not after["allCurrentSelected"] or set(after["selectedLeafIds"]) != set(ids):
        raise source.SourceIntakeError("打包面板完整选中集合与本案 RWID 叶子不一致，停止打包")
    return after["leaves"]


def _node_executable() -> Path:
    runtime_path = Path(os.environ["LOCALAPPDATA"]) / "CodexBrowser/playwright/runtime.json"
    runtime = json.loads(runtime_path.read_text(encoding="utf-8-sig"))
    node = Path(runtime["node_executable"])
    if not node.is_absolute() or not node.is_file():
        raise source.SourceIntakeError("官方浏览器运行配置的 Node 入口无法回读")
    return node


def _start_request(request_path: Path) -> None:
    request = json.loads(request_path.read_text(encoding="utf-8"))
    result = subprocess.run(
        [str(_node_executable()), str(Path(__file__).with_name("playwright_cli_download.cjs")),
         str(request_path)],
        cwd=request.get("browserCwd"),
        capture_output=True, text=True, encoding="utf-8", timeout=65,
    )
    if result.returncode and not Path(request["checkpointPath"]).exists():
        raise source.SourceIntakeError("打包动作未建立断点；保留原请求，不重复新建基线")


def _request_outcome(layout, config, context, directory, request, timeout, *, started):
    checkpoint = Path(request["checkpointPath"])
    if checkpoint.exists():
        action = ws._read_progress_json(checkpoint, layout.root, "原打包动作")
        if action.get("submitted") is False:
            if action.get("state") == "SOURCE_DOCUMENT_HIDDEN":
                return {**capture.save_visibility_wait(layout, directory, context, "PACKAGE"),
                        "submitted": False, "packageClickAttempts": 0}
            raise source.SourceIntakeError(
                "原打包动作明确未发出：" + str(action.get("state")) + "；保留原请求，不重复点击"
            )
    return {**_await_request(layout, config, context, request, timeout),
            "packageClickAttempts": int(started)}


def package_known_case(
    layout, config, browser, batch_id: str, rwid: str, project: str, *,
    session: str, browser_name: str = "edge", attempt: int = 1, timeout: int = 60,
    start_request=_start_request,
) -> dict:
    if attempt not in {1, 2} or type(timeout) is not int or not 1 <= timeout <= 60:
        raise source.SourceIntakeError("单案最多两次打包，单次接收观察最多 60 秒")
    context = capture.case_context(layout, batch_id, rwid, project)
    if source._verified_completed_waterline(layout, project):
        return {"status": "ALREADY_COMPLETED", "projectNo": project, "packageClickAttempts": 0}
    existing = capture.verified_package(layout, project)
    if existing:
        return {**existing, "packageClickAttempts": 0}
    record, state = context["record"], context["state"]
    annual = state.get("filters", {}).get("selectionMode") == "ANNUAL_CASE_BASELINE"
    if annual:
        if not source._material_collection_active(layout, state, rwid, record):
            raise source.SourceIntakeError("年度索引须先显式提升并采集完整详情，才能打包")
        detail = capture.verified_detail(layout, record)
    else:
        detail = record.get("detail")
    if not detail:
        raise source.SourceIntakeError("本案尚无完整详情证据，停止打包")
    context["unitName"] = source._find_first(detail["fields"], source._UNIT_NAME_KEYS)
    pending = capture.pending_download_request(layout, project)
    if pending and pending.get("batchId") != batch_id:
        return {**pending, "packageClickAttempts": 0}
    directory = capture.material_directory(layout, batch_id, project)
    suffix = "" if attempt == 1 else "-2"
    request_path = directory / f"download-request{suffix}.json"
    if pending and attempt == 1:
        request_path = Path(pending["requestPath"])
    if request_path.exists():
        request = _read_request(layout, request_path, context)
        checkpoint = Path(request["checkpointPath"])
        if checkpoint.exists():
            action = ws._read_progress_json(checkpoint, layout.root, "原打包动作")
            if action.get("submitted") is not False or action.get(
                "state"
            ) != "SOURCE_DOCUMENT_HIDDEN":
                return _request_outcome(layout, config, context, directory, request, timeout,
                                        started=False)
            # A hidden-page refusal proves no click happened. Only observe before
            # resuming the same request; the adapter creates an exclusive resume intent.
            try:
                browser.read_full_detail(rwid, project, context["unitName"])
            except source.SourceIntakeError as error:
                if "SOURCE_DOCUMENT_HIDDEN" not in str(error):
                    raise
                return _request_outcome(layout, config, context, directory, request, timeout,
                                        started=False)
        # No checkpoint means the adapter could not issue a click. Reuse its
        # original request and baseline; the adapter still rechecks live identity.
        with suppress(subprocess.TimeoutExpired):
            start_request(request_path)
        return _request_outcome(layout, config, context, directory, request, timeout, started=True)
    if attempt == 2:
        first = directory / "download-request.json"
        request = _read_request(layout, first, context)
        checkpoint = Path(request["checkpointPath"])
        if not checkpoint.exists():
            raise source.SourceIntakeError("第一次动作未确认；续看原请求，不新建第二次打包")
        action = ws._read_progress_json(checkpoint, layout.root, "第一次打包动作")
        if action.get("submitted") is not True:
            raise source.SourceIntakeError("第一次动作结果未知或被拒绝；续看原断点，不再次点击")
        before = _await_request(layout, config, context, request, min(timeout, 3))
        if before["status"] != "WAITING" or before.get("zipCandidates") or before.get(
            "partialCandidates"
        ):
            return {**before, "packageClickAttempts": 0}
    try:
        leaves = _select_leaves(browser, context)
    except source.SourceIntakeError as error:
        if "SOURCE_DOCUMENT_HIDDEN" in str(error):
            return {**capture.save_visibility_wait(layout, directory, context, "PACKAGE"),
                    "packageClickAttempts": 0}
        if getattr(error, "observation", None):
            return {**capture.save_observation_wait(layout, directory, context, "PACKAGE",
                                                   error.observation), "packageClickAttempts": 0}
        raise
    leaf_path = directory / f"leaf-manifest{suffix}.json"
    leaf_value = {"projectNo": project, "rwid": rwid, "leaves": leaves}
    if leaf_path.exists():
        if ws._read_progress_json(leaf_path, layout.root, "打包叶子断点") != leaf_value:
            raise source.SourceIntakeError("原打包叶子发生变化；保留证据，不覆盖")
    else:
        source._write_json_exclusive(leaf_path, leaf_value)
    baseline = source.record_download_baseline(layout, batch_id, rwid, config.download_dir)
    origin = urlsplit(state["sourceOrigin"])
    request = {
        "browser": browser_name, "session": session,
        "browserCwd": str(getattr(browser, "cwd", Path.cwd())),
        "origin": f"{origin.scheme}://{origin.netloc}",
        "batchId": batch_id, "rwid": rwid, "projectNo": project,
        "unitName": context["unitName"], "expectedLeafCount": len(leaves),
        "expectedLeafIds": [leaf["id"] for leaf in leaves],
        "baselinePath": str(layout.root / baseline["relativePath"]),
        "checkpointPath": str(directory / f"packaging-action{suffix}.json"),
    }
    source._write_json_exclusive(request_path, request)
    with suppress(subprocess.TimeoutExpired):
        start_request(request_path)
    return _request_outcome(layout, config, context, directory, request, timeout, started=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="从原案卷详情和下载请求继续接收来源 ZIP")
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--rwid", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument("--browser", choices=["edge", "chrome"], default="edge")
    parser.add_argument("--browser-cwd", type=Path, default=Path.cwd())
    parser.add_argument("--workspace-root", type=Path)
    parser.add_argument("--attempt", type=int, choices=[1, 2], default=1)
    parser.add_argument("--timeout-seconds", type=int, default=60)
    args = parser.parse_args()
    config, layout = ws.resolve_workspace(work_root=args.workspace_root, create_layout=False)
    browser = browser_api.Session(args.browser_cwd, args.session, args.browser)
    result = package_known_case(
        layout, config, browser, args.batch_id, args.rwid, args.project,
        session=args.session, browser_name=args.browser, attempt=args.attempt,
        timeout=args.timeout_seconds,
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
