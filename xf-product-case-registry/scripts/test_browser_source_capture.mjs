import assert from "node:assert/strict";
import { mkdtemp, readFile, readdir, realpath, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import vm from "node:vm";
import { assertMaterialIdentity, assignLeafMembership, captureSourceListPage, compareSelection, identityGate, leafCompleteness, validateListObservation, validateSourceDetail, waitSourceListPage } from "./browser_source_capture.mjs";

const year = new Intl.DateTimeFormat("en", { timeZone: "Asia/Shanghai", year: "numeric" }).format(new Date());
const fixture = {
  ready: true, pageNumber: 12, pageSize: 50, totalCount: 580, totalPages: 12,
  dateValues: [`${year}-01-01`, `${year}-12-31`],
  items: Array.from({ length: 30 }, (_, index) => ({ rwid: `fixture-${index}`, caseName: "合成测试", documentName: "测试记录", createdAt: `${year}-01-01 00:00:00` })),
};
const good = validateListObservation(fixture, 12, 580);
const crossYear = {startDate: "2025-11-20", endDate: "2026-02-20"};
assert.equal(validateListObservation({...fixture, dateValues: Object.values(crossYear)},
  12, 580, undefined, 50, crossYear).rows, 30);
assert.throws(() => validateListObservation(fixture, 12, 580, undefined, 50, crossYear), /SOURCE_YEAR_RANGE_CHANGED/);
assert.equal(validateListObservation({...fixture, pageNumber:1,totalCount:0,totalPages:1,items:[]},1,0).rows,0);
assert.equal(good.rows, 30);
assert.match(good.rowsDigest, /^[a-f0-9]{64}$/);
assert.throws(() => validateListObservation({ ...fixture, ready: false, reason: "SOURCE_LOADING" }, 12, 580), /SOURCE_LOADING/);
assert.throws(() => validateListObservation({ ...fixture, pageNumber: 11 }, 12, 580), /SOURCE_PAGINATION_NOT_COMMITTED/);
assert.throws(() => validateListObservation({ ...fixture, items: fixture.items.slice(1) }, 12, 580), /SOURCE_ROW_COUNT_OR_IDENTITY_INVALID/);
assert.throws(() => validateListObservation(fixture, 12, 581), /SOURCE_PAGINATION_NOT_COMMITTED/);
assert.throws(() => validateListObservation({ ...fixture, dateValues: ["20zzh26-01-01", `${year}-12-31`] }, 12, 580), /SOURCE_YEAR_RANGE_CHANGED/);
assert.throws(() => validateListObservation(fixture, 12, 580, good.rowsDigest), /SOURCE_PREVIOUS_PAGE_STILL_VISIBLE/);
assert.throws(() => validateListObservation({ ...fixture, items: fixture.items.map((item, i) => i ? item : { ...item, rwid: "" }) }, 12, 580), /SOURCE_ROW_COUNT_OR_IDENTITY_INVALID/);
assert.equal(validateListObservation({ ...fixture, pageNumber: 29, pageSize: 20, totalPages: 29, items: fixture.items.slice(0, 20) }, 29, 580, undefined, 20).rows, 20);
const detailFixture = { ready: true, rwid: "fixture-detail", fields: { 项目编号: "99999999T209900001", 单位名称: "测试  单位（个体工商户）", 文书目录: [{ title: "测试文书" }] } };
assert.equal(validateSourceDetail(detailFixture, { rwid: "fixture-detail", caseName: "测试单位" }).projectNo, "99999999T209900001");
assert.throws(() => validateSourceDetail(detailFixture, { rwid: "wrong-detail", caseName: "测试单位" }), /CASE_IDENTITY_CHAIN_MISMATCH/);
assert.throws(() => validateSourceDetail(detailFixture, { rwid: "fixture-detail", caseName: "其他单位" }), /CASE_IDENTITY_CHAIN_MISMATCH/);

// ---- 可见 DOM 方法：身份/根归属/完整性护栏(合成数据) ----
{
  const PN = "32002201C202600062";
  const full = { expectedProjectNo: PN, detailProjectNo: PN, expectedRwid: "32100934444", urlRwid: "32100934444", expectedUnit: "单位甲", detailUnit: "单位甲", expectedOrg: "大队甲", detailOrg: "大队甲" };
  assert.equal(identityGate(full).ok, true);
  for (const key of Object.keys(full)) {
    const input = { ...full }; delete input[key];
    const gate = identityGate(input);
    assert.equal(gate.ok, false, key); assert.equal(gate.materialActionAllowed, false, key);
  }
  assert.equal(identityGate({ ...full, detailProjectNo: "32002201C202600064" }).ok, false);
  assert.equal(identityGate({ ...full, detailOrg: "大队乙" }).readOnlyLocateAllowed, false);
  assert.throws(() => assertMaterialIdentity({ rwid: "32100934444", caseName: "单位甲" }, { rwid: "32100934444", fields: { 项目编号: PN, 单位名称: "单位甲", 消防管辖: "大队甲" } }), /MATERIAL_IDENTITY_BLOCKED:MISSING_EXPECTED_PROJECT_NO,MISSING_EXPECTED_ORG/);
  assert.equal(assertMaterialIdentity({ projectNo: PN, rwid: "32100934444", caseName: "单位甲", org: "大队甲" }, { rwid: "32100934444", fields: { 项目编号: PN, 单位名称: "单位甲", 消防管辖: "大队甲" } }).ok, true);
  const own = { id: null, title: `[产]日常监督-${PN}-单位甲`, level: 1 };
  const sel = (m) => compareSelection({ leafIds: m.ownIds, selectedIds: m.ownSelected, foreignIds: m.foreignSelected, unknownState: m.unknownState, completeLeafSet: true });
  for (const rootId of [null, "99"]) {
    const m = assignLeafMembership([own, { id: "11", title: "产品检查记录", checked: true, level: 2 }, { id: rootId, hasCheckbox: rootId !== null, title: "[罚]一般-32002201X202600075-单位甲", checked: rootId !== null, level: 1 }, { id: "22", title: "行政处罚决定书", checked: true, level: 2 }], PN);
    assert.deepEqual(m.ownIds, ["11"]); assert.ok(m.foreignIds.includes("22")); assert.equal(sel(m).ok, false);
  }
  for (const title of ["一般-32002201X202600075-单位甲", "处罚32002201X202600075单位甲"]) {
    const m = assignLeafMembership([own, { id: "11", title: "a", checked: true }, { id: "99", hasCheckbox: true, title, checked: true }, { id: "22", title: "b", checked: true }], PN);
    assert.equal(m.unknownState, true); assert.ok(!m.ownIds.includes("22")); assert.equal(sel(m).ok, false);
  }
  const okM = assignLeafMembership([own, { id: "11", title: "a", checked: true, level: 2 }, { id: "12", title: "b", checked: true, level: 2 }], PN);
  assert.equal(compareSelection({ leafIds: okM.ownIds, selectedIds: okM.ownSelected, foreignIds: [], unknownState: okM.unknownState, completeLeafSet: leafCompleteness({ ownCount: 2, declaredLeafCount: 2, collapsedCount: 0, unreadableCount: 0, expandStateKnown: true }).complete }).ok, true);
  assert.equal(compareSelection({ leafIds: okM.ownIds, selectedIds: okM.ownSelected, foreignIds: [], unknownState: false }).ok, false); // 缺完整性证据
  assert.equal(leafCompleteness({ ownCount: 2, declaredLeafCount: null, collapsedCount: 0, unreadableCount: 0, expandStateKnown: true }).complete, false);
  assert.equal(leafCompleteness({ ownCount: 2, declaredLeafCount: 2, collapsedCount: 1, unreadableCount: 0, expandStateKnown: true }).complete, false);
}
// ---- 列表/详情读取器只读可见 DOM：访问 __vue__ 即失败 ----
{
  const src = fs.readFileSync(new URL("./browser_source_capture.mjs", import.meta.url), "utf8");
  assert.doesNotMatch(src, /__vue__|tableFullData|store\?\.states/);
  const grab = (name, next) => src.match(new RegExp(`function ${name}\\(\\) \\{[\\s\\S]*?\\n\\}\\n\\n(?=${next})`))[0];
  const listFn = grab("currentListObservation", "export function validateListObservation");
  const detailFn = grab("currentDetailObservation", "export async function readSourceDetail");
  const shown = { offsetWidth: 400, offsetHeight: 40 };
  const T = (o) => Object.defineProperty(o, "__vue__", { get() { throw new Error("INTERNAL_STATE_READ"); }, enumerable: false });
  const cell = (t) => ({ innerText: t });
  const pager = T({ ...shown, querySelector: (sel) => ({ ".el-pagination__total": cell("共 2 条"), "input[readonly]": { value: "50条/页" }, ".el-pagination__editor input": { value: "1" } })[sel] ?? null });
  const rows = [["关于甲的文书", "单位甲", "2026-03-01 08:00:00"], ["关于乙的文书", "单位乙", "2026-03-02 09:00:00"]].map((r) => ({ cells: r.map(cell) }));
  const main = { querySelector: (sel) => (sel === "thead tr" ? { cells: ["文号或文书名称", "关联项目", "创建日期"].map(cell) } : null), querySelectorAll: () => rows };
  const table = T({ ...shown, querySelector: (sel) => (sel === "thead" ? cell("文号或文书名称 关联项目 创建日期") : sel === ".elx-table--main-wrapper" ? main : null) });
  const dateInput = { ...shown, value: `${year}-01-01` };
  const doc = { visibilityState: "visible", body: { innerText: "" }, querySelectorAll: (sel) => ({ ".elx-table": [table], ".el-pagination": [pager], 'input[type="password"]': [], "[class*=loading],#nprogress": [], ".avue-view form > .el-col > div > .el-date-editor.el-range-editor input": [dateInput, { ...shown, value: `${year}-12-31` }] })[sel] ?? [] };
  const list = vm.runInNewContext(`(${listFn.replace(/^function currentListObservation/, "function")})()`, { document: doc, location: { pathname: "/", hash: "#/xfjd/cpjd/cxtj/flwscx/flws" }, getComputedStyle: () => ({ visibility: "visible", opacity: "0" }), innerWidth: 1, innerHeight: 1 });
  assert.equal(list.ready, true); assert.equal(list.visibleOnly, true); assert.equal(list.items.length, 2); assert.equal(list.items[0].rwid, null);
  assert.equal(validateListObservation(list, 1, 2, undefined, 50, { startDate: `${year}-01-01`, endDate: `${year}-12-31` }).rows, 2);
  const mismatch = vm.runInNewContext(`(${listFn.replace(/^function currentListObservation/, "function")})()`, { document: { ...doc, querySelectorAll: (sel) => (sel === ".elx-table" ? [{ ...table, querySelector: (q) => (q === ".elx-table--main-wrapper" ? { ...main, querySelectorAll: () => rows.slice(0, 1) } : table.querySelector(q)) }] : doc.querySelectorAll(sel)) }, location: { pathname: "/", hash: "#/xfjd/cpjd/cxtj/flwscx/flws" }, getComputedStyle: () => ({ visibility: "visible", opacity: "0" }), innerWidth: 1, innerHeight: 1 });
  assert.equal(mismatch.ready, false); assert.equal(mismatch.reason, "SOURCE_VISIBLE_ROWS_MISMATCH");
  // 详情：目录只读可见树行；存在未展开节点 → 目录不完整
  const PN = "32002201C202600062";
  const spanFor = (label, value) => ({ textContent: label, parentElement: { nextElementSibling: { innerText: value } } });
  const spans = [["单位名称", "单位甲"], ["单位类别", "x"], ["单位地址", "y"], ["受理日期", "2026-03-01"], ["检查情况", "已结案"], ["承办人", "z"], ["消防管辖", "大队甲"], ["预定检查日期", "2026-02-01"]].map(([l, v]) => spanFor(l, v));
  const mkTree = (collapsed, opts = {}) => {
    const tr = (cls, texts, icon, ph) => ({ className: cls, cells: texts.map(cell), querySelector: (sel) => (sel === ".el-table__expand-icon" ? icon : sel === ".el-table__placeholder" ? (ph ? {} : null) : null) });
    const icon = (expanded) => ({ classList: { contains: (c) => expanded && c === "el-table__expand-icon--expanded" } });
    if (opts.zeroIcons) return [tr("el-table__row", [`[产]日常监督-${PN}-单位甲`, "", "2026-03-01"], null, false), tr("el-table__row el-table__row--level-1", ["监督检查记录", "", "2026-03-02"], null, false)];
    return [tr("el-table__row", [`[产]日常监督-${PN}-单位甲`, "", "2026-03-01"], icon(!collapsed), false), tr("el-table__row el-table__row--level-1", ["监督检查记录", "", "2026-03-02"], null, true), ...(opts.extra ?? [])];
  };
  const mkDetail = (collapsed, opts = {}) => {
    const body = opts.noBody ? null : { scrollHeight: opts.overflow ? 900 : 120, clientHeight: 120 };
    const dirTable = T({ ...shown, querySelector: (sel) => (sel === ".el-table__header-wrapper" ? cell("名称 创建时间 审批日期") : sel === ".el-table__body-wrapper" ? body : null), querySelectorAll: (sel) => (sel === ".el-table__header-wrapper th" ? ["名称", "创建时间", "审批日期"].map(cell) : sel === ".el-table__body-wrapper tr.el-table__row" ? mkTree(collapsed, opts).map((r) => ({ ...shown, ...r })) : []) });
    const prodTable = T({ ...shown, querySelector: (sel) => (sel === ".el-table__header-wrapper" ? cell("产品名称（规格型号） 检查结果") : null), querySelectorAll: (sel) => (sel === ".el-table__header-wrapper th" ? ["产品名称（规格型号）", "检查结果"].map(cell) : sel === ".el-table__body-wrapper tr" ? [{ cells: [cell("灭火器"), { innerText: "未发现不合格现象", querySelectorAll: () => [] }] }] : []) });
    const main = { ...shown, querySelectorAll: (sel) => (sel === "span" ? spans : []) };
    const d = { visibilityState: "visible", body: { innerText: `项目编号：${PN}` }, querySelectorAll: (sel) => ({ 'input[type="password"]': [], "main.el-main": [main], ".el-table": [prodTable, dirTable], "[class*=loading],#nprogress": [] })[sel] ?? [] };
    return vm.runInNewContext(`(${detailFn.replace(/^function currentDetailObservation/, "function")})()`, { document: d, location: { pathname: "/", origin: "https://x.test", hash: "#/xfjd/projectDetail?RWID=32100934444" }, URLSearchParams, getComputedStyle: () => ({ visibility: "visible", opacity: "0" }), innerWidth: 1, innerHeight: 1 });
  };
  const open = mkDetail(false), shut = mkDetail(true);
  assert.equal(open.ready, true); assert.equal(open.fields.文书目录[0].currentProject, true); assert.equal(open.fields.文书目录[0].children.length, 1);
  assert.equal(open.fields.文书目录完整性.complete, true); assert.equal(open.fields.文书目录完整性.coverageProven, true);
  // 完整性不能由自身行数推出：零展开图标/占位、滚动溢出(虚拟化/被裁)、缺滚动容器 都保持 unknown
  assert.equal(mkDetail(false, { zeroIcons: true }).fields.文书目录完整性.complete, false);
  assert.equal(mkDetail(false, { zeroIcons: true }).fields.文书目录完整性.unknownExpandRows, 2);
  // 逐行归属：未知展开行在其所在节点上显式标记 expandUnknown；有图标/占位的行不带该键
  const zi = mkDetail(false, { zeroIcons: true }).fields.文书目录[0];
  assert.equal(zi.expandUnknown, true); assert.equal(zi.children[0].expandUnknown, true);
  assert.equal("expandUnknown" in open.fields.文书目录[0], false); assert.equal("expandUnknown" in open.fields.文书目录[0].children[0], false);
  assert.equal(mkDetail(false, { overflow: true }).fields.文书目录完整性.complete, false);
  assert.equal(mkDetail(false, { noBody: true }).fields.文书目录完整性.complete, false);
  // 产品根：同号[罚]不能归本案产品根；其他案号的可识别根=false；无法识别=null
  const penalty = mkDetail(false, { extra: [{ className: "el-table__row", cells: [cell(`[罚]一般-${PN}-单位甲`), cell(""), cell("")], querySelector: (sel) => (sel === ".el-table__placeholder" ? {} : null) }, { className: "el-table__row", cells: [cell("[罚]一般-32002201X202600075-单位甲"), cell(""), cell("")], querySelector: (sel) => (sel === ".el-table__placeholder" ? {} : null) }, { className: "el-table__row", cells: [cell("未知分类根"), cell(""), cell("")], querySelector: (sel) => (sel === ".el-table__placeholder" ? {} : null) }] });
  assert.equal(JSON.stringify(penalty.fields.文书目录.map((n) => n.currentProject)), JSON.stringify([true, null, false, null])); assert.equal(shut.fields.文书目录完整性.complete, false); assert.equal(shut.fields.文书目录完整性.collapsedNodes, 1);
  assert.equal(validateSourceDetail(open, { rwid: "32100934444", caseName: "单位甲", projectNo: PN }).directoryComplete, true);
  assert.equal(validateSourceDetail(shut, { rwid: "32100934444", caseName: "单位甲" }).directoryComplete, false);
  assert.equal(validateSourceDetail(open, { caseName: "单位甲" }).identityComplete, false); // 发现阶段：身份不完整，不可做材料动作
}
const taskTempRoot = await realpath(os.tmpdir());
const fixtureDir = await mkdtemp(path.join(taskTempRoot, "source-capture-test-"));
assert.equal(path.dirname(await realpath(fixtureDir)), taskTempRoot);
try {
  let screenshots = 0;
  const cdp = { send: async () => ({ result: { value: fixture } }) };
  const tab = { screenshot: async () => { screenshots += 1; return Buffer.from("synthetic screenshot fixture"); } };
  const args = { tab, cdp, evidenceDir: fixtureDir, round: 1, expectedPage: 12, expectedTotal: 580 };
  const captured = await captureSourceListPage(args);
  assert.match(path.basename(captured.screenshotPath), /^列表_轮1_页012_[a-f0-9]{12}\.png$/);
  const retried = await captureSourceListPage(args);
  assert.equal(retried.screenshotPath, captured.screenshotPath);
  assert.equal(screenshots, 1);
  assert.equal((await readdir(fixtureDir)).filter((name) => name.endsWith(".png")).length, 1);
  const acceptedPage = JSON.parse(await readFile(captured.pagePath, "utf8"));
  assert.equal(acceptedPage.items.length, 30);
  assert.match(acceptedPage.captureScreenshotSha256, /^[a-f0-9]{64}$/);
  await writeFile(captured.screenshotPath, "changed synthetic fixture");
  await assert.rejects(captureSourceListPage(args), /SOURCE_SCREENSHOT_DIGEST_CHANGED/);
  await assert.rejects(captureSourceListPage({ ...args, round: 4 }), /SOURCE_CAPTURE_ARGUMENT_INVALID/);
  const ready = await waitSourceListPage(cdp, { expectedPage: 12, expectedTotal: 580 });
  assert.equal(ready.rows, 30);
  await assert.rejects(waitSourceListPage(cdp, { expectedPage: 12, expectedTotal: 580, timeoutMs: 60001 }), /SOURCE_WAIT_ARGUMENT_INVALID/);
} finally {
  if (path.dirname(await realpath(fixtureDir)) !== taskTempRoot || !path.basename(fixtureDir).startsWith("source-capture-test-")) {
    throw new Error("TEST_CLEANUP_PATH_INVALID");
  }
  await rm(fixtureDir, { recursive: true });
}
console.log("browser source capture: validation, immutable screenshot reuse, tamper and wait checks passed");
