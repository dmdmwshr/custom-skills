// ego_online_sheet.mjs 的保护测试：纯逻辑 + 用假的 WPSOpenApi 页面跑 applyPlan/reconcileApply；不需要浏览器。
import assert from 'node:assert/strict';
import { test } from 'node:test';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { createHash } from 'node:crypto';
import * as m from './ego_online_sheet.mjs';

const URL_KEY = 'https://www.example.invalid/l/abc';
const TITLE = '26年通信科技科月考核情况记录表';
const SHEET = '11月情况';

// ---- 假页面 ----
function makeSheetModel(cells) { return { cells: { ...cells }, fills: {} }; }

function installFake(model, opts = {}) {
  const sheets = [{ name: '10月情况', ...makeSheetModel({ A1: '别的月' }) }, { name: SHEET, ...model }];
  const state = { active: 2, saves: 0, sets: [], href: URL_KEY + '?from=x', title: TITLE, ...opts };
  const sheetOf = (i) => sheets[i - 1];
  const addrRe = /^([A-Z]+)(\d+)$/;
  const rangeObj = (sh, addr) => {
    if (addr.includes(':')) {
      const [a, b] = addr.split(':'); const [r1, c1] = m.splitCoord(a); const [r2, c2] = m.splitCoord(b);
      const grid = []; for (let r = r1; r <= r2; r++) { const row = []; for (let c = c1; c <= c2; c++) row.push(sh.cells[m.colName(c) + r] ?? ''); grid.push(row); }
      return { get Value2() { return grid; }, get Formula() { return grid[0][0]; } };
    }
    assert.ok(addrRe.test(addr));
    return {
      get Value2() { return sh.cells[addr] ?? ''; },
      set Value2(v) { state.sets.push(addr); if (state.onSet) state.onSet(addr, v, sheets); sh.cells[addr] = v; },
      get Formula() { return sh.cells[addr] ?? ''; },
      Interior: { get Color() { return sh.fills[addr] ?? 16777215; }, set Color(v) { if (!state.failFill) sh.fills[addr] = v; } },
    };
  };
  const sheetApi = (sh, name) => ({
    get Name() { return name; },
    UsedRange: { Rows: { get Count() { return Math.max(19, ...Object.keys(sh.cells).map((k) => m.splitCoord(k)[0])); } }, Columns: { get Count() { return 14; } } },
    Range: (a) => rangeObj(sh, a),
  });
  const app = {
    Worksheets: { get Count() { return sheets.length; }, Item: (i) => sheetApi(sheetOf(i), sheetOf(i).name) },
    get ActiveSheet() { return sheetApi(sheetOf(state.active), sheetOf(state.active).name); },
    ActiveWorkbook: { Save: async () => { state.saves++; if (state.failSave) throw new Error('save failed'); } },
  };
  globalThis.WPSOpenApi = { Application: app };
  Object.defineProperty(globalThis, 'location', { configurable: true, get: () => ({ href: state.href }) });
  Object.defineProperty(globalThis, 'document', { configurable: true, get: () => ({ title: state.title }) });
  return { state, sheets, page: { evaluate: async (fn, arg) => fn(arg) } };
}

function layoutCells(extra = {}) {
  const cells = { A1: '月考核情况记录表', C14: '产品案卷核查', C15: '产品工作实效', C16: '联网系统核查', D3: '他人维护的内容' };
  m.BRIGADES.forEach((b, i) => { cells[m.colName(4 + i) + 2] = b; });
  return { ...cells, ...extra };
}

async function setup({ cellsExtra = {}, fakeOpts = {} } = {}) {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'ego-online-'));
  const src = path.join(dir, '【待补】2026年11月科室月考核情况记录表.xlsx');
  await fs.writeFile(src, 'fake-source-bytes');
  const sha = createHash('sha256').update('fake-source-bytes').digest('hex');
  const items = [];
  m.BRIGADES.forEach((b, bi) => m.ITEMS.forEach((it, ii) => {
    const row = 14 + ii; const col = m.colName(4 + bi);
    items.push({ brigade: b, item: it, local_cell: col + row, online_cell: col + row, local_value: `${b}${it}\n第二行`, baseline: null, action: 'write', local_pending_fill: it === '产品工作实效' && bi > 0 });
  }));
  items.find((i) => i.brigade === '梁溪大队' && i.item === '产品工作实效').local_value = '\\';
  const plan = {
    schema: 'online-sheet-plan/1', year: 2026, month: 11, status: 'ok', blockers: [],
    source: { path: src, sha256: sha, sheet: '1', period: [2026, 11], period_source: 'filename' },
    online: { identity: { url: URL_KEY, title: TITLE }, url_key: URL_KEY, file_id: null, sheet_name: SHEET, worksheet_id: 2, sheets: [{ name: '10月情况', id: 1 }, { name: SHEET, id: 2 }] },
    mapping: { header_row: 2, brigade_cols: Object.fromEntries(m.BRIGADES.map((b, i) => [b, 4 + i])), item_rows: { 产品案卷核查: 14, 产品工作实效: 15, 联网系统核查: 16 } },
    items,
  };
  plan.fingerprint = m.planFingerprint(plan);
  const planPath = path.join(dir, 'plan.json');
  await fs.writeFile(planPath, JSON.stringify(plan), 'utf8');
  const fake = installFake(makeSheetModel(layoutCells(cellsExtra)), fakeOpts);
  return { dir, plan, planPath, fake, run: (opts = {}) => m.applyPlan(fake.page, planPath, dir, { year: 2026, month: 11, settleMs: 0, ...opts }) };
}

// ---- 纯逻辑 ----
test('coords and url/path helpers', () => {
  assert.equal(m.colName(27), 'AA');
  assert.deepEqual(m.splitCoord('K16'), [16, 11]);
  assert.equal(m.normUrl('https://WWW.Example.invalid/l/abc/?x=1#t'), URL_KEY);
  assert.equal(m.resolveSourcePath('/mnt/d/x/y.xlsx'), process.platform === 'win32' ? 'D:/x/y.xlsx' : '/mnt/d/x/y.xlsx');
});

test('business whitelist constants match resources/monthly_workflow.json', async () => {
  const cfg = JSON.parse(await fs.readFile(new URL('../resources/monthly_workflow.json', import.meta.url), 'utf8')).online_sheet_rules;
  assert.deepEqual(m.BRIGADES, cfg.brigades);
  assert.deepEqual(m.ITEMS, cfg.items);
  assert.equal(m.SHEET_NAME_PATTERN(11), cfg.sheet_name_pattern.replace('{month}', '11'));
});

test('locate requires unique header row, columns and item rows', () => {
  const cells = Object.fromEntries(Object.entries(layoutCells()).map(([k, v]) => [k, { v, f: null }]));
  assert.equal(m.locate(cells).mapping.item_rows['联网系统核查'], 16);
  assert.equal(m.locate({ ...cells, B30: { v: '产品工作实效', f: null } }).blockers[0].code, 'item_ambiguous');
  assert.equal(m.locate({ ...cells, Z2: { v: '梁溪大队', f: null } }).blockers[0].code, 'brigade_duplicate');
  const second = { ...cells }; m.BRIGADES.forEach((b, i) => { second[m.colName(4 + i) + 40] = { v: b, f: null }; });
  assert.equal(m.locate(second).blockers[0].code, 'header_ambiguous');
});

test('validatePlan recomputes whitelist instead of trusting coordinates', async () => {
  const { plan } = await setup();
  const clone = () => JSON.parse(JSON.stringify(plan));
  assert.deepEqual(m.validatePlan(plan, 2026, 11), []);
  const a100 = clone(); a100.items[0].online_cell = 'A100';
  assert.equal(m.validatePlan(a100, 2026, 11)[0].code, 'fingerprint_mismatch');
  a100.fingerprint = m.planFingerprint(a100); // 指纹被同步改也不行：语义坐标重算
  const cells = Object.fromEntries(Object.entries(layoutCells()).map(([k, v]) => [k, { v, f: null }]));
  assert.ok(m.validatePlan(a100, 2026, 11, cells).some((e) => e.code === 'online_cell_not_semantic'));
  assert.equal(m.validatePlan(plan, 2026, 12)[0].code, 'plan_period_mismatch');
  const wrongMonth = clone(); wrongMonth.month = 10;
  assert.ok(m.validatePlan(wrongMonth, 2026, 11).some((e) => e.code === 'plan_period_mismatch'));
  const short = clone(); short.items.pop();
  assert.ok(m.validatePlan(short, 2026, 11).some((e) => e.code === 'items_not_whitelist'));
  const dup = clone(); dup.items[1].online_cell = dup.items[0].online_cell;
  assert.ok(m.validatePlan(dup, 2026, 11).some((e) => e.code === 'cells_invalid'));
  for (const bad of ['=1+1', '+1', '-1', '@x']) {
    const f = clone(); f.items[0].local_value = bad; f.fingerprint = m.planFingerprint(f);
    assert.ok(m.validatePlan(f, 2026, 11).some((e) => e.code === 'write_value_invalid'), bad);
  }
  assert.ok(m.validatePlan(plan, 2026, 11, { ...cells, B30: { v: '产品工作实效', f: null } }).some((e) => e.code === 'mapping_drift'));
});

test('checkBinding: document url/title/sheet/index', async () => {
  const { plan } = await setup();
  const live = { url: URL_KEY + '#x', title: TITLE, activeSheet: SHEET, sheets: [{ index: 1, name: '10月情况' }, { index: 2, name: SHEET }] };
  assert.deepEqual(m.checkBinding(plan, live), []);
  assert.equal(m.checkBinding(plan, { ...live, url: 'https://www.example.invalid/l/other' })[0].code, 'document_url_mismatch');
  assert.equal(m.checkBinding(plan, { ...live, title: '别的文档' })[0].code, 'document_title_mismatch');
  assert.ok(m.checkBinding(plan, { ...live, activeSheet: '12月情况' }).some((p) => p.code === 'worksheet_mismatch'));
  assert.ok(m.checkBinding(plan, { ...live, sheets: [{ index: 5, name: SHEET }] }).some((p) => p.code === 'worksheet_id_mismatch'));
});

test('classify: baseline conflicts skipped, backslash literal, fills only for local pending cells', async () => {
  const { plan } = await setup();
  const blank = { v: '', f: null, fill: 16777215 };
  const cur = (a) => (a === 'D14' ? { ...blank, v: '同事刚写' } : a === 'E15' ? { ...blank, v: plan.items.find((i) => i.online_cell === 'E15').local_value } : blank);
  const r = m.classify(plan, cur);
  assert.deepEqual(r.conflicts.map((c) => c.online_cell), ['D14']);
  assert.equal(r.toWrite.length, 22);
  assert.deepEqual(r.fillOnly.map((e) => e.online_cell), ['E15']);
  assert.equal(plan.items.find((i) => i.online_cell === 'D15').local_value, '\\');
  assert.ok(r.toWrite.every((e) => e.online_cell !== 'D14'));
});

test('diffFreeze separates whitelist from outside changes', () => {
  const mk = (a, b) => ({ sheets: [{ index: 1, name: '10月情况', cells: { A1: { v: a, f: null } } }, { index: 2, name: SHEET, cells: { D14: { v: b, f: null } } }] });
  const d = m.diffFreeze(mk('x', ''), mk('x2', 'new'), new Set([`${SHEET}!D14`]));
  assert.equal(d.inside.length, 1);
  assert.deepEqual(d.outside.map((c) => `${c.sheet}!${c.cell}`), ['10月情况!A1']);
});

test('readState fails closed on corrupt files', async () => {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'ego-state-'));
  assert.equal(await m.readState(fs, path.join(dir, 'none.json')), null);
  await fs.writeFile(path.join(dir, 'bad.json'), '{坏', 'utf8');
  await assert.rejects(() => m.readState(fs, path.join(dir, 'bad.json')), /state_corrupt/);
  await fs.writeFile(path.join(dir, 'shape.json'), '{"status":"weird"}', 'utf8');
  await assert.rejects(() => m.readState(fs, path.join(dir, 'shape.json')), /state_corrupt/);
  await fs.writeFile(path.join(dir, 'ok.json'), '{"status":"done"}', 'utf8');
  assert.equal((await m.readState(fs, path.join(dir, 'ok.json'))).status, 'done');
});

test('unsupported page is refused and left alone', async () => {
  const saved = globalThis.WPSOpenApi; delete globalThis.WPSOpenApi;
  Object.defineProperty(globalThis, 'location', { configurable: true, get: () => ({ href: 'https://example.invalid' }) });
  Object.defineProperty(globalThis, 'document', { configurable: true, get: () => ({ title: 't' }) });
  const page = { evaluate: async (fn, arg) => fn(arg) };
  await assert.rejects(() => m.detect(page), /unsupported_page/);
  globalThis.WPSOpenApi = saved;
});

// ---- 假页面端到端 ----
test('applyPlan writes 24 cells, only pending-fill cells go red, verifies and is idempotent', async () => {
  const t = await setup();
  const r = await t.run();
  assert.equal(r.status, 'done');
  assert.equal(r.verified.length, 24);
  const sh = t.fake.sheets[1];
  assert.equal(sh.cells.D15, '\\');
  assert.ok(sh.cells.D14.includes('\n第二行'));
  const red = Object.entries(sh.fills).filter(([, v]) => v === 255).map(([k]) => k).sort();
  assert.deepEqual(red, t.plan.items.filter((i) => i.local_pending_fill).map((i) => i.online_cell).sort());
  assert.equal(r.outside_target_changes.length, 0);
  assert.equal(t.fake.sheets[0].cells.A1, '别的月');
  // 再算一次 plan 等价：已全部等于本地值 -> 不再写
  const before = t.fake.state.sets.length;
  const again = await t.run();
  assert.equal(again.status, 'done');
  assert.equal(t.fake.state.sets.length, before);
});

test('applyPlan refuses another document, wrong month, changed source and wrong arguments before any write', async () => {
  for (const [label, mutate, opts, code] of [
    ['url', (t) => { t.fake.state.href = 'https://www.example.invalid/l/other'; }, {}, 'binding_mismatch'],
    ['title', (t) => { t.fake.state.title = '别的文档'; }, {}, 'binding_mismatch'],
    ['month-page', (t) => { t.fake.state.active = 1; }, {}, 'binding_mismatch'],
    ['source', async (t) => { await fs.writeFile(t.plan.source.path, 'changed'); }, {}, 'source_changed'],
    ['args-month', () => {}, { month: 12 }, 'plan_invalid'],
  ]) {
    const t = await setup();
    await mutate(t);
    await assert.rejects(() => t.run(opts), (e) => e.code === code, label);
    assert.equal(t.fake.state.sets.length, 0, label);
  }
});

test('applyPlan re-checks each cell right before writing and does not overwrite later human edits', async () => {
  const t = await setup();
  t.fake.state.onSet = (addr, v, sheets) => { if (addr === 'D14') sheets[1].cells.E14 = '同事在写入期间填的内容'; };
  const r = await t.run();
  assert.equal(t.fake.sheets[1].cells.E14, '同事在写入期间填的内容');
  assert.deepEqual(r.conflicts.map((c) => c.online_cell), ['E14']);
  assert.equal(r.verified.length, 23);
});

test('applyPlan aborts if the layout gains a duplicate header/item row or a label moves mid-run', async () => {
  const t = await setup({ cellsExtra: { B30: '产品工作实效' } });
  await assert.rejects(() => t.run(), (e) => e.code === 'mapping_drift');
  assert.equal(t.fake.state.sets.length, 0);
  const t2 = await setup();
  t2.fake.state.onSet = (addr, v, sheets) => { if (addr === 'D14') delete sheets[1].cells.C15; };
  const r = await t2.run();
  assert.match(r.aborted, /layout_changed/);
  assert.equal(r.status, 'needs_reconcile');
});

test('fill failure and save error never end as done; unknown state blocks reruns; corrupt state fails closed', async () => {
  const t = await setup({ fakeOpts: { failFill: true } });
  const r = await t.run();
  assert.equal(r.status, 'needs_reconcile');
  assert.ok(r.fill_failed.length > 0);
  await assert.rejects(() => t.run(), (e) => e.code === 'unknown_prior_write');
  const t2 = await setup({ fakeOpts: { failSave: true } });
  const r2 = await t2.run();
  assert.equal(r2.status, 'needs_reconcile');
  assert.match(r2.save, /save_error/);
  const t3 = await setup();
  await fs.writeFile(path.join(t3.dir, 'apply-state.json'), '{坏', 'utf8');
  await assert.rejects(() => t3.run(), (e) => e.code === 'state_corrupt');
  assert.equal(t3.fake.state.sets.length, 0);
  assert.equal(await fs.readFile(path.join(t3.dir, 'apply-state.json'), 'utf8'), '{坏');
});

test('reconcileApply is bound to the original state and plan', async () => {
  const t = await setup({ fakeOpts: { failFill: true } });
  await t.run();
  const stateBefore = await fs.readFile(path.join(t.dir, 'apply-state.json'), 'utf8');
  // 填充仍缺：不能解除
  const partial = await m.reconcileApply(t.fake.page, t.planPath, t.dir);
  assert.equal(partial.all_in_place, false);
  assert.equal(await fs.readFile(path.join(t.dir, 'apply-state.json'), 'utf8'), stateBefore);
  // 错计划（指纹不符）
  const other = JSON.parse(await fs.readFile(t.planPath, 'utf8')); other.items[0].local_value = '别的';
  const otherPath = path.join(t.dir, 'other.json'); await fs.writeFile(otherPath, JSON.stringify(other));
  await assert.rejects(() => m.reconcileApply(t.fake.page, otherPath, t.dir), (e) => e.code === 'reconcile_plan_mismatch');
  // 错文档
  t.fake.state.href = 'https://www.example.invalid/l/other';
  await assert.rejects(() => m.reconcileApply(t.fake.page, t.planPath, t.dir), (e) => e.code === 'binding_mismatch');
  t.fake.state.href = URL_KEY;
  // 无状态
  const empty = await fs.mkdtemp(path.join(os.tmpdir(), 'ego-empty-'));
  await assert.rejects(() => m.reconcileApply(t.fake.page, t.planPath, empty), (e) => e.code === 'no_apply_state');
  assert.equal(await fs.readFile(path.join(t.dir, 'apply-state.json'), 'utf8'), stateBefore);
  // 修好填充后对账到位才解除
  for (const e of t.plan.items.filter((i) => i.local_pending_fill)) t.fake.sheets[1].fills[e.online_cell] = 255;
  const ok = await m.reconcileApply(t.fake.page, t.planPath, t.dir);
  assert.equal(ok.state_cleared, true);
  assert.equal(JSON.parse(await fs.readFile(path.join(t.dir, 'apply-state.json'), 'utf8')).status, 'done');
});
