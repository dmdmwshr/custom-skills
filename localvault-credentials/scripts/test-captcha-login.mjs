#!/usr/bin/env node
// captcha-login.mjs / captcha-ocr.py 的合成回归：不启动浏览器、不读凭据、不连网络、不登录。
// 必填环境：CAPTCHA_TEST_DIR（仓外可写目录）。可选：CAPTCHA_TEST_PYTHON（含 Pillow+rapidocr 的 Windows Python，
// 缺省则离线 OCR 项记为 not-covered）、CAPTCHA_TEST_REPORT（报告 JSON 路径）、CAPTCHA_TEST_REPO（检查既有文件未变）。
// 用法：CAPTCHA_TEST_DIR=<仓外目录> node scripts/test-captcha-login.mjs
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SKILL_ROOT = path.dirname(HERE);
const RUNNER = path.join(HERE, 'captcha-login.mjs');
const OCR_WORKER = path.join(HERE, 'captcha-ocr.py');
const TEST_DIR = process.env.CAPTCHA_TEST_DIR ? path.resolve(process.env.CAPTCHA_TEST_DIR) : '';
const REAL_PY = process.env.CAPTCHA_TEST_PYTHON || '';
const REPO = process.env.CAPTCHA_TEST_REPO || '';
const norm = (p) => path.resolve(p).replace(/\\/g, '/').toLowerCase();
if (!TEST_DIR || norm(TEST_DIR).startsWith(norm(SKILL_ROOT)) || (REPO && norm(TEST_DIR).startsWith(norm(REPO)))) {
  console.error('CAPTCHA_TEST_DIR 必须设置为仓外目录');
  process.exit(2);
}
const RUN = `run-${Date.now()}`;
const ROOT = path.join(TEST_DIR, RUN);
const STATE_ROOT = path.join(ROOT, 'state');
fs.mkdirSync(STATE_ROOT, { recursive: true });

// 合成凭据（仅用于断言“不落盘/不回显”，不是任何真实账号）
const USER = 'synthetic-user-Qq9';
const PASS = 'Pw!SyntheticSecret-8143';
const CODE = '6039';
const sha = (s) => crypto.createHash('sha256').update(s).digest('hex');
const LOGIN = 'http://test.invalid:11888/#/login';
const BUSINESS = 'http://test.invalid:11888/#/wel/index';
const ACCOUNT = '钱奡';
const MARKER = '产品监督';
const SEL = {
  account: 'input[placeholder="请输入账号"]', password: 'input[type="password"]', captcha: 'input[placeholder="请输入验证码"]',
  captchaImg: 'img.login-code-img', submit: 'button.login-submit', messages: '.el-message,.el-notification,[role=alert],.el-form-item__error',
};
const BIND = ['--target-url', LOGIN, '--expected-account', ACCOUNT, '--business-marker', MARKER, '--account-selector', '.account-name', '--business-selector', '.business-marker', '--secret-name', 'synthetic-secret-name', '--max-submissions', '5'];

// ---- 测试替身：假 Ego（执行真实生成的脚本，对假页面）与假 OCR ----
const FAKE_EGO = `
import fs from 'node:fs'; import path from 'node:path'; import crypto from 'node:crypto';
const dir = process.env.FAKE_EGO_DIR; const sc = JSON.parse(fs.readFileSync(path.join(dir, 'scenario.json'), 'utf8'));
const pagePath = path.join(dir, 'page.json');
// 真实 Ego 的 console 输出落在 stderr：用 channel 模拟 stdout/stderr/双路/冲突，prefixMarkers 模拟标记不在行首。
{
  const orig = console.log.bind(console);
  const pre = sc.prefixMarkers ?? '';
  if (sc.channel === 'stderr') console.log = (...a) => console.error(pre + a.join(' '));
  else if (sc.channel === 'both') console.log = (...a) => { orig(pre + a.join(' ')); console.error(pre + a.join(' ')); };
  else if (sc.channel === 'conflict') console.log = (...a) => { orig(a.join(' ')); console.error(a.join(' ').replace('"ok":true', '"ok":false')); };
  else if (pre) console.log = (...a) => orig(pre + a.join(' '));
  if (sc.dropResultInSubmit) { const base = console.log; console.log = (...a) => { if (globalThis.__dropR && String(a[0]).startsWith('@@R@@')) return; base(...a); }; }
  if (sc.noiseStderr) {
    console.error('warning: plain noise line');
    console.error('[log] @@R@@{"ok":true,"urlOk":true}');
    console.error('@@R@@not-json');
    console.error('@@R@@[1,2]');
    console.error('echo of script secret ' + process.env.FAKE_PASS);
  }
  if (sc.noiseHuge) {
    for (let i = 0; i < 3000; i += 1) console.error('noise line '.repeat(100));
    process.stderr.write('x'.repeat(4500000) + '\\n');
  }
}
const st = fs.existsSync(pagePath) ? JSON.parse(fs.readFileSync(pagePath, 'utf8')) : { url: sc.startUrl, acct: false, pw: false, code: '', imgIdx: 0, imgSrc: 'data:image/png;base64,' + (sc.images ? sc.images[0] : 'FAKEIMG0'), submits: 0, loggedIn: false, messages: [], fills: {} };
const save = () => fs.writeFileSync(pagePath, JSON.stringify(st));
const sha = (s) => crypto.createHash('sha256').update(s).digest('hex');
let script = ''; for await (const c of process.stdin) script += c;
const step = /p\\.reload/.test(script) ? 'verify' : /P\\.sel\\.submit\\)/.test(script) ? 'submit' : /P\\.sel\\.captchaImg\\)/.test(script) ? 'refresh' : /P\\.sel\\.account, P\\.cred/.test(script) ? 'fill' : 'preflight';
globalThis.__dropR = step === 'submit';
fs.appendFileSync(path.join(dir, 'calls.jsonl'), JSON.stringify({ step, hasPassword: script.includes(process.env.FAKE_PASS), hasCode: script.includes(process.env.FAKE_CODE) }) + '\\n');
if (step === 'submit' && !sc.toastsPersist) { st.messages = []; save(); } // 上一次提交的 toast 在两步之间已过期
const S = ${JSON.stringify(SEL)};
globalThis.getComputedStyle = (el) => ({ display: el && el.hidden ? 'none' : 'block', visibility: 'visible', opacity: '1' });
const controlNodes = (selector) => {
  if (!st.loggedIn) return [];
  const list = selector === '.account-name' ? (sc.accountNodes ?? [{ text: sc.accountText ?? ${JSON.stringify(ACCOUNT)} }]) : (sc.markerNodes ?? [{ text: ${JSON.stringify(MARKER)} }]);
  return list.map((n) => ({ innerText: n.text, textContent: n.text, hidden: !!n.hidden, getClientRects() { return n.hidden ? [] : [{}]; }, checkVisibility() { return !n.hidden; } }));
};
const documentFake = {
  querySelector: (sel) => sel === S.account ? { value: st.acct ? 'x' : '' } : sel === S.password ? (st.loginPageVisible !== false && !st.loggedIn ? { value: st.pw ? 'x' : '' } : null) : sel === S.captchaImg ? { src: st.imgSrc } : null,
  querySelectorAll: (sel) => (sel === '.account-name' || sel === '.business-marker') ? controlNodes(sel) : [...(sc.staleMessages ?? []), ...st.messages].map((m) => ({ innerText: m })),
  body: { get innerText() { return st.loggedIn ? (sc.bodyText ?? ((sc.accountText ?? ${JSON.stringify(ACCOUNT)}) + ' ' + ${JSON.stringify(MARKER)})) : ''; } },
};
const locationFake = { get href() { return st.url; } };
const page = {
  async url() { return st.url; },
  async fill(sel, v) {
    if (sc.failFillCaptcha && sel === S.captcha) throw new Error('synthetic fill failure');
    if (sel === S.account) { st.acct = true; st.fills.userOk = sha(v) === process.env.FAKE_USER_SHA; }
    else if (sel === S.password) { st.pw = true; st.fills.passOk = sha(v) === process.env.FAKE_PASS_SHA; }
    else if (sel === S.captcha) { st.code = v; st.fills.codeOk = v === sc.expectCode; }
    save();
  },
  async click(sel) {
    if (sel === S.captchaImg) { st.imgIdx += 1; const imgs = sc.images; st.imgSrc = 'data:image/png;base64,' + (imgs ? imgs[st.imgIdx % imgs.length] : 'FAKEIMG' + st.imgIdx); if (sc.refreshBroken) st.imgSrc = 'data:image/png;base64,' + (imgs ? imgs[0] : 'FAKEIMG0'); save(); return; }
    if (sel !== S.submit) return;
    const outcome = (sc.submits ?? [])[st.submits] ?? 'captcha_error';
    st.submits += 1; st.messages = []; save();
    if (sc.submitDelayMs) await new Promise((r) => setTimeout(r, sc.submitDelayMs));
    if (outcome === 'ok') { st.loggedIn = true; st.url = sc.okUrl ?? ${JSON.stringify(BUSINESS)}; }
    else if (outcome === 'captcha_error') st.messages = ['验证码错误'];
    else if (outcome === 'credential_error') st.messages = ['用户名或密码错误'];
    else if (outcome === 'restricted') st.messages = ['登录失败次数过多，请稍后再试'];
    else if (outcome === 'other_toast') st.messages = ['验证码错误', '系统繁忙'];
    else if (outcome === 'unknown') st.messages = [];
    else if (outcome === 'hang') { save(); await new Promise(() => {}); }
    else if (outcome === 'crash-parent') { save(); process.kill(process.ppid, 'SIGKILL'); process.exit(0); }
    else if (outcome === 'throw') { save(); throw new Error('synthetic click failure'); }
    save();
  },
  async evaluate(fn, arg) { const f = new Function('document', 'location', 'return (' + fn.toString() + ')'); return f(documentFake, locationFake)(arg); },
  async waitForTimeout(ms) { await new Promise((r) => setTimeout(r, Math.min(ms, 3))); },
  async reload() { if (sc.reloadLosesSession) { st.loggedIn = false; st.url = sc.startUrl; save(); } },
};
const taskSpace = async () => ({ ownership: sc.ownership ?? 'agent', pages: async () => sc.pages ?? [{ label: 'p1' }], page: () => page });
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
await new AsyncFunction('taskSpace', script)(taskSpace);
if (sc.hangAfterOutput === step) await new Promise(() => {});
if (sc.nonzeroStep === step) process.exitCode = 2;
`;
const FAKE_OCR = `
import fs from 'node:fs'; import path from 'node:path';
let input = ''; for await (const c of process.stdin) input += c;
const list = JSON.parse(fs.readFileSync(path.join(process.env.FAKE_EGO_DIR, 'ocr.json'), 'utf8'));
const idx = Number((/FAKEIMG([0-9]+)/.exec(input) ?? [0, 0])[1]);
const row = list[(Math.max(idx, 1) - 1) % list.length];
if (row.kind === 'dep') { console.log(JSON.stringify({ ok: false, error: 'dependency_unavailable' })); process.exit(3); }
if (row.kind === 'nonzero-ok') { console.log(JSON.stringify({ ok: true, code: row.code, score: row.score, accepted: true, ocrMs: 1 })); process.exit(1); }
if (row.kind === 'fail') { console.log(JSON.stringify({ ok: false, error: 'ocr_failed' })); process.exit(1); }
console.log(JSON.stringify({ ok: true, code: row.code, score: row.score, accepted: true, ocrMs: 12 }));
`;
const FAKE_EGO_PATH = path.join(ROOT, 'fake-ego.mjs');
const FAKE_OCR_PATH = path.join(ROOT, 'fake-ocr.mjs');
fs.writeFileSync(FAKE_EGO_PATH, FAKE_EGO);
fs.writeFileSync(FAKE_OCR_PATH, FAKE_OCR);
const EVIDENCE = path.join(ROOT, 'evidence.txt');
fs.writeFileSync(EVIDENCE, 'synthetic prior-success evidence\n');
const EVIDENCE_SHA = sha(fs.readFileSync(EVIDENCE));

// ---- 工具 ----
const observations = [];
function check(id, type, method, expected, actual, ok) {
  observations.push({ id, type, method, expected, actual, status: ok ? 'pass' : 'fail' });
  if (!ok) console.error(`FAIL ${id}: expected ${JSON.stringify(expected)} actual ${JSON.stringify(actual)}`);
}
function notCovered(id, type, method, expected, reason) {
  observations.push({ id, type, method, expected, actual: reason, status: 'not-covered' });
}

function runRunner(argv, { stdin = '', env = {}, killAfterMs } = {}) {
  return new Promise((resolve) => {
    const child = spawn(process.execPath, [RUNNER, ...argv], {
      env: { ...process.env, CAPTCHA_LOGIN_STATE_ROOT: STATE_ROOT, CAPTCHA_LOGIN_ALLOW_TEST_ROOT: '1', FAKE_PASS: PASS, FAKE_CODE: CODE, FAKE_USER_SHA: sha(USER), FAKE_PASS_SHA: sha(PASS), ...env },
      stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true,
    });
    let stdout = ''; let stderr = '';
    child.stdout.on('data', (c) => { stdout += c; });
    child.stderr.on('data', (c) => { stderr += c; });
    child.stdin.on('error', () => {});
    child.stdin.end(stdin);
    if (killAfterMs) setTimeout(() => child.kill('SIGKILL'), killAfterMs);
    child.on('close', (code, signal) => {
      const lines = stdout.split(/\r?\n/).filter(Boolean).map((l) => { try { return JSON.parse(l); } catch { return { raw: l }; } });
      const final = [...lines].reverse().find((l) => l.result !== undefined) ?? null;
      resolve({ code, signal, stdout, stderr, lines, final });
    });
  });
}

const CRED = JSON.stringify({ username: USER, password: PASS });
let seq = 0;
const allOutputs = [];
async function runIt(argv, opts) { const r = await runRunner(argv, opts); allOutputs.push(r.stdout, r.stderr); return r; }

// 建一个案例：假页面场景、OCR 序列、初始化持久任务状态
async function makeCase(name, scenario, ocr, { historical = 2, max = 5 } = {}) {
  seq += 1;
  const task = `t${seq}-${name}`;
  const dir = path.join(ROOT, 'fake', task);
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, 'scenario.json'), JSON.stringify({ startUrl: LOGIN, expectCode: CODE, ...scenario }));
  fs.writeFileSync(path.join(dir, 'ocr.json'), JSON.stringify(ocr ?? [{ code: CODE, score: 0.93 }]));
  const bind = [...BIND.slice(0, -1), String(max)];
  const init = await runIt(['init', '--task-id', task, ...bind, '--historical-used', String(historical), '--evidence-ref', EVIDENCE, '--evidence-sha256', EVIDENCE_SHA]);
  if (init.final?.result !== 'initialized') throw new Error(`init failed for ${name}: ${init.stdout}`);
  const ego = ['--space', '5', '--page', 'p1', '--ego-bin', process.execPath, '--ego-arg', FAKE_EGO_PATH, '--python', process.execPath, '--ocr-script', FAKE_OCR_PATH, '--ocr-flag=--no-warnings', '--step-timeout-ms', '4000'];
  const env = { FAKE_EGO_DIR: dir };
  const calls = () => (fs.existsSync(path.join(dir, 'calls.jsonl')) ? fs.readFileSync(path.join(dir, 'calls.jsonl'), 'utf8').trim().split('\n').filter(Boolean).map((l) => JSON.parse(l)) : []);
  const page = () => JSON.parse(fs.readFileSync(path.join(dir, 'page.json'), 'utf8'));
  const state = () => JSON.parse(fs.readFileSync(path.join(STATE_ROOT, task, 'state.json'), 'utf8'));
  const login = (extra = [], opts = {}) => runIt(['login', '--task-id', task, ...bind, ...ego, ...extra], { stdin: CRED, env, ...opts });
  const status = () => runIt(['status', '--task-id', task]);
  return { task, dir, bind, ego, env, calls, page, state, login, status };
}
const count = (list, step) => list.filter((c) => c.step === step).length;

// ================= 用例 =================
async function main() {
  // --- L2：初始化与连续计数 ---
  {
    const c = await makeCase('init', { submits: ['ok'] });
    const st = await c.status();
    check('L2-init-2of5', 'behavior', 'init --historical-used 2 then status', 'used=2 max=5 remaining=3 with evidence-bound history', { used: st.final.used, max: st.final.max, remaining: st.final.remaining }, st.final.used === 2 && st.final.max === 5 && st.final.remaining === 3);
    const hist = c.state().historical;
    check('L2-init-evidence', 'behavior', 'state.historical evidence reference', 'evidence path + sha256 recorded', { sha: hist.evidenceSha256 === EVIDENCE_SHA, ref: hist.evidenceRef === EVIDENCE }, hist.evidenceSha256 === EVIDENCE_SHA && hist.evidenceRef === EVIDENCE);

    const again = await runIt(['init', '--task-id', c.task, ...c.bind, '--historical-used', '0', '--evidence-ref', EVIDENCE, '--evidence-sha256', EVIDENCE_SHA]);
    check('L2-init-no-overwrite', 'behavior', 'init again with historical-used 0 on existing task', 'state_exists, count stays 2', { outcome: again.final.result, used: c.state().used }, again.final.result === 'state_exists' && c.state().used === 2);
    const noHist = await runIt(['init', '--task-id', 'x-nohist', ...BIND, '--evidence-ref', EVIDENCE, '--evidence-sha256', EVIDENCE_SHA]);
    check('L2-init-no-default-zero', 'behavior', 'init without --historical-used', 'usage_error, no state created', { outcome: noHist.final.result, exists: fs.existsSync(path.join(STATE_ROOT, 'x-nohist', 'state.json')) }, noHist.final.result === 'usage_error' && !fs.existsSync(path.join(STATE_ROOT, 'x-nohist', 'state.json')));
    const badSha = await runIt(['init', '--task-id', 'x-badsha', ...BIND, '--historical-used', '2', '--evidence-ref', EVIDENCE, '--evidence-sha256', 'a'.repeat(64)]);
    check('L2-init-evidence-sha', 'behavior', 'init with wrong evidence sha256', 'usage_error evidence_sha_mismatch', { outcome: badSha.final.result, reason: badSha.final.reason }, badSha.final.result === 'usage_error' && badSha.final.reason === 'evidence_sha_mismatch');
    const missing = await runIt(['login', '--task-id', 'x-never-initialised', ...BIND, ...c.ego], { stdin: CRED, env: c.env });
    check('L2-state-missing-not-rebuilt', 'behavior', 'login on a task without state', 'state_missing, no browser call, no state created', { outcome: missing.final.result, calls: c.calls().length, created: fs.existsSync(path.join(STATE_ROOT, 'x-never-initialised', 'state.json')) }, missing.final.result === 'state_missing' && c.calls().length === 0 && !fs.existsSync(path.join(STATE_ROOT, 'x-never-initialised', 'state.json')));
  }

  // --- L1/L4：成功路径、刷新后核验 ---
  {
    const c = await makeCase('happy', { submits: ['ok'] });
    const r = await c.login();
    const f = r.final;
    check('L4-happy-verified', 'behavior', 'login with OCR accepted, one submission, reload verify', 'login_verified; used 2->3; URL+account+marker checked after reload', { outcome: f.result, used: f.used, verify: f.verify }, f.result === 'login_verified' && f.used === 3 && f.verify?.urlOk && f.verify?.accountState === 'ok' && f.verify?.markerState === 'ok' && !f.verify?.loginForm && r.code === 0);
    const calls = c.calls();
    check('L4-reload-before-verdict', 'behavior', 'calls order', 'verify step (reload) runs after the submit step', calls.map((x) => x.step), calls.map((x) => x.step).join(',') === 'preflight,fill,refresh,submit,verify');
    const fills = c.page().fills;
    check('L3-credentials-used-correctly', 'behavior', 'fake page compared sha256 of filled values', 'username/password/captcha filled exactly once with expected values', fills, fills.userOk && fills.passOk && fills.codeOk);
    check('L3-password-only-in-fill-script', 'behavior', 'which generated scripts contain the password', 'only the fill step carries credentials; no step carries the captcha code except submit', calls.map((x) => [x.step, x.hasPassword, x.hasCode]), calls.every((x) => x.hasPassword === (x.step === 'fill')) && calls.every((x) => x.hasCode === (x.step === 'submit')));
    const ev = c.state().events.map((e) => e.kind);
    check('L2-events-whitelist', 'behavior', 'state events', 'intent persisted before outcome and verify', ev, ev.join(',') === 'init,submit_intent,submit_outcome,verify');
  }
  {
    const c = await makeCase('wrongaccount', { submits: ['ok'], accountText: 'someone-else' });
    const r = await c.login();
    check('L4-account-mismatch-not-success', 'behavior', 'business page visible but account text differs', 'account_mismatch, exit 10 (not success), further submissions blocked', { outcome: r.final.result, code: r.code, blocked: c.state().blocked?.reason }, r.final.result === 'account_mismatch' && r.code === 10 && c.state().blocked?.reason === 'account_mismatch');
    const before = c.calls().length;
    const again = await c.login();
    check('L2-failed-verification-blocks-next-submit', 'behavior', 'second process after account_mismatch', 'blocked_reconcile_required with zero browser calls', { outcome: again.final.result, newCalls: c.calls().length - before }, again.final.result === 'blocked_reconcile_required' && c.calls().length === before);
  }
  {
    const c = await makeCase('reloadlost', { submits: ['ok'], reloadLosesSession: true });
    const r = await c.login();
    check('L4-leaving-login-not-enough', 'behavior', 'URL leaves login but reload returns to login form', 'not_logged_in, exit 10, blocked', { outcome: r.final.result, code: r.code, blocked: c.state().blocked?.reason }, r.final.result === 'not_logged_in' && r.code === 10 && c.state().blocked?.reason === 'not_logged_in');
  }
  {
    const c = await makeCase('already', { submits: ['ok'], startUrl: BUSINESS });
    // 已在业务页：先把假页面置为已登录
    fs.writeFileSync(path.join(c.dir, 'page.json'), JSON.stringify({ url: BUSINESS, acct: false, pw: false, code: '', imgIdx: 0, imgSrc: 'data:image/png;base64,FAKEIMG0', submits: 0, loggedIn: true, messages: [], fills: {} }));
    const r = await c.login();
    check('L4-already-logged-in', 'behavior', 'start on business page', 'already_logged_in_verified, zero submissions, count unchanged', { outcome: r.final.result, used: r.final.used, submits: c.page().submits }, r.final.result === 'already_logged_in_verified' && r.final.used === 2 && c.page().submits === 0);
  }

  {
    const c = await makeCase('stdin-shape', { submits: ['ok'] });
    const r = await c.login([], { stdin: `\uFEFF${CRED}\n` });
    check('L3-stdin-bom-and-newline-tolerated', 'behavior', 'credential JSON with BOM and trailing newline (as WriteLine delivers it)', 'login_verified', r.final.result, r.final.result === 'login_verified');
  }

  // --- L2：重试分类 ---
  {
    const c = await makeCase('captcha-retry', { submits: ['captcha_error', 'ok'] });
    const r = await c.login();
    check('L2-captcha-error-retries', 'behavior', 'captcha_error then ok', 'second refresh+submit, used 2->4, login_verified', { outcome: r.final.result, used: r.final.used, refresh: count(c.calls(), 'refresh'), submits: c.page().submits }, r.final.result === 'login_verified' && r.final.used === 4 && count(c.calls(), 'refresh') === 2 && c.page().submits === 2);
  }
  for (const [name, outcome, expected] of [['credential', 'credential_error', 'credential_error'], ['restricted', 'restricted', 'access_restricted']]) {
    const c = await makeCase(`stop-${name}`, { submits: [outcome, 'ok'] });
    const r = await c.login();
    check(`L2-${name}-stops`, 'behavior', `${outcome} message`, `${expected}, exit 10, exactly one submission, no refresh retry`, { outcome: r.final.result, used: r.final.used, submits: c.page().submits, refresh: count(c.calls(), 'refresh'), code: r.code }, r.final.result === expected && r.code === 10 && r.final.used === 3 && c.page().submits === 1 && count(c.calls(), 'refresh') === 1);
    const before = c.calls().length;
    const again = await c.login();
    check(`L2-${name}-blocked-across-processes`, 'behavior', 'second process after the stop', 'blocked_reconcile_required, ZERO new browser calls, still one click, count 3 (no refund)', { outcome: again.final.result, newCalls: c.calls().length - before, submits: c.page().submits, used: c.state().used, blocked: c.state().blocked?.reason }, again.final.result === 'blocked_reconcile_required' && c.calls().length === before && c.page().submits === 1 && c.state().used === 3 && c.state().blocked?.reason === expected);
    const noEv = await runIt(['reconcile', '--task-id', c.task, '--resolution', 'blocked-reviewed']);
    const submitted = await runIt(['reconcile', '--task-id', c.task, '--resolution', 'submitted', '--evidence-ref', EVIDENCE, '--evidence-sha256', EVIDENCE_SHA]);
    check(`L2-${name}-unblock-needs-evidence-and-right-resolution`, 'behavior', 'blocked-reviewed without evidence; pending-style resolution while blocked', 'both refused, still blocked', { noEvidence: noEv.final.result, wrongResolution: submitted.final.reason, blocked: !!c.state().blocked }, noEv.final.result === 'usage_error' && submitted.final.result === 'usage_error' && !!c.state().blocked);
    const unblock = await runIt(['reconcile', '--task-id', c.task, '--resolution', 'blocked-reviewed', '--evidence-ref', EVIDENCE, '--evidence-sha256', EVIDENCE_SHA]);
    check(`L2-${name}-unblock-keeps-count`, 'behavior', 'blocked-reviewed with evidence', 'unblocked, count stays 3 (no reset/refund)', { outcome: unblock.final.result, used: c.state().used, blocked: c.state().blocked }, unblock.final.result === 'reconciled' && c.state().used === 3 && c.state().blocked === null);
    const next = await c.login();
    check(`L2-${name}-login-possible-only-after-unblock`, 'behavior', 'login after the evidence-backed unblock', 'proceeds (second click) and succeeds, count 4', { outcome: next.final.result, used: c.state().used, submits: c.page().submits }, next.final.result === 'login_verified' && c.state().used === 4 && c.page().submits === 2);
  }
  {
    const c = await makeCase('mixed-toast', { submits: ['other_toast', 'ok'] });
    const r = await c.login();
    check('L2-ambiguous-message-is-unknown', 'behavior', 'captcha message plus unrelated toast', 'submit_result_unknown, pending kept, exit 20, no auto retry', { outcome: r.final.result, pending: c.state().pending?.stage, submits: c.page().submits, code: r.code }, r.final.result === 'submit_result_unknown' && c.state().pending?.stage === 'unknown' && c.page().submits === 1 && r.code === 20);
  }

  // --- R2：只认本次提交产生的新反馈 ---
  {
    const c = await makeCase('stale-captcha', { submits: ['unknown', 'ok'], staleMessages: ['验证码错误'] });
    const r = await c.login();
    check('L2-stale-captcha-alert-not-current-evidence', 'behavior', 'pre-existing unchanged “验证码错误” alert, click yields no new feedback', 'exactly one click, submit_result_unknown + pending, count 3 (no auto retries to 5/5)', { outcome: r.final.result, submits: c.page().submits, used: c.state().used, pending: c.state().pending?.stage }, r.final.result === 'submit_result_unknown' && c.page().submits === 1 && c.state().used === 3 && c.state().pending?.stage === 'unknown');
    const before = c.calls().length;
    const again = await c.login();
    check('L2-stale-captcha-no-replay', 'behavior', 'second process', 'pending_reconcile_required, zero browser calls', { outcome: again.final.result, newCalls: c.calls().length - before }, again.final.result === 'pending_reconcile_required' && c.calls().length === before);
  }
  {
    const c = await makeCase('stale-plus-new', { submits: ['captcha_error', 'ok'], staleMessages: ['验证码错误'] });
    const r = await c.login();
    check('L2-new-captcha-feedback-beside-stale-retries', 'behavior', 'stale alert present AND a new captcha error appears after the click', 'new feedback counts: retry once more and succeed, count 4', { outcome: r.final.result, used: r.final.used, submits: c.page().submits }, r.final.result === 'login_verified' && r.final.used === 4 && c.page().submits === 2);
  }
  {
    const c = await makeCase('stale-credential', { submits: ['captcha_error', 'ok'], staleMessages: ['用户名或密码错误'] });
    const r = await c.login();
    check('L2-stale-credential-alert-does-not-stop-new-captcha-retry', 'behavior', 'stale credential alert unchanged, genuine new captcha error', 'classified by the new feedback only (captcha_error then success)', { outcome: r.final.result, used: r.final.used }, r.final.result === 'login_verified' && r.final.used === 4);
  }

  // --- R3：URL 精确源、账号/入口整词匹配 ---
  {
    const forged = 'http://test.invalid:11888@evil.invalid/#/wel/index';
    const c = await makeCase('url-userinfo', { startUrl: forged, accountText: `${ACCOUNT} ${MARKER}` });
    fs.writeFileSync(path.join(c.dir, 'page.json'), JSON.stringify({ url: forged, acct: false, pw: false, code: '', imgIdx: 0, imgSrc: 'data:image/png;base64,FAKEIMG0', submits: 0, loggedIn: true, messages: [], fills: {} }));
    const v = await runIt(['verify', '--task-id', c.task, ...c.bind, ...c.ego], { env: c.env });
    check('L4-url-userinfo-origin-rejected', 'behavior', `verify on ${forged}`, 'not login_verified (origin is evil.invalid)', { outcome: v.final.result, code: v.code, verify: v.final.verify }, v.final.result !== 'login_verified' && v.code === 10 && v.final.verify?.urlOk === false);
    const l = await c.login();
    check('L4-url-userinfo-origin-login-refuses', 'behavior', 'login with the page on that forged origin', 'unexpected_page, zero fill/submit', { outcome: l.final.result, steps: c.calls().map((x) => x.step) }, l.final.result === 'unexpected_page' && !c.calls().some((x) => ['fill', 'submit'].includes(x.step)));
  }
  for (const [name, url] of [['login-lookalike-hash', 'http://test.invalid:11888/#/login.evil'], ['login-userinfo', 'http://test.invalid:11888@evil.invalid/#/login'], ['other-port', 'http://test.invalid:11889/#/login']]) {
    const c = await makeCase(`url-${name}`, { startUrl: url });
    const l = await c.login();
    check(`L4-login-url-exact-${name}`, 'behavior', `page URL ${url} vs bound ${LOGIN}`, 'not treated as the login page: unexpected_page, no fill/submit', { outcome: l.final.result, steps: c.calls().map((x) => x.step) }, l.final.result === 'unexpected_page' && !c.calls().some((x) => ['fill', 'submit'].includes(x.step)));
  }
  {
    const bad = await runIt(['init', '--task-id', 'x-userinfo-target', ...BIND.map((v) => (v === LOGIN ? 'http://test.invalid:11888@evil.invalid/#/login' : v)), '--historical-used', '2', '--evidence-ref', EVIDENCE, '--evidence-sha256', EVIDENCE_SHA]);
    check('L4-bound-target-url-rejects-userinfo', 'behavior', 'init with a userinfo target URL', 'usage_error bad_target_url', { outcome: bad.final.result, reason: bad.final.reason }, bad.final.result === 'usage_error' && bad.final.reason === 'bad_target_url');
  }
  // --- R3（a4）：账号/业务入口只认显式配置的控件：恰好一个可见元素 + 规范化后精确相等，不看整页文本 ---
  for (const [name, extra, want, accountState, markerState] of [
    ['account-exact', {}, 'login_verified', 'ok', 'ok'],
    ['account-whitespace-normalised', { accountNodes: [{ text: ` ${ACCOUNT} \n` }] }, 'login_verified', 'ok', 'ok'],
    ['account-substring', { accountNodes: [{ text: `${ACCOUNT}2` }] }, 'account_mismatch', 'mismatch', 'ok'],
    ['account-prefix-letters', { accountNodes: [{ text: `x${ACCOUNT}` }] }, 'account_mismatch', 'mismatch', 'ok'],
    ['account-in-sentence', { accountNodes: [{ text: `欢迎，${ACCOUNT}` }] }, 'account_mismatch', 'mismatch', 'ok'],
    ['other-account-with-expected-name-elsewhere-in-page', { accountNodes: [{ text: '其他人' }], bodyText: `历史经办 ${ACCOUNT} ${MARKER}` }, 'account_mismatch', 'mismatch', 'ok'],
    ['two-visible-account-nodes', { accountNodes: [{ text: ACCOUNT }, { text: ACCOUNT }] }, 'account_mismatch', 'ambiguous', 'ok'],
    ['hidden-duplicate-ignored', { accountNodes: [{ text: ACCOUNT }, { text: '其他人', hidden: true }] }, 'login_verified', 'ok', 'ok'],
    ['only-hidden-account', { accountNodes: [{ text: ACCOUNT, hidden: true }] }, 'account_mismatch', 'missing', 'ok'],
    ['missing-account-node', { accountNodes: [] }, 'account_mismatch', 'missing', 'ok'],
    ['marker-inside-longer-text', { markerNodes: [{ text: `${MARKER}管理` }] }, 'verify_failed', 'ok', 'mismatch'],
    ['marker-missing', { markerNodes: [] }, 'verify_failed', 'ok', 'missing'],
    ['marker-two-visible', { markerNodes: [{ text: MARKER }, { text: MARKER }] }, 'verify_failed', 'ok', 'ambiguous'],
    ['marker-only-hidden', { markerNodes: [{ text: MARKER, hidden: true }] }, 'verify_failed', 'ok', 'missing'],
    ['marker-exact-only-in-body', { markerNodes: [{ text: '工作台' }], bodyText: `${ACCOUNT} ${MARKER}` }, 'verify_failed', 'ok', 'mismatch'],
  ]) {
    const c = await makeCase(`ctl-${name}`, { startUrl: BUSINESS, ...extra });
    fs.writeFileSync(path.join(c.dir, 'page.json'), JSON.stringify({ url: BUSINESS, acct: false, pw: false, code: '', imgIdx: 0, imgSrc: 'data:image/png;base64,FAKEIMG0', submits: 0, loggedIn: true, messages: [], fills: {} }));
    const v = await runIt(['verify', '--task-id', c.task, ...c.bind, ...c.ego], { env: c.env });
    check(`L4-control-match-${name}`, 'behavior', `explicit controls ${JSON.stringify({ accountNodes: extra.accountNodes, markerNodes: extra.markerNodes, bodyText: extra.bodyText })}`, `verify → ${want} (account ${accountState}, marker ${markerState})`, { outcome: v.final.result, verify: v.final.verify }, v.final.result === want && v.final.verify?.accountState === accountState && v.final.verify?.markerState === markerState && (want === 'login_verified') === (v.code === 0));
  }
  {
    const base = BIND.filter((v, i) => !['--account-selector', '--business-selector'].includes(v) && !['--account-selector', '--business-selector'].includes(BIND[i - 1]));
    const results = {};
    for (const [k, args] of Object.entries({
      noAccountSelector: [...base, '--business-selector', '.business-marker'],
      noBusinessSelector: [...base, '--account-selector', '.account-name'],
      neither: base,
      genericBody: [...base, '--account-selector', 'body', '--business-selector', '.business-marker'],
      genericStar: [...base, '--account-selector', '.account-name', '--business-selector', '*'],
    })) {
      const r = await runIt(['init', '--task-id', `x-sel-${k}`, ...args, '--historical-used', '2', '--evidence-ref', EVIDENCE, '--evidence-sha256', EVIDENCE_SHA]);
      results[k] = `${r.final.result}:${r.final.reason}`;
    }
    const created = fs.readdirSync(STATE_ROOT).filter((d) => d.startsWith('x-sel-'));
    check('L4-selectors-required-fail-closed', 'behavior', 'init without explicit account/business selectors, or with whole-page selectors', 'usage_error (missing_option / bad_selector); no state created, so no login can run without them', { results, created }, Object.values(results).every((v) => v.startsWith('usage_error:')) && results.noAccountSelector.endsWith('missing_option') && results.genericBody.endsWith('bad_selector') && results.genericStar.endsWith('bad_selector') && created.length === 0);
    const c = await makeCase('sel-login-without-selectors', { submits: ['ok'] });
    const l = await runIt(['login', '--task-id', c.task, ...base, ...c.ego], { stdin: CRED, env: c.env });
    check('L4-login-and-verify-require-selectors', 'behavior', 'login/verify invoked without the selectors on an existing task', 'usage_error missing_option, zero browser calls', { outcome: l.final.result, reason: l.final.reason, calls: c.calls().length }, l.final.result === 'usage_error' && l.final.reason === 'missing_option' && c.calls().length === 0);
  }

  // --- R4：子进程非零/超时/缺结构一律不采信 ---
  for (const step of ['preflight', 'verify']) {
    const c = await makeCase(`nonzero-${step}`, { startUrl: BUSINESS, nonzeroStep: step });
    fs.writeFileSync(path.join(c.dir, 'page.json'), JSON.stringify({ url: BUSINESS, acct: false, pw: false, code: '', imgIdx: 0, imgSrc: 'data:image/png;base64,FAKEIMG0', submits: 0, loggedIn: true, messages: [], fills: {} }));
    const v = await runIt(['verify', '--task-id', c.task, ...c.bind, ...c.ego], { env: c.env });
    check(`L4-nonzero-ego-exit-not-trusted-${step}`, 'behavior', `Ego child prints a valid ok result but exits 2 at ${step}`, 'never login_verified; exit 10', { outcome: v.final.result, code: v.code }, v.final.result !== 'login_verified' && v.code === 10 && !JSON.stringify(v.final).includes('@@R@@'));
  }
  {
    const c = await makeCase('nonzero-submit', { submits: ['ok'], nonzeroStep: 'submit' });
    const r = await c.login();
    check('L4-nonzero-submit-exit-is-unknown', 'behavior', 'submit step printed ok:true/left login but exited 2 after the click began', 'submit_result_unknown + pending, never success, count 3', { outcome: r.final.result, used: c.state().used, pending: c.state().pending?.stage }, r.final.result === 'submit_result_unknown' && c.state().used === 3 && c.state().pending?.stage === 'unknown' && c.calls().every((x) => x.step !== 'verify'));
  }
  {
    const c = await makeCase('hang-after-output', { submits: ['ok'], hangAfterOutput: 'submit' });
    const r = await c.login(['--step-timeout-ms', '1500']);
    check('L4-timeout-after-valid-output-is-unknown', 'behavior', 'submit step printed a valid ok result then hung until the timeout', 'unknown + pending (no assumption that it completed), no verify', { outcome: r.final.result, pending: c.state().pending?.stage, steps: c.calls().map((x) => x.step) }, r.final.result === 'submit_result_unknown' && c.state().pending?.stage === 'unknown' && !c.calls().some((x) => x.step === 'verify'));
  }
  {
    const c = await makeCase('ocr-nonzero-ok', { submits: ['ok'] }, [{ kind: 'nonzero-ok', code: CODE, score: 0.99 }]);
    const r = await c.login();
    check('L1-ocr-nonzero-exit-not-trusted', 'behavior', 'OCR worker prints accepted code but exits 1', 'ocr_failed, zero submissions', { outcome: r.final.result, submits: c.page().submits }, r.final.result === 'ocr_failed' && c.page().submits === 0);
  }

  // --- R5：reconcile 取锁后读状态、互斥、不删活跃者的锁 ---
  {
    const c = await makeCase('reconcile-race', { submits: ['crash-parent'] });
    await c.login();
    const results = await Promise.all(Array.from({ length: 6 }, () => runIt(['reconcile', '--task-id', c.task, '--resolution', 'not-submitted', '--evidence-ref', EVIDENCE, '--evidence-sha256', EVIDENCE_SHA])));
    const names = results.map((x) => x.final?.result + (x.final?.reason ? `:${x.final.reason}` : ''));
    const ok = results.filter((x) => x.final?.result === 'reconciled').length;
    check('L2-reconcile-concurrent-single-refund', 'behavior', 'six parallel reconcile not-submitted against one pending intent with a dead lock holder', 'exactly one reconciled; others locked or nothing_pending; count refunded exactly once (3→2); no lock or guard left', { ok, names, used: c.state().used, leftovers: fs.readdirSync(path.join(STATE_ROOT, c.task)).filter((f) => f.endsWith('.lock') || f === 'lock') }, ok === 1 && c.state().used === 2 && c.state().pending === null && results.every((x) => ['reconciled', 'locked', 'usage_error'].includes(x.final?.result)) && !fs.existsSync(path.join(STATE_ROOT, c.task, 'lock')) && !fs.existsSync(path.join(STATE_ROOT, c.task, 'reconcile.lock')));
  }
  {
    const c = await makeCase('reconcile-load-under-lock', { submits: ['crash-parent'] });
    await c.login();
    const slow = runIt(['reconcile', '--task-id', c.task, '--resolution', 'not-submitted', '--evidence-ref', EVIDENCE, '--evidence-sha256', EVIDENCE_SHA], { env: { CAPTCHA_LOGIN_TEST_PAUSE_BEFORE_RECONCILE_LOCK_MS: '2500' } });
    await new Promise((r) => setTimeout(r, 700));
    const fast = await runIt(['reconcile', '--task-id', c.task, '--resolution', 'not-submitted', '--evidence-ref', EVIDENCE, '--evidence-sha256', EVIDENCE_SHA]);
    const late = await slow;
    check('L2-reconcile-reads-state-after-taking-the-lock', 'behavior', 'reconcile B pauses before taking its lock while reconcile A completes the refund, then B resumes', 'B sees the fresh state (nothing_pending); the refund is applied exactly once (3→2)', { fast: fast.final.result, late: late.final.result, lateReason: late.final.reason, used: c.state().used }, fast.final.result === 'reconciled' && late.final.result === 'usage_error' && late.final.reason === 'nothing_pending' && c.state().used === 2);
  }
  {
    const c = await makeCase('reconcile-live-holder', { submits: ['ok'] });
    const lockFile = path.join(STATE_ROOT, c.task, 'lock');
    const text = JSON.stringify({ pid: process.pid, since: new Date().toISOString() });
    fs.writeFileSync(lockFile, text);
    const r = await runIt(['reconcile', '--task-id', c.task, '--resolution', 'clear-stale-lock']);
    check('L2-reconcile-never-removes-live-holder-lock', 'behavior', 'lock owned by a live process (this test process)', 'locked; lock file byte-identical; no guard left behind', { outcome: r.final.result, unchanged: fs.readFileSync(lockFile, 'utf8') === text, guard: fs.existsSync(path.join(STATE_ROOT, c.task, 'reconcile.lock')) }, r.final.result === 'locked' && fs.readFileSync(lockFile, 'utf8') === text && !fs.existsSync(path.join(STATE_ROOT, c.task, 'reconcile.lock')));
    fs.unlinkSync(lockFile);
  }
  {
    const c = await makeCase('reconcile-guard', { submits: ['ok'] });
    const guard = path.join(STATE_ROOT, c.task, 'reconcile.lock');
    fs.writeFileSync(guard, '{}');
    const r = await runIt(['reconcile', '--task-id', c.task, '--resolution', 'clear-stale-lock']);
    check('L2-reconcile-guard-not-auto-cleared', 'behavior', 'reconcile.lock left by another reconcile', 'locked, guard untouched', { outcome: r.final.result, still: fs.existsSync(guard) }, r.final.result === 'locked' && fs.existsSync(guard));
    fs.unlinkSync(guard);
  }

  // --- a15：真实 Ego 把 console 标记写到 stderr；产品两路都认，但只采信精确标记行 + 正常退出 ---
  {
    const c = await makeCase('chan-stderr-success', { submits: ['ok'], channel: 'stderr' });
    const r = await c.login();
    check('L4-stderr-only-markers-success', 'behavior', 'every Ego step prints its @@R@@/@@STAGE@@ markers only on stderr (as the real Ego does), exit 0', 'login_verified through the normal runner path; count 2→3', { outcome: r.final.result, used: r.final.used, steps: c.calls().map((x) => x.step) }, r.final.result === 'login_verified' && r.final.used === 3 && c.calls().map((x) => x.step).join(',') === 'preflight,fill,refresh,submit,verify');
  }
  {
    const c = await makeCase('chan-stderr-noise', { submits: ['ok'], channel: 'stderr', noiseStderr: true, noiseHuge: true });
    const r = await c.login();
    check('L4-stderr-noise-and-oversize-ignored', 'behavior', 'stderr also carries plain noise, a mid-line “[log] @@R@@…”, marker-prefixed non-JSON/array lines BEFORE the real result, 3000 long noise lines, one 4.5 MB line and an echo of the script secret', 'only the exact real marker lines count: login_verified; nothing from stderr is echoed or persisted (global scan below)', { outcome: r.final.result, used: r.final.used }, r.final.result === 'login_verified' && r.final.used === 3);
  }
  {
    const c = await makeCase('chan-both-same', { submits: ['ok'], channel: 'both' });
    const r = await c.login();
    check('L4-both-channels-identical-accepted', 'behavior', 'same marker lines on stdout and stderr', 'login_verified', r.final.result, r.final.result === 'login_verified');
  }
  {
    const c = await makeCase('chan-conflict', { submits: ['ok'], channel: 'conflict' });
    const r = await c.login();
    check('L4-conflicting-channels-rejected', 'behavior', 'stdout says ok:true, stderr says ok:false for the same step', 'no result → ego_error at preflight; zero fill/refresh/submit', { outcome: r.final.result, steps: c.calls().map((x) => x.step), used: c.state().used }, r.final.result === 'ego_error' && c.calls().every((x) => x.step === 'preflight') && c.state().used === 2);
  }
  {
    const c = await makeCase('chan-not-at-line-start', { submits: ['ok'], channel: 'stderr', prefixMarkers: '[log] ' });
    const r = await c.login();
    check('L4-marker-not-at-line-start-rejected', 'behavior', 'markers present only as “[log] @@R@@…” on stderr', 'not accepted (no loose substring matching): ego_error, zero fill/submit', { outcome: r.final.result, steps: c.calls().map((x) => x.step) }, r.final.result === 'ego_error' && c.calls().every((x) => x.step === 'preflight'));
  }
  {
    const c = await makeCase('chan-stderr-nonzero-preflight', { startUrl: BUSINESS, channel: 'stderr', nonzeroStep: 'preflight' });
    const v = await runIt(['verify', '--task-id', c.task, ...c.bind, ...c.ego], { env: c.env });
    check('L4-stderr-result-with-nonzero-exit-rejected', 'behavior', 'preflight prints a valid ok result on stderr but exits 2', 'ego_error / not login_verified, exit 10', { outcome: v.final.result, code: v.code }, v.final.result === 'ego_error' && v.code === 10);
  }
  {
    const c = await makeCase('chan-stderr-click-nonzero', { submits: ['ok'], channel: 'stderr', nonzeroStep: 'submit' });
    const r = await c.login();
    check('L2-stderr-click-marker-plus-nonzero-is-unknown-pending', 'behavior', 'click-begin and an ok result on stderr, then exit 2', 'submit_result_unknown + pending (click stage seen on stderr), count 3 NOT refunded, no verify', { outcome: r.final.result, used: c.state().used, pending: c.state().pending?.stage, steps: c.calls().map((x) => x.step) }, r.final.result === 'submit_result_unknown' && c.state().used === 3 && c.state().pending?.stage === 'unknown' && !c.calls().some((x) => x.step === 'verify'));
    const before = c.calls().length;
    const again = await c.login();
    check('L2-stderr-click-no-replay', 'behavior', 'next process', 'pending_reconcile_required, zero browser calls, still one click', { outcome: again.final.result, newCalls: c.calls().length - before, submits: c.page().submits }, again.final.result === 'pending_reconcile_required' && c.calls().length === before && c.page().submits === 1);
  }
  {
    const c = await makeCase('chan-stderr-click-timeout', { submits: ['ok'], channel: 'stderr', hangAfterOutput: 'submit' });
    const r = await c.login(['--step-timeout-ms', '1500']);
    check('L2-stderr-click-marker-plus-timeout-is-unknown-pending', 'behavior', 'click-begin and ok on stderr, then the process hangs until the step timeout', 'submit_result_unknown + pending, count 3, no verify', { outcome: r.final.result, used: c.state().used, pending: c.state().pending?.stage }, r.final.result === 'submit_result_unknown' && c.state().used === 3 && c.state().pending?.stage === 'unknown' && !c.calls().some((x) => x.step === 'verify'));
  }
  {
    const c = await makeCase('chan-stderr-preclick-release', { submits: ['ok'], channel: 'stderr', failFillCaptcha: true });
    const r = await c.login();
    check('L2-stderr-preclick-failure-still-released', 'behavior', 'structured pre-click failure (clickInvoked:false) printed on stderr, exit 0', 'fill_failed, intent released, count back to 2', { outcome: r.final.result, released: r.final.released, used: c.state().used, pending: c.state().pending }, r.final.result === 'fill_failed' && r.final.released === true && c.state().used === 2 && c.state().pending === null);
  }
  {
    const c = await makeCase('chan-stderr-marker-no-result', { submits: ['ok'], channel: 'stderr', hangAfterOutput: 'never' });
    // 只有 click-begin、没有结果行：模拟“结果行丢失”，不能当成功
    const scenario = JSON.parse(fs.readFileSync(path.join(c.dir, 'scenario.json'), 'utf8'));
    scenario.dropResultInSubmit = true;
    fs.writeFileSync(path.join(c.dir, 'scenario.json'), JSON.stringify(scenario));
    const r = await c.login();
    check('L2-click-marker-without-result-is-unknown', 'behavior', 'stderr has click-begin but no result line', 'submit_result_unknown + pending, count 3', { outcome: r.final.result, used: c.state().used, pending: c.state().pending?.stage }, r.final.result === 'submit_result_unknown' && c.state().used === 3 && c.state().pending?.stage === 'unknown');
  }

  // --- L1：OCR 门槛与有界刷新 ---
  {
    const c = await makeCase('lowscore', { submits: ['ok'] }, [{ code: CODE, score: 0.5 }]);
    const r = await c.login(['--max-ocr-rounds', '3']);
    const rejected = c.state().events.filter((e) => e.kind === 'ocr_rejected');
    check('L1-low-confidence-never-submits', 'behavior', 'OCR score 0.5 every round, max 3 rounds', 'ocr_rounds_exhausted, zero submissions, count unchanged, bounded refresh', { outcome: r.final.result, used: r.final.used, submits: c.page().submits, refresh: count(c.calls(), 'refresh'), rejected: rejected.length, reasons: rejected.map((e) => e.reason) }, r.final.result === 'ocr_rounds_exhausted' && r.final.used === 2 && c.page().submits === 0 && count(c.calls(), 'refresh') === 3 && rejected.length === 3 && rejected.every((e) => e.reason === 'score'));
  }
  for (const bad of ['12a4', '１２３４', '123', '12345']) {
    const c = await makeCase('format', { submits: ['ok'] }, [{ code: bad, score: 0.99 }]);
    const r = await c.login(['--max-ocr-rounds', '2']);
    check(`L1-format-rejected-${[...bad].length}-${/^[0-9]+$/.test(bad) ? 'digits' : 'mixed'}`, 'behavior', `OCR code ${JSON.stringify(bad)} score 0.99`, 'not submitted: only ASCII 4 digits accepted', { outcome: r.final.result, submits: c.page().submits }, c.page().submits === 0 && r.final.result === 'ocr_rounds_exhausted');
  }
  {
    const c = await makeCase('boundary', { submits: ['ok'] }, [{ code: CODE, score: 0.79 }, { code: CODE, score: 0.8 }]);
    const r = await c.login();
    check('L1-score-boundary', 'behavior', 'score 0.79 rejected, next round 0.80 accepted', 'one rejection then one submission', { outcome: r.final.result, rejected: r.final.ocrRejected, used: r.final.used }, r.final.result === 'login_verified' && r.final.ocrRejected === 1 && r.final.used === 3);
  }
  {
    const c = await makeCase('ocr-dep', { submits: ['ok'] }, [{ kind: 'dep' }]);
    const r = await c.login();
    check('L1-ocr-dependency-unavailable', 'behavior', 'worker reports dependency_unavailable', 'ocr_unavailable, zero submissions, reported not-covered rather than pass', { outcome: r.final.result, submits: c.page().submits }, r.final.result === 'ocr_unavailable' && c.page().submits === 0);
  }
  {
    const c = await makeCase('refresh-broken', { submits: ['ok'], refreshBroken: true });
    const r = await c.login();
    check('L1-refresh-failure-bounded', 'behavior', 'captcha image never changes', 'captcha_refresh_failed after 2 tries, zero submissions', { outcome: r.final.result, refresh: count(c.calls(), 'refresh'), submits: c.page().submits }, r.final.result === 'captcha_refresh_failed' && count(c.calls(), 'refresh') === 2 && c.page().submits === 0);
  }

  // --- L2：预算上限与单次运行上限 ---
  {
    const c = await makeCase('budget-last', { submits: ['captcha_error', 'ok'] }, null, { historical: 4 });
    const r = await c.login();
    check('L2-budget-hard-stop', 'behavior', 'historical 4/5 with captcha_error on the last submission', 'submission_budget_exhausted after the 5th submission; never a 6th', { outcome: r.final.result, used: c.state().used, submits: c.page().submits }, r.final.result === 'submission_budget_exhausted' && c.state().used === 5 && c.page().submits === 1);
  }
  {
    const c = await makeCase('budget-zero', { submits: ['ok'] }, null, { historical: 5 });
    const r = await c.login();
    check('L2-budget-exhausted-before-fill', 'behavior', 'historical 5/5', 'stops before filling credentials or submitting', { outcome: r.final.result, steps: c.calls().map((x) => x.step) }, r.final.result === 'submission_budget_exhausted' && !c.calls().some((x) => ['fill', 'refresh', 'submit'].includes(x.step)));
  }
  {
    const c = await makeCase('runcap', { submits: Array(10).fill('captcha_error') }, null, { historical: 0 });
    const r = await c.login();
    check('L2-run-submission-cap', 'behavior', 'five consecutive captcha_error, default per-run cap 3', 'run_submission_cap_reached at 3 submissions, count=3', { outcome: r.final.result, used: c.state().used, submits: c.page().submits }, r.final.result === 'run_submission_cap_reached' && c.state().used === 3 && c.page().submits === 3);
    const next = await c.login();
    check('L2-count-continues-across-runs', 'behavior', 'second run on same task', 'count continues from 3 (cap again 3, budget stops at 5)', { used: c.state().used, outcome: next.final.result }, c.state().used === 5 && next.final.result === 'submission_budget_exhausted');
  }

  // --- L2：未知结果、崩溃边界、对账 ---
  {
    const c = await makeCase('hang', { submits: ['hang', 'ok'] });
    const r = await c.login(['--step-timeout-ms', '1500']);
    check('L2-timeout-is-unknown', 'behavior', 'submit step hangs after click began', 'submit_result_unknown exit 20, count kept at 3, pending=unknown', { outcome: r.final.result, used: c.state().used, pending: c.state().pending?.stage, code: r.code }, r.final.result === 'submit_result_unknown' && r.code === 20 && c.state().used === 3 && c.state().pending?.stage === 'unknown');
    const before = c.calls().length;
    const again = await c.login();
    check('L2-unknown-no-replay', 'behavior', 'login again while pending', 'pending_reconcile_required with ZERO browser calls', { outcome: again.final.result, newCalls: c.calls().length - before, submits: c.page().submits }, again.final.result === 'pending_reconcile_required' && c.calls().length === before && c.page().submits === 1);
    const noEv = await runIt(['reconcile', '--task-id', c.task, '--resolution', 'submitted']);
    check('L2-reconcile-needs-evidence', 'behavior', 'reconcile without evidence', 'usage_error, pending kept', { outcome: noEv.final.result, pending: !!c.state().pending }, noEv.final.result === 'usage_error' && !!c.state().pending);
    const rec = await runIt(['reconcile', '--task-id', c.task, '--resolution', 'submitted', '--evidence-ref', EVIDENCE, '--evidence-sha256', EVIDENCE_SHA]);
    check('L2-reconcile-submitted-keeps-count', 'behavior', 'reconcile submitted with evidence', 'pending cleared, count stays 3', { outcome: rec.final.result, used: c.state().used, pending: c.state().pending }, rec.final.result === 'reconciled' && c.state().used === 3 && c.state().pending === null);
  }
  {
    const c = await makeCase('throw-after-click', { submits: ['throw'] });
    const r = await c.login();
    check('L2-error-after-click-is-unknown', 'behavior', 'script throws after click was invoked', 'submit_result_unknown, count kept', { outcome: r.final.result, used: c.state().used }, r.final.result === 'submit_result_unknown' && c.state().used === 3);
  }
  {
    const c = await makeCase('preclick-fail', { submits: ['ok'], failFillCaptcha: true });
    const r = await c.login();
    check('L2-preclick-failure-released', 'behavior', 'captcha fill fails before the click marker', 'fill_failed, intent released, count back to 2, nothing pending', { outcome: r.final.result, released: r.final.released, used: c.state().used, pending: c.state().pending, submits: c.page().submits }, r.final.result === 'fill_failed' && r.final.released === true && c.state().used === 2 && c.state().pending === null && c.page().submits === 0);
  }
  {
    const c = await makeCase('crash', { submits: ['crash-parent', 'ok'] });
    const r = await c.login();
    const st = c.state();
    check('L2-crash-after-intent-persisted', 'behavior', 'runner SIGKILLed during submit', 'count already 3 with pending intent on disk and lock left behind', { signal: r.signal, used: st.used, pending: st.pending?.stage, lock: fs.existsSync(path.join(STATE_ROOT, c.task, 'lock')) }, st.used === 3 && st.pending?.stage === 'intent' && fs.existsSync(path.join(STATE_ROOT, c.task, 'lock')));
    const before = c.calls().length;
    const again = await c.login();
    check('L2-stale-lock-not-auto-cleared', 'behavior', 'login after crash', 'locked, zero browser calls, lock untouched', { outcome: again.final.result, holderAlive: again.final.holder?.alive, newCalls: c.calls().length - before, lock: fs.existsSync(path.join(STATE_ROOT, c.task, 'lock')) }, again.final.result === 'locked' && c.calls().length === before && fs.existsSync(path.join(STATE_ROOT, c.task, 'lock')));
    const rec = await runIt(['reconcile', '--task-id', c.task, '--resolution', 'not-submitted', '--evidence-ref', EVIDENCE, '--evidence-sha256', EVIDENCE_SHA]);
    check('L2-reconcile-not-submitted-refunds', 'behavior', 'reconcile not-submitted after dead holder', 'lock cleared by explicit command, count 3->2, pending null', { outcome: rec.final.result, used: c.state().used, pending: c.state().pending, lock: fs.existsSync(path.join(STATE_ROOT, c.task, 'lock')) }, rec.final.result === 'reconciled' && c.state().used === 2 && c.state().pending === null && !fs.existsSync(path.join(STATE_ROOT, c.task, 'lock')));
  }

  // --- L2：并发排他 ---
  {
    const c = await makeCase('concurrent', { submits: ['ok'], submitDelayMs: 1200 });
    const [a, b] = await Promise.all([c.login(), c.login()]);
    const outcomes = [a.final.result, b.final.result].sort();
    check('L2-concurrent-exclusive', 'behavior', 'two logins on the same task at once', 'exactly one proceeds (login_verified), the other is locked; one submission, count 2->3', { outcomes, used: c.state().used, submits: c.page().submits }, outcomes.join(',') === 'locked,login_verified' && c.state().used === 3 && c.page().submits === 1);
  }

  // --- L2/L3：绑定、篡改、根目录、凭据参数 ---
  {
    const c = await makeCase('binding', { submits: ['ok'] });
    const variants = {
      max: c.bind.map((v, i) => (c.bind[i - 1] === '--max-submissions' ? '9' : v)),
      account: c.bind.map((v, i) => (c.bind[i - 1] === '--expected-account' ? 'other-account' : v)),
      url: c.bind.map((v, i) => (c.bind[i - 1] === '--target-url' ? 'http://other.invalid/#/login' : v)),
      secret: c.bind.map((v, i) => (c.bind[i - 1] === '--secret-name' ? 'other-secret' : v)),
      accountSelector: c.bind.map((v, i) => (c.bind[i - 1] === '--account-selector' ? '.other-account' : v)),
      businessSelector: c.bind.map((v, i) => (c.bind[i - 1] === '--business-selector' ? '.other-marker' : v)),
    };
    const results = {};
    for (const [k, bind] of Object.entries(variants)) {
      const r = await runIt(['login', '--task-id', c.task, ...bind, ...c.ego], { stdin: CRED, env: c.env });
      results[k] = r.final.result;
    }
    check('L2-binding-conflict', 'behavior', 'same task id with changed max/account/url/secret', 'binding_mismatch for each (incl. the account/business selectors), zero browser calls, count 2', { results, calls: c.calls().length, used: c.state().used }, Object.values(results).every((v) => v === 'binding_mismatch') && c.calls().length === 0 && c.state().used === 2);
    const file = path.join(STATE_ROOT, c.task, 'state.json');
    const original = fs.readFileSync(file, 'utf8');
    const tampered = JSON.parse(original); tampered.used = 0;
    fs.writeFileSync(file, JSON.stringify(tampered));
    const t = await c.login();
    check('L2-tampered-state-detected', 'behavior', 'state.used edited to 0 on disk', 'state_corrupt, no browser call, not silently rebuilt', { outcome: t.final.result, calls: c.calls().length }, t.final.result === 'state_corrupt' && c.calls().length === 0);
    fs.writeFileSync(file, '{broken');
    const u = await c.login();
    check('L2-unparseable-state-detected', 'behavior', 'state.json truncated', 'state_corrupt', { outcome: u.final.result }, u.final.result === 'state_corrupt');
    fs.writeFileSync(file, original);
    const rootOverride = await runRunner(['status', '--task-id', c.task], { env: { CAPTCHA_LOGIN_ALLOW_TEST_ROOT: '' } });
    check('L2-state-root-override-refused', 'behavior', 'CAPTCHA_LOGIN_STATE_ROOT without test allowance', 'usage_error state_root_override_refused', { outcome: rootOverride.final.result, reason: rootOverride.final.reason }, rootOverride.final.result === 'usage_error' && rootOverride.final.reason === 'state_root_override_refused');
    const credArg = await runRunner(['login', '--task-id', c.task, ...c.bind, '--password', 'x']);
    check('L3-credential-arguments-refused', 'behavior', 'login --password <value>', 'usage_error credential_argument_refused', { outcome: credArg.final.result, reason: credArg.final.reason }, credArg.final.result === 'usage_error' && credArg.final.reason === 'credential_argument_refused');
    const noStdin = await runRunner(['login', '--task-id', c.task, ...c.bind, ...c.ego], { stdin: '', env: c.env });
    check('L3-stdin-required', 'behavior', 'login with empty stdin', 'usage_error credentials_stdin_invalid, zero browser calls', { outcome: noStdin.final.result, calls: c.calls().length }, noStdin.final.result === 'usage_error' && c.calls().length === 0);
  }

  // --- Ego 精确对象：不 claim、不新建 ---
  for (const [name, scenario, expected] of [
    ['user-owned', { ownership: 'user' }, 'ego_space_not_agent_owned'],
    ['page-missing', { pages: [{ label: 'p2' }] }, 'ego_page_missing'],
    ['other-url', { startUrl: 'http://elsewhere.invalid/' }, 'unexpected_page'],
  ]) {
    const c = await makeCase(`ego-${name}`, scenario);
    const r = await c.login();
    check(`L4-ego-${name}`, 'behavior', `Ego object ${name}`, `${expected}; only preflight ran, no fill/submit, count unchanged`, { outcome: r.final.result, steps: c.calls().map((x) => x.step), used: c.state().used }, r.final.result === expected && c.calls().every((x) => x.step === 'preflight') && c.state().used === 2);
  }

  // --- L3：不落盘/不回显、源码目录清洁 ---
  {
    const needles = [USER, PASS, 'FAKEIMG', 'base64,'];
    const hits = [];
    const walk = (dir) => { for (const e of fs.readdirSync(dir, { withFileTypes: true })) { const p = path.join(dir, e.name); if (e.isDirectory()) walk(p); else { const text = fs.readFileSync(p, 'utf8'); for (const n of needles) if (text.includes(n)) hits.push([path.relative(ROOT, p), n.slice(0, 6)]); if (new RegExp(`(?<![0-9])${CODE}(?![0-9])`).test(text) && !p.includes(`${path.sep}fake${path.sep}`)) hits.push([path.relative(ROOT, p), 'code']); } } };
    walk(STATE_ROOT);
    const out = allOutputs.join('\n');
    const outHits = needles.filter((n) => out.includes(n)).concat(new RegExp(`(?<![0-9])${CODE}(?![0-9])`).test(out) ? ['code'] : []);
    check('L3-nothing-persisted-or-echoed', 'behavior', 'scan every state file plus all runner stdout/stderr for synthetic username/password/captcha code/image', 'zero occurrences', { stateHits: hits, outputHits: outHits }, hits.length === 0 && outHits.length === 0);
    const sensitiveFiles = [];
    const scan = (dir) => { for (const e of fs.readdirSync(dir, { withFileTypes: true })) { const p = path.join(dir, e.name); if (e.name === '__pycache__') sensitiveFiles.push(p); else if (e.isDirectory()) scan(p); else if (/\.(png|jpe?g|gif|bmp|b64|pyc)$/i.test(e.name)) sensitiveFiles.push(p); } };
    scan(ROOT); scan(SKILL_ROOT);
    check('L3-no-image-or-bytecode-files', 'behavior', 'search test dir and skill source for image/base64/pyc files', 'none', sensitiveFiles.map((p) => path.basename(p)), sensitiveFiles.length === 0);
    const mode = process.platform === 'win32' ? null : (fs.statSync(path.join(STATE_ROOT, fs.readdirSync(STATE_ROOT).find((d) => fs.existsSync(path.join(STATE_ROOT, d, 'state.json'))), 'state.json')).mode & 0o777);
    check('L3-state-file-private', 'behavior', 'state.json permission bits (POSIX only)', '0600', mode === null ? 'n/a (Windows ACL not inspected)' : mode.toString(8), mode === null || mode === 0o600);
  }

  // --- L3：既有入口与文档兼容（静态） ---
  {
    const untouched = ['scripts/Manage-LocalVaultCredential.ps1', 'scripts/Invoke-LocalVaultWebLogin.ps1', 'scripts/Initialize-LocalVaultBrowserRuntime.ps1', 'scripts/local-web-login-runner.mjs', 'browser-login/package.json', 'browser-login/package-lock.json', 'agents/openai.yaml'];
    const missing = untouched.filter((f) => !fs.existsSync(path.join(SKILL_ROOT, f)));
    check('L3-compat-files-present', 'behavior', 'existing Save/Info/Invoke/manual-login files exist', 'all present', missing, missing.length === 0);
    if (REPO) {
      const rel = (f) => `${path.relative(REPO, SKILL_ROOT).replace(/\\/g, '/')}/${f}`;
      const diff = spawnSync('git', ['-c', 'safe.directory=*', '-C', REPO, 'diff', '--name-only', 'HEAD', '--', ...untouched.map(rel)], { encoding: 'utf8' });
      check('L3-compat-files-byte-identical', 'behavior', 'git diff HEAD over untouched existing files', 'empty diff', diff.status === 0 ? diff.stdout.trim().split('\n').filter(Boolean) : `git failed (${diff.status})`, diff.status === 0 && !diff.stdout.trim());
    } else notCovered('L3-compat-files-byte-identical', 'behavior', 'git diff HEAD over untouched existing files', 'empty diff', 'CAPTCHA_TEST_REPO not set');
    const skill = fs.readFileSync(path.join(SKILL_ROOT, 'SKILL.md'), 'utf8');
    const docs = fs.readFileSync(path.join(SKILL_ROOT, 'references', 'captcha-login.md'), 'utf8');
    const compat = ['-Action Save', '-Action Info', '-Action Invoke', 'Invoke-LocalVaultWebLogin.ps1', 'vault_login.cjs', 'references/captcha-login.md'].filter((s) => !skill.includes(s));
    check('L3-skill-keeps-existing-actions', 'behavior', 'SKILL.md mentions Save/Info/Invoke, manual web login and the new reference', 'nothing missing', compat, compat.length === 0);
    check('L3-skill-metadata', 'behavior', 'SKILL.md frontmatter markers', 'x-custom-skill true and x-source-repo dmdmwshr/custom-skills', skill.slice(0, 400), /x-custom-skill: true/.test(skill.slice(0, 600)) && /x-source-repo: dmdmwshr\/custom-skills/.test(skill.slice(0, 600)));
    const stale = ['自建skills', '验证码、\nMFA', '优先使用独立的临时 Edge/Playwright'].filter((s) => skill.includes(s));
    check('L3-skill-no-legacy-conflicts', 'behavior', 'SKILL.md has no legacy checkout path nor blanket manual-captcha/default-temp-Playwright text', 'none', stale, stale.length === 0);
    const needed = ['Ego', '.codex\\skills\\localvault-credentials', 'init', 'historical-used', 'reconcile', 'blocked-reviewed', 'blocked_reconcile_required', 'userinfo', '--account-selector', '--business-selector', '不回退', 'stdin', '0.8', '最多 5', 'not-covered'].filter((s) => !docs.includes(s));
    check('L4-contract-sections', 'behavior', 'references/captcha-login.md covers Ego target, managed entry, init/reconcile, stdin-only credentials, 0.8 gate, budget', 'nothing missing', needed, needed.length === 0);
    const passwordArg = /-(?:Password|passwd)\b|--password/i.test(docs + skill);
    check('L3-docs-never-pass-password-as-argument', 'behavior', 'grep docs for password arguments', 'none', passwordArg, !passwordArg);
  }

  // --- L1：真实离线 RapidOCR（仅合成图片；缺依赖则 not-covered） ---
  if (!REAL_PY) {
    notCovered('L1-real-rapidocr', 'behavior', 'real captcha-ocr.py on synthetic digit images', 'accepted for clear digits, rejected for blank/noise', 'CAPTCHA_TEST_PYTHON not set (offline RapidOCR not exercised on this host)');
    notCovered('L1-e2e-real-ocr-fake-ego', 'behavior', 'runner + real worker + fake Ego', 'submission with the OCR code', 'CAPTCHA_TEST_PYTHON not set');
  } else {
    const gen = `
import sys,io,base64,random
from PIL import Image, ImageDraw, ImageFont
digits, mode = sys.argv[1], sys.argv[2]
img = Image.new('RGB', (120, 40), (255, 255, 255)); d = ImageDraw.Draw(img)
if mode == 'clear': d.text((10, 2), digits, fill=(20, 20, 20), font=ImageFont.load_default(size=30))
elif mode == 'noise':
    random.seed(7)
    for x in range(120):
        for y in range(40): img.putpixel((x, y), (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255)))
buf = io.BytesIO(); img.save(buf, 'PNG'); sys.stdout.write('data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode())
`;
    const image = (digits, mode) => spawnSync(REAL_PY, ['-B', '-c', gen, digits, mode], { encoding: 'utf8', env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1' } }).stdout;
    const ocr = (dataUrl) => { const r = spawnSync(REAL_PY, ['-B', OCR_WORKER], { input: dataUrl, encoding: 'utf8', env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1' }, timeout: 60000 }); try { return JSON.parse(r.stdout.trim()); } catch { return { ok: false, error: 'unparseable' }; } };
    const clear = ['4827', '1953', CODE].map((d) => ({ d, r: ocr(image(d, 'clear')) }));
    check('L1-real-rapidocr-clear-digits', 'behavior', 'real offline RapidOCR on three synthetic clear 4-digit images', 'accepted with exact code and score >= 0.8, ocrMs reported', clear.map((x) => ({ want: x.d, got: x.r.code, score: x.r.score, accepted: x.r.accepted, ocrMs: x.r.ocrMs })), clear.every((x) => x.r.ok && x.r.accepted && x.r.code === x.d && x.r.score >= 0.8));
    const blank = ocr(image('0000', 'blank')); const noise = ocr(image('0000', 'noise'));
    check('L1-real-rapidocr-rejects-blank-noise', 'behavior', 'real offline RapidOCR on blank and random-noise images', 'not accepted (no code emitted)', { blank: { accepted: blank.accepted, code: blank.code }, noise: { accepted: noise.accepted, code: noise.code } }, blank.ok && noise.ok && !blank.accepted && !noise.accepted && blank.code === '' && noise.code === '');
    check('L1-real-rapidocr-offline-cpu', 'behavior', 'worker source', 'RapidOCR only, threads=1, use_det=False/use_cls=False, no network libraries', 'checked statically', (() => { const src = fs.readFileSync(OCR_WORKER, 'utf8'); return /intra_op_num_threads=1/.test(src) && /inter_op_num_threads=1/.test(src) && /use_det=False/.test(src) && /use_cls=False/.test(src) && !/urllib|requests|socket|http/.test(src); })());
    const c = await makeCase('real-ocr', { submits: ['captcha_error', 'ok'], images: [Buffer.from('x').toString('base64')] }, [{ code: CODE, score: 0.93 }]);
    // 用真实合成图替换假页面图片，并让真实 worker 识别
    const pngA = image('4827', 'clear').split(',')[1]; const pngB = image(CODE, 'clear').split(',')[1];
    fs.writeFileSync(path.join(c.dir, 'scenario.json'), JSON.stringify({ startUrl: LOGIN, expectCode: '1953', submits: ['ok'], images: [pngA, image('1953', 'clear').split(',')[1], pngB] }));
    const r = await runIt(['login', '--task-id', c.task, ...c.bind, '--space', '5', '--page', 'p1', '--ego-bin', process.execPath, '--ego-arg', FAKE_EGO_PATH, '--python', REAL_PY, '--step-timeout-ms', '20000', '--ocr-timeout-ms', '60000'], { stdin: CRED, env: c.env });
    check('L1-e2e-real-ocr-fake-ego', 'behavior', 'runner + real offline worker + fake Ego (synthetic images, no browser)', 'refreshed image recognised as 1953 and submitted once; login_verified', { outcome: r.final?.result, used: r.final?.used, codeOk: c.page().fills.codeOk, submissionEvent: r.lines.find((l) => l.event === 'submission') }, r.final?.result === 'login_verified' && c.page().fills.codeOk === true && c.page().submits === 1);
  }

  const summary = { pass: observations.filter((o) => o.status === 'pass').length, fail: observations.filter((o) => o.status === 'fail').length, notCovered: observations.filter((o) => o.status === 'not-covered').length };
  const report = { schema: 1, run: RUN, platform: process.platform, node: process.version, testDir: ROOT, summary, observations };
  if (process.env.CAPTCHA_TEST_REPORT) fs.writeFileSync(process.env.CAPTCHA_TEST_REPORT, JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ summary, platform: process.platform, node: process.version, failed: observations.filter((o) => o.status === 'fail').map((o) => o.id) }));
  process.exitCode = summary.fail === 0 ? 0 : 1;
}

await main();
