import { createHash } from "node:crypto";
import { mkdir, readFile, realpath, writeFile } from "node:fs/promises";
import path from "node:path";
import { advanceSourceStage } from "./browser_stage.mjs";
export { advanceSourceStage, saveSourceStageCheckpoint } from "./browser_stage.mjs";

/** Read only the current legal-document table. Never read session or request data. */
function currentListObservation() {
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
  // This legacy page renders virtual rows. Read its current table model only,
  // then verify its business fields against every rendered main-table row.
  const records = table.__vue__?.tableFullData;
  if (!Array.isArray(records)) return { ready: false, reason: "SOURCE_TABLE_DATA_UNAVAILABLE" };
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
    (element) => visible(element) && getComputedStyle(element).visibility !== "hidden",
  );
  const items = records.map((record, index) => ({
    rwid: String(record.RWID ?? "").trim(),
    caseName: String(record.RWMC ?? "").trim(),
    documentName: String(record.TITLE ?? "").trim(),
    createdAt: String(record.CRT_TIME ?? "").trim(),
    sourcePage: pageNumber,
    sourceRow: index + 1,
    sourceOrder: (pageNumber - 1) * pageSize + index + 1,
  }));
  const main = table.querySelector(".elx-table--main-wrapper");
  const headers = [...(main?.querySelector("thead tr")?.cells ?? [])].map((cell) => cell.innerText.trim());
  const indexes = ["文号或文书名称", "关联项目", "创建日期"].map((label) => headers.indexOf(label));
  if (indexes.some((index) => index < 0)) return { ready: false, reason: "SOURCE_COLUMNS_CHANGED" };
  const visibleRows = [...(main?.querySelectorAll(".body--wrapper tbody tr") ?? [])].map((row) => {
    const cells = [...row.cells].map((cell) => cell.innerText.trim());
    return { documentName: cells[indexes[0]], caseName: cells[indexes[1]], createdAt: cells[indexes[2]] };
  });
  const renderedText = (value) => String(value ?? "").replace(/\s+/g, " ").trim();
  const visibleMatched = visibleRows.length > 0 && visibleRows.every((row) => items.some(
    (item) => renderedText(item.documentName) === renderedText(row.documentName) &&
      renderedText(item.caseName) === renderedText(row.caseName) && renderedText(item.createdAt) === renderedText(row.createdAt),
  ));
  // The other date editor belongs to the approval-date form item. The creation
  // range is the directly rendered column in this observed legacy search form.
  const dateInputs = [...document.querySelectorAll(
    ".avue-view form > .el-col > div > .el-date-editor.el-range-editor input",
  )].filter(visible);
  return {
    ready: !loading && visibleMatched, busy: loading,
    reason: loading ? "SOURCE_LOADING" : visibleMatched ? null : "SOURCE_VISIBLE_ROWS_MISMATCH",
    pageNumber, pageSize, totalCount, totalPages: Math.ceil(totalCount / pageSize),
    visibleRows: visibleRows.length, items,
    dateValues: dateInputs.map((input) => input.value),
    viewport: { width: innerWidth, height: innerHeight },
  };
}

export function validateListObservation(value, expectedPage, expectedTotal, previousRowsDigest, expectedPageSize = 50) {
  if (!value?.ready) throw new Error(value?.reason ?? "SOURCE_NOT_READY");
  const { pageNumber, pageSize, totalCount, totalPages, items, dateValues } = value;
  if (pageNumber !== expectedPage || pageSize !== expectedPageSize || totalCount !== expectedTotal ||
      totalPages !== Math.ceil(totalCount / pageSize) || pageNumber > totalPages) {
    throw new Error("SOURCE_PAGINATION_NOT_COMMITTED");
  }
  const expectedRows = Math.min(pageSize, totalCount - (pageNumber - 1) * pageSize);
  if (!Array.isArray(items) || items.length !== expectedRows || items.some((item) =>
    !item.rwid || !item.caseName || !item.documentName || !item.createdAt)) {
    throw new Error("SOURCE_ROW_COUNT_OR_IDENTITY_INVALID");
  }
  const year = new Intl.DateTimeFormat("en", { timeZone: "Asia/Shanghai", year: "numeric" }).format(new Date());
  if (dateValues?.length !== 2 || dateValues[0] !== `${year}-01-01` || dateValues[1] !== `${year}-12-31`) {
    throw new Error("SOURCE_YEAR_RANGE_CHANGED");
  }
  const rowValues = items.map(({ rwid, caseName, documentName, createdAt }) => ({ rwid, caseName, documentName, createdAt }));
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
export async function waitSourceListPage(cdp, { expectedPage, expectedTotal, previousRowsDigest, expectedPageSize = 50, timeoutMs = 45000 }) {
  if (!Number.isInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 60000) throw new Error("SOURCE_WAIT_ARGUMENT_INVALID");
  const deadline = Date.now() + timeoutMs;
  let lastReason;
  do {
    const observation = await readSourceListPage(cdp);
    try {
      return validateListObservation(observation, expectedPage, expectedTotal, previousRowsDigest, expectedPageSize);
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
export async function captureSourceListPage({ tab, cdp, evidenceDir, round, expectedPage, expectedTotal, previousRowsDigest, expectedPageSize = 50 }) {
  if (![1, 2, 3].includes(round) || !path.isAbsolute(evidenceDir)) throw new Error("SOURCE_CAPTURE_ARGUMENT_INVALID");
  const first = await readSourceListPage(cdp);
  const firstReceipt = validateListObservation(first, expectedPage, expectedTotal, previousRowsDigest, expectedPageSize);
  const second = await readSourceListPage(cdp);
  const receipt = validateListObservation(second, expectedPage, expectedTotal, previousRowsDigest, expectedPageSize);
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
    const afterReceipt = validateListObservation(after, expectedPage, expectedTotal, previousRowsDigest, expectedPageSize);
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
  if (productTables.length !== 1 || documentTables.length !== 1) return { ready: false, reason: "SOURCE_DETAIL_TABLES_NOT_READY" };
  const productTable = productTables[0];
  const productHeaders = [...productTable.querySelectorAll(".el-table__header-wrapper th")].map((el) => el.innerText.trim());
  fields.检查产品信息 = [...productTable.querySelectorAll(".el-table__body-wrapper tr")].map((tr, index) => {
    const cells = [...tr.cells];
    const value = Object.fromEntries(cells.map((cell, i) => [productHeaders[i], cell.innerText.trim()]).filter(([key]) => key));
    const resultCell = cells[productHeaders.indexOf("检查结果")];
    value.检查结果标记 = [...(resultCell?.querySelectorAll("i") ?? [])].map((el) => el.className);
    const source = productTable.__vue__?.store?.states?.data?.[index];
    value.页面记录 = Object.fromEntries(["GGXH", "JCRQ", "SFHG", "SFCPFC"].map((key) => [key, source?.[key] ?? null]));
    return value;
  });
  const roots = documentTables[0].__vue__?.store?.states?.data;
  if (!Array.isArray(roots) || !roots.length) return { ready: false, reason: "SOURCE_DOCUMENT_DIRECTORY_NOT_READY" };
  // Business labels and dates only: never copy URLs, file paths, raw requests or session data.
  const cleanNode = (node) => ({
    title: String(node.TITLE ?? node.WSLBMC ?? node.RWMC ?? "").trim(),
    createdAt: String(node.CRT_TIME ?? "").trim(),
    approvalDate: String(node.SPSJ ?? "").trim(),
    status: String(node.STATUS ?? node.RWSTATUS ?? "").trim(),
    currentProject: node.IS_CURRENT ?? null,
    children: (node.children ?? []).map(cleanNode),
  });
  fields.文书目录 = roots.map(cleanNode);
  const loading = [...document.querySelectorAll("[class*=loading],#nprogress")].some(
    (el) => visible(el) && getComputedStyle(el).visibility !== "hidden",
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
  if (value.rwid !== target.rwid || !/^[A-Za-z0-9_-]{1,64}$/.test(value.rwid ?? "") || !/^[0-9]{8}[A-Z][0-9]{9}$/.test(value.fields?.项目编号 ?? "") ||
      (target.projectNo && value.fields?.项目编号 !== target.projectNo) ||
      normalizeName(value.fields?.单位名称) !== normalizeName(target.caseName)) throw new Error("CASE_IDENTITY_CHAIN_MISMATCH");
  if (!value.fields?.文书目录?.length) throw new Error("SOURCE_DOCUMENT_DIRECTORY_NOT_READY");
  return {
    rwid: value.rwid, projectNo: value.fields.项目编号,
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
export async function openSourceCase({ tab, cdp, target }) {
  const first = await readSourceListPage(cdp);
  const firstReceipt = validateListObservation(first, first.pageNumber, first.totalCount, undefined, 20);
  const second = await readSourceListPage(cdp);
  const secondReceipt = validateListObservation(second, first.pageNumber, first.totalCount, undefined, 20);
  if (firstReceipt.rowsDigest !== secondReceipt.rowsDigest) throw new Error("SOURCE_PAGE_CHANGED_BEFORE_CLICK");
  const equivalent = (value) => String(value ?? "").replace(/\s+/g, " ").trim();
  const matches = second.items.filter((item) => item.rwid === target.rwid &&
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
  const { expectedPage, expectedTotal, previousRowsDigest, expectedPageSize = 50, ...runtime } = options;
  return advanceSourceStage({ ...runtime, stage: "LIST",
    identity: { expectedPage, expectedTotal, previousRowsDigest: previousRowsDigest ?? null, expectedPageSize },
    read: () => readSourceListPage(cdp),
    validate: (value) => validateListObservation(value, expectedPage, expectedTotal, previousRowsDigest, expectedPageSize) });
}

export async function advanceSourceDetail(cdp, { target, ...runtime }) {
  return advanceSourceStage({ ...runtime, stage: "DETAIL",
    identity: { rwid: target.rwid, caseName: target.caseName, projectNo: target.projectNo ?? null },
    read: () => readSourceDetail(cdp), validate: (value) => validateSourceDetail(value, target) });
}
