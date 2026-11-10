import { createHash } from "node:crypto";
import { mkdir, readFile, realpath, writeFile } from "node:fs/promises";
import path from "node:path";
import { advanceSourceStage } from "./browser_stage.mjs";
export { advanceSourceStage, saveSourceStageCheckpoint } from "./browser_stage.mjs";

// VISIBLE_GUARDS_START
// 可见 DOM 方法的纯函数护栏(无页面/框架内部状态依赖)。同一段源码被 package_source_case.py 内联进页面读取器，也被本文件导出供 Node 侧校验。
// 身份：预期与实测的 项目号/RWID/单位/大队 必须“四项都有且相等”才允许材料动作；发现阶段信息不全只能只读定位。
const GUARD_NORM = (s) => String(s ?? "").replace(/\s+/g, " ").trim();
function identityGate(e) {
  e = e || {};
  const miss = [];
  const need = [
    ["PROJECT_NO", e.expectedProjectNo, e.detailProjectNo, (x) => String(x ?? "").trim()],
    ["RWID", e.expectedRwid, e.urlRwid, (x) => String(x ?? "").trim()],
    ["UNIT", e.expectedUnit, e.detailUnit, GUARD_NORM],
    ["ORG", e.expectedOrg, e.detailOrg, GUARD_NORM],
  ];
  for (const [name, exp, act, f] of need) {
    if (!f(exp)) miss.push("MISSING_EXPECTED_" + name);
    if (!f(act)) miss.push("MISSING_ACTUAL_" + name);
    if (f(exp) && f(act) && f(exp) !== f(act)) miss.push(name);
  }
  const pnFmt = /^\d{8}[A-Z](\d{4})\d+$/.exec(String(e.detailProjectNo ?? "").trim());
  if (!pnFmt) miss.push("PROJECT_NO_FORMAT");
  if (!/^\d{8,}$/.test(String(e.urlRwid ?? "").trim())) miss.push("RWID_FORMAT");
  const ok = miss.length === 0;
  const hardMismatch = miss.some((m) => ["PROJECT_NO", "RWID", "UNIT", "ORG"].includes(m));
  const readOnlyLocateAllowed = !hardMismatch && !!GUARD_NORM(e.detailUnit) && !!GUARD_NORM(e.expectedUnit);
  return { ok, miss, materialActionAllowed: ok, readOnlyLocateAllowed, projectYear: pnFmt ? Number(pnFmt[1]) : null };
}
// 叶完整性：必须有“实际可见的声明总数与本案叶数一致”或“已证明覆盖”(coverageProven：无滚动溢出、所有节点展开状态已知且全部展开)之一，
// 且无折叠节点、无读不到ID的叶；自己数出来的行数不是声明总数，缺证一律 unknown 阻断。
function leafCompleteness({ ownCount, declaredLeafCount, collapsedCount, unreadableCount, expandStateKnown, coverageProven }) {
  const why = [];
  const hasDeclared = Number.isInteger(declaredLeafCount) && declaredLeafCount >= 0;
  if (hasDeclared && declaredLeafCount !== ownCount) why.push("DECLARED_TOTAL_MISMATCH");
  if (!hasDeclared && coverageProven !== true) why.push("NO_DECLARED_LEAF_TOTAL");
  if (expandStateKnown !== true) why.push("EXPAND_STATE_UNKNOWN");
  if (!(collapsedCount === 0)) why.push(collapsedCount > 0 ? "COLLAPSED_NODES_PRESENT" : "COLLAPSED_COUNT_UNKNOWN");
  if (!(unreadableCount === 0)) why.push(unreadableCount > 0 ? "UNREADABLE_LEAF_IDS" : "UNREADABLE_COUNT_UNKNOWN");
  return { complete: why.length === 0, why };
}
// 选择集合：完整性证明 + 无UNKNOWN + 无外案选中 + 选中集合与本案叶集合精确相等，缺一阻断。
function compareSelection({ leafIds, selectedIds, foreignIds, unknownState, completeLeafSet }) {
  const leaf = new Set(leafIds || []);
  const sel = new Set(selectedIds || []);
  const missing = [...leaf].filter((i) => !sel.has(i));
  const extra = [...sel].filter((i) => !leaf.has(i));
  const foreign = [...new Set(foreignIds || [])];
  const blockReasons = [];
  if (completeLeafSet !== true) blockReasons.push("LEAF_SET_NOT_PROVEN_COMPLETE");
  if (unknownState) blockReasons.push("UNKNOWN_STATE");
  if (leaf.size === 0) blockReasons.push("NO_OWN_LEAVES");
  if (missing.length) blockReasons.push("MISSING_LEAVES");
  if (extra.length) blockReasons.push("EXTRA_SELECTED");
  if (foreign.length) blockReasons.push("FOREIGN_LEAVES_PRESENT");
  return { ok: blockReasons.length === 0, missing, extra, foreign, unknownState: !!unknownState, blockReasons };
}
// 叶归属：根边界(按标题)优先于叶判断；复选框数字值不能把根变成叶；根/叶无法分辨 → UNKNOWN，不继承上一个根。
function assignLeafMembership(rows, expectedProjectNo) {
  const ROOT = /^\[([^\]]{1,4})\][^-]*-(\d{8}[A-Z]\d{4}\d+)-/;
  const LOOSE = /\d{8}[A-Z]\d{4}\d+/;
  const NONE = { kind: null, projectNo: null, level: null };
  let ctx = NONE;
  const own = [], foreign = [], orphan = [], unreadable = [];
  const rootChecked = { foreign: [], unknown: [] };
  const roots = [];
  let rootSeen = false;
  const deeperThanCtx = (r) => Number.isInteger(r.level) && Number.isInteger(ctx.level) && r.level > ctx.level;
  for (const r of rows || []) {
    const title = GUARD_NORM(r.title);
    const m = ROOT.exec(title);
    if (m) {
      const tag = m[1], pn = m[2];
      const hasBox = !!(r.id || r.hasCheckbox);
      if (deeperThanCtx(r)) {
        roots.push({ tag, projectNo: pn, kind: "ambiguous_root_or_leaf" });
        if (hasBox && r.checked) rootChecked.unknown.push(r.id || "unreadable-root-box");
        ctx = NONE; continue;
      }
      if (pn === expectedProjectNo && tag === "产") { ctx = { kind: "own", projectNo: pn, level: r.level ?? null }; rootSeen = true; roots.push({ tag, projectNo: pn, kind: "own" }); }
      else if (pn === expectedProjectNo) { ctx = NONE; roots.push({ tag, projectNo: pn, kind: "unknown_conflict" }); if (hasBox && r.checked) rootChecked.unknown.push(r.id || "unreadable-root-box"); }
      else { ctx = { kind: "foreign", projectNo: pn, level: r.level ?? null }; roots.push({ tag, projectNo: pn, kind: "foreign" }); if (hasBox && r.checked) rootChecked.foreign.push(r.id || "unreadable-root-box"); }
      continue;
    }
    if (LOOSE.test(title)) {
      roots.push({ tag: null, projectNo: null, kind: "ambiguous_root_or_leaf" });
      ctx = NONE;
      if (r.id) orphan.push(r); else if (r.hasCheckbox) unreadable.push(r);
      continue;
    }
    if (!r.id) {
      if (r.hasCheckbox) { unreadable.push(r); continue; }
      if (!deeperThanCtx(r)) { ctx = NONE; roots.push({ tag: null, projectNo: null, kind: "unrecognized_row" }); }
      continue;
    }
    if (ctx.kind === "own") own.push(r);
    else if (ctx.kind === "foreign") foreign.push(r);
    else orphan.push(r);
  }
  return {
    rootSeen, roots,
    ownIds: own.map((r) => r.id), ownSelected: own.filter((r) => r.checked).map((r) => r.id),
    foreignIds: foreign.map((r) => r.id), foreignSelected: foreign.filter((r) => r.checked).map((r) => r.id).concat(rootChecked.foreign),
    orphanCount: orphan.length, orphanSelected: orphan.filter((r) => r.checked).map((r) => r.id).concat(rootChecked.unknown), unreadableCount: unreadable.length,
    unknownState: !rootSeen || orphan.length > 0 || unreadable.length > 0 || roots.some((x) => ["unknown_conflict", "unrecognized_row", "ambiguous_root_or_leaf"].includes(x.kind)),
  };
}
// VISIBLE_GUARDS_END
export { identityGate, leafCompleteness, compareSelection, assignLeafMembership };

/** 材料动作(开面板/选叶/打包/下载/归档)前的身份闸门：预期值必须在打开详情之前由台账或列表登记，缺项或冲突一律阻断。 */
export function assertMaterialIdentity(expected, detail) {
  const gate = identityGate({
    expectedProjectNo: expected?.projectNo, detailProjectNo: detail?.fields?.项目编号,
    expectedRwid: expected?.rwid, urlRwid: detail?.rwid,
    expectedUnit: expected?.caseName, detailUnit: detail?.fields?.单位名称,
    expectedOrg: expected?.org, detailOrg: detail?.fields?.消防管辖,
  });
  if (!gate.ok) throw new Error(`MATERIAL_IDENTITY_BLOCKED:${gate.miss.join(",")}`);
  return gate;
}

/** Read only the current legal-document table. Never read session or request data. */
function currentListObservation() {
  if (document.visibilityState === "hidden") return { ready: false, reason: "SOURCE_DOCUMENT_HIDDEN" };
  const visible = (element) => !!element?.offsetWidth && !!element?.offsetHeight;
  if (/login|signin/i.test(location.pathname + location.hash) ||
      [...document.querySelectorAll('input[type="password"]')].some(visible)) {
    return { ready: false, loginRequired: true, reason: "SOURCE_LOGIN_REQUIRED" };
  }
  const route = location.hash.split("?")[0];
  if (route !== "#/xfjd/cpjd/cxtj/flwscx/flws") {
    return { ready: false, reason: "SOURCE_ROUTE_CHANGED" };
  }
  const tables = [...document.querySelectorAll(".elx-table")].filter(
    (element) => visible(element) && element.querySelector("thead")?.innerText.includes("关联项目"),
  );
  const pagers = [...document.querySelectorAll(".el-pagination")].filter(visible);
  if (tables.length === 0 || pagers.length === 0) {
    return { ready: false, reason: "SOURCE_CONTAINER_NOT_READY" };
  }
  if (tables.length !== 1 || pagers.length !== 1) {
    return { ready: false, reason: "SOURCE_CONTAINER_NOT_UNIQUE" };
  }
  const table = tables[0];
  const pager = pagers[0];
  const totalText = pager.querySelector(".el-pagination__total")?.innerText ?? "";
  const totalMatch = totalText.match(/^共\s*(\d+)\s*条$/);
  const sizeMatch = (pager.querySelector("input[readonly]")?.value ?? "").match(/^(\d+)条\/页$/);
  const pageNumber = Number(pager.querySelector(".el-pagination__editor input")?.value);
  if (!totalMatch || !sizeMatch || !Number.isInteger(pageNumber) || pageNumber < 1) {
    return { ready: false, reason: "SOURCE_PAGINATION_UNAVAILABLE" };
  }
  const totalCount = Number(totalMatch[1]);
  const pageSize = Number(sizeMatch[1]);
  const loading = [...document.querySelectorAll("[class*=loading],#nprogress")].some(
    (element) => visible(element) && getComputedStyle(element).visibility !== "hidden" &&
      Number(getComputedStyle(element).opacity) > 0,
  );
  const main = table.querySelector(".elx-table--main-wrapper");
  const headers = [...(main?.querySelector("thead tr")?.cells ?? [])].map((cell) => cell.innerText.trim());
  const indexes = ["文号或文书名称", "关联项目", "创建日期"].map((label) => headers.indexOf(label));
  if (indexes.some((index) => index < 0)) return { ready: false, reason: "SOURCE_COLUMNS_CHANGED" };
  const visibleRows = [...(main?.querySelectorAll(".body--wrapper tbody tr") ?? [])].map((row) => {
    const cells = [...row.cells].map((cell) => cell.innerText.trim());
    return { documentName: cells[indexes[0]], caseName: cells[indexes[1]], createdAt: cells[indexes[2]] };
  });
  // 只读可见行：列表不显示 RWID/项目编号，因此 rwid 恒为 null，身份必须在打开详情后由 URL 与详情字段闭合。
  const items = visibleRows.map((row, index) => ({
    rwid: null,
    caseName: row.caseName,
    documentName: row.documentName,
    createdAt: row.createdAt,
    sourcePage: pageNumber,
    sourceRow: index + 1,
    sourceOrder: (pageNumber - 1) * pageSize + index + 1,
  }));
  const expectedRows = Math.max(0, Math.min(pageSize, totalCount - (pageNumber - 1) * pageSize));
  const visibleMatched = items.length === expectedRows && items.every((item) => item.caseName && item.documentName && item.createdAt);
  // The other date editor belongs to the approval-date form item. The creation
  // range is the directly rendered column in this observed legacy search form.
  const dateInputs = [...document.querySelectorAll(
    ".avue-view form > .el-col > div > .el-date-editor.el-range-editor input",
  )].filter(visible);
  return {
    ready: !loading && visibleMatched, busy: loading,
    reason: loading ? "SOURCE_LOADING" : visibleMatched ? null : "SOURCE_VISIBLE_ROWS_MISMATCH",
    pageNumber, pageSize, totalCount, totalPages: Math.max(1, Math.ceil(totalCount / pageSize)),
    visibleRows: visibleRows.length, items, visibleOnly: true,
    dateValues: dateInputs.map((input) => input.value),
    viewport: { width: innerWidth, height: innerHeight },
  };
}

export function validateListObservation(value, expectedPage, expectedTotal, previousRowsDigest, expectedPageSize = 50, expectedWindow = null) {
  if (!value?.ready) throw new Error(value?.reason ?? "SOURCE_NOT_READY");
  const { pageNumber, pageSize, totalCount, totalPages, items, dateValues } = value;
  if (pageNumber !== expectedPage || pageSize !== expectedPageSize || totalCount !== expectedTotal ||
      totalPages !== Math.max(1, Math.ceil(totalCount / pageSize)) || pageNumber > totalPages) {
    throw new Error("SOURCE_PAGINATION_NOT_COMMITTED");
  }
  const expectedRows = Math.min(pageSize, totalCount - (pageNumber - 1) * pageSize);
  if (!Array.isArray(items) || items.length !== expectedRows || items.some((item) =>
    !item.caseName || !item.documentName || !item.createdAt || (item.rwid !== undefined && item.rwid !== null && !String(item.rwid).trim()))) {
    throw new Error("SOURCE_ROW_COUNT_OR_IDENTITY_INVALID");
  }
  const year = new Intl.DateTimeFormat("en", { timeZone: "Asia/Shanghai", year: "numeric" }).format(new Date());
  const expectedDates = expectedWindow ? [expectedWindow.startDate, expectedWindow.endDate] : [`${year}-01-01`, `${year}-12-31`];
  if (expectedDates.some(value => !/^\d{4}-\d{2}-\d{2}$/.test(value ?? "")) || expectedDates[0] > expectedDates[1] ||
      dateValues?.length !== 2 || dateValues.some((value, index) => value !== expectedDates[index])) {
    throw new Error("SOURCE_YEAR_RANGE_CHANGED");
  }
  const rowValues = items.map(({ rwid, caseName, documentName, createdAt }) => ({ rwid: rwid ?? null, caseName, documentName, createdAt }));
  const rowsDigest = createHash("sha256").update(JSON.stringify(rowValues)).digest("hex");
  if (previousRowsDigest && rowsDigest === previousRowsDigest) throw new Error("SOURCE_PREVIOUS_PAGE_STILL_VISIBLE");
  return { pageNumber, pageSize, totalCount, totalPages, rows: items.length, rowsDigest };
}

export async function readSourceListPage(cdp) {
  const result = await cdp.send("Runtime.evaluate", {
    expression: `(${currentListObservation.toString()})()`, returnByValue: true,
  });
  if (result.exceptionDetails || !result.result?.value) throw new Error("SOURCE_READ_FAILED");
  return result.result.value;
}

/** A bounded read-only wait. Navigation is always performed separately, once. */
export async function waitSourceListPage(cdp, { expectedPage, expectedTotal, previousRowsDigest, expectedPageSize = 50, expectedWindow = null, timeoutMs = 45000 }) {
  if (!Number.isInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 60000) throw new Error("SOURCE_WAIT_ARGUMENT_INVALID");
  const deadline = Date.now() + timeoutMs;
  let lastReason;
  do {
    const observation = await readSourceListPage(cdp);
    try {
      return validateListObservation(observation, expectedPage, expectedTotal, previousRowsDigest, expectedPageSize, expectedWindow);
    } catch (error) {
      lastReason = error.message;
      const transitional = ["SOURCE_LOADING", "SOURCE_PAGINATION_NOT_COMMITTED", "SOURCE_PREVIOUS_PAGE_STILL_VISIBLE"];
      if (!transitional.includes(lastReason)) throw error;
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  } while (Date.now() < deadline);
  throw new Error(`SOURCE_WAIT_EXPIRED:${lastReason}`);
}

async function writeImmutable(destination, content) {
  try {
    await writeFile(destination, content, { flag: "wx" });
  } catch (error) {
    if (error.code !== "EEXIST") throw error;
    const existing = await readFile(destination);
    if (!existing.equals(Buffer.from(content))) throw new Error("SOURCE_EVIDENCE_ALREADY_DIFFERENT");
  }
}

/** Capture two agreeing observations and one screenshot; returns paths, never image bytes. */
export async function captureSourceListPage({ tab, cdp, evidenceDir, round, expectedPage, expectedTotal, previousRowsDigest, expectedPageSize = 50, expectedWindow = null }) {
  if (![1, 2, 3].includes(round) || !path.isAbsolute(evidenceDir)) throw new Error("SOURCE_CAPTURE_ARGUMENT_INVALID");
  const first = await readSourceListPage(cdp);
  const firstReceipt = validateListObservation(first, expectedPage, expectedTotal, previousRowsDigest, expectedPageSize, expectedWindow);
  const second = await readSourceListPage(cdp);
  const receipt = validateListObservation(second, expectedPage, expectedTotal, previousRowsDigest, expectedPageSize, expectedWindow);
  if (firstReceipt.rowsDigest !== receipt.rowsDigest) throw new Error("SOURCE_PAGE_CHANGED_DURING_CAPTURE");
  await mkdir(evidenceDir, { recursive: true });
  const resolvedDir = await realpath(evidenceDir);
  if (path.resolve(resolvedDir).toLowerCase() !== path.resolve(evidenceDir).toLowerCase()) throw new Error("SOURCE_EVIDENCE_REPARSE_POINT");
  const stem = `分页_轮${round}_页${String(expectedPage).padStart(3, "0")}`;
  const pagePath = path.join(evidenceDir, `${stem}.json`);
  let screenshotPath;
  const page = { pageNumber: second.pageNumber, totalCount: second.totalCount, totalPages: second.totalPages, items: second.items };
  // Existing page evidence is immutable; reuse its screenshot on a safe retry.
  let existingPage = null;
  try { existingPage = JSON.parse(await readFile(pagePath, "utf8")); } catch (error) { if (error.code !== "ENOENT") throw error; }
  const screenshotName = (sha) => `列表_轮${round}_页${String(expectedPage).padStart(3, "0")}_${sha.slice(-12)}.png`;
  if (existingPage === null) {
    const screenshot = await tab.screenshot({ fullPage: false });
    const after = await readSourceListPage(cdp);
    const afterReceipt = validateListObservation(after, expectedPage, expectedTotal, previousRowsDigest, expectedPageSize, expectedWindow);
    if (afterReceipt.rowsDigest !== receipt.rowsDigest) throw new Error("SOURCE_PAGE_CHANGED_DURING_SCREENSHOT");
    const screenshotSha256 = createHash("sha256").update(screenshot).digest("hex");
    screenshotPath = path.join(evidenceDir, screenshotName(screenshotSha256));
    await writeImmutable(screenshotPath, screenshot);
    await writeImmutable(pagePath, JSON.stringify({ ...page, captureScreenshotSha256: screenshotSha256 }, null, 2) + "\n");
  } else {
    const { captureScreenshotSha256, ...savedPage } = existingPage;
    if (!/^[a-f0-9]{64}$/.test(captureScreenshotSha256 ?? "") || JSON.stringify(savedPage) !== JSON.stringify(page)) {
      throw new Error("SOURCE_EVIDENCE_ALREADY_DIFFERENT");
    }
    screenshotPath = path.join(evidenceDir, screenshotName(captureScreenshotSha256));
    const savedScreenshot = await readFile(screenshotPath);
    if (createHash("sha256").update(savedScreenshot).digest("hex") !== captureScreenshotSha256) {
      throw new Error("SOURCE_SCREENSHOT_DIGEST_CHANGED");
    }
  }
  return { ...receipt, pagePath, screenshotPath, viewport: second.viewport, visibleRows: second.visibleRows };
}

function currentDetailObservation() {
  if (document.visibilityState === "hidden") return { ready: false, reason: "SOURCE_DOCUMENT_HIDDEN" };
  const visible = (element) => !!element?.offsetWidth && !!element?.offsetHeight;
  if (/login|signin/i.test(location.pathname + location.hash) ||
      [...document.querySelectorAll('input[type="password"]')].some(visible)) {
    return { ready: false, loginRequired: true, reason: "SOURCE_LOGIN_REQUIRED" };
  }
  const [route, query] = location.hash.split("?");
  const rwid = new URLSearchParams(query ?? "").get("RWID");
  if (route !== "#/xfjd/projectDetail" || !rwid) return { ready: false, reason: "SOURCE_DETAIL_ROUTE_CHANGED" };
  const mains = [...document.querySelectorAll("main.el-main")].filter(visible);
  const projectMatches = [...document.body.innerText.matchAll(/项目编号[：:]\s*([0-9]{8}[A-Z][0-9]{9})\b/g)];
  if (mains.length !== 1 || projectMatches.length !== 1) return { ready: false, reason: "SOURCE_DETAIL_IDENTITY_NOT_READY" };
  const labels = ["单位名称", "单位类别", "单位地址", "受理日期", "检查情况", "承办人", "消防管辖", "预定检查日期"];
  const fields = { 项目编号: projectMatches[0][1] };
  for (const label of labels) {
    const matches = [...mains[0].querySelectorAll("span")].filter((el) => el.textContent.trim() === label);
    if (matches.length !== 1) return { ready: false, reason: "SOURCE_DETAIL_FIELDS_CHANGED" };
    fields[label] = matches[0].parentElement.nextElementSibling?.innerText.trim() ?? "";
  }
  const tables = [...document.querySelectorAll(".el-table")].filter(visible);
  const productTables = tables.filter((t) => t.querySelector(".el-table__header-wrapper")?.innerText.includes("产品名称（规格型号）"));
  const documentTables = tables.filter((t) => t.querySelector(".el-table__header-wrapper")?.innerText.includes("审批日期"));
  // 火灾事故调查核查等任务详情没有“检查产品信息”区块：页面不含该标题且无产品表时，按空产品信息读取(标题已出现则仍须产品表唯一)。
  const noProductSection = productTables.length === 0 && !document.body.innerText.includes("检查产品信息");
  if ((productTables.length !== 1 && !noProductSection) || documentTables.length !== 1) return { ready: false, reason: "SOURCE_DETAIL_TABLES_NOT_READY" };
  const productTable = productTables[0];
  const productHeaders = productTable ? [...productTable.querySelectorAll(".el-table__header-wrapper th")].map((el) => el.innerText.trim()) : [];
  fields.检查产品信息 = noProductSection ? [] : [...productTable.querySelectorAll(".el-table__body-wrapper tr")].map((tr) => {
    const cells = [...tr.cells];
    const value = Object.fromEntries(cells.map((cell, i) => [productHeaders[i], cell.innerText.trim()]).filter(([key]) => key));
    const resultCell = cells[productHeaders.indexOf("检查结果")];
    value.检查结果标记 = [...(resultCell?.querySelectorAll("i") ?? [])].map((el) => el.className);
    return value;
  });
  // 文书目录只读已渲染的可见行(Element UI 树表：层级 class el-table__row--level-N，展开图标 el-table__expand-icon[--expanded])。
  // 折叠节点的子级未渲染，不能当作不存在：存在未展开节点时目录视为不完整，调用方不得把它当完整文书集合。
  const dirHeaders = [...documentTables[0].querySelectorAll(".el-table__header-wrapper th")].map((el) => el.innerText.trim());
  const col = (label) => dirHeaders.indexOf(label);
  const dirRows = [...documentTables[0].querySelectorAll(".el-table__body-wrapper tr.el-table__row")].filter(visible);
  if (!dirRows.length) return { ready: false, reason: "SOURCE_DOCUMENT_DIRECTORY_NOT_READY" };
  let collapsed = 0, expandable = 0, unknownExpand = 0;
  const flat = dirRows.map((tr) => {
    const cells = [...tr.cells].map((c) => c.innerText.trim());
    const lv = /el-table__row--level-(\d+)/.exec(tr.className);
    const icon = tr.querySelector(".el-table__expand-icon");
    const isExpandable = !!icon && !tr.querySelector(".el-table__placeholder");
    const isExpanded = !!icon && icon.classList.contains("el-table__expand-icon--expanded");
    if (!icon && !tr.querySelector(".el-table__placeholder")) unknownExpand += 1;
    if (isExpandable) { expandable += 1; if (!isExpanded) collapsed += 1; }
    const at = (label, fallback) => (col(label) >= 0 ? cells[col(label)] : fallback) ?? "";
    const unknownRow = !icon && !tr.querySelector(".el-table__placeholder");
    return { level: lv ? Number(lv[1]) : 0, title: cells.find((c) => c) ?? "", createdAt: at("创建时间", ""), approvalDate: at("审批日期", ""), status: at("状态", ""), ...(unknownRow ? { expandUnknown: true } : {}) };
  });
  // Business labels and dates only: never copy URLs, file paths, raw requests or session data.
  const projectNo = fields.项目编号;
  // 产品根必须“类型[产] + 案号精确相符”；同号的[罚]等其他类型=冲突(null)，其他案号的可识别根=false，无法识别=null(未知，不推断)。
  const rootCurrent = (title) => {
    const m = /^\[([^\]]{1,4})\][^-]*-(\d{8}[A-Z]\d{4}\d+)-/.exec(title);
    if (m) return m[2] === projectNo ? (m[1] === "产" ? true : null) : false;
    return null;
  };
  const roots = [];
  const stack = [];
  for (const row of flat) {
    const node = { title: row.title, createdAt: row.createdAt, approvalDate: row.approvalDate, status: row.status, currentProject: null, children: [], ...(row.expandUnknown ? { expandUnknown: true } : {}) };
    while (stack.length && stack[stack.length - 1].level >= row.level) stack.pop();
    if (!stack.length) { node.currentProject = rootCurrent(row.title); roots.push(node); } else { node.currentProject = stack[stack.length - 1].node.currentProject; stack[stack.length - 1].node.children.push(node); }
    stack.push({ level: row.level, node });
  }
  fields.文书目录 = roots;
  // 覆盖证明：滚动容器无溢出(没有被裁掉/虚拟化的行)、每行展开状态已知、无折叠。任一缺失 → 不完整(unknown)，不由自身行数推出。
  const bodyWrap = documentTables[0].querySelector(".el-table__body-wrapper");
  const overflow = bodyWrap && Number.isFinite(bodyWrap.scrollHeight) && Number.isFinite(bodyWrap.clientHeight) ? bodyWrap.scrollHeight - bodyWrap.clientHeight > 1 : null;
  const coverageProven = overflow === false && unknownExpand === 0 && collapsed === 0 && flat.length > 0;
  fields.文书目录完整性 = { renderedRows: flat.length, expandableNodes: expandable, collapsedNodes: collapsed, unknownExpandRows: unknownExpand, scrollOverflow: overflow, coverageProven, complete: coverageProven };
  const loading = [...document.querySelectorAll("[class*=loading],#nprogress")].some(
    (el) => visible(el) && getComputedStyle(el).visibility !== "hidden" &&
      Number(getComputedStyle(el).opacity) > 0,
  );
  return {
    ready: !loading, busy: loading, reason: loading ? "SOURCE_LOADING" : null, rwid, fields,
    sourceUrl: `${location.origin}${location.pathname}${route}?RWID=${encodeURIComponent(rwid)}`,
    viewport: { width: innerWidth, height: innerHeight },
  };
}

export async function readSourceDetail(cdp) {
  const result = await cdp.send("Runtime.evaluate", { expression: `(${currentDetailObservation.toString()})()`, returnByValue: true });
  if (result.exceptionDetails || !result.result?.value) throw new Error("SOURCE_DETAIL_READ_FAILED");
  return result.result.value;
}

export async function waitSourceDetail(cdp, target, timeoutMs = 45000) {
  if (!Number.isInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 60000) throw new Error("SOURCE_WAIT_ARGUMENT_INVALID");
  const deadline = Date.now() + timeoutMs;
  let reason;
  do {
    const detail = await readSourceDetail(cdp);
    if (detail.ready) return validateSourceDetail(detail, target);
    reason = detail.reason;
    if (!["SOURCE_LOADING", "SOURCE_DETAIL_IDENTITY_NOT_READY", "SOURCE_DETAIL_TABLES_NOT_READY", "SOURCE_DOCUMENT_DIRECTORY_NOT_READY"].includes(reason)) {
      throw new Error(reason);
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  } while (Date.now() < deadline);
  throw new Error(`SOURCE_WAIT_EXPIRED:${reason}`);
}

export function validateSourceDetail(value, target) {
  if (!value?.ready) throw new Error(value?.reason ?? "SOURCE_DETAIL_NOT_READY");
  const normalizeName = (name) => String(name ?? "").normalize("NFKC").replace(/\s+/g, "").replace(/\((?:个体工商户|个体户)\)$/, "");
  // 列表不显示 RWID：target.rwid 未知时(发现阶段)只接受详情 URL 给出的 RWID，回执标 identityComplete=false，不得用于材料动作。
  if ((target.rwid && value.rwid !== target.rwid) || !/^[A-Za-z0-9_-]{1,64}$/.test(value.rwid ?? "") || !/^[0-9]{8}[A-Z][0-9]{9}$/.test(value.fields?.项目编号 ?? "") ||
      (target.projectNo && value.fields?.项目编号 !== target.projectNo) ||
      normalizeName(value.fields?.单位名称) !== normalizeName(target.caseName)) throw new Error("CASE_IDENTITY_CHAIN_MISMATCH");
  if (!value.fields?.文书目录?.length) throw new Error("SOURCE_DOCUMENT_DIRECTORY_NOT_READY");
  return {
    rwid: value.rwid, projectNo: value.fields.项目编号,
    identityComplete: !!(target.rwid && target.projectNo && target.caseName && target.org),
    directoryComplete: value.fields.文书目录完整性?.coverageProven === true,
    products: value.fields.检查产品信息?.length ?? 0,
    detailDigest: createHash("sha256").update(JSON.stringify(value.fields)).digest("hex"),
  };
}

export async function captureSourceDetail({ tab, cdp, stagingDir, target }) {
  if (!path.isAbsolute(stagingDir)) throw new Error("SOURCE_CAPTURE_ARGUMENT_INVALID");
  const first = await readSourceDetail(cdp);
  const firstReceipt = validateSourceDetail(first, target);
  const second = await readSourceDetail(cdp);
  const receipt = validateSourceDetail(second, target);
  if (firstReceipt.detailDigest !== receipt.detailDigest) throw new Error("SOURCE_DETAIL_CHANGED_DURING_CAPTURE");
  await mkdir(stagingDir, { recursive: true });
  if (path.resolve(await realpath(stagingDir)).toLowerCase() !== path.resolve(stagingDir).toLowerCase()) throw new Error("SOURCE_EVIDENCE_REPARSE_POINT");
  const detailPath = path.join(stagingDir, `详情_${receipt.rwid}.json`);
  const screenshotPath = path.join(stagingDir, `详情_${receipt.rwid}.png`);
  const serialized = JSON.stringify(second.fields, null, 2) + "\n";
  let existing = null;
  try { existing = await readFile(detailPath, "utf8"); } catch (error) { if (error.code !== "ENOENT") throw error; }
  if (existing !== null && existing !== serialized) throw new Error("SOURCE_EVIDENCE_ALREADY_DIFFERENT");
  if (existing === null) {
    const screenshot = await tab.screenshot({ fullPage: true });
    const after = validateSourceDetail(await readSourceDetail(cdp), target);
    if (after.detailDigest !== receipt.detailDigest) throw new Error("SOURCE_DETAIL_CHANGED_DURING_SCREENSHOT");
    await writeImmutable(screenshotPath, screenshot);
    await writeImmutable(detailPath, serialized);
  } else await readFile(screenshotPath);
  return { ...receipt, sourceUrl: second.sourceUrl, detailPath, screenshotPath, viewport: second.viewport };
}

/** Open only the associated case link after rechecking the current visible triple. */
export async function openSourceCase({ tab, cdp, target, expectedWindow = null }) {
  const first = await readSourceListPage(cdp);
  const firstReceipt = validateListObservation(first, first.pageNumber, first.totalCount, undefined, 20, expectedWindow);
  const second = await readSourceListPage(cdp);
  const secondReceipt = validateListObservation(second, first.pageNumber, first.totalCount, undefined, 20, expectedWindow);
  if (firstReceipt.rowsDigest !== secondReceipt.rowsDigest) throw new Error("SOURCE_PAGE_CHANGED_BEFORE_CLICK");
  const equivalent = (value) => String(value ?? "").replace(/\s+/g, " ").trim();
  const matches = second.items.filter((item) => (item.rwid == null || item.rwid === target.rwid) &&
    ["caseName", "documentName", "createdAt"].every((key) => equivalent(item[key]) === equivalent(target[key])));
  if (matches.length !== 1) throw new Error("SOURCE_TARGET_NOT_UNIQUE");
  const row = tab.playwright.locator(".elx-table").filter({ visible: true })
    .locator(".elx-table--main-wrapper .body--wrapper tbody tr")
    .filter({ hasText: equivalent(target.caseName) }).filter({ hasText: equivalent(target.documentName) })
    .filter({ hasText: target.createdAt });
  if (await row.count() !== 1) throw new Error("SOURCE_VISIBLE_TARGET_NOT_UNIQUE");
  await row.getByText(equivalent(target.caseName), { exact: true }).click();
  const detail = await readSourceDetail(cdp);
  return detail.ready ? validateSourceDetail(detail, target) : { ready: false, reason: detail.reason };
}

/** Bounded list transition: action is optional and is not repeated after a checkpoint. */
export async function advanceSourceListPage(cdp, options) {
  const { expectedPage, expectedTotal, previousRowsDigest, expectedPageSize = 50, expectedWindow = null, ...runtime } = options;
  return advanceSourceStage({ ...runtime, stage: "LIST",
    identity: { expectedPage, expectedTotal, previousRowsDigest: previousRowsDigest ?? null, expectedPageSize, expectedWindow },
    read: () => readSourceListPage(cdp),
    validate: (value) => validateListObservation(value, expectedPage, expectedTotal, previousRowsDigest, expectedPageSize, expectedWindow) });
}

export async function advanceSourceDetail(cdp, { target, ...runtime }) {
  return advanceSourceStage({ ...runtime, stage: "DETAIL",
    identity: { rwid: target.rwid, caseName: target.caseName, projectNo: target.projectNo ?? null },
    read: () => readSourceDetail(cdp), validate: (value) => validateSourceDetail(value, target) });
}
