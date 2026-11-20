// R01 在线维护分支的浏览器（Ego）执行器：消费 monthly_online_sheet.py plan 产出的 plan.json。
// 只支持页面里实际存在 WPSOpenApi.Application（金山文档 WebOffice JSAPI）的在线表；其他页面明示不支持并保留。
// 不改权限，不调后台 HTTP。写前核对映射与基线，只写 plan 里 action=write 的格，只新增本地待补红底。
// a2：年月/文档/工作表/源哈希绑定，固定业务白名单重算，逐格动作前再核，对账绑定原状态，状态损坏 fail closed。
// 用法：由当次任务的 round 脚本 import 本文件，传入已选定的 Ego page，见 references/monthly/output_R01_online_sheet.md。

import { createHash } from 'node:crypto';

const RED_BGR = 255; // Excel Interior.Color 为 BGR：纯红 = 0x0000FF = 255
const MAX_ROWS = 200;
const MAX_COLS = 40;
// 固定业务白名单（与 resources/monthly_workflow.json 的 online_sheet_rules 一致，由测试核对）
export const BRIGADES = ['梁溪大队', '锡山大队', '惠山大队', '滨湖大队', '新吴大队', '江阴大队', '宜兴大队', '经开大队'];
export const ITEMS = ['产品案卷核查', '产品工作实效', '联网系统核查'];
export const SHEET_NAME_PATTERN = (month) => `${month}月情况`;
const FORMULA_PREFIXES = ['=', '+', '-', '@'];
const PLAN_ACTIONS = new Set(['write', 'no_change', 'skip_source_blank', 'skip_online_formula', 'skip_protected']);

export function colName(n) {
  let s = '';
  while (n > 0) { const m = (n - 1) % 26; s = String.fromCharCode(65 + m) + s; n = Math.floor((n - 1) / 26); }
  return s;
}

export function splitCoord(a1) {
  const m = /^([A-Z]+)(\d+)$/.exec(a1);
  if (!m) throw new Error('bad cell ' + a1);
  let c = 0;
  for (const ch of m[1]) c = c * 26 + ch.charCodeAt(0) - 64;
  return [Number(m[2]), c];
}

export function normText(v) {
  if (v === null || v === undefined) return null;
  const t = String(v).replace(/\r\n/g, '\n').replace(/\r/g, '\n');
  return t.trim() === '' ? null : t;
}

// ---- 纯逻辑（可在 node 里离线测试）----


export function normUrl(url) {
  if (!url) return null;
  try { const u = new URL(String(url).trim()); return `${u.protocol.replace(':', '').toLowerCase()}://${u.host.toLowerCase()}${u.pathname.replace(/\/+$/, '')}`; } catch { return null; }
}

export function planFingerprint(plan) {
  // 与 monthly_online_sheet.py plan_fingerprint 同算法
  const o = plan.online;
  const py = (v) => (v === null || v === undefined ? 'None' : String(v));
  const parts = ['online-sheet-plan-fp/1', plan.source.sha256, String(plan.year), String(plan.month), py(o.sheet_name), py(o.worksheet_id), o.url_key || ''];
  const cmp = (a, b) => (a < b ? -1 : a > b ? 1 : 0);
  const items = [...plan.items].sort((a, b) => cmp(a.brigade, b.brigade) || cmp(a.item, b.item));
  for (const i of items) {
    parts.push([i.brigade, i.item, i.local_cell, i.online_cell || '', i.local_value === null || i.local_value === undefined ? '\x00' : i.local_value,
      i.baseline === null || i.baseline === undefined ? '\x00' : i.baseline, i.action, i.local_pending_fill ? '1' : '0'].join('\x1f'));
  }
  return createHash('sha256').update(parts.join('\x1e'), 'utf8').digest('hex');
}

export function locate(cells) {
  // cells: {A1:{v,f}}；返回 {mapping, blockers}，与 Python locate 同口径：表头行/大队列/项目行都必须唯一
  const blockers = [];
  const byRow = new Map();
  const grid = [];
  for (const [k, c] of Object.entries(cells)) {
    const text = normText(c.f || c.v);
    if (text === null) continue;
    const [r, col] = splitCoord(k);
    grid.push([r, col, text.trim()]);
    if (BRIGADES.includes(text.trim())) { if (!byRow.has(r)) byRow.set(r, []); byRow.get(r).push([col, text.trim()]); }
  }
  const headerRows = [...byRow.entries()].filter(([, cs]) => new Set(cs.map((x) => x[1])).size === BRIGADES.length).map(([r]) => r);
  if (headerRows.length !== 1) return { mapping: null, blockers: [{ code: headerRows.length ? 'header_ambiguous' : 'header_missing', detail: JSON.stringify(headerRows) }] };
  const headerRow = headerRows[0];
  const cols = {};
  for (const [col, name] of byRow.get(headerRow)) {
    if (name in cols) blockers.push({ code: 'brigade_duplicate', detail: name });
    cols[name] = col;
  }
  if (blockers.length) return { mapping: null, blockers };
  const first = Math.min(...Object.values(cols));
  const rows = {};
  for (const item of ITEMS) {
    const hits = [...new Set(grid.filter(([r, c, t]) => r > headerRow && c < first && t === item).map(([r]) => r))].sort((a, b) => a - b);
    if (hits.length !== 1) blockers.push({ code: hits.length ? 'item_ambiguous' : 'item_missing', detail: `${item} 命中行 ${JSON.stringify(hits)}` });
    else rows[item] = hits[0];
  }
  if (blockers.length) return { mapping: null, blockers };
  return { mapping: { header_row: headerRow, brigade_cols: cols, item_rows: rows }, blockers: [] };
}

export function sameMapping(a, b) {
  if (!a || !b || a.header_row !== b.header_row) return false;
  const eq = (x, y) => { const kx = Object.keys(x || {}).sort(); const ky = Object.keys(y || {}).sort(); return JSON.stringify(kx) === JSON.stringify(ky) && kx.every((k) => x[k] === y[k]); };
  return eq(a.brigade_cols, b.brigade_cols) && eq(a.item_rows, b.item_rows);
}

export function validatePlan(plan, year, month, freshCells = null) {
  // 严格重算：8 队 × 3 项固定白名单、计划完整唯一、年月、工作表、指纹、（有在线现读时）语义坐标；返回问题数组
  const errors = [];
  const bad = (code, detail = '') => errors.push({ code, detail });
  if (plan.schema !== 'online-sheet-plan/1') bad('plan_schema', String(plan.schema));
  if (plan.status !== 'ok') bad('plan_blocked');
  if (!(Number.isInteger(year) && Number.isInteger(month) && month >= 1 && month <= 12)) bad('bad_period_arg', `${year}-${month}`);
  if (plan.year !== year || plan.month !== month) bad('plan_period_mismatch', `计划 ${plan.year}-${plan.month}，参数 ${year}-${month}`);
  const period = (plan.source || {}).period;
  if (!Array.isArray(period) || period[0] !== year || period[1] !== month) bad('source_period_mismatch', JSON.stringify(period));
  if ((plan.online || {}).sheet_name !== SHEET_NAME_PATTERN(month)) bad('plan_sheet_mismatch', String((plan.online || {}).sheet_name));
  const items = plan.items;
  if (!Array.isArray(items)) { bad('items_missing'); return errors; }
  const expected = new Set(BRIGADES.flatMap((b) => ITEMS.map((i) => `${b}|${i}`)));
  const pairs = items.map((i) => `${i.brigade}|${i.item}`);
  if (items.length !== expected.size || new Set(pairs).size !== pairs.length || !pairs.every((p) => expected.has(p))) bad('items_not_whitelist', `计划 ${items.length} 项，应恰为 ${expected.size} 项`);
  for (const key of ['online_cell', 'local_cell']) {
    const vals = items.map((i) => i[key]);
    if (new Set(vals).size !== vals.length || !vals.every((v) => typeof v === 'string' && /^[A-Z]{1,3}[1-9]\d{0,3}$/.test(v))) bad('cells_invalid', `${key} 缺失、格式不对或重复`);
  }
  for (const e of items) {
    if (!PLAN_ACTIONS.has(e.action)) bad('action_invalid', `${e.brigade}/${e.item}: ${e.action}`);
    if (e.action === 'write') {
      const v = e.local_value;
      if (typeof v !== 'string' || normText(v) === null || FORMULA_PREFIXES.some((p) => v.startsWith(p)) || v === e.baseline) bad('write_value_invalid', `${e.brigade}/${e.item}`);
    }
  }
  if (errors.length) return errors;
  if (plan.fingerprint !== planFingerprint(plan)) bad('fingerprint_mismatch', '计划被改过（坐标、值、年月、文档或动作）');
  if (freshCells) {
    const { mapping, blockers } = locate(freshCells);
    if (blockers.length) bad('mapping_drift', JSON.stringify(blockers));
    else if (!sameMapping(mapping, plan.mapping)) bad('mapping_drift', '在线布局与计划映射不同');
    else for (const e of items) {
      if (e.online_cell !== colName(mapping.brigade_cols[e.brigade]) + mapping.item_rows[e.item]) bad('online_cell_not_semantic', `${e.brigade}/${e.item}`);
    }
  }
  return errors;
}

export function checkBinding(plan, live) {
  // live: {url, title, activeSheet, sheets:[{index,name}]}；文档 URL、标题、工作表名与索引必须与计划一致
  const problems = [];
  const o = plan.online;
  if (normUrl(live.url) !== o.url_key) problems.push({ code: 'document_url_mismatch', detail: `${o.url_key} → ${normUrl(live.url)}` });
  if ((o.identity || {}).title !== live.title) problems.push({ code: 'document_title_mismatch', detail: String(live.title) });
  if (live.activeSheet !== o.sheet_name) problems.push({ code: 'worksheet_mismatch', detail: `${o.sheet_name} → ${live.activeSheet}` });
  const same = live.sheets.filter((s) => s.name === o.sheet_name);
  if (same.length !== 1 || same[0].index !== o.worksheet_id) problems.push({ code: 'worksheet_id_mismatch', detail: `${o.sheet_name}#${o.worksheet_id}` });
  return problems;
}

export function classify(plan, readCell) {
  // readCell(a1) -> {v, f, fill}；与 Python apply_plan 同口径：只有基线仍成立的 write 项才写；填充只给本地待补格
  const toWrite = []; const conflicts = []; const skipped = []; const fillOnly = [];
  for (const e of plan.items) {
    const cur = readCell(e.online_cell) || {};
    const now = normText(cur.v);
    const brief = { brigade: e.brigade, item: e.item, online_cell: e.online_cell };
    const needFill = e.local_pending_fill === true && cur.fill !== RED_BGR && !cur.f;
    if (e.action === 'write') {
      if (cur.f) skipped.push({ ...brief, action: 'skip_online_formula' });
      else if (now === e.local_value) { skipped.push({ ...brief, action: 'already_equal' }); if (needFill) fillOnly.push(e); }
      else if (now !== e.baseline) conflicts.push({ ...brief, baseline: e.baseline, now });
      else toWrite.push(e);
    } else {
      skipped.push({ ...brief, action: e.action });
      if (needFill && e.action === 'no_change' && now === e.local_value) fillOnly.push(e);
    }
  }
  return { toWrite, conflicts, skipped, fillOnly };
}

export async function readState(fsp, file) {
  // 不存在 -> null；存在但读不出/结构不对 -> 抛错（fail closed），不当作没有状态
  let text;
  try { text = await fsp.readFile(file, 'utf8'); } catch (e) { if (e && e.code === 'ENOENT') return null; throw Object.assign(new Error(`state_corrupt: ${file} 读不出：${e.message}`), { code: 'state_corrupt' }); }
  let st;
  try { st = JSON.parse(text); } catch (e) { throw Object.assign(new Error(`state_corrupt: ${file} 不是合法 JSON，保留原文件，不重跑`), { code: 'state_corrupt' }); }
  if (!st || typeof st !== 'object' || !['writing', 'needs_reconcile', 'done'].includes(st.status)) throw Object.assign(new Error('state_corrupt: 状态结构不对，保留原文件，不重跑'), { code: 'state_corrupt' });
  return st;
}

export function resolveSourcePath(p) {
  // 计划里的源路径在当前主机上的实际路径：Windows 上把 /mnt/d/x 转成 D:/x，Linux 上把 D:\x 转成 /mnt/d/x
  const win = /^([A-Za-z]):[\\/](.*)$/.exec(p);
  const mnt = /^\/mnt\/([A-Za-z])\/(.*)$/.exec(p);
  if (process.platform === 'win32' && mnt) return `${mnt[1].toUpperCase()}:/${mnt[2]}`;
  if (process.platform !== 'win32' && win) return `/mnt/${win[1].toLowerCase()}/${win[2].replace(/\\/g, '/')}`;
  return p;
}

export function diffFreeze(before, after, allowed) {
  // allowed: Set of "工作表名!A1"；返回所有变化，并分成白名单内/外
  const changes = [];
  const sheets = new Map(after.sheets.map((s) => [s.index, s]));
  for (const b of before.sheets) {
    const a = sheets.get(b.index);
    if (!a || a.name !== b.name) { changes.push({ sheet: b.name, cell: '*', before: b.name, after: a && a.name, kind: 'sheet_changed' }); continue; }
    const keys = new Set([...Object.keys(b.cells), ...Object.keys(a.cells)]);
    for (const k of keys) {
      const x = b.cells[k] || { v: null, f: null }; const y = a.cells[k] || { v: null, f: null };
      if (x.v !== y.v || x.f !== y.f) changes.push({ sheet: b.name, cell: k, before: x.v, after: y.v });
    }
  }
  return {
    changes,
    inside: changes.filter((c) => allowed.has(`${c.sheet}!${c.cell}`)),
    outside: changes.filter((c) => !allowed.has(`${c.sheet}!${c.cell}`)),
  };
}

// ---- 页面访问 ----

export async function detect(page) {
  const info = await page.evaluate(async () => {
    const app = typeof WPSOpenApi !== 'undefined' ? WPSOpenApi.Application : null;
    if (!app) return { supported: false, url: location.href, title: document.title };
    const count = await app.Worksheets.Count;
    const sheets = [];
    for (let i = 1; i <= count; i++) sheets.push({ index: i, name: await app.Worksheets.Item(i).Name });
    return { supported: true, url: location.href, title: document.title, activeSheet: await app.ActiveSheet.Name, sheets };
  });
  if (!info.supported) throw new Error('unsupported_page: 当前页面没有 WPSOpenApi.Application，本脚本不处理；保留页面，改用 CLI 或人工');
  return info;
}

async function readRegion(page, index, rows, cols) {
  // index=null 读当前活动表；返回 {name, usedRows, usedCols, cells:{A1:{v,f}}}
  return await page.evaluate(async ({ index, rows, cols, MAX_ROWS, MAX_COLS }) => {
    const app = WPSOpenApi.Application;
    const sh = index ? app.Worksheets.Item(index) : app.ActiveSheet;
    const usedRows = await sh.UsedRange.Rows.Count;
    const usedCols = await sh.UsedRange.Columns.Count;
    const r = Math.min(Math.max(rows || 0, usedRows), MAX_ROWS);
    const c = Math.min(Math.max(cols || 0, usedCols), MAX_COLS);
    const col = (n) => { let s = ''; while (n > 0) { const m = (n - 1) % 26; s = String.fromCharCode(65 + m) + s; n = Math.floor((n - 1) / 26); } return s; };
    const addr = `A1:${col(c)}${r}`;
    const values = await sh.Range(addr).Value2;
    const cells = {};
    for (let i = 0; i < r; i++) for (let j = 0; j < c; j++) {
      const v = values[i][j];
      const f = await sh.Range(col(j + 1) + (i + 1)).Formula; // 多格 Formula 只返回首格，须逐格读
      const sv = v === null || v === undefined ? '' : String(v);
      const isF = typeof f === 'string' && f.startsWith('=');
      if (sv !== '' || isF) cells[col(j + 1) + (i + 1)] = { v: sv, f: isF ? f : null };
    }
    return { name: await sh.Name, usedRows, usedCols, region: addr, cells };
  }, { index, rows, cols, MAX_ROWS, MAX_COLS });
}

export async function readSnapshot(page) {
  // 与 monthly_online_sheet.py 的 online-sheet-snapshot/1 同形
  const info = await detect(page);
  const region = await readRegion(page, null, 19, 14);
  return {
    schema: 'online-sheet-snapshot/1',
    identity: { url: info.url, title: info.title, reader: 'ego-wpsopenapi', captured_at: new Date().toISOString() },
    sheets: info.sheets.map((s) => ({ name: s.name, id: s.index })),
    sheet_name: info.activeSheet,
    worksheet_id: info.sheets.find((s) => s.name === info.activeSheet)?.index ?? null,
    used_range: { rowTo: region.usedRows, colTo: region.usedCols },
    cells: region.cells,
    protected: [],
  };
}

export async function freezeAllSheets(page) {
  // 所有工作表各自 A1:N19（及更大的已用区域，封顶 60×20）的值/公式
  const info = await detect(page);
  const sheets = [];
  for (const s of info.sheets) {
    const r = await readRegion(page, s.index, 19, 14);
    sheets.push({ index: s.index, name: s.name, usedRows: r.usedRows, usedCols: r.usedCols, region: r.region, cells: r.cells });
  }
  return { captured_at: new Date().toISOString(), url: info.url, title: info.title, activeSheet: info.activeSheet, sheets };
}


async function readTargetCells(page, items) {
  const addrs = items.map((e) => e.online_cell);
  return await page.evaluate(async ({ addrs }) => {
    const sh = WPSOpenApi.Application.ActiveSheet; const out = {};
    for (const a of addrs) {
      const rg = sh.Range(a);
      const f = await rg.Formula;
      out[a] = { v: await rg.Value2, f: typeof f === 'string' && f.startsWith('=') ? f : null, fill: await rg.Interior.Color };
    }
    return out;
  }, { addrs });
}

async function guardedCell(page, ctx, e, action) {
  // 每格动作前在页面里再核：文档 URL/标题、活动月份表、本格表头/项目名仍对、当前值/公式仍等于期望基线；通过才动。
  // action: 'write'（写文字，可带红底）或 'fill'（只补红底，要求值已等于本地值）。官方 API 无原子比较并写，这只是缩小窗口。
  return await page.evaluate(async ({ ctx, e, action, RED }) => {
    const norm = (v) => { if (v === null || v === undefined) return null; const t = String(v).replace(/\r\n/g, '\n').replace(/\r/g, '\n'); return t.trim() === '' ? null : t; };
    const normUrl = (u) => { try { const x = new URL(u); return `${x.protocol.replace(':', '').toLowerCase()}://${x.host.toLowerCase()}${x.pathname.replace(/\/+$/, '')}`; } catch { return null; } };
    const app = WPSOpenApi.Application; const sh = app.ActiveSheet;
    if (normUrl(location.href) !== ctx.urlKey) return { state: 'abort', reason: 'document_url_changed' };
    if (document.title !== ctx.title) return { state: 'abort', reason: 'document_title_changed' };
    if ((await sh.Name) !== ctx.sheetName) return { state: 'abort', reason: 'active_sheet_changed: ' + (await sh.Name) };
    const hv = norm(await sh.Range(e.headerAddr).Value2);
    if ((hv || '').trim() !== e.brigade) return { state: 'abort', reason: 'layout_changed: 表头 ' + e.headerAddr };
    let label = false;
    for (const a of e.labelAddrs) if ((norm(await sh.Range(a).Value2) || '').trim() === e.item) label = true;
    if (!label) return { state: 'abort', reason: 'layout_changed: 项目名 ' + e.item };
    const rg = sh.Range(e.online_cell);
    const f = await rg.Formula;
    if (typeof f === 'string' && f.startsWith('=')) return { state: 'conflict', now: String(f), reason: 'formula' };
    const now = norm(await rg.Value2);
    if (action === 'write') {
      if (now === e.local_value) return { state: 'already_equal' };
      if (now !== e.baseline) return { state: 'conflict', now };
      rg.Value2 = e.local_value;
    } else if (now !== e.local_value) return { state: 'conflict', now };
    if (e.local_pending_fill === true) { if ((await rg.Interior.Color) !== RED) rg.Interior.Color = RED; }
    if (ctx.settleMs > 0) await new Promise((r) => setTimeout(r, ctx.settleMs));
    return { state: 'wrote', v: await rg.Value2, fill: await rg.Interior.Color };
  }, { ctx, e, action, RED: RED_BGR });
}

async function saveWorkbook(page) {
  return await page.evaluate(async () => {
    try { await WPSOpenApi.Application.ActiveWorkbook.Save(); return 'save_called'; } catch (e) { return 'save_error: ' + String(e && e.message || e).slice(0, 100); }
  });
}

async function sha256File(fsp, path) {
  return createHash('sha256').update(await fsp.readFile(path)).digest('hex');
}

function guardContext(plan, e) {
  const m = plan.mapping; const first = Math.min(...Object.values(m.brigade_cols));
  const labelAddrs = []; for (let c = 1; c < first; c++) labelAddrs.push(colName(c) + m.item_rows[e.item]);
  return { ...e, headerAddr: colName(m.brigade_cols[e.brigade]) + m.header_row, labelAddrs };
}

async function bindLive(page, plan) {
  const info = await detect(page);
  const problems = checkBinding(plan, info);
  if (problems.length) throw Object.assign(new Error('binding_mismatch: ' + JSON.stringify(problems)), { code: 'binding_mismatch' });
  return info;
}

// ---- 入口 ----

export async function applyPlan(page, planPath, outDir, options = {}) {
  // options: { year, month }（必填，与计划和本地源年月核对）, onlyCells（首次试写）
  const fs = await import('node:fs/promises');
  const plan = JSON.parse(await fs.readFile(planPath, 'utf8'));
  const only = options.onlyCells ? new Set(options.onlyCells) : null;
  const w = async (name, data) => fs.writeFile(`${outDir}/${name}`, JSON.stringify(data, null, 2), 'utf8');
  const stateFile = `${outDir}/apply-state.json`;
  const state = await readState(fs, stateFile);
  if (state && state.status !== 'done') throw Object.assign(new Error('unknown_prior_write: 先 reconcile，不重放'), { code: 'unknown_prior_write' });
  if (plan.status !== 'ok') throw new Error('plan_blocked');
  const errs = validatePlan(plan, options.year, options.month);
  if (errs.length) throw Object.assign(new Error('plan_invalid: ' + JSON.stringify(errs)), { code: 'plan_invalid' });
  let srcSha;
  try { srcSha = await sha256File(fs, resolveSourcePath(plan.source.path)); } catch (e) { throw Object.assign(new Error('source_unreadable: ' + e.message), { code: 'source_unreadable' }); }
  if (srcSha !== plan.source.sha256) throw Object.assign(new Error('source_changed: 本地源哈希与计划不同，重新 plan'), { code: 'source_changed' });

  const info = await bindLive(page, plan);
  const beforeAll = await freezeAllSheets(page);
  await w('before-all-sheets.apply.json', beforeAll);
  const active = beforeAll.sheets.find((s) => s.name === info.activeSheet);
  const lay = validatePlan(plan, options.year, options.month, active.cells);
  if (lay.length) throw Object.assign(new Error('mapping_drift: ' + JSON.stringify(lay)), { code: 'mapping_drift' });

  const items = plan.items;
  const current = await readTargetCells(page, items);
  const cls = classify(plan, (a) => current[a]);
  const todo = cls.toWrite.filter((e) => !only || only.has(e.online_cell));
  const fills = cls.fillOnly.filter((e) => !only || only.has(e.online_cell));
  await w('recovery.json', {
    values: Object.fromEntries(todo.map((e) => [e.online_cell, e.baseline])),
    fills: Object.fromEntries([...todo, ...fills].filter((e) => e.local_pending_fill).map((e) => [e.online_cell, current[e.online_cell].fill])),
    note: '按格写回写前值即恢复；空值格需人工清空；填充原值为 BGR 整数（白底 16777215）',
  });
  const conflicts = [...cls.conflicts];
  const result = { started_at: new Date().toISOString(), plan_fingerprint: plan.fingerprint, conflicts, skipped: cls.skipped, written: [], verified: [], mismatch: [], write_error: null, aborted: null, fills: [], fill_failed: [],
    limits: '官方 JSAPI 无原子比较并写；每格动作前近时复核只能缩小并发窗口，不能绝对消除极短竞争' };
  const ctx = { urlKey: plan.online.url_key, title: plan.online.identity.title, sheetName: plan.online.sheet_name, settleMs: options.settleMs ?? 300 };
  const binding = { plan_fingerprint: plan.fingerprint, url_key: plan.online.url_key, sheet_name: plan.online.sheet_name, worksheet_id: plan.online.worksheet_id, source_sha256: plan.source.sha256, year: options.year, month: options.month };
  const pending = todo.map((e) => e.online_cell);
  const fillCells = [...todo, ...fills].filter((e) => e.local_pending_fill).map((e) => e.online_cell);
  if (pending.length || fillCells.length) await fs.writeFile(stateFile, JSON.stringify({ ...binding, status: 'writing', cells: pending, fill_cells: fillCells, at: new Date().toISOString() }), 'utf8');
  try {
    for (const e of todo) {
      const r = await guardedCell(page, ctx, guardContext(plan, e), 'write');
      const b = { online_cell: e.online_cell };
      if (r.state === 'abort') { result.aborted = r.reason; break; }
      if (r.state === 'conflict') { conflicts.push({ brigade: e.brigade, item: e.item, ...b, baseline: e.baseline, now: r.now }); continue; }
      if (r.state === 'already_equal') { result.skipped.push({ ...b, action: 'already_equal' }); continue; }
      result.written.push(b);
      if (normText(r.v) === e.local_value) result.verified.push(b); else { result.mismatch.push({ ...b, got: normText(r.v) }); break; }
      if (e.local_pending_fill) { result.fills.push({ ...b, fill: r.fill }); if (r.fill !== RED_BGR) result.fill_failed.push(b); }
    }
    if (!result.aborted && !result.mismatch.length) for (const e of fills) {
      const r = await guardedCell(page, ctx, guardContext(plan, e), 'fill');
      const b = { online_cell: e.online_cell };
      if (r.state === 'abort') { result.aborted = r.reason; break; }
      if (r.state === 'conflict') { conflicts.push({ brigade: e.brigade, item: e.item, ...b, baseline: e.local_value, now: r.now }); continue; }
      result.fills.push({ ...b, fill: r.fill });
      if (r.fill !== RED_BGR) result.fill_failed.push(b);
    }
  } catch (err) {
    result.write_error = String(err && err.message || err); // 结果未知：只回读，不重试
  }
  result.save = await saveWorkbook(page);
  const afterAll = await freezeAllSheets(page);
  await w('after-all-sheets.apply.json', afterAll);
  const allowed = new Set(result.written.map((x) => `${plan.online.sheet_name}!${x.online_cell}`));
  const d = diffFreeze(beforeAll, afterAll, allowed);
  result.outside_target_changes = d.outside;
  result.inside_target_changes = d.inside.length;
  const saveOk = result.save === 'save_called';
  result.status = !result.write_error && !result.mismatch.length && !result.aborted && !result.fill_failed.length && saveOk ? 'done' : 'needs_reconcile';
  result.finished_at = new Date().toISOString();
  await w('result.apply.json', result);
  if (pending.length || fillCells.length) await fs.writeFile(stateFile, JSON.stringify({ ...binding, status: result.status, cells: pending, fill_cells: fillCells, at: new Date().toISOString() }), 'utf8');
  return result;
}

export async function reconcileApply(page, planPath, outDir, options = {}) {
  // 只读对账：绑定原 apply-state 的计划指纹、文档/工作表和真实待确认格；条件不全一律抛错，原状态不动
  const fs = await import('node:fs/promises');
  const plan = JSON.parse(await fs.readFile(planPath, 'utf8'));
  const stateFile = `${outDir}/apply-state.json`;
  const state = await readState(fs, stateFile);
  if (!state) throw Object.assign(new Error('no_apply_state'), { code: 'no_apply_state' });
  if (state.status === 'done') throw Object.assign(new Error('nothing_to_reconcile'), { code: 'nothing_to_reconcile' });
  if (plan.fingerprint !== planFingerprint(plan) || state.plan_fingerprint !== plan.fingerprint) throw Object.assign(new Error('reconcile_plan_mismatch'), { code: 'reconcile_plan_mismatch' });
  const byCell = new Map(plan.items.filter((e) => e.action === 'write' || e.local_pending_fill).map((e) => [e.online_cell, e]));
  const cells = Array.isArray(state.cells) ? state.cells : []; const fillCells = Array.isArray(state.fill_cells) ? state.fill_cells : [];
  if ((!cells.length && !fillCells.length) || [...cells, ...fillCells].some((c) => !byCell.has(c))) throw Object.assign(new Error('reconcile_no_pending_cells'), { code: 'reconcile_no_pending_cells' });
  if (state.url_key !== plan.online.url_key || state.sheet_name !== plan.online.sheet_name || state.worksheet_id !== plan.online.worksheet_id) throw Object.assign(new Error('reconcile_document_mismatch'), { code: 'reconcile_document_mismatch' });
  await bindLive(page, plan);
  const cur = await readTargetCells(page, [...new Set([...cells, ...fillCells])].map((c) => byCell.get(c)));
  const rows = [...new Set([...cells, ...fillCells])].map((c) => {
    const e = byCell.get(c);
    return { online_cell: c, equals_local: normText(cur[c].v) === e.local_value, equals_baseline: normText(cur[c].v) === e.baseline, fill_ok: !fillCells.includes(c) || cur[c].fill === RED_BGR };
  });
  const report = { checked_at: new Date().toISOString(), plan_fingerprint: plan.fingerprint, cells: rows, all_in_place: rows.every((r) => r.equals_local && r.fill_ok) };
  report.state_cleared = report.all_in_place;
  await fs.writeFile(`${outDir}/reconcile.json`, JSON.stringify(report, null, 2), 'utf8');
  if (report.all_in_place) await fs.writeFile(stateFile, JSON.stringify({ ...state, status: 'done', via: 'reconcile', at: new Date().toISOString() }), 'utf8');
  return report;
}

export async function verifyFills(page, planPath) {
  const fs = await import('node:fs/promises');
  const plan = JSON.parse(await fs.readFile(planPath, 'utf8'));
  await detect(page);
  const cur = await readTargetCells(page, plan.items);
  return plan.items.map((e) => ({ online_cell: e.online_cell, local_pending_fill: e.local_pending_fill, fill: cur[e.online_cell].fill, red: cur[e.online_cell].fill === RED_BGR }));
}
