"""Bounded annual indexing through the existing official browser CLI session.

Only the named task-owned tab is operated. This module never reads credentials,
storage, network APIs or PDFs. Each saved page feeds the existing capture core.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

if __package__:
    from . import source_coverage as coverage
    from . import source_intake as source
    from . import workspace_state as ws
else:
    import source_coverage as coverage
    import source_intake as source
    import workspace_state as ws


LIST_READER = r"""() => {
  if(document.visibilityState==='hidden')return {ready:false,reason:'SOURCE_DOCUMENT_HIDDEN'};
  const visible=e=>!!e?.offsetWidth&&!!e?.offsetHeight;
  const form=[...document.querySelectorAll('.avue-view form')].find(visible);
  const table=[...document.querySelectorAll('.elx-table')].filter(visible);
  const pagers=[...document.querySelectorAll('.el-pagination')].filter(visible);
  if(!form||table.length!==1||pagers.length!==1) return {ready:false,reason:'CONTAINER_NOT_READY'};
  const activeProgress=e=>visible(e)&&getComputedStyle(e).visibility!=='hidden'&&
    Number(getComputedStyle(e).opacity)>0;
  if([...document.querySelectorAll('#nprogress,.el-loading-mask')].some(activeProgress))
    return {ready:false,reason:'BUSY'};
  const pager=pagers[0], model=table[0].__vue__?.tableFullData;
  if(!Array.isArray(model)) return {ready:false,reason:'TABLE_MODEL_UNAVAILABLE'};
  const count=pager.querySelector('.el-pagination__total')?.innerText.match(/共\s*(\d+)\s*条/);
  const size=pager.querySelector('input[readonly]')?.value.match(/(\d+)条\/页/);
  const pageNumber=Number(pager.querySelector('.el-pagination__editor input')?.value);
  if(!count||!size||!pageNumber) return {ready:false,reason:'PAGER_NOT_READY'};
  const totalCount=Number(count[1]),pageSize=Number(size[1]);
  if(model.some((r,i)=>Number(r.ROW_ID)!==(pageNumber-1)*pageSize+i+1))
    return {ready:false,reason:'PAGER_CHANGED_BUT_ROWS_OLD',pageNumber,pageSize,totalCount,
      observedFirstRowId:Number(model[0]?.ROW_ID??0),
      observedLastRowId:Number(model.at(-1)?.ROW_ID??0),visibilityState:document.visibilityState};
  const items=model.map((r,index)=>({rwid:String(r.ID??'').trim(),
    unitName:String(r.DWMC??'').trim(),
    sourceOrganization:String(r['$ZGDWID']??'').trim(),
    taskStatus:String(r.RWZT??''),sourcePage:pageNumber,sourceRow:index+1,
    sourceOrder:(pageNumber-1)*pageSize+index+1}));
  const main=table[0].querySelector('.elx-table--main-wrapper');
  const headers=[...(main?.querySelector('thead tr')?.cells??[])].map(e=>e.innerText.trim());
  const nameIndex=headers.indexOf('单位名称');
  const rendered=[...(main?.querySelectorAll('.body--wrapper tbody tr')??[])]
    .map(r=>({name:r.cells[nameIndex]?.innerText.trim(),xid:r.getAttribute('data-rowid')}));
  const expected=Math.max(0,Math.min(pageSize,totalCount-(pageNumber-1)*pageSize));
  const displayed=s=>String(s??'').replace(/\s+/g,' ').trim();
  if(nameIndex<0||items.length!==expected||items.some(r=>!r.rwid||!r.unitName)||
     (totalCount>0&&(!rendered.length||rendered.some(row=>!row.xid||
       !model.some(r=>String(r._XID)===row.xid&&displayed(r.DWMC)===displayed(row.name))))))
    return {ready:false,reason:'VISIBLE_ROWS_MISMATCH'};
  const date=form.querySelector(':scope > .el-col > div > .el-date-editor');
  const dates=[...(date?.querySelectorAll('input')??[])].map(e=>e.value);
  const otherDates=[...form.querySelectorAll('.el-date-editor')].filter(e=>e!==date)
    .flatMap(e=>[...e.querySelectorAll('input')].map(i=>i.value));
  const inputs=[...form.querySelectorAll('input')].filter(visible)
    .filter(e=>!e.closest('.el-date-editor'))
    .map(e=>({placeholder:e.placeholder,value:e.value,readonly:e.readOnly}));
  const queryCategory=new URLSearchParams(location.hash.split('?')[1]??'').get('name');
  return {ready:true,url:location.origin+location.pathname+location.hash.split('?')[0]+
    '?name='+encodeURIComponent(queryCategory??''),pageNumber,pageSize,totalCount,
    totalPages:Math.max(1,Math.ceil(totalCount/pageSize)),
    queryCategory,
    items,dates,otherDates,inputs,dateFieldLabel:date?.__vue__?.$parent?.$parent?.content,
    committedDates:date?.__vue__?.value,lawEnforcementEmpty:form.innerText.includes('请选择执法单位')};
}"""


IDENTITY_READER = r"""() => {
  if(document.visibilityState==='hidden')return {ready:false,reason:'SOURCE_DOCUMENT_HIDDEN'};
  const visible=e=>!!e?.offsetWidth&&!!e?.offsetHeight;
  const [route,query]=location.hash.split('?');
  const rwid=new URLSearchParams(query??'').get('RWID');
  if(route!=='#/xfjd/projectDetail'||!rwid)return {ready:false};
  const activeProgress=e=>visible(e)&&getComputedStyle(e).visibility!=='hidden'&&
    Number(getComputedStyle(e).opacity)>0;
  if([...document.querySelectorAll('#nprogress,.el-loading-mask')].some(activeProgress))
    return {ready:false};
  const projects=[...document.querySelectorAll('span')].filter(e=>visible(e)&&
    /^项目编号[：:]\s*\d{8}[A-Z]\d{9}$/.test(e.textContent.trim()));
  const main=[...document.querySelectorAll('main.el-main')].find(visible);
  if(projects.length!==1||!main)return {ready:false};
  const fields={projectNo:projects[0].textContent.match(/\d{8}[A-Z]\d{9}/)[0]};
  for(const label of ['单位名称','检查情况','受理日期']){
    const nodes=[...main.querySelectorAll('span')].filter(e=>e.textContent.trim()===label);
    if(nodes.length!==1)return {ready:false};
    fields[label]=nodes[0].parentElement.nextElementSibling?.innerText.trim()??'';
  }
  const tables=[...document.querySelectorAll('.el-table')].filter(visible);
  const directory=tables.find(t=>t.querySelector('.el-table__header-wrapper')
    ?.innerText.includes('审批日期'));
  const roots=directory?.__vue__?.store?.states?.data;
  if(!Array.isArray(roots))return {ready:false};
  const clean=n=>({title:String(n.TITLE??n.WSLBMC??n.RWMC??'').trim(),
    createdAt:String(n.CRT_TIME??'').trim(),currentProject:n.IS_CURRENT??null,
    children:(n.children??[]).map(clean)});
  fields.文书目录=roots.filter(n=>n.IS_CURRENT===true).map(clean);
  return {ready:!!fields.单位名称,rwid,fields,
    sourceUrl:location.origin+location.pathname+route+'?RWID='+encodeURIComponent(rwid)};
}"""


class Session:
    def __init__(self, cwd: Path, session: str, browser: str = "edge"):
        self.cwd, self.session, self.browser = cwd, session, browser
        self.cli = Path.home() / ".codex/skills/playwright-browser-control/scripts/browser_cli.py"

    def run(self, code: str) -> dict:
        code = (
            "async page => {if(!page.url().startsWith('http://180.101.229.94:11888/')"
            "||page.url().includes('#/login'))throw new Error('SOURCE_SESSION_CHANGED');"
            "if(await page.evaluate(()=>document.visibilityState)==='hidden')"
            "throw new Error('SOURCE_DOCUMENT_HIDDEN');"
            "return await (" + code + ")(page);}"
        )
        result = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                str(self.cli),
                "--browser",
                self.browser,
                "--session",
                self.session,
                "run-code",
                code,
            ],
            cwd=self.cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=65,
        )
        marker = "### Result\n"
        if result.returncode or marker not in result.stdout or "### Error" in result.stdout:
            # Do not echo the browser body or unrelated console messages.
            error = result.stdout.split("### Error\n")[-1].split("\n")[0][:180]
            raise source.SourceIntakeError(f"浏览器步骤未确认：{error}")
        return json.JSONDecoder().raw_decode(result.stdout.split(marker, 1)[1])[0]

    def read_list(self, expected_page: int | None = None) -> dict:
        readiness = "const value=(" + LIST_READER + ")();return value.ready"
        if expected_page is not None:
            readiness += f"&&value.pageNumber==={expected_page}"
        value = self.run(
            "async page => {const read=" + LIST_READER + ";"
            "try{await page.waitForFunction(()=>{" + readiness
            + "||value.reason==='SOURCE_DOCUMENT_HIDDEN';},null,{timeout:45000,polling:300});}"
            "catch(error){const observed=await page.evaluate(read);"
            "return {waiting:true,observation:{...observed,ready:false,"
            "reason:observed.reason??'PAGE_NOT_COMMITTED'}};}"
            "const first=await page.evaluate(read); const second=await page.evaluate(read);"
            "if(first.reason==='SOURCE_DOCUMENT_HIDDEN'||second.reason==='SOURCE_DOCUMENT_HIDDEN')"
            "return {waiting:true,observation:second};"
            "if(!first.ready||JSON.stringify(first)!==JSON.stringify(second))"
            "throw new Error('LIST_NOT_STABLE');"
            + (
                f"if(second.pageNumber!=={expected_page})throw new Error('PAGE_NOT_COMMITTED');"
                if expected_page
                else ""
            )
            + "return second;}"
        )
        if value.get("waiting"):
            error = source.SourceIntakeError(
                "列表尚未提交：" + value["observation"].get("reason", "LIST_NOT_STABLE")
            )
            error.observation = value["observation"]
            raise error
        return value

    def configure(self, category: str, *, year: int | None = None) -> None:
        if category not in coverage.TASK_CATEGORIES:
            raise source.SourceIntakeError("未知的来源任务类别")
        if year is not None and year != coverage.execution_year():
            raise source.SourceIntakeError("历史年度续跑须保留原日期；本年快捷项不能替代旧批次")
        self.run(
            "async page => {await page.getByRole('menubar').getByText("
            + json.dumps(category)
            + ",{exact:true}).evaluate(e=>e.click());"
            "return {navigated:true};}"
        )
        self.run(
            "async page => {await page.waitForFunction(category=>"
            "new URLSearchParams(location.hash.split('?')[1]??'').get('name')===category"
            "&&[...document.querySelectorAll('li.el-menu-item.is-active')]"
            ".some(e=>e.textContent.trim()===category),"
            + json.dumps(category)
            + ",{timeout:45000,polling:300});return {categoryVerified:true};}"
        )
        self.run(
            "async page => {await page.waitForFunction(()=>[...document.querySelectorAll("
            "'.avue-view form .el-form-item__label')].some(e=>e.textContent.includes('单位名称'))"
            ",null,{timeout:45000,polling:300});return {formReady:true};}"
        )
        self.run(
            "async page => {await page.locator('.avue-view:visible button:visible')"
            ".filter({hasText:/^清空$/}).press('Enter');return {cleared:true};}"
        )
        self.run(
            "async page => {await page.locator('.avue-view:visible form > .el-col > div > "
            ".el-date-editor input').first().focus();"
            "await page.getByRole('button',{name:'本年',exact:true}).press('Enter');"
            "return {yearSelected:true};}"
        )
        self.run(
            "async page => {await page.getByPlaceholder('请选择管辖范围',{exact:true}).focus();"
            "await page.keyboard.press('Enter');await page"
            ".getByText('全部管辖单位(含派出所)',{exact:true}).evaluate(e=>e.click());"
            "return {jurisdictionSelected:true};}"
        )
        self.run(
            "async page => {await page.locator('.avue-view:visible button:visible')"
            ".filter({hasText:/^搜索$/}).press('Enter');return {searched:true};}"
        )
        self.read_list()
        self.run(
            "async page => {const input=page.locator('.el-pagination:visible input[readonly]');"
            "if(await input.inputValue()!=='50条/页'){await input.focus();"
            "await page.keyboard.press('Enter');await page.getByText('50条/页',{exact:true})"
            ".evaluate(e=>e.click());}return {pageSizeSelected:true};}"
        )
        check_filters(self.read_list(), year or coverage.execution_year(), category)

    def page_to(self, target: int, current: dict | None = None) -> dict:
        current = current or self.read_list()
        if type(target) is not int or not 1 <= target <= current["totalPages"]:
            raise source.SourceIntakeError("目标页码超出当前来源实际范围")
        if current["pageNumber"] == target:
            return current
        self.run(
            "async page => {const pager=page.locator('.el-pagination:visible');"
            f"const number=pager.locator('.el-pager').getByText('{target}',{{exact:true}});"
            "if(await number.count()===1)await number.evaluate(e=>e.click());"
            "else {const input=pager.locator('.el-pagination__editor input');"
            f"await input.fill('{target}');await input.press('Enter');}}"
            "return {submitted:true};}"
        )
        return self.read_list(target)

    def locate_known_case(
        self, rwid: str, unit_name: str, source_order: int, *, year: int,
        category: str, baseline_count: int, max_pages: int = 5,
    ) -> dict:
        """位置只是提示；原页、按新增量修正的页和近邻有界回读，不从第一页重扫。"""
        if type(source_order) is not int or source_order < 1 or not 1 <= max_pages <= 10:
            raise source.SourceIntakeError("已知单案定位缺少有效位置或分页预算")
        current = self.read_list()
        check_filters(current, year, category)
        size, pages = current["pageSize"], current["totalPages"]
        original = min(pages, (source_order - 1) // size + 1)
        drift = max(0, current["totalCount"] - baseline_count)
        shifted = min(pages, (source_order + drift - 1) // size + 1)
        hints = [current["pageNumber"], original, shifted]
        for distance in range(1, max_pages + 1):
            hints.extend([shifted - distance, shifted + distance])
        visited = []
        for page_no in dict.fromkeys(p for p in hints if 1 <= p <= pages):
            if len(visited) >= max_pages:
                break
            current = self.page_to(page_no, current)
            check_filters(current, year, category)
            visited.append(page_no)
            matches = [row for row in current["items"] if row["rwid"] == rwid]
            if len(matches) > 1:
                raise source.SourceIntakeError("当前列表目标 RWID 不唯一，保留单案断点")
            if matches:
                if source._normalized_case_name(matches[0]["unitName"]) != (
                    source._normalized_case_name(unit_name)
                ):
                    raise source.SourceIntakeError("当前列表 RWID 的单位与总账身份不一致")
                return {"row": matches[0], "list": current, "visitedPages": visited,
                        "baselineCount": baseline_count, "observedCount": current["totalCount"]}
        raise source.SourceIntakeError(
            f"SOURCE_KNOWN_CASE_NOT_FOUND：已核对 {len(visited)} 页；"
            "只刷新相关类别身份，不重扫历史详情"
        )

    def open_case(self, rwid: str) -> dict:
        """只发出一次详情动作；观察、截图和落盘分别继续，不捆绑返回动作。"""
        return self.run(
            "async page => {const target=" + json.dumps(rwid) + ";"
            "const xid=await page.evaluate(target=>{"
            "const tables=[...document.querySelectorAll('.elx-table')].filter(e=>"
            "e.offsetWidth&&e.offsetHeight);if(tables.length!==1)"
            "throw new Error('SOURCE_TABLE_NOT_UNIQUE');const t=tables[0],"
            "data=t.__vue__.tableFullData,matches=data.filter(r=>String(r.ID)===target);"
            "if(matches.length!==1)throw new Error('IDENTITY_TARGET_NOT_UNIQUE');"
            "const row=matches[0],container=t.querySelector("
            "'.elx-table--main-wrapper .elx-table--body-wrapper');"
            "const height=container.querySelector('tbody tr')?.offsetHeight;"
            "if(!height)throw new Error('ROW_HEIGHT_UNAVAILABLE');"
            "container.scrollTop=data.indexOf(row)*height;"
            "container.dispatchEvent(new Event('scroll'));return String(row._XID);},target);"
            "const link=page.locator('.elx-table:visible .elx-table--main-wrapper "
            ".body--wrapper tr[data-rowid=\"'+xid+'\"] span.url');"
            "await link.waitFor({state:'visible',timeout:10000});"
            "await link.evaluate(e=>e.click());return {submitted:true};}"
        )

    @staticmethod
    def full_detail_reader() -> str:
        module = Path(__file__).with_name("browser_source_capture.mjs").read_text(encoding="utf-8")
        return "function currentDetailObservation() {" + module.split(
            "function currentDetailObservation() {", 1
        )[1].split("\nexport async function readSourceDetail", 1)[0]

    def read_full_detail(self, rwid: str, project: str, unit_name: str) -> dict:
        reader = self.full_detail_reader()
        value = self.run(
            "async page => {const read=" + reader + ";"
            "try{await page.waitForFunction(()=>{const observed=(" + reader
            + ")();return observed.ready||observed.reason==='SOURCE_DOCUMENT_HIDDEN';},"
            "null,{timeout:45000,polling:300});}catch(error){"
            "const observed=await page.evaluate(read);"
            "return {waiting:true,observation:{...observed,ready:false,"
            "reason:observed.reason??'SOURCE_DETAIL_NOT_READY'}};}"
            "const first=await page.evaluate(read),second=await page.evaluate(read);"
            "if(first.reason==='SOURCE_DOCUMENT_HIDDEN'||second.reason==='SOURCE_DOCUMENT_HIDDEN')"
            "return {waiting:true,observation:second};"
            "if(JSON.stringify(first)!==JSON.stringify(second))"
            "throw new Error('SOURCE_DETAIL_NOT_STABLE');return second;}"
        )
        if value.get("waiting"):
            error = source.SourceIntakeError(
                "详情尚未提交：" + value["observation"].get("reason", "SOURCE_DETAIL_NOT_READY")
            )
            error.observation = value["observation"]
            raise error
        if (
            value["rwid"] != rwid or value["fields"]["项目编号"] != project
            or source._normalized_case_name(value["fields"]["单位名称"])
            != source._normalized_case_name(unit_name)
        ):
            raise source.SourceIntakeError("CASE_IDENTITY_CHAIN_MISMATCH：当前详情身份与目标不一致")
        return value

    def capture_detail_screenshot(self, screenshot: Path, observation: dict) -> None:
        reader = self.full_detail_reader()
        self.run(
            "async page => {const expected=" + json.dumps(observation, ensure_ascii=False)
            + ",read=" + reader + ";"
            "if(JSON.stringify(await page.evaluate(read))!==JSON.stringify(expected))"
            "throw new Error('SOURCE_DETAIL_CHANGED_BEFORE_SCREENSHOT');"
            "await page.screenshot({path:" + json.dumps(str(screenshot)) + "});"
            "if(JSON.stringify(await page.evaluate(read))!==JSON.stringify(expected))"
            "throw new Error('SOURCE_DETAIL_CHANGED_DURING_SCREENSHOT');return {captured:true};}"
        )

    def open_identity(self, rwid: str, screenshot: Path | None = None) -> dict:
        return self.run(
            "async page => {const target=" + json.dumps(rwid) + ";"
            "const xid=await page.evaluate(target=>{"
            "const t=[...document.querySelectorAll('.elx-table')].find(e=>"
            "e.offsetWidth&&e.offsetHeight);const data=t.__vue__.tableFullData;"
            "const matches=data.filter(r=>String(r.ID)===target);"
            "if(matches.length!==1)throw new Error('IDENTITY_TARGET_NOT_UNIQUE');"
            "const row=matches[0],i=data.indexOf(row);"
            "const container=t.querySelector('.elx-table--main-wrapper .elx-table--body-wrapper');"
            "const height=container.querySelector('tbody tr')?.offsetHeight;"
            "if(!height)throw new Error('ROW_HEIGHT_UNAVAILABLE');"
            "container.scrollTop=i*height;container.dispatchEvent(new Event('scroll'));"
            "return String(row._XID);},target);"
            "const link=page.locator('.elx-table:visible .elx-table--main-wrapper "
            ".body--wrapper tr[data-rowid=\"'+xid+'\"] span.url');"
            "await link.waitFor({state:'visible',timeout:10000});"
            "await link.evaluate(e=>e.click());"
            "await page.waitForFunction(target=>{const v=(" + IDENTITY_READER + ")();"
            "return v.ready&&v.rwid===target;},target,{timeout:45000,polling:200});"
            "const first=await page.evaluate(" + IDENTITY_READER + ");"
            "const second=await page.evaluate(" + IDENTITY_READER + ");"
            "if(JSON.stringify(first)!==JSON.stringify(second))"
            "throw new Error('IDENTITY_NOT_STABLE');"
            + (
                "await page.screenshot({path:" + json.dumps(str(screenshot)) + "});"
                if screenshot
                else ""
            )
            + "if(JSON.stringify(second)!==JSON.stringify(await page.evaluate("
            + IDENTITY_READER
            + ")))throw new Error('IDENTITY_CHANGED_DURING_SCREENSHOT');"
            "await page.getByRole('button',{name:'返回',exact:true}).press('Enter');"
            "await page.waitForFunction(()=> (" + LIST_READER + ")().ready,null,"
            "{timeout:45000,polling:200});const list=await page.evaluate(" + LIST_READER + ");"
            "return {identity:second,list};}"
        )

    def back_to_list(self) -> dict:
        self.run(
            "async page => {await page.getByRole('button',{name:'返回',exact:true})"
            ".press('Enter');return {returned:true};}"
        )
        return self.read_list()


def check_filters(observation: dict, year: int, category: str | None = None) -> None:
    if category and observation.get("queryCategory") != category:
        raise source.SourceIntakeError("菜单类别与实际查询页面不一致，拒绝登记该清单")
    if observation["dates"] != [f"{year}-01-01", f"{year}-12-31"]:
        raise source.SourceIntakeError("本年日期未提交")
    if observation.get("dateFieldLabel") not in {"创建时间", "创建日期"} or any(
        observation["otherDates"]
    ):
        raise source.SourceIntakeError("日期字段未确认或存在额外日期筛选")
    if not observation["lawEnforcementEmpty"]:
        raise source.SourceIntakeError("执法单位筛选未清空")
    inputs = observation["inputs"]
    if not any(
        i["placeholder"] == "请选择管辖范围" and i["value"] == "全部管辖单位(含派出所)"
        for i in inputs
    ):
        raise source.SourceIntakeError("没有覆盖全部管辖单位")
    if any(i["value"] for i in inputs if i["placeholder"] not in {"请选择管辖范围", "", "请选择"}):
        raise source.SourceIntakeError("存在额外单位、状态、检查结果或任务来源筛选")


def write_new(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != data:
            raise source.SourceIntakeError("已有证据不同，拒绝覆盖")
        return
    with path.open("x", encoding="utf-8") as stream:
        stream.write(data)


def collect_list(
    layout: ws.BusinessLayout,
    browser: Session,
    batch_id: str,
    baseline_id: str,
    category: str,
    *,
    max_pages: int = 2,
) -> dict:
    """The caller selects/reads the actual source query before invoking this step."""
    observation = browser.read_list()
    year = coverage.execution_year()
    check_filters(observation, year, category)
    if not 1 <= max_pages <= 100:
        raise source.SourceIntakeError("单次分页预算必须为 1 至 100")
    route = observation["url"].split("#", 1)[-1].split("?", 1)[0]
    state_path = layout.capture_batches / batch_id / "browser-capture.json"
    if not state_path.exists():
        query = {k: v for k, v in observation.items() if k != "items"}
        evidence_path = state_path.parent / "annual-query.json"
        write_new(evidence_path, query)
        filters = {
            "selectionMode": coverage.ANNUAL_MODE,
            "year": year,
            "baselineId": baseline_id,
            "taskCategory": category,
            "dateShortcut": "本年",
            "sourceListKind": "CASE_TASK",
            "taskStatus": "ALL",
            "lawEnforcementUnit": "ALL",
            "brigadeScope": "ALL",
            "jurisdiction": "全部管辖单位(含派出所)",
            "dateFieldLabel": observation["dateFieldLabel"],
            "startDate": observation["dates"][0],
            "endDate": observation["dates"][1],
            "queryRoute": "#" + route,
            "queryEvidencePath": evidence_path.relative_to(layout.root).as_posix(),
        }
        state = source.begin_capture(
            layout, filters, batch_id=batch_id, origin=observation["url"].split("#")[0]
        )
    else:
        _, state = source._load_capture(layout, batch_id)
        if (
            state["filters"]["taskCategory"] != category
            or state["filters"]["queryRoute"] != "#" + route
        ):
            raise source.SourceIntakeError("当前来源页面与原批次类别不一致")
    saved = 0
    while saved < max_pages and state.get("stableRounds", 0) < 2:
        round_no = state["currentRound"]
        pages = state.get("rounds", {}).get(str(round_no), {}).get("pages", {})
        missing = [p for p in range(1, observation["totalPages"] + 1) if str(p) not in pages]
        if not missing:
            state = source.finalize_capture(layout, batch_id)
            continue
        page_no = missing[0]
        if observation["pageNumber"] != page_no:
            previous_ids = [item["rwid"] for item in observation["items"]]
            browser.run(
                "async page => {const pager=page.locator('.el-pagination:visible');"
                f"const number=pager.locator('.el-pager').getByText('{page_no}',{{exact:true}});"
                "if(await number.count()===1)await number.evaluate(e=>e.click());"
                f"else if({page_no}==={observation['pageNumber']}+1)"
                "await pager.locator('.btn-next').evaluate(e=>e.click());"
                f"else if({page_no}==={observation['pageNumber']}-1)"
                "await pager.locator('.btn-prev').evaluate(e=>e.click());"
                "else throw new Error('TARGET_PAGE_NOT_VISIBLE');return {submitted:true};}"
            )
            # Wait for both page commitment and stable content; a pager value alone is not proof.
            browser.run(
                "async page => {const read=" + LIST_READER + ";await page.waitForFunction("
                f"() => {{const value=({LIST_READER})();"
                f"return value.ready&&value.pageNumber==={page_no}"
                "&&JSON.stringify(value.items.map(i=>i.rwid))!=="
                + json.dumps(json.dumps(previous_ids, separators=(",", ":")))
                + ";}"
                ",null,{timeout:45000,polling:300});return {ready:true};}"
            )
        observation = browser.read_list(page_no)
        check_filters(observation, year, category)
        screenshot = state_path.parent / f"列表_轮{round_no}_页{page_no:03}.png"
        if not screenshot.exists():
            browser.run(
                "async page => {await page.screenshot({path:"
                + json.dumps(str(screenshot))
                + "});return {captured:true};}"
            )
        after = browser.read_list(page_no)
        if after != observation:
            raise source.SourceIntakeError("截图前后列表变化，保留断点")
        state = source.add_page(
            layout,
            batch_id,
            page_no,
            observation["items"],
            observation["totalCount"],
            observation["totalPages"],
            screenshot=screenshot,
            round_no=round_no,
        )
        saved += 1
        print(
            json.dumps(
                {"savedPage": page_no, "round": round_no, "sourceTotal": observation["totalCount"]}
            ),
            flush=True,
        )
        if len(state["rounds"][str(round_no)]["pages"]) == observation["totalPages"]:
            state = source.finalize_capture(layout, batch_id)
        if state.get("listResult") == "CHANGING":
            break
    return {
        "batchId": batch_id,
        "category": category,
        "savedPages": saved,
        "currentRound": state["currentRound"],
        "stableRounds": state.get("stableRounds"),
        "sourceListCount": state.get("sourceListCount"),
        "status": state["status"],
    }


def collect_identities(layout, browser, batch_id: str, *, limit: int = 10) -> dict:
    if not 1 <= limit <= 100:
        raise source.SourceIntakeError("单次身份读取预算必须为 1 至 100")
    path, state = source._load_capture(layout, batch_id)
    if state["filters"].get("selectionMode") != coverage.ANNUAL_MODE:
        raise source.SourceIntakeError("身份索引只允许年度任务清单")
    if coverage.query_evidence_issue(layout, state) or state.get("stableRounds", 0) < 2:
        raise source.SourceIntakeError("先完成正确类别的两轮年度列表")
    year, category = state["filters"]["year"], state["filters"]["taskCategory"]
    census = coverage.annual_coverage(layout, year, details=True)
    priorities = {i["rwid"]: i["candidatePriority"] for i in census["identities"]}
    bindings = coverage.identity_bindings(
        coverage.formal_captures(layout), ws.load_waterline(layout)
    )
    targets = [r for key, r in state["records"].items() if not bindings.get(key)]
    targets.sort(key=lambda r: (priorities.get(r["rwid"], 3), r.get("sourceOrder", 0)))
    completed = 0
    observation = browser.read_list()
    for target in targets[:limit]:
        check_filters(observation, year, category)
        if observation["totalCount"] != state["sourceListCount"]:
            state["listDrift"] = {
                "observedAt": ws._utc_now(),
                "observedTotal": observation["totalCount"],
            }
            source._write_json(path, state)
            raise source.SourceIntakeError("SOURCE_LIST_CHANGED：仅刷新本类别两轮清单后续跑身份")
        # Position is only a hint; the current RWID must be present before clicking.
        order = target.get("sourceOrder") or target.get("sourceAppearances", [{}])[0].get(
            "sourceOrder"
        )
        if not order:
            raise source.SourceIntakeError("年度来源行缺少位置证据")
        page_no = (order - 1) // observation["pageSize"] + 1
        observation = browser.page_to(page_no, observation)
        live = next((r for r in observation["items"] if r["rwid"] == target["rwid"]), None)
        if live is None:
            raise source.SourceIntakeError("原页未找到目标身份；先刷新年度列表")
        screenshot = path.parent / "identity-screenshots" / (target["rwid"] + ".png")
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        received = browser.open_identity(
            target["rwid"], screenshot if not screenshot.exists() else None
        )
        identity, observation = received["identity"], received["list"]
        if live.get("sourceOrganization"):
            identity["fields"]["来源检查单位"] = live["sourceOrganization"]
        if source._normalized_case_name(identity["fields"]["单位名称"]) != (
            source._normalized_case_name(live["unitName"])
        ):
            raise source.SourceIntakeError("年度详情单位与当前列表不符")
        source.add_detail(
            layout,
            batch_id,
            target["rwid"],
            identity["fields"],
            source_url=identity["sourceUrl"],
            screenshot=screenshot,
        )
        completed += 1
        if completed == 1 or completed % 10 == 0:
            print(json.dumps({"identitiesSaved": completed}), flush=True)
    return {"identitiesSaved": completed, "remainingBeforeRun": len(targets)}


def record_unavailable(layout, browser, baseline_id: str, category: str) -> dict:
    index = coverage.TASK_CATEGORIES.index(category)
    directory = layout.capture_batches / baseline_id
    ws._validated_workspace_path(directory, layout.root, "年度入口证据", must_exist=False)
    saved = directory / f"availability-{index}.json"
    if saved.exists():
        coverage.unavailable_categories(layout, baseline_id, coverage.execution_year())
        return ws._read_progress_json(saved, layout.root, "来源入口可用性")
    directory.mkdir(parents=True, exist_ok=True)
    image = directory / f"availability-{index}.png"
    value = browser.run(
        "async page => {await page.getByRole('menubar').getByText("
        + json.dumps(category)
        + ",{exact:true}).evaluate(e=>e.click());"
        "const message=page.locator('.el-message:visible')"
        ".filter({hasText:'开发测试中，暂时不能使用'});"
        "await message.waitFor({state:'visible',timeout:5000});"
        "const text=await message.innerText();await page.screenshot({path:"
        + json.dumps(str(image))
        + "});return {message:text};}"
    )
    record = {
        "schemaVersion": "SourceCategoryAvailabilityV1",
        "baselineId": baseline_id,
        "sourceYear": coverage.execution_year(),
        "category": category,
        "status": "UNAVAILABLE",
        "message": value["message"],
        "observedAt": ws._utc_now(),
        "screenshot": source._evidence_file(image, layout.root),
    }
    write_new(directory / f"availability-{index}.json", record)
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description="已配置来源年度查询的有界采集；不登录、不下载正文")
    parser.add_argument("--browser-cwd", type=Path, required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--baseline-id", required=True)
    parser.add_argument("--category", choices=coverage.TASK_CATEGORIES, required=True)
    parser.add_argument("--max-pages", type=int, default=2)
    parser.add_argument("--identities", action="store_true")
    parser.add_argument("--record-unavailable", action="store_true")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument(
        "--configure", action="store_true", help="先在当前任务页面选择本年和全部单位"
    )
    args = parser.parse_args()
    _, layout = ws.resolve_workspace(work_root=args.workspace_root, create_layout=False)
    session = Session(args.browser_cwd, args.session)
    if args.record_unavailable:
        print(
            json.dumps(
                record_unavailable(layout, session, args.baseline_id, args.category),
                ensure_ascii=False,
            )
        )
        return
    if args.configure:
        session.configure(args.category)
    result = (
        collect_identities(layout, session, args.batch_id, limit=args.limit)
        if args.identities
        else collect_list(
            layout,
            session,
            args.batch_id,
            args.baseline_id,
            args.category,
            max_pages=args.max_pages,
        )
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
