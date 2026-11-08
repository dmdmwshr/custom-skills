import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

// Execute the actual embedded reader with synthetic DOM only; no browser exists.
const source = fs.readFileSync(new URL('./annual_browser.py', import.meta.url), 'utf8');
const visibleReader = source.match(/\nLIST_READER = r"""([\s\S]*?)"""/)[1];
const shown = {offsetWidth: 500, offsetHeight: 100};

// ---- 默认读取器：只读可见 DOM(访问 __vue__ 立即失败) ----
test('default list reader contains no internal-state access', () => {
  assert.doesNotMatch(visibleReader, /__vue__|tableFullData|\$parent|store\?\.states/);
  // 整个活动模块不再含任何内部状态读取器或旧开关(环境变量也无法恢复)
  assert.doesNotMatch(source, /__vue__|tableFullData|\$parent|store\??\.states|LEGACY_|legacy_internal_state|XF_ALLOW_LEGACY/);
});

function visibleFixture({rowsShown = 45, total = 395, page = 8, noName = false, mask = null, hidden = false} = {}, probe = {}) {
  const trap = o => Object.defineProperty(o, '__vue__', {get() { throw new Error('INTERNAL_STATE_READ'); }, enumerable: false});
  const headers = noName ? ['其他', '状态'] : ['单位名称', '状态', '检查单位'];
  const rows = Array.from({length: rowsShown}, (_, i) => ({cells: headers.map(h => ({innerText: h === '单位名称' ? `合成单位${i}` : `${h}${i}`}))}));
  const main = {
    querySelector: () => ({cells: headers.map(h => ({innerText: h}))}),
    querySelectorAll: () => rows,
  };
  const inputs = [{value: '2026-01-01'}, {value: '2026-12-31'}];
  const date = trap({
    closest: () => ({querySelector: () => ({innerText: '创建时间'})}),
    querySelectorAll: () => inputs,
  });
  const form = trap({...shown, innerText: '请选择执法单位',
    querySelector: () => date,
    querySelectorAll: selector => selector === '.el-date-editor' ? [date] : [],
  });
  const table = trap({...shown, querySelector: () => main});
  const pager = trap({...shown, querySelector: selector => ({
    '.el-pagination__total': {innerText: `共 ${total} 条`},
    'input[readonly]': {value: '50条/页'},
    '.el-pagination__editor input': {value: String(page)},
  })[selector]});
  const document = {visibilityState: hidden ? 'hidden' : 'visible', querySelectorAll: selector => (probe.queries = (probe.queries || 0) + 1, {
    '#nprogress,.el-loading-mask': mask ? [mask] : [], '.avue-view form': [form],
    '.elx-table': [table], '.el-pagination': [pager]}[selector] || [])};
  return vm.runInNewContext(`(${visibleReader})()`, {
    document, location: {origin: 'https://example.test', pathname: '/', hash: '#/xfjd/fixture?name=日常监督检查'},
    URLSearchParams, getComputedStyle: e => e.style || {visibility: 'visible', opacity: '0'},
  });
}

test('visible list reader returns rows without any RWID and without touching internal state', () => {
  const value = visibleFixture();
  assert.equal(value.ready, true);
  assert.equal(value.visibleOnly, true);
  assert.equal(value.items.length, 45);
  assert.ok(value.items.every(item => item.rwid === null));
  assert.equal(value.items[0].sourceOrder, 351);
  assert.equal(value.dateFieldLabel, '创建时间');
  assert.equal(JSON.stringify(value.dates), JSON.stringify(['2026-01-01', '2026-12-31']));
});

test('visible list reader blocks on row count that disagrees with the visible pager', () => {
  assert.equal(visibleFixture({rowsShown: 40}).reason, 'VISIBLE_ROWS_MISMATCH');
  assert.equal(visibleFixture({noName: true}).reason, 'VISIBLE_ROWS_MISMATCH');
});

test('visible list reader: a transparent leaving mask does not block committed rows', () => {
  const mask = {...shown, style: {opacity: '0', visibility: 'visible'}};
  const value = visibleFixture({mask});
  assert.equal(value.ready, true);
  assert.equal(value.items.length, 45);
});

test('visible list reader: a hidden document only reports waiting without reading the table', () => {
  const probe = {};
  const value = visibleFixture({hidden: true}, probe);
  assert.equal(value.ready, false);
  assert.equal(value.reason, 'SOURCE_DOCUMENT_HIDDEN');
  assert.equal(probe.queries || 0, 0);
});

test('visible list reader blocks on an opaque loading mask', () => {
  const mask = {...shown, style: {opacity: '1', visibility: 'visible'}};
  assert.equal(visibleFixture({mask}).reason, 'BUSY');
});

// ---- 打包面板选择读取器(共享 SELECTION_BODY，位于 playwright_cli_download.cjs；Python 与点击前控制器共用) ----
const pkgSource = fs.readFileSync(new URL('./package_source_case.py', import.meta.url), 'utf8');
const controller = fs.readFileSync(new URL('./playwright_cli_download.cjs', import.meta.url), 'utf8');
const selectionBody = controller.split('// SELECTION_BODY_START\nconst SELECTION_BODY = String.raw`')[1].split('`;\n// SELECTION_BODY_END')[0];
const captureSource = fs.readFileSync(new URL('./browser_source_capture.mjs', import.meta.url), 'utf8');
const guards = captureSource.split('// VISIBLE_GUARDS_START\n')[1].split('// VISIBLE_GUARDS_END')[0];
assert.doesNotMatch(pkgSource + controller, /__vue__|tableFullData|store\??\.states/);
const selectionReader = `function readPackageSelection(r){${guards}${selectionBody}}`;
const PN = '32002201C202600062';
const RW = '32100934444';

function panel({rows, org = '大队甲', unit = '单位甲', pn = PN, rwid = RW, mask = false, overflow = false, noWrap = false}) {
  const trap = o => Object.defineProperty(o, '__vue__', {get() { throw new Error('INTERNAL_STATE_READ'); }, enumerable: false});
  const trs = rows.map(r => ({...shown, className: `el-table__row el-table__row--level-${r.level ?? 0}`,
    cells: [{innerText: r.title}],
    querySelector: sel => sel === 'input[type="checkbox"]'
      ? (r.box === false ? null : {value: r.id ?? 'x', checked: !!r.checked, disabled: !!r.disabled})
      : sel === '.el-table__expand-icon' ? (r.icon === false ? null : {classList: {contains: c => !!r.expanded && c === 'el-table__expand-icon--expanded'}})
      : sel === '.el-table__placeholder' ? (r.placeholder ? {} : null) : null}));
  const wrap = noWrap ? null : {scrollHeight: overflow ? 900 : 100, clientHeight: 100};
  const table = trap({...shown,
    querySelector: sel => sel === '.el-table__header-wrapper' ? {innerText: '名称 审批日期'} : sel === '.el-table__body-wrapper' ? wrap : null,
    querySelectorAll: sel => sel === '.el-table__body-wrapper tr.el-table__row' ? trs : []});
  const span = (label, value) => ({textContent: label, parentElement: {nextElementSibling: {innerText: value}}});
  const maskEl = {...shown, style: {opacity: '1', visibility: 'visible'}};
  const document = {visibilityState: 'visible', body: {innerText: `项目编号：${pn}`}, querySelectorAll: sel => ({
    '.el-table': [table], 'main.el-main span': [span('单位名称', unit), span('消防管辖', org)],
    '[class*=loading],#nprogress,.el-loading-mask': mask ? [maskEl] : []}[sel] || [])};
  return request => vm.runInNewContext(`(${selectionReader})(${JSON.stringify(request)})`, {
    document, location: {hash: `#/xfjd/projectDetail?RWID=${rwid}`}, URLSearchParams,
    getComputedStyle: e => e.style || {visibility: 'visible', opacity: '0'}});
}
const reqBase = {rwid: RW, projectNo: PN, unitName: '单位甲', org: '大队甲', expectedLeafIds: null};
const ownRoot = {title: `[产]日常监督-${PN}-单位甲`, level: 1, expanded: true, box: false};
const leaf = (id, extra = {}) => ({id, title: '文书' + id, level: 2, placeholder: true, icon: false, ...extra});

test('selection reader accepts exactly the proven-complete own leaf set (coverage proof, no self-count)', () => {
  const before = panel({rows: [ownRoot, leaf('11'), leaf('12')]})(reqBase);
  assert.equal(before.ready, true);
  assert.equal(before.leaves.length, 2);
  assert.equal(before.allCurrentSelected, false);
  assert.equal(before.canSelectAll, true);
  const after = panel({rows: [ownRoot, leaf('11', {checked: true}), leaf('12', {checked: true})]})({...reqBase, expectedLeafIds: ['11', '12']});
  assert.equal(after.ready, true);
  assert.equal(after.allCurrentSelected, true);
});

test('selection reader: unselected foreign leaves keep the own set exact but forbid select-all', () => {
  const rows = [ownRoot, leaf('11'), leaf('12'), {title: '[监]日常监督抽查-32002201J202600052-单位甲', level: 1, expanded: true, box: false}, leaf('201')];
  const value = panel({rows})(reqBase);
  assert.equal(value.ready, true);
  assert.equal(value.canSelectAll, false);
  assert.equal(value.foreignLeafCount, 1);
});

test('selection reader: a foreign penalty root with a numeric checkbox value never lends its leaves to this case', () => {
  const rows = [ownRoot, leaf('11', {checked: true}), leaf('12', {checked: true}),
    {id: '99', title: '[罚]一般-32002201X202600075-单位甲', level: 1, expanded: true, checked: true}, leaf('22', {checked: true})];
  const value = panel({rows})(reqBase);
  assert.equal(value.ready, false);
  assert.equal(value.reason, 'SELECTION_MISMATCH');
  assert.ok(value.foreignSelectedCount >= 2);
});

test('selection reader blocks without complete-leaf evidence: collapsed, zero icons, scroll overflow/virtualisation, no container, mismatched declared total', () => {
  const rows = [ownRoot, leaf('11'), leaf('12')];
  assert.ok(panel({rows: [{...ownRoot, expanded: false}, leaf('11'), leaf('12')]})(reqBase).blockReasons.includes('COLLAPSED_NODES_PRESENT'));
  const flat = [ownRoot, leaf('11', {placeholder: false}), leaf('12', {placeholder: false})];
  assert.ok(panel({rows: flat})(reqBase).blockReasons.includes('EXPAND_STATE_UNKNOWN'));
  assert.ok(panel({rows, overflow: true})(reqBase).blockReasons.includes('NO_DECLARED_LEAF_TOTAL'));
  assert.ok(panel({rows, noWrap: true})(reqBase).blockReasons.includes('NO_DECLARED_LEAF_TOTAL'));
  // 自己数出来的叶数不是声明数：未标记“页面可见声明”的数字只能作交叉核对，且不一致即阻断
  assert.ok(panel({rows})({...reqBase, crossCheckLeafCount: 3}).blockReasons.includes('CROSS_CHECK_MISMATCH'));
  // 被声明为页面可见总数时：与本案叶数不一致阻断；一致且无覆盖证明时放行(声明证据路径)
  assert.ok(panel({rows})({...reqBase, declaredLeafCount: 3, declaredSourceVisible: true}).blockReasons.includes('DECLARED_TOTAL_MISMATCH'));
  assert.equal(panel({rows, overflow: true})({...reqBase, declaredLeafCount: 2, declaredSourceVisible: true}).ready, true);
  assert.ok(panel({rows, overflow: true})({...reqBase, declaredLeafCount: 2, declaredSourceVisible: false}).blockReasons.includes('NO_DECLARED_LEAF_TOTAL'));
});

test('selection reader blocks incomplete identity expectations, live mismatches and busy pages', () => {
  const rows = [ownRoot, leaf('11'), leaf('12')];
  const noOrg = panel({rows})({...reqBase, org: null});
  assert.equal(noOrg.reason, 'IDENTITY_BLOCKED');
  assert.ok(noOrg.identityMiss.includes('MISSING_EXPECTED_ORG'));
  assert.equal(panel({rows, org: '大队乙'})(reqBase).reason, 'IDENTITY_BLOCKED');
  assert.equal(panel({rows, pn: '32002201C202600064'})(reqBase).reason, 'IDENTITY_BLOCKED');
  assert.equal(panel({rows, rwid: '32100000001'})(reqBase).reason, 'IDENTITY_BLOCKED');
  assert.equal(panel({rows, unit: '另一家单位'})(reqBase).reason, 'IDENTITY_BLOCKED');
  assert.equal(panel({rows, mask: true})(reqBase).reason, 'SOURCE_LOADING');
});

test('selection reader: ambiguous root-like rows and a same-number penalty root make the state UNKNOWN', () => {
  const ambiguous = [ownRoot, leaf('11'), {id: '99', title: '一般-32002201X202600075-单位甲', level: 1, expanded: true}, leaf('12', {checked: true})];
  assert.ok(panel({rows: ambiguous})(reqBase).blockReasons.includes('UNKNOWN_STATE'));
  const samePenalty = [{title: `[罚]一般-${PN}-单位甲`, level: 1, expanded: true, box: false}, leaf('11'), leaf('12')];
  const value = panel({rows: samePenalty})(reqBase);
  assert.equal(value.ready, false);
  assert.ok(value.blockReasons.includes('UNKNOWN_STATE'));
});
