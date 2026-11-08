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


# 可见 DOM 列表读取器：只读已渲染的可见行与分页文字；列表不显示 RWID/项目号，rwid 恒为 null，身份必须在详情页闭合。
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
  const pager=pagers[0];
  const count=pager.querySelector('.el-pagination__total')?.innerText.match(/共\s*(\d+)\s*条/);
  const size=pager.querySelector('input[readonly]')?.value.match(/(\d+)条\/页/);
  const pageNumber=Number(pager.querySelector('.el-pagination__editor input')?.value);
  if(!count||!size||!pageNumber) return {ready:false,reason:'PAGER_NOT_READY'};
  const totalCount=Number(count[1]),pageSize=Number(size[1]);
  const clean=v=>String(v??'').replace(/\s+/g,' ').trim();
  const main=table[0].querySelector('.elx-table--main-wrapper');
  const headers=[...(main?.querySelector('thead tr')?.cells??[])].map(e=>clean(e.innerText));
  const nameIndex=headers.indexOf('单位名称');
  const rows=[...(main?.querySelectorAll('.body--wrapper tbody tr')??[])];
  const expected=Math.max(0,Math.min(pageSize,totalCount-(pageNumber-1)*pageSize));
  const items=rows.map((r,index)=>({rwid:null,
    unitName:clean(r.cells[nameIndex]?.innerText),
    columns:Object.fromEntries(headers.map((h,i)=>[h,clean(r.cells[i]?.innerText)]).filter(([h])=>h)),
    sourcePage:pageNumber,sourceRow:index+1,
    sourceOrder:(pageNumber-1)*pageSize+index+1}));
  if(nameIndex<0||items.length!==expected||items.some(r=>!r.unitName))
    return {ready:false,reason:'VISIBLE_ROWS_MISMATCH'};
  const date=form.querySelector(':scope > .el-col > div > .el-date-editor');
  const dates=[...(date?.querySelectorAll('input')??[])].map(e=>e.value);
  const otherDates=[...form.querySelectorAll('.el-date-editor')].filter(e=>e!==date)
    .flatMap(e=>[...e.querySelectorAll('input')].map(i=>i.value));
  const inputs=[...form.querySelectorAll('input')].filter(visible)
    .filter(e=>!e.closest('.el-date-editor'))
    .map(e=>({placeholder:e.placeholder,value:e.value,readonly:e.readOnly}));
  const queryCategory=new URLSearchParams(location.hash.split('?')[1]??'').get('name');
  const dateLabel=clean(date?.closest('.el-form-item')?.querySelector('.el-form-item__label')?.innerText)||null;
  return {ready:true,visibleOnly:true,url:location.origin+location.pathname+location.hash.split('?')[0]+
    '?name='+encodeURIComponent(queryCategory??''),pageNumber,pageSize,totalCount,
    totalPages:Math.max(1,Math.ceil(totalCount/pageSize)),
    queryCategory,
    items,dates,otherDates,inputs,dateFieldLabel:dateLabel,committedDates:dates,
    lawEnforcementEmpty:form.innerText.includes('请选择执法单位')};
}"""

# 详情身份读取器直接复用 browser_source_capture.mjs 的可见 DOM 详情读取(项目号/单位/大队/受理日期/可见文书目录)。
IDENTITY_READER = r"""() => {
  const visible=e=>!!e?.offsetWidth&&!!e?.offsetHeight;
  const [route,query]=location.hash.split('?');
  const rwid=new URLSearchParams(query??'').get('RWID');
  if(route!=='#/xfjd/projectDetail'||!rwid||document.visibilityState==='hidden')return {ready:false};
  const projects=[...document.querySelectorAll('span')].filter(e=>visible(e)&&
    /^项目编号[：:]\s*\d{8}[A-Z]\d{9}$/.test(e.textContent.trim()));
  const main=[...document.querySelectorAll('main.el-main')].find(visible);
  if(projects.length!==1||!main)return {ready:false};
  const fields={projectNo:projects[0].textContent.match(/\d{8}[A-Z]\d{9}/)[0]};
  for(const label of ['单位名称','检查情况','受理日期','消防管辖']){
    const nodes=[...main.querySelectorAll('span')].filter(e=>e.textContent.trim()===label);
    if(nodes.length!==1)return {ready:false};
    fields[label]=nodes[0].parentElement.nextElementSibling?.innerText.trim()??'';
  }
  return {ready:!!fields.单位名称,rwid,fields,
    sourceUrl:location.origin+location.pathname+route+'?RWID='+encodeURIComponent(rwid)};
}"""


# 纯只读：表单/筛选/下拉当前状态，用于每个状态动作后的独立新观察。
FORM_READER = r"""() => {
  if(document.visibilityState==='hidden')return {ready:false,reason:'SOURCE_DOCUMENT_HIDDEN'};
  const visible=e=>!!e?.offsetWidth&&!!e?.offsetHeight;
  const clean=v=>String(v??'').replace(/\s+/g,' ').trim();
  const form=[...document.querySelectorAll('.avue-view form')].find(visible);
  if(!form)return {ready:false,reason:'FORM_NOT_READY'};
  const date=form.querySelector(':scope > .el-col > div > .el-date-editor');
  const dates=[...(date?.querySelectorAll('input')??[])].map(e=>e.value);
  const inputs=[...form.querySelectorAll('input')].filter(visible).filter(e=>!e.closest('.el-date-editor'))
    .map(e=>({placeholder:e.placeholder,value:e.value,readonly:e.readOnly}));
  const pager=[...document.querySelectorAll('.el-pagination')].find(visible);
  return {ready:true,dates,inputs,
    dropdownOptions:[...document.querySelectorAll('.el-select-dropdown__item')].filter(visible).map(e=>clean(e.innerText)),
    shortcutButtons:[...document.querySelectorAll('.el-picker-panel__shortcut,.el-picker-panel button')].filter(visible).map(e=>clean(e.innerText)),
    queryCategory:new URLSearchParams(location.hash.split('?')[1]??'').get('name'),
    activeMenu:[...document.querySelectorAll('li.el-menu-item.is-active')].map(e=>clean(e.textContent)),
    pageSizeText:pager?.querySelector('input[readonly]')?.value??null,
    pageInput:pager?.querySelector('.el-pagination__editor input')?.value??null};
}"""


class Session:
    def __init__(self, cwd: Path, session: str, browser: str = "edge"):
        self.cwd, self.session, self.browser = cwd, session, browser
        self.cli = Path.home() / ".codex/skills/playwright-browser-control/scripts/browser_cli.py"
        self.list_reader = LIST_READER
        self.identity_reader = IDENTITY_READER

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
        readiness = "const value=(" + self.list_reader + ")();return value.ready"
        if expected_page is not None:
            readiness += f"&&value.pageNumber==={expected_page}"
        value = self.run(
            "async page => {const read=" + self.list_reader + ";"
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

    def observe_form(self) -> dict:
        """纯只读观察；每个状态动作之后调用，用页面可见状态核对该动作的效果。"""
        value = self.run("async page => {return await page.evaluate(" + FORM_READER + ");}")
        if not value.get("ready"):
            raise source.SourceIntakeError("表单尚未就绪：" + str(value.get("reason")))
        return value

    def _act(self, code: str) -> dict:
        """一个脚本只含一个页面状态动作；调用方随后必须做新的独立观察。"""
        return self.run(code)

    def configure(self, category: str, *, year: int | None = None) -> None:
        if category not in coverage.TASK_CATEGORIES:
            raise source.SourceIntakeError("未知的来源任务类别")
        if year is not None and year != coverage.execution_year():
            raise source.SourceIntakeError("历史年度续跑须保留原日期；本年快捷项不能替代旧批次")
        target_year = year or coverage.execution_year()
        self._act(  # 动作：点击菜单
            "async page => {await page.getByRole('menubar').getByText("
            + json.dumps(category)
            + ",{exact:true}).evaluate(e=>e.click());"
            "return {navigated:true};}"
        )
        self.run(  # 观察：类别已提交
            "async page => {await page.waitForFunction(category=>"
            "new URLSearchParams(location.hash.split('?')[1]??'').get('name')===category"
            "&&[...document.querySelectorAll('li.el-menu-item.is-active')]"
            ".some(e=>e.textContent.trim()===category),"
            + json.dumps(category)
            + ",{timeout:45000,polling:300});return {categoryVerified:true};}"
        )
        self.run(  # 观察：表单已渲染
            "async page => {await page.waitForFunction(()=>[...document.querySelectorAll("
            "'.avue-view form .el-form-item__label')].some(e=>e.textContent.includes('单位名称'))"
            ",null,{timeout:45000,polling:300});return {formReady:true};}"
        )
        self._act(  # 动作：清空
            "async page => {await page.locator('.avue-view:visible button:visible')"
            ".filter({hasText:/^清空$/}).press('Enter');return {cleared:true};}"
        )
        self.observe_form()
        self._act(  # 动作：聚焦日期输入以显示快捷项
            "async page => {await page.locator('.avue-view:visible form > .el-col > div > "
            ".el-date-editor input').first().focus();return {dateFocused:true};}"
        )
        self.run(  # 观察：快捷项“本年”可见
            "async page => {await page.getByRole('button',{name:'本年',exact:true})"
            ".waitFor({state:'visible',timeout:10000});return {yearShortcutVisible:true};}"
        )
        self._act(  # 动作：选择“本年”
            "async page => {await page.getByRole('button',{name:'本年',exact:true})"
            ".press('Enter');return {yearSelected:true};}"
        )
        form = self.observe_form()
        if form["dates"] != [f"{target_year}-01-01", f"{target_year}-12-31"]:
            raise source.SourceIntakeError("本年日期未提交（动作后新观察不一致）")
        self._act(  # 动作：聚焦管辖范围
            "async page => {await page.getByPlaceholder('请选择管辖范围',{exact:true}).focus();"
            "return {jurisdictionFocused:true};}"
        )
        self.observe_form()
        self._act(  # 动作：展开下拉
            "async page => {await page.keyboard.press('Enter');return {jurisdictionOpened:true};}"
        )
        if "全部管辖单位(含派出所)" not in self.observe_form()["dropdownOptions"]:
            raise source.SourceIntakeError("管辖范围下拉选项未出现，停止配置")
        self._act(  # 动作：选择全部管辖
            "async page => {await page.getByText('全部管辖单位(含派出所)',{exact:true})"
            ".evaluate(e=>e.click());return {jurisdictionSelected:true};}"
        )
        form = self.observe_form()
        if not any(i["placeholder"] == "请选择管辖范围" and i["value"] == "全部管辖单位(含派出所)"
                   for i in form["inputs"]):
            raise source.SourceIntakeError("管辖范围未提交（动作后新观察不一致）")
        self._act(  # 动作：搜索
            "async page => {await page.locator('.avue-view:visible button:visible')"
            ".filter({hasText:/^搜索$/}).press('Enter');return {searched:true};}"
        )
        self.read_list()
        if self.observe_form()["pageSizeText"] != "50条/页":
            self._act(  # 动作：聚焦页大小
                "async page => {await page.locator('.el-pagination:visible input[readonly]').focus();"
                "return {pageSizeFocused:true};}"
            )
            self.observe_form()
            self._act(  # 动作：展开页大小下拉
                "async page => {await page.keyboard.press('Enter');return {pageSizeOpened:true};}"
            )
            if "50条/页" not in self.observe_form()["dropdownOptions"]:
                raise source.SourceIntakeError("页大小下拉选项未出现，停止配置")
            self._act(  # 动作：选择 50 条/页
                "async page => {await page.getByText('50条/页',{exact:true})"
                ".evaluate(e=>e.click());return {pageSizeSelected:true};}"
            )
            self.read_list()
        check_filters(self.read_list(), target_year, category)

    def page_to(self, target: int, current: dict | None = None) -> dict:
        current = current or self.read_list()
        if type(target) is not int or not 1 <= target <= current["totalPages"]:
            raise source.SourceIntakeError("目标页码超出当前来源实际范围")
        if current["pageNumber"] == target:
            return current
        visible_number = self.run(
            "async page => {const number=page.locator('.el-pagination:visible .el-pager')"
            f".getByText('{target}',{{exact:true}});return {{count:await number.count()}};}}"
        )["count"]
        if visible_number == 1:
            self._act(  # 动作：点击可见页码
                "async page => {const number=page.locator('.el-pagination:visible .el-pager')"
                f".getByText('{target}',{{exact:true}});await number.evaluate(e=>e.click());"
                "return {submitted:true};}"
            )
        else:
            self._act(  # 动作：在页码输入框填入目标页
                "async page => {const input=page.locator('.el-pagination:visible "
                f".el-pagination__editor input');await input.fill('{target}');return {{filled:true}};}}"
            )
            if self.observe_form()["pageInput"] != str(target):
                raise source.SourceIntakeError("页码输入框未接受目标页（动作后新观察不一致）")
            self._act(  # 动作：回车提交
                "async page => {await page.locator('.el-pagination:visible "
                ".el-pagination__editor input').press('Enter');return {submitted:true};}"
            )
        return self.read_list(target)

    @staticmethod
    def full_detail_reader() -> str:
        module = Path(__file__).with_name("browser_source_capture.mjs").read_text(encoding="utf-8")
        return "function currentDetailObservation() {" + module.split(
            "function currentDetailObservation() {", 1
        )[1].split("\nexport async function readSourceDetail", 1)[0]

    def read_full_detail(self, rwid: str | None, project: str | None, unit_name: str, org: str | None = None) -> dict:
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
            (rwid and value["rwid"] != rwid) or (project and value["fields"]["项目编号"] != project)
            or source._normalized_case_name(value["fields"]["单位名称"])
            != source._normalized_case_name(unit_name)
        ):
            raise source.SourceIntakeError("CASE_IDENTITY_CHAIN_MISMATCH：当前详情身份与目标不一致")
        if org is not None and (value["fields"].get("消防管辖") or "").strip() != org.strip():
            raise source.SourceIntakeError("CASE_IDENTITY_CHAIN_MISMATCH：当前详情大队与目标不一致")
        # 发现阶段不给 RWID/项目号/大队时，身份不完整，只允许只读，不得做材料动作。
        value["identityComplete"] = bool(rwid and project and org)
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

    def open_visible_case(self, unit_name: str, expected_page: int | None = None) -> dict:
        """打开当前可见列表中唯一匹配的候选：先做新观察(按规范化单位名精确比对，0 个或多个即失败)，再只发一次点击。
        不读取内部数据模型；RWID 只有进入详情后由 URL 取得。"""
        if not str(unit_name or "").strip():
            raise source.SourceIntakeError("缺少单位名称，不能按可见行打开详情")
        current = self.read_list(expected_page)
        want = source._normalized_case_name(unit_name)
        exact = [r for r in current["items"] if source._normalized_case_name(r["unitName"]) == want]
        if len(exact) != 1:
            raise source.SourceIntakeError(
                f"VISIBLE_CANDIDATE_NOT_UNIQUE：当前页精确匹配 {len(exact)} 行，歧义或缺证，停止打开"
            )
        return self._act(
            "async page => {const name=" + json.dumps(unit_name, ensure_ascii=False) + ";"
            "const rows=page.locator('.elx-table:visible .elx-table--main-wrapper .body--wrapper tbody tr')"
            ".filter({hasText:name});if(await rows.count()!==1)throw new Error('VISIBLE_TARGET_NOT_UNIQUE');"
            "const link=rows.locator('span.url');if(await link.count()!==1)throw new Error('VISIBLE_LINK_NOT_UNIQUE');"
            "await link.evaluate(e=>e.click());return {submitted:true};}"
        )

    def locate_known_case(
        self, rwid: str, unit_name: str, source_order: int, *, year: int,
        category: str, baseline_count: int, max_pages: int = 5, discriminators: dict | None = None,
    ) -> dict:
        """正常入口：按可见候选(单位名称及可选可见列)定位。列表不显示 RWID，故 RWID 只在进入详情后由 URL 闭合(pending)。
        在已访问页内出现 0 个→找不到，多个→歧义；位置只是访问顺序提示，不作身份。"""
        if type(source_order) is not int or source_order < 1 or not 1 <= max_pages <= 10:
            raise source.SourceIntakeError("已知单案定位缺少有效位置或分页预算")
        if not str(unit_name or "").strip():
            raise source.SourceIntakeError("缺少单位名称，无法唯一定位可见候选")
        want = source._normalized_case_name(unit_name)
        current = self.read_list()
        check_filters(current, year, category)
        size, pages = current["pageSize"], current["totalPages"]
        original = min(pages, (source_order - 1) // size + 1)
        shifted = min(pages, (source_order + max(0, current["totalCount"] - baseline_count) - 1) // size + 1)
        hints = [current["pageNumber"], original, shifted]
        for distance in range(1, max_pages + 1):
            hints.extend([shifted - distance, shifted + distance])
        visited, found = [], []
        for page_no in dict.fromkeys(p for p in hints if 1 <= p <= pages):
            if len(visited) >= max_pages:
                break
            current = self.page_to(page_no, current)
            check_filters(current, year, category)
            visited.append(page_no)
            exact = [row for row in current["items"] if row.get("rwid") and row["rwid"] == rwid]
            if exact:  # 旧调用方传入的列表行自带 RWID 时沿原语义：唯一且单位一致即止，不继续翻页
                if len(exact) > 1:
                    raise source.SourceIntakeError("当前列表目标 RWID 不唯一，保留单案断点")
                if source._normalized_case_name(exact[0]["unitName"]) != want:
                    raise source.SourceIntakeError("当前列表 RWID 的单位与总账身份不一致")
                self._located = {"unitName": exact[0]["unitName"], "page": page_no,
                                 "sourceOrder": exact[0].get("sourceOrder"), "expectedRwid": rwid}
                return {"row": exact[0], "list": current, "visitedPages": visited,
                        "baselineCount": baseline_count, "observedCount": current["totalCount"]}
            for row in current["items"]:
                if row.get("rwid"):
                    continue
                if source._normalized_case_name(row["unitName"]) != want:
                    continue
                if any(row.get("columns", {}).get(k) != v for k, v in (discriminators or {}).items()):
                    continue
                found.append((page_no, row))
        if len(found) > 1:
            raise source.SourceIntakeError(
                f"VISIBLE_CANDIDATE_AMBIGUOUS：已访问 {len(visited)} 页内同名候选 {len(found)} 个，缺可见判别量，停止"
            )
        if not found:
            raise source.SourceIntakeError(
                f"SOURCE_KNOWN_CASE_NOT_FOUND：已核对 {len(visited)} 页；"
                "只刷新相关类别身份，不重扫历史详情"
            )
        page_no, row = found[0]
        if current["pageNumber"] != page_no:
            current = self.page_to(page_no, current)
            check_filters(current, year, category)
        self._located = {"unitName": row["unitName"], "page": page_no, "sourceOrder": row["sourceOrder"],
                         "expectedRwid": rwid}
        return {"row": row, "list": current, "visitedPages": visited, "baselineCount": baseline_count,
                "observedCount": current["totalCount"], "rwidPending": True,
                "uniquenessScope": f"visitedPages={visited}"}

    def open_case(self, rwid: str) -> dict:
        """只发出一次详情动作。正常入口打开 locate_known_case 唯一定位的可见候选；RWID 随后由详情路由闭合。"""
        located = getattr(self, "_located", None)
        if not located or (located.get("expectedRwid") not in (None, rwid)):
            raise source.SourceIntakeError("没有与该 RWID 绑定的已定位可见候选；先 locate_known_case，不猜行")
        return self.open_visible_case(located["unitName"], located["page"])

    def open_identity(self, rwid: str | None, screenshot: Path | None = None, *, unit_name: str | None = None,
                      page: int | None = None) -> dict:
        """正常入口：打开可见候选 → 详情双次读取(项目号/RWID/单位/大队)一致 → 截图 → 返回列表。每步独立脚本。"""
        self.open_visible_case(unit_name or "", page)
        self.run(  # 观察：详情路由与身份可读
            "async page => {await page.waitForFunction(()=>{const v=(" + self.identity_reader + ")();"
            "return v.ready;},null,{timeout:45000,polling:200});return {identityReady:true};}"
        )
        first = self.run("async page => {return await page.evaluate(" + self.identity_reader + ");}")
        second = self.run("async page => {return await page.evaluate(" + self.identity_reader + ");}")
        if not first.get("ready") or json.dumps(first, sort_keys=True) != json.dumps(second, sort_keys=True):
            raise source.SourceIntakeError("IDENTITY_NOT_STABLE")
        if rwid and second["rwid"] != rwid:
            raise source.SourceIntakeError("CASE_IDENTITY_CHAIN_MISMATCH：详情路由 RWID 与目标不一致")
        if source._normalized_case_name(second["fields"].get("单位名称")) != source._normalized_case_name(unit_name):
            raise source.SourceIntakeError("CASE_IDENTITY_CHAIN_MISMATCH：详情单位与列表候选不一致")
        if not (second["fields"].get("消防管辖") or "").strip():
            raise source.SourceIntakeError("CASE_IDENTITY_CHAIN_MISMATCH：详情缺消防管辖(大队)")
        if screenshot:
            self.run("async page => {await page.screenshot({path:" + json.dumps(str(screenshot)) + "});return {captured:true};}")
        third = self.run("async page => {return await page.evaluate(" + self.identity_reader + ");}")
        if json.dumps(second, sort_keys=True) != json.dumps(third, sort_keys=True):
            raise source.SourceIntakeError("IDENTITY_CHANGED_DURING_SCREENSHOT")
        self._act("async page => {await page.getByRole('button',{name:'返回',exact:true}).press('Enter');return {returned:true};}")
        return {"identity": second, "list": self.read_list()}

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


def _candidates_path(layout, batch_id: str) -> Path:
    return layout.capture_batches / batch_id / "visible-candidates.json"


def _load_candidates(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _store_candidates(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def collect_list(
    layout: ws.BusinessLayout,
    browser: Session,
    batch_id: str,
    baseline_id: str,
    category: str,
    *,
    max_pages: int = 2,
) -> dict:
    """正常年度入口(可见 DOM)：逐页保存可见候选行(单位/日期/大队等可见列，rwid 恒为 null)、分页总数和观察时间。
    列表不显示 RWID/项目号，故这里只形成“候选”；RWID 在 collect_identities 打开详情后才闭合(pending)。
    调用前由调用方选好实际来源查询。旧内部模型流程只有显式开启环境变量才走原实现。"""
    if not 1 <= max_pages <= 100:
        raise source.SourceIntakeError("单次分页预算必须为 1 至 100")
    observation = browser.read_list()
    year = coverage.execution_year()
    check_filters(observation, year, category)
    path = _candidates_path(layout, batch_id)
    state = _load_candidates(path)
    if not state:
        state = {"schema": "VisibleCandidatesV1", "batchId": batch_id, "baselineId": baseline_id, "year": year,
                 "category": category, "pages": {}, "rwidPolicy": "PENDING_UNTIL_DETAIL_ROUTE",
                 "totalCount": observation["totalCount"], "totalPages": observation["totalPages"],
                 "pageSize": observation["pageSize"]}
    elif state["category"] != category or state["year"] != year:
        raise source.SourceIntakeError("当前来源页面与原批次类别不一致")
    elif state["totalCount"] != observation["totalCount"]:
        state["listDrift"] = {"observedAt": ws._utc_now(), "observedTotal": observation["totalCount"]}
        _store_candidates(path, state)
        raise source.SourceIntakeError("SOURCE_LIST_CHANGED：来源总数变化，保留断点，先刷新本类别清单")
    saved = 0
    for page_no in range(1, observation["totalPages"] + 1):
        if str(page_no) in state["pages"]:
            continue
        if saved >= max_pages:
            break
        if observation["pageNumber"] != page_no:
            observation = browser.page_to(page_no, observation)
        check_filters(observation, year, category)
        state["pages"][str(page_no)] = {
            "observedAt": ws._utc_now(), "items": observation["items"],
            "visibleOnly": True, "rwid": None,
        }
        _store_candidates(path, state)
        saved += 1
        print(json.dumps({"savedPage": page_no, "sourceTotal": observation["totalCount"]}), flush=True)
    state["complete"] = len(state["pages"]) == state["totalPages"]
    _store_candidates(path, state)
    return {"batchId": batch_id, "category": category, "savedPages": saved, "candidateFile": path.name,
            "sourceListCount": state["totalCount"], "complete": state["complete"], "rwidStatus": "pending"}


def collect_identities(layout, browser, batch_id: str, *, limit: int = 10) -> dict:
    """可见候选 → 唯一单位名称 → 一次打开 → 详情路由 RWID 与 项目号/单位/大队 收敛。同页同名(歧义)、缺大队、
    身份不稳定即阻断并写入 blocked，不猜行。仅当 RWID 已在原批次采集状态内时才写入 source.add_detail。"""
    if not 1 <= limit <= 100:
        raise source.SourceIntakeError("单次身份读取预算必须为 1 至 100")
    path = _candidates_path(layout, batch_id)
    state = _load_candidates(path)
    if not state or not state.get("pages"):
        raise source.SourceIntakeError("先用 collect_list 形成可见候选")
    year, category = state["year"], state["category"]
    resolved = state.setdefault("resolved", {})
    blocked = state.setdefault("blocked", {})
    completed = 0
    observation = browser.read_list()
    for page_key in sorted(state["pages"], key=int):
        for item in state["pages"][page_key]["items"]:
            key = f"{page_key}:{item['sourceOrder']}"
            if key in resolved or key in blocked:
                continue
            if completed >= limit:
                break
            check_filters(observation, year, category)
            if observation["totalCount"] != state["totalCount"]:
                state["listDrift"] = {"observedAt": ws._utc_now(), "observedTotal": observation["totalCount"]}
                _store_candidates(path, state)
                raise source.SourceIntakeError("SOURCE_LIST_CHANGED：仅刷新本类别清单后续跑身份")
            observation = browser.page_to(int(page_key), observation)
            same = [r for r in observation["items"]
                    if source._normalized_case_name(r["unitName"]) == source._normalized_case_name(item["unitName"])]
            if len(same) != 1:
                blocked[key] = {"reason": "VISIBLE_CANDIDATE_NOT_UNIQUE", "matches": len(same), "unitName": item["unitName"]}
                _store_candidates(path, state)
                continue
            screenshot = path.parent / "identity-screenshots" / f"p{page_key}-{item['sourceOrder']}.png"
            screenshot.parent.mkdir(parents=True, exist_ok=True)
            try:
                received = browser.open_identity(
                    None, screenshot if not screenshot.exists() else None,
                    unit_name=item["unitName"], page=int(page_key))
            except source.SourceIntakeError as exc:
                blocked[key] = {"reason": str(exc)[:200], "unitName": item["unitName"]}
                _store_candidates(path, state)
                raise
            identity, observation = received["identity"], received["list"]
            resolved[key] = {"rwid": identity["rwid"], "unitName": item["unitName"],
                             "org": identity["fields"].get("消防管辖"),
                             "projectNo": identity["fields"].get("projectNo"), "sourceUrl": identity["sourceUrl"]}
            batch_state = layout.capture_batches / batch_id / "browser-capture.json"
            if batch_state.exists():
                _, cap = source._load_capture(layout, batch_id)
                if identity["rwid"] in cap.get("records", {}):
                    source.add_detail(layout, batch_id, identity["rwid"], identity["fields"],
                                      source_url=identity["sourceUrl"], screenshot=screenshot)
                    resolved[key]["boundToCapture"] = True
            _store_candidates(path, state)
            completed += 1
    pending = sum(len(v["items"]) for v in state["pages"].values()) - len(resolved) - len(blocked)
    return {"identitiesSaved": completed, "pending": pending, "blocked": len(blocked)}


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
