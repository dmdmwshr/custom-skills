import assert from "node:assert/strict";
import { mkdtemp, readFile, readdir, realpath, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { captureSourceListPage, validateListObservation, validateSourceDetail, waitSourceListPage } from "./browser_source_capture.mjs";

const year = new Intl.DateTimeFormat("en", { timeZone: "Asia/Shanghai", year: "numeric" }).format(new Date());
const fixture = {
  ready: true, pageNumber: 12, pageSize: 50, totalCount: 580, totalPages: 12,
  dateValues: [`${year}-01-01`, `${year}-12-31`],
  items: Array.from({ length: 30 }, (_, index) => ({ rwid: `fixture-${index}`, caseName: "合成测试", documentName: "测试记录", createdAt: `${year}-01-01 00:00:00` })),
};
const good = validateListObservation(fixture, 12, 580);
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
