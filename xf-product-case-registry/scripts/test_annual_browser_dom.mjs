import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

// Execute the actual embedded reader with synthetic DOM only; no browser exists.
const source = fs.readFileSync(new URL('./annual_browser.py', import.meta.url), 'utf8');
const reader = source.match(/LIST_READER = r"""([\s\S]*?)"""/)[1];
const shown = {offsetWidth: 500, offsetHeight: 100};

function fixture({hidden = false, opacity = '0', oldRows = false, wrongDom = false} = {}) {
  let queries = 0;
  const model = Array.from({length: oldRows ? 50 : 45}, (_, i) => ({
    ROW_ID: (oldRows ? 0 : 350) + i + 1, ID: String(10000 + i),
    DWMC: `合成单位${i}`, _XID: `fixture-${i}`,
  }));
  const date = {
    __vue__: {value: ['2026-01-01', '2026-12-31'], $parent: {$parent: {content: '创建时间'}}},
    querySelectorAll: () => [{value: '2026-01-01'}, {value: '2026-12-31'}],
  };
  const form = {...shown, innerText: '请选择执法单位',
    querySelector: () => date,
    querySelectorAll: selector => selector === '.el-date-editor' ? [date] : [],
  };
  const main = {
    querySelector: () => ({cells: [{innerText: '单位名称'}]}),
    querySelectorAll: () => model.slice(0, 5).map((row, i) => ({
      cells: [{innerText: row.DWMC}],
      getAttribute: () => wrongDom && i === 0 ? 'stale-xid' : row._XID,
    })),
  };
  const table = {...shown, __vue__: {tableFullData: model}, querySelector: () => main};
  const pager = {...shown, querySelector: selector => ({
    '.el-pagination__total': {innerText: '共 395 条'},
    'input[readonly]': {value: '50条/页'},
    '.el-pagination__editor input': {value: '8'},
  })[selector]};
  const mask = {...shown, style: {opacity, visibility: 'visible'}};
  const document = {visibilityState: hidden ? 'hidden' : 'visible', querySelectorAll(selector) {
    queries++;
    return {'#nprogress,.el-loading-mask': [mask], '.avue-view form': [form],
      '.elx-table': [table], '.el-pagination': [pager]}[selector] || [];
  }};
  const value = vm.runInNewContext(`(${reader})()`, {
    document, location: {origin: 'https://example.test', pathname: '/',
      hash: '#/xfjd/fixture?name=日常监督检查'}, URLSearchParams,
    getComputedStyle: e => e.style,
  });
  return {value, queries};
}

test('a transparent leaving mask does not block committed rows', () => {
  const {value} = fixture();
  assert.equal(value.ready, true);
  assert.equal(value.pageNumber, 8);
  assert.equal(value.items.length, 45);
  assert.equal(value.items[0].sourceOrder, 351);
});

test('a transparent mask cannot approve the old page model', () => {
  const {value} = fixture({oldRows: true});
  assert.equal(value.ready, false);
  assert.equal(value.reason, 'PAGER_CHANGED_BUT_ROWS_OLD');
});

test('an old DOM row with the same unit name cannot approve the new model', () => {
  const {value} = fixture({wrongDom: true});
  assert.equal(value.ready, false);
  assert.equal(value.reason, 'VISIBLE_ROWS_MISMATCH');
});

test('an opaque loading mask still blocks collection', () => {
  const {value} = fixture({opacity: '1'});
  assert.equal(value.ready, false);
  assert.equal(value.reason, 'BUSY');
});

test('a hidden document only reports waiting without reading the table', () => {
  const {value, queries} = fixture({hidden: true});
  assert.equal(value.ready, false);
  assert.equal(value.reason, 'SOURCE_DOCUMENT_HIDDEN');
  assert.equal(queries, 0);
});
