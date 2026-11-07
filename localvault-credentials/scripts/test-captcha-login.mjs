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
    check('L4-happy-verified', 'behavior', 'login with OCR accepted, one submission, reload verify', 'login_verified; total 2->3 and the round closes (used 0, remaining 5); URL+account+marker checked after reload', { outcome: f.result, used: f.used, total: f.total, remaining: f.remaining, verify: f.verify }, f.result === 'login_verified' && f.total === 3 && f.used === 0 && f.remaining === 5 && f.verify?.urlOk && f.verify?.accountState === 'ok' && f.verify?.markerState === 'ok' && !f.verify?.loginForm && r.code === 0);
    const calls = c.calls();
    check('L4-reload-before-verdict', 'behavior', 'calls order', 'verify step (reload) runs after the submit step', calls.map((x) => x.step), calls.map((x) => x.step).join(',') === 'preflight,fill,refresh,submit,verify');
    const fills = c.page().fills;
    check('L3-credentials-used-correctly', 'behavior', 'fake page compared sha256 of filled values', 'username/password/captcha filled exactly once with expected values', fills, fills.userOk && fills.passOk && fills.codeOk);
    check('L3-password-only-in-fill-script', 'behavior', 'which generated scripts contain the password', 'only the fill step carries credentials; no step carries the captcha code except submit', calls.map((x) => [x.step, x.hasPassword, x.hasCode]), calls.every((x) => x.hasPassword === (x.step === 'fill')) && calls.every((x) => x.hasCode === (x.step === 'submit')));
    const ev = c.state().events.map((e) => e.kind);
    check('L2-events-whitelist', 'behavior', 'state events', 'intent persisted before outcome and verify, then the round closes (round_reset)', ev, ev.join(',') === 'init,submit_intent,submit_outcome,verify,round_reset');
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
    check('L4-already-logged-in', 'behavior', 'start on business page', 'already_logged_in_verified, zero submissions, total unchanged (2); the verified session closes the open round (used 0)', { outcome: r.final.result, used: r.final.used, total: r.final.total, submits: c.page().submits }, r.final.result === 'already_logged_in_verified' && r.final.total === 2 && r.final.used === 0 && c.page().submits === 0);
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
    check('L2-captcha-error-retries', 'behavior', 'captcha_error then ok', 'second refresh+submit, total 2->4, login_verified, round closed (used 0)', { outcome: r.final.result, used: r.final.used, total: r.final.total, refresh: count(c.calls(), 'refresh'), submits: c.page().submits }, r.final.result === 'login_verified' && r.final.total === 4 && r.final.used === 0 && count(c.calls(), 'refresh') === 2 && c.page().submits === 2);
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
    check(`L2-${name}-login-possible-only-after-unblock`, 'behavior', 'login after the evidence-backed unblock', 'proceeds (second click) and succeeds, total 4, round closed (used 0)', { outcome: next.final.result, used: c.state().used, total: c.state().total, submits: c.page().submits }, next.final.result === 'login_verified' && c.state().total === 4 && c.state().used === 0 && c.page().submits === 2);
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
    check('L2-new-captcha-feedback-beside-stale-retries', 'behavior', 'stale alert present AND a new captcha error appears after the click', 'new feedback counts: retry once more and succeed, total 4, round closed', { outcome: r.final.result, used: r.final.used, total: r.final.total, submits: c.page().submits }, r.final.result === 'login_verified' && r.final.total === 4 && r.final.used === 0 && c.page().submits === 2);
  }
  {
    const c = await makeCase('stale-credential', { submits: ['captcha_error', 'ok'], staleMessages: ['用户名或密码错误'] });
    const r = await c.login();
    check('L2-stale-credential-alert-does-not-stop-new-captcha-retry', 'behavior', 'stale credential alert unchanged, genuine new captcha error', 'classified by the new feedback only (captcha_error then success)', { outcome: r.final.result, used: r.final.used, total: r.final.total }, r.final.result === 'login_verified' && r.final.total === 4 && r.final.used === 0);
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
    check('L4-stderr-only-markers-success', 'behavior', 'every Ego step prints its @@R@@/@@STAGE@@ markers only on stderr (as the real Ego does), exit 0', 'login_verified through the normal runner path; total 2→3, round closed', { outcome: r.final.result, used: r.final.used, total: r.final.total, steps: c.calls().map((x) => x.step) }, r.final.result === 'login_verified' && r.final.total === 3 && r.final.used === 0 && c.calls().map((x) => x.step).join(',') === 'preflight,fill,refresh,submit,verify');
  }
  {
    const c = await makeCase('chan-stderr-noise', { submits: ['ok'], channel: 'stderr', noiseStderr: true, noiseHuge: true });
    const r = await c.login();
    check('L4-stderr-noise-and-oversize-ignored', 'behavior', 'stderr also carries plain noise, a mid-line “[log] @@R@@…”, marker-prefixed non-JSON/array lines BEFORE the real result, 3000 long noise lines, one 4.5 MB line and an echo of the script secret', 'only the exact real marker lines count: login_verified; nothing from stderr is echoed or persisted (global scan below)', { outcome: r.final.result, used: r.final.used, total: r.final.total }, r.final.result === 'login_verified' && r.final.total === 3 && r.final.used === 0);
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

  // --- a20：每轮连续 5 次（成功归零）、累计 total 单列、schema 1 → 2 迁移 ---
  const readState = (task) => JSON.parse(fs.readFileSync(path.join(STATE_ROOT, task, 'state.json'), 'utf8'));
  const stateFile = (task) => path.join(STATE_ROOT, task, 'state.json');
  const fileSha = (f) => sha(fs.readFileSync(f));
  const expire = (c) => { // 模拟会话过期：回到登录页，保留已提交次数
    const f = path.join(c.dir, 'page.json');
    const s = JSON.parse(fs.readFileSync(f, 'utf8'));
    Object.assign(s, { loggedIn: false, url: LOGIN, acct: false, pw: false, code: '', messages: [] });
    fs.writeFileSync(f, JSON.stringify(s));
  };
  const intents = (task) => readState(task).events.filter((e) => e.kind === 'submit_intent').map((e) => e.intent);
  const strictlyIncreasing = (list) => list.every((v, i) => i === 0 || Number(v.split('#').pop()) > Number(list[i - 1].split('#').pop()));
  const canon = (value) => JSON.stringify(value, (_k, v) => (v && typeof v === 'object' && !Array.isArray(v) ? Object.fromEntries(Object.keys(v).sort().map((k) => [k, v[k]])) : v));
  const BINDING_OBJ = { profile: 'fire-supervision', targetUrl: LOGIN, expectedAccount: ACCOUNT, businessMarker: MARKER, accountSelector: '.account-name', businessSelector: '.business-marker', secretName: 'synthetic-secret-name', maxSubmissions: 5 };
  const stamp = (state) => { const { integrity: _x, ...body } = state; state.integrity = sha(canon(body)); return state; };
  const fullOk = { outcome: 'login_verified', urlOk: true, loginForm: false, accountState: 'ok', markerState: 'ok' };
  // 构造一个“旧 schema 1”状态：steps 依次是 intent / outcome / verifyOK / verifyBare / verifyFail / unknownOutcome / blocked
  function legacyState(task, binding, steps, { hist = 3, pending = false, blocked = false } = {}) {
    const s = { schema: 1, taskId: task, binding, bindingSha256: sha(canon(binding)), max: binding.maxSubmissions, used: hist, historical: { used: hist, evidenceRef: EVIDENCE, evidenceSha256: EVIDENCE_SHA, at: '2026-11-06T00:00:00.000Z' }, pending: null, blocked: null, seq: 0, events: [] };
    const push = (kind, f = {}) => { s.seq += 1; s.events.push({ seq: s.seq, at: '2026-11-06T00:00:0' + (s.seq % 10) + '.000Z', kind, ...f }); };
    push('init', { historicalUsed: hist, max: s.max });
    for (const step of steps) {
      if (step === 'intent') { s.used += 1; push('submit_intent', { intent: `${task}#${s.used}`, used: s.used, max: s.max, score: 0.9, ocrMs: 5 }); }
      else if (step === 'outcome') push('submit_outcome', { intent: `${task}#${s.used}`, classification: 'left_login', used: s.used });
      else if (step === 'unknownOutcome') push('submit_unknown', { intent: `${task}#${s.used}` });
      else if (step === 'verifyOK') push('verify', fullOk);
      else if (step === 'verifyBare') push('verify', { via: 'already-on-business-page', outcome: 'login_verified' });
      else if (step === 'verifyFail') push('verify', { outcome: 'account_mismatch', urlOk: true, loginForm: false, accountState: 'mismatch', markerState: 'ok' });
      else if (step === 'blockedEvent') push('blocked', { reason: 'verify_failed', intent: `${task}#${s.used}` });
    }
    if (pending) s.pending = { intent: `${task}#${s.used}`, at: '2026-11-06T00:00:00.000Z', stage: 'unknown' };
    if (blocked) s.blocked = { reason: 'credential_error', intent: `${task}#${s.used}`, at: '2026-11-06T00:00:00.000Z' };
    return stamp(s);
  }
  const putLegacy = (c, state) => fs.writeFileSync(stateFile(c.task), JSON.stringify(state, null, 2));
  const migrateArgs = (c) => ['migrate', '--task-id', c.task, ...c.bind];

  // (1) 同 task 两次完整成功：每次归零，累计单调，过期后再次登录仍有完整 5 次
  {
    const c = await makeCase('a20-two-rounds', { submits: ['ok', 'ok'] }, null, { historical: 0 });
    const r1 = await c.login();
    const s1 = c.state();
    check('A20-1-first-success-zeroes', 'behavior', 'fresh task (0), one captcha attempt then full reload-verified success', 'login_verified; used 0 remaining 5 total 1 roundsCompleted 1; round_reset event; nothing pending/blocked', { outcome: r1.final.result, used: s1.used, total: s1.total, rounds: s1.roundsCompleted, remaining: r1.final.remaining, pending: s1.pending, blocked: s1.blocked }, r1.final.result === 'login_verified' && s1.used === 0 && s1.total === 1 && s1.roundsCompleted === 1 && r1.final.remaining === 5 && r1.final.total === 1 && !s1.pending && !s1.blocked && s1.events.some((e) => e.kind === 'round_reset'));
    const again = await c.login();
    check('A20-1-already-session-idempotent', 'behavior', 'login again while the correct session is still open', 'already_logged_in_verified, zero submissions, no new round (roundsCompleted stays 1, no second round_reset)', { outcome: again.final.result, rounds: c.state().roundsCompleted, resets: c.state().events.filter((e) => e.kind === 'round_reset').length, submits: c.page().submits }, again.final.result === 'already_logged_in_verified' && c.state().roundsCompleted === 1 && c.state().events.filter((e) => e.kind === 'round_reset').length === 1 && c.page().submits === 1);
    expire(c);
    const r2 = await c.login();
    const s2 = c.state();
    check('A20-1-second-round-after-expiry', 'behavior', 'session expired, login again, success', 'login_verified; used 0 remaining 5; total 2 (monotonic); roundsCompleted 2; intents unique and strictly increasing', { outcome: r2.final.result, used: s2.used, total: s2.total, rounds: s2.roundsCompleted, intents: intents(c.task) }, r2.final.result === 'login_verified' && s2.used === 0 && s2.total === 2 && s2.roundsCompleted === 2 && new Set(intents(c.task)).size === 2 && strictlyIncreasing(intents(c.task)));
    // 第三次过期登录：全是验证码错误，仍可完整提交 5 次，不因累计 2 次而少
    expire(c);
    const r3 = await c.login(['--run-submission-cap', '5']);
    check('A20-1-fresh-five-after-two-successes', 'behavior', 'third login (expired again), every captcha wrong, run cap 5', 'exactly 5 more submissions in this round, then submission_budget_exhausted; used 5 total 7', { outcome: r3.final.result, used: c.state().used, total: c.state().total, submits: c.page().submits }, r3.final.result === 'submission_budget_exhausted' && c.state().used === 5 && c.state().total === 7 && c.page().submits === 7);
  }

  // (2) 验证码错误跨进程累计 5 次后停；刷新不扣提交且有界
  {
    const c = await makeCase('a20-captcha-5', { submits: [] }, [{ code: CODE, score: 0.5 }, { code: CODE, score: 0.93 }], { historical: 0 });
    const a = await c.login(['--run-submission-cap', '2']);
    const b = await c.login(['--run-submission-cap', '2']);
    const d = await c.login(['--run-submission-cap', '2']);
    const e = await c.login(['--run-submission-cap', '2']);
    const rejected = c.state().events.filter((x) => x.kind === 'ocr_rejected').length;
    check('A20-2-captcha-errors-accumulate-across-processes', 'behavior', 'four separate CLI processes, every submission a captcha error, OCR alternates reject/accept, cap 2 per run', 'runs stop at 2,2,1 submissions (cap, cap, budget); 4th process stops before filling; the round never exceeds 5', { outcomes: [a, b, d, e].map((x) => x.final.result), used: c.state().used, total: c.state().total, submits: c.page().submits, fillsInLastRun: e.final.step ?? null }, [a, b].every((x) => x.final.result === 'run_submission_cap_reached') && d.final.result === 'submission_budget_exhausted' && e.final.result === 'submission_budget_exhausted' && c.state().used === 5 && c.state().total === 5 && c.page().submits === 5);
    check('A20-2-refresh-not-counted-and-bounded', 'behavior', 'OCR rejected rounds between submissions', 'rejections never consumed submissions (used equals real clicks); every run bounded by max-ocr-rounds', { rejected, used: c.state().used, clicks: c.page().submits }, rejected >= 4 && c.state().used === c.page().submits);
    const bounded = await makeCase('a20-refresh-bounded', { submits: [] }, [{ code: CODE, score: 0.3 }], { historical: 0 });
    const rb = await bounded.login(['--max-ocr-rounds', '4']);
    check('A20-2-ocr-refresh-bounded-no-spend', 'behavior', 'OCR never accepts, max-ocr-rounds 4', 'ocr_rounds_exhausted after exactly 4 refresh rounds; used and total stay 0', { outcome: rb.final.result, refresh: count(bounded.calls(), 'refresh'), used: bounded.state().used, total: bounded.state().total }, rb.final.result === 'ocr_rounds_exhausted' && count(bounded.calls(), 'refresh') === 4 && bounded.state().used === 0 && bounded.state().total === 0);
  }

  // (3) 第 5 次成功仍可核验并归零；连续失败 5 次后下一命令不补额度
  {
    const c = await makeCase('a20-fifth-success', { submits: ['captcha_error', 'captcha_error', 'captcha_error', 'captcha_error', 'ok'] }, null, { historical: 0 });
    const r = await c.login(['--run-submission-cap', '5']);
    check('A20-3-fifth-submission-success-zeroes', 'behavior', 'four wrong captchas then the fifth attempt succeeds', 'login_verified with full reload check; used 0 remaining 5 total 5; round closed after 5 submissions', { outcome: r.final.result, used: c.state().used, total: c.state().total, closed: r.final.roundClosed }, r.final.result === 'login_verified' && c.state().used === 0 && c.state().total === 5 && r.final.roundClosed === true);
    const f = await makeCase('a20-fifth-failure', { submits: [] }, null, { historical: 0 });
    await f.login(['--run-submission-cap', '5']);
    const before = f.calls().length;
    const next = await f.login(['--run-submission-cap', '5']);
    const stat = await f.status();
    check('A20-3-five-failures-no-new-round', 'behavior', 'five consecutive captcha errors, then another login and a status call', 'next login stops with submission_budget_exhausted before any fill/refresh/submit; used stays 5 remaining 0; status read-only does not refill', { outcome: next.final.result, newSteps: f.calls().slice(before).map((x) => x.step), used: f.state().used, status: { used: stat.final.used, remaining: stat.final.remaining } }, next.final.result === 'submission_budget_exhausted' && f.calls().slice(before).every((x) => x.step === 'preflight') && f.state().used === 5 && stat.final.used === 5 && stat.final.remaining === 0);
  }

  // (4) 账号 / 业务 / URL / 刷新 任一不通过：不归零
  for (const [name, extra, outcomes] of [
    ['wrong-account', { submits: ['ok'], accountText: '别人' }, ['account_mismatch']],
    ['missing-business', { submits: ['ok'], markerNodes: [] }, ['verify_failed']],
    ['wrong-url', { submits: ['ok'], okUrl: 'http://elsewhere.invalid/#/wel/index' }, ['verify_failed', 'submit_result_unknown']],
    ['reload-loses-session', { submits: ['ok'], reloadLosesSession: true }, ['not_logged_in']],
  ]) {
    const c = await makeCase(`a20-nozero-${name}`, extra, null, { historical: 0 });
    const r = await c.login();
    const s = c.state();
    check(`A20-4-${name}-does-not-zero`, 'behavior', `submission leaves the login page but the full reload check fails (${name})`, `${outcomes.join(' or ')}, protected (blocked or pending), used stays 1 (no zero), roundsCompleted 0, no round_reset`, { outcome: r.final.result, used: s.used, total: s.total, rounds: s.roundsCompleted, blocked: s.blocked?.reason, pending: s.pending?.stage, resets: s.events.filter((e) => e.kind === 'round_reset').length }, outcomes.includes(r.final.result) && s.used === 1 && s.total === 1 && s.roundsCompleted === 0 && (!!s.blocked || !!s.pending) && s.events.every((e) => e.kind !== 'round_reset'));
  }
  {
    const c = await makeCase('a20-verify-cmd', { startUrl: BUSINESS, accountText: '别人' }, null, { historical: 2 });
    fs.writeFileSync(path.join(c.dir, 'page.json'), JSON.stringify({ url: BUSINESS, acct: false, pw: false, code: '', imgIdx: 0, imgSrc: 'data:image/png;base64,FAKEIMG0', submits: 0, loggedIn: true, messages: [], fills: {} }));
    const bad = await runIt(['verify', '--task-id', c.task, ...c.bind, ...c.ego], { env: c.env });
    check('A20-4-verify-command-mismatch-keeps-count', 'behavior', 'verify on a business page with a different account label', 'account_mismatch; used stays 2, no zero', { outcome: bad.final.result, used: c.state().used }, bad.final.result === 'account_mismatch' && c.state().used === 2 && c.state().roundsCompleted === 0);
    const good = await makeCase('a20-verify-cmd-ok', { startUrl: BUSINESS }, null, { historical: 2 });
    fs.writeFileSync(path.join(good.dir, 'page.json'), JSON.stringify({ url: BUSINESS, acct: false, pw: false, code: '', imgIdx: 0, imgSrc: 'data:image/png;base64,FAKEIMG0', submits: 0, loggedIn: true, messages: [], fills: {} }));
    const v1 = await runIt(['verify', '--task-id', good.task, ...good.bind, ...good.ego], { env: good.env });
    const v2 = await runIt(['verify', '--task-id', good.task, ...good.bind, ...good.ego], { env: good.env });
    check('A20-4-verify-command-zeroes-once', 'behavior', 'two verify commands on a correct session carrying 2 open attempts', 'first closes the round (used 0, total 2, roundsCompleted 1); second is idempotent (still 1, one round_reset)', { first: v1.final.roundClosed, second: v2.final.roundClosed, state: { used: good.state().used, total: good.state().total, rounds: good.state().roundsCompleted }, resets: good.state().events.filter((e) => e.kind === 'round_reset').length }, v1.final.roundClosed === true && v2.final.roundClosed === false && good.state().used === 0 && good.state().total === 2 && good.state().roundsCompleted === 1 && good.state().events.filter((e) => e.kind === 'round_reset').length === 1);
  }

  // (5) pending / unknown / blocked 不被成功、迁移、status 清除；not-submitted 只退本次意图；intent 编号唯一
  {
    const c = await makeCase('a20-protected', { submits: ['ok', 'unknown', 'ok'] }, null, { historical: 0 });
    await c.login();
    expire(c);
    const u = await c.login();
    check('A20-5-unknown-pending-after-closed-round', 'behavior', 'round 1 succeeds (closed), round 2 submission result unknown', 'submit_result_unknown, pending kept; used 1 total 2', { outcome: u.final.result, used: c.state().used, total: c.state().total, pending: c.state().pending?.stage }, u.final.result === 'submit_result_unknown' && c.state().used === 1 && c.state().total === 2 && c.state().pending?.stage === 'unknown');
    const fsBefore = fileSha(stateFile(c.task));
    const stat = await c.status();
    check('A20-5-status-strictly-read-only', 'behavior', 'status on a pending schema 2 state', 'state bytes unchanged, pending still reported', { same: fileSha(stateFile(c.task)) === fsBefore, pending: stat.final.pending?.stage }, fileSha(stateFile(c.task)) === fsBefore && stat.final.pending?.stage === 'unknown');
    fs.writeFileSync(path.join(c.dir, 'page.json'), JSON.stringify({ ...JSON.parse(fs.readFileSync(path.join(c.dir, 'page.json'), 'utf8')), loggedIn: true, url: BUSINESS }));
    const ver = await runIt(['verify', '--task-id', c.task, ...c.bind, ...c.ego], { env: c.env });
    check('A20-5-success-verify-cannot-clear-pending', 'behavior', 'a full verify success while the unknown submission is still pending', 'login_verified but pending kept, no zero (used 1), no round_reset for it', { outcome: ver.final.result, closed: ver.final.roundClosed, used: c.state().used, pending: c.state().pending?.stage }, ver.final.result === 'login_verified' && ver.final.roundClosed === false && c.state().used === 1 && c.state().pending?.stage === 'unknown' && c.state().roundsCompleted === 1);
    const refuse = await c.login();
    check('A20-5-pending-blocks-next-process', 'behavior', 'login in a new process', 'pending_reconcile_required, no browser call', { outcome: refuse.final.result }, refuse.final.result === 'pending_reconcile_required');
    const rec = await runIt(['reconcile', '--task-id', c.task, '--resolution', 'not-submitted', '--evidence-ref', EVIDENCE, '--evidence-sha256', EVIDENCE_SHA]);
    check('A20-5-not-submitted-refunds-only-this-intent', 'behavior', 'reconcile not-submitted for the round-2 intent', 'used 1→0 and total 2→1: the already-closed round 1 submission is NOT refunded', { outcome: rec.final.result, used: c.state().used, total: c.state().total, rounds: c.state().roundsCompleted }, rec.final.result === 'reconciled' && c.state().used === 0 && c.state().total === 1 && c.state().roundsCompleted === 1);
    const none = await runIt(['reconcile', '--task-id', c.task, '--resolution', 'not-submitted', '--evidence-ref', EVIDENCE, '--evidence-sha256', EVIDENCE_SHA]);
    check('A20-5-no-cross-round-refund', 'behavior', 'second not-submitted with nothing pending', 'usage_error nothing_pending; total stays 1 (cannot refund the success round)', { outcome: none.final.result, reason: none.final.reason, total: c.state().total }, none.final.result === 'usage_error' && none.final.reason === 'nothing_pending' && c.state().total === 1);
    expire(c);
    const r3 = await c.login();
    const ids = intents(c.task);
    check('A20-5-intent-ids-unique-after-refund-and-reset', 'behavior', 'next login after refund and an earlier closed round', 'login_verified; every submit_intent id is distinct and strictly increasing (refund and zeroing never reuse a number)', { outcome: r3.final.result, ids }, r3.final.result === 'login_verified' && new Set(ids).size === ids.length && ids.length === 3 && strictlyIncreasing(ids));
  }
  {
    const c = await makeCase('a20-blocked', { submits: ['credential_error'] }, null, { historical: 0 });
    await c.login();
    const stop = c.state();
    fs.writeFileSync(path.join(c.dir, 'page.json'), JSON.stringify({ ...JSON.parse(fs.readFileSync(path.join(c.dir, 'page.json'), 'utf8')), loggedIn: true, url: BUSINESS }));
    const ver = await runIt(['verify', '--task-id', c.task, ...c.bind, ...c.ego], { env: c.env });
    check('A20-5-blocked-survives-success-verify', 'behavior', 'credential_error blocked the task; then a full verify success appears', 'blocked kept, used stays 1, no zero, login still refused', { outcome: ver.final.result, closed: ver.final.roundClosed, blocked: c.state().blocked?.reason, used: c.state().used, was: stop.blocked?.reason }, ver.final.result === 'login_verified' && ver.final.roundClosed === false && c.state().blocked?.reason === 'credential_error' && c.state().used === 1);
    const ref = await c.login();
    check('A20-5-blocked-still-refuses-login', 'behavior', 'login in a new process', 'blocked_reconcile_required', ref.final.result, ref.final.result === 'blocked_reconcile_required');
  }

  // (6) schema 1 → 2 迁移
  {
    const c = await makeCase('a20-legacy-ok', { submits: ['ok'] }, null, { historical: 0 });
    putLegacy(c, legacyState(c.task, BINDING_OBJ, ['intent', 'outcome', 'verifyOK', 'intent', 'outcome', 'verifyOK']));
    const legacyBytes = fs.readFileSync(stateFile(c.task));
    const legacySha = sha(legacyBytes);
    const st = await c.status();
    check('A20-6-status-on-schema1-read-only', 'behavior', 'status on a schema 1 state that has a trusted last success', 'legacy cumulative numbers reported (used 5), migrationRequired true, migrationWouldReset true; bytes unchanged', { used: st.final.used, total: st.final.total, req: st.final.migrationRequired, would: st.final.migrationWouldReset, same: fileSha(stateFile(c.task)) === legacySha }, st.final.used === 5 && st.final.migrationRequired === true && st.final.migrationWouldReset === true && fileSha(stateFile(c.task)) === legacySha);
    const ref = await c.login();
    check('A20-6-login-refuses-unmigrated', 'behavior', 'login/verify on schema 1', 'migration_required (exit 31), zero browser calls, state untouched', { outcome: ref.final.result, code: ref.code, calls: c.calls().length, same: fileSha(stateFile(c.task)) === legacySha }, ref.final.result === 'migration_required' && ref.code === 31 && c.calls().length === 0 && fileSha(stateFile(c.task)) === legacySha);
    const m = await runIt(migrateArgs(c));
    const s = c.state();
    const backup = path.join(STATE_ROOT, c.task, m.final.backup ?? 'missing');
    check('A20-6-migrate-trusted-success-zeroes', 'behavior', 'migrate a legacy used=5 state whose last events are a full verify success', 'migrated: used 0 remaining 5 total 5 roundsCompleted 2; pending/blocked null; history events + migration event kept; backup is the exact original bytes', { outcome: m.final.result, used: s.used, total: s.total, rounds: s.roundsCompleted, reset: m.final.reset, backupSame: fs.existsSync(backup) && fileSha(backup) === legacySha, migration: s.migration?.reason, events: s.events.map((e) => e.kind) }, m.final.result === 'migrated' && s.schema === 2 && s.used === 0 && s.total === 5 && s.roundsCompleted === 2 && m.final.reset === true && !s.pending && !s.blocked && fs.existsSync(backup) && fileSha(backup) === legacySha && s.migration.previousFileSha256 === legacySha && s.events.length === 8 && s.events.at(-1).kind === 'migrated_schema' && s.events.slice(0, 7).map((e) => e.kind).join(',') === 'init,submit_intent,submit_outcome,verify,submit_intent,submit_outcome,verify');
    const afterBytes = fileSha(stateFile(c.task));
    const m2 = await runIt(migrateArgs(c));
    check('A20-6-migrate-idempotent', 'behavior', 'migrate a second time', 'already_migrated; state bytes unchanged; still exactly one migrated_schema event', { outcome: m2.final.result, same: fileSha(stateFile(c.task)) === afterBytes, count: c.state().events.filter((e) => e.kind === 'migrated_schema').length }, m2.final.result === 'already_migrated' && fileSha(stateFile(c.task)) === afterBytes && c.state().events.filter((e) => e.kind === 'migrated_schema').length === 1);
    const lg = await c.login();
    check('A20-6-login-works-after-migrate', 'behavior', 'normal login on the migrated task (login page, correct captcha)', 'login_verified; total 6; used 0; intent id continues after 5', { outcome: lg.final.result, total: c.state().total, used: c.state().used, ids: intents(c.task) }, lg.final.result === 'login_verified' && c.state().total === 6 && c.state().used === 0 && intents(c.task).at(-1).endsWith('#6'));
  }
  const conservative = [
    ['no-success', ['intent', 'outcome'], {}, 'no_trusted_success', 4],
    ['failure-after-success', ['intent', 'outcome', 'verifyOK', 'intent', 'outcome'], {}, 'unfinished_after_success', 5],
    ['unknown-after-success', ['intent', 'outcome', 'verifyOK', 'intent', 'unknownOutcome'], { pending: true }, 'pending_kept', 5],
    ['blocked-state', ['intent', 'outcome', 'verifyOK', 'blockedEvent'], { blocked: true }, 'blocked_kept', 4],
    ['bare-success-string-only', ['intent', 'outcome', 'verifyBare'], {}, 'no_trusted_success', 4],
    ['failed-verify-last', ['intent', 'outcome', 'verifyOK', 'verifyFail'], {}, 'unfinished_after_success', 4],
  ];
  for (const [name, steps, opts, reason, usedExpected] of conservative) {
    const c = await makeCase(`a20-legacy-${name}`, { submits: ['ok'] }, null, { historical: 0 });
    const st = legacyState(c.task, BINDING_OBJ, steps, opts);
    putLegacy(c, st);
    const m = await runIt(migrateArgs(c));
    const s = c.state();
    check(`A20-6-conservative-${name}`, 'behavior', `migrate a legacy state without a clean trusted success (${name})`, `no credit: reset false (${reason}); used stays ${usedExpected}, total ${usedExpected}; pending/blocked preserved exactly`, { outcome: m.final.result, reason: m.final.reason, reset: m.final.reset, used: s.used, total: s.total, pending: !!s.pending, blocked: !!s.blocked }, m.final.result === 'migrated' && m.final.reset === false && m.final.reason === reason && s.used === usedExpected && s.total === usedExpected && !!s.pending === !!opts.pending && !!s.blocked === !!opts.blocked);
    if (opts.pending || opts.blocked) {
      const lg = await c.login();
      check(`A20-6-protected-after-migrate-${name}`, 'behavior', 'login after migrating a pending/blocked legacy state', opts.pending ? 'pending_reconcile_required' : 'blocked_reconcile_required', lg.final.result, lg.final.result === (opts.pending ? 'pending_reconcile_required' : 'blocked_reconcile_required') && c.calls().length === 0);
    }
  }
  {
    const c = await makeCase('a20-legacy-tampered', { submits: ['ok'] }, null, { historical: 0 });
    const st = legacyState(c.task, BINDING_OBJ, ['intent', 'outcome', 'verifyOK']);
    st.used = 0; // 改了计数但不重算 integrity
    putLegacy(c, st);
    const before = fileSha(stateFile(c.task));
    const m = await runIt(migrateArgs(c));
    const unchangedA = fileSha(stateFile(c.task)) === before && fs.readdirSync(path.join(STATE_ROOT, c.task)).every((f) => !f.startsWith('state.schema1-'));
    const forged = legacyState(c.task, BINDING_OBJ, ['intent', 'outcome', 'verifyOK']);
    forged.used = 5; // 重算了 integrity、字段仍合法，但与事件回放（应为 4）不一致
    stamp(forged);
    putLegacy(c, forged);
    const m2 = await runIt(migrateArgs(c));
    check('A20-6-corrupt-and-inconsistent', 'behavior', 'migrate (a) a state whose integrity hash does not match, (b) a re-hashed state whose used disagrees with its own event log', '(a) state_corrupt, file unchanged, no backup; (b) migrated without credit (used_mismatch), used stays 5', { corrupt: m.final.result, unchangedAndNoBackup: unchangedA, inconsistent: { outcome: m2.final.result, reason: m2.final.reason, reset: m2.final.reset, used: c.state().used } }, m.final.result === 'state_corrupt' && unchangedA && m2.final.result === 'migrated' && m2.final.reason === 'used_mismatch' && m2.final.reset === false && c.state().used === 5 && fs.readdirSync(path.join(STATE_ROOT, c.task)).filter((f) => f.startsWith('state.schema1-')).length === 1);
  }
  {
    const c = await makeCase('a20-legacy-binding', { submits: ['ok'] }, null, { historical: 0 });
    putLegacy(c, legacyState(c.task, BINDING_OBJ, ['intent', 'outcome', 'verifyOK']));
    const before = fileSha(stateFile(c.task));
    const wrong = [...c.bind]; wrong[wrong.indexOf('--expected-account') + 1] = '别人';
    const m = await runIt(['migrate', '--task-id', c.task, ...wrong]);
    check('A20-6-binding-mismatch-no-write', 'behavior', 'migrate with a different expected account', 'binding_mismatch; state bytes unchanged; no backup file', { outcome: m.final.result, same: fileSha(stateFile(c.task)) === before, files: fs.readdirSync(path.join(STATE_ROOT, c.task)).sort() }, m.final.result === 'binding_mismatch' && fileSha(stateFile(c.task)) === before && fs.readdirSync(path.join(STATE_ROOT, c.task)).every((f) => !f.startsWith('state.schema1-')));
  }
  {
    const c = await makeCase('a20-legacy-concurrent', { submits: ['ok'] }, null, { historical: 0 });
    putLegacy(c, legacyState(c.task, BINDING_OBJ, ['intent', 'outcome', 'verifyOK']));
    const runs = await Promise.all(Array.from({ length: 6 }, () => runIt(migrateArgs(c))));
    const outs = runs.map((r) => r.final?.result).sort();
    const s = c.state();
    check('A20-6-concurrent-migrate-single-effect', 'behavior', 'six migrate processes at once on one schema 1 state', 'exactly one `migrated`; others `already_migrated` or `locked`; one migrated_schema event; used 0 total 4', { outcomes: outs, events: s.events.filter((e) => e.kind === 'migrated_schema').length, used: s.used, total: s.total }, outs.filter((o) => o === 'migrated').length === 1 && outs.every((o) => ['migrated', 'already_migrated', 'locked'].includes(o)) && s.events.filter((e) => e.kind === 'migrated_schema').length === 1 && s.used === 0 && s.total === 4);
    const again = await Promise.all([runIt(migrateArgs(c)), runIt(migrateArgs(c))]);
    check('A20-6-migrate-on-schema2-concurrent', 'behavior', 'two more migrates on the already-current state', 'already_migrated/locked only, state unchanged', again.map((r) => r.final.result), again.every((r) => ['already_migrated', 'locked'].includes(r.final.result)) && c.state().events.filter((e) => e.kind === 'migrated_schema').length === 1);
  }
  {
    // 冻结的正式账副本（只读输入，复制到隔离根；不复制回、不写原文件）
    const FROZEN = '/mnt/d/ws/05-temp/20261106_飞书与案卷续办/验证码Skill/预算澄清-正式账只读基线-20261107.json';
    if (!fs.existsSync(FROZEN)) notCovered('A20-6-frozen-formal-ledger-copy', 'behavior', 'migrate the frozen schema 1 copy (used 5/5, seq 7) inside the isolated root', 'used 0 / total 5 with history kept', 'frozen copy not reachable on this host');
    else {
      const srcBytes = fs.readFileSync(FROZEN);
      const srcSha = sha(srcBytes);
      const frozen = JSON.parse(srcBytes.toString('utf8'));
      const dir = path.join(STATE_ROOT, frozen.taskId);
      fs.mkdirSync(dir, { recursive: true });
      fs.writeFileSync(path.join(dir, 'state.json'), srcBytes);
      const b = frozen.binding;
      const args = ['--task-id', frozen.taskId, '--target-url', b.targetUrl, '--expected-account', b.expectedAccount, '--business-marker', b.businessMarker, '--account-selector', b.accountSelector, '--business-selector', b.businessSelector, '--secret-name', b.secretName, '--max-submissions', String(b.maxSubmissions)];
      const st = await runIt(['status', '--task-id', frozen.taskId]);
      const m = await runIt(['migrate', ...args]);
      const s = readState(frozen.taskId);
      const m2 = await runIt(['migrate', ...args]);
      const after = readState(frozen.taskId);
      check('A20-6-frozen-formal-ledger-copy', 'behavior', 'status then migrate the frozen schema 1 copy of the formal ledger (used 5/5, seq 7, last event a full verify success) in the isolated test root', 'status shows used 5 remaining 0 and would-reset; migrate → used 0 remaining 5 total 5 roundsCompleted 2, history kept, backup == frozen bytes, second migrate idempotent; the frozen file is untouched', { srcSha, statusUsed: st.final.used, would: st.final.migrationWouldReset, migrated: m.final.result, used: s.used, total: s.total, rounds: s.roundsCompleted, seq: s.seq, backupSha: fileSha(path.join(dir, m.final.backup)), second: m2.final.result, srcAfter: fileSha(FROZEN), integrityPrev: s.migration?.previousIntegrity === frozen.integrity }, srcSha === 'c31fb0fffa51c8b1204396973181f89ab4d1ca58636f87da6f5c00983c1eca95' && st.final.used === 5 && st.final.remaining === 0 && st.final.migrationWouldReset === true && m.final.result === 'migrated' && s.used === 0 && s.total === 5 && s.roundsCompleted === 2 && s.seq === 8 && fileSha(path.join(dir, m.final.backup)) === srcSha && m2.final.result === 'already_migrated' && after.events.length === 8 && fileSha(FROZEN) === srcSha && s.migration.previousIntegrity === frozen.integrity);
    }
  }

  // (7) 旧参数兼容、原子写与读取接口
  {
    const c = await makeCase('a20-compat', { submits: ['ok'] }, null, { historical: 2 });
    const st = await c.status();
    const keys = ['used', 'max', 'remaining', 'historicalUsed', 'total', 'roundsCompleted', 'schema', 'pending', 'blocked', 'lock', 'bindingSha256', 'stateRootKind'].filter((k) => !(k in st.final));
    check('A20-7-status-fields-compatible', 'behavior', 'status on a fresh schema 2 task initialised with the old init arguments (--historical-used 2)', 'old fields used/max/remaining/historicalUsed still present and meaning "this round" (2/5/3); new total/roundsCompleted/schema added', { missing: keys, used: st.final.used, max: st.final.max, remaining: st.final.remaining, total: st.final.total, schema: st.final.schema }, keys.length === 0 && st.final.used === 2 && st.final.remaining === 3 && st.final.total === 2 && st.final.schema === 2);
    const s0 = c.state();
    check('A20-7-schema2-atomic-private-file', 'behavior', 'state file written with the existing atomic/fsync path', 'integrity stamped, no tmp leftovers, no lock leftover', { files: fs.readdirSync(path.join(STATE_ROOT, c.task)).sort(), integrity: typeof s0.integrity }, JSON.stringify(fs.readdirSync(path.join(STATE_ROOT, c.task))) === JSON.stringify(['state.json']) && typeof s0.integrity === 'string');
    const crash = await makeCase('a20-crash-in-new-round', { submits: ['hang'] }, null, { historical: 0 });
    const killed = await crash.login(['--step-timeout-ms', '60000'], { killAfterMs: 2500 });
    const cs = crash.state();
    check('A20-7-intent-persisted-before-click-new-schema', 'behavior', 'runner SIGKILLed while the submit step hangs (schema 2)', 'used 1 / total 1 / pending intent already on disk, lock left behind; next process refuses with pending/locked, no replay', { signal: killed.signal, used: cs.used, total: cs.total, pending: cs.pending?.stage }, cs.used === 1 && cs.total === 1 && !!cs.pending);
  }

  // --- a21：旧账结构校验必须发生在任何持久写入之前（独验 B6 返工） ---
  {
    const baseSteps = ['intent', 'outcome', 'verifyOK'];
    const dirOf = (task) => path.join(STATE_ROOT, task);
    const cases = [
      ['events-length-not-seq', (s) => { s.seq += 1; }],
      ['max-not-binding', (s) => { s.max = 4; }],
      ['events-null', (s) => { s.events = null; }],
      ['event-seq-gap', (s) => { s.events[2].seq = 9; }],
      ['events-not-array', (s) => { s.events = { length: s.seq }; }],
      ['event-null-entry', (s) => { s.events[1] = null; }],
      ['event-missing-kind', (s) => { delete s.events[1].kind; }],
      ['seq-negative', (s) => { s.seq = -1; }],
      ['seq-fraction', (s) => { s.seq = 1.5; }],
      ['seq-string', (s) => { s.seq = String(s.seq); }],
      ['seq-null', (s) => { s.seq = null; }],
      ['seq-huge', (s) => { s.seq = 1e21; }],
      ['pending-not-object', (s) => { s.pending = 'x'; }],
      ['blocked-array', (s) => { s.blocked = []; }],
    ];
    for (const [name, mutate] of cases) {
      const c = await makeCase(`a21-bad-${name}`, { submits: ['ok'] }, null, { historical: 0 });
      const st = legacyState(c.task, BINDING_OBJ, baseSteps);
      mutate(st);
      stamp(st); // 完整性摘要重新算对：这是“结构损坏但摘要有效”的合成旧账
      putLegacy(c, st);
      const before = fileSha(stateFile(c.task));
      const m = await runIt(migrateArgs(c));
      const afterMigrate = { same: fileSha(stateFile(c.task)) === before, files: fs.readdirSync(dirOf(c.task)).sort() };
      const s1 = await c.status();
      const l1 = await c.login();
      const rc = await runIt(['reconcile', '--task-id', c.task, '--resolution', 'not-submitted', '--evidence-ref', EVIDENCE, '--evidence-sha256', EVIDENCE_SHA]);
      const noInternal = ![m, s1, l1, rc].some((r) => /internal_error|TypeError|at .*\.mjs/.test(r.stdout + r.stderr));
      check(`A21-structure-${name}`, 'behavior', `schema 1 state with valid integrity but broken structure (${name}) → migrate, status, login, reconcile`, 'every command: state_corrupt (exit 31), never internal_error; state bytes unchanged; directory holds only state.json (no backup, no tmp, no lock); zero browser calls', { migrate: [m.final?.result, m.final?.reason, m.code], status: s1.final?.result, login: [l1.final?.result, l1.code], reconcile: rc.final?.result, ...afterMigrate, sameAtEnd: fileSha(stateFile(c.task)) === before, calls: c.calls().length, noInternal }, [m, s1, l1, rc].every((r) => r.final?.result === 'state_corrupt' && r.code === 31) && noInternal && afterMigrate.same && JSON.stringify(afterMigrate.files) === JSON.stringify(['state.json']) && fileSha(stateFile(c.task)) === before && c.calls().length === 0);
    }
    // 当前 schema 同样要在使用前拒绝结构损坏
    for (const [name, mutate] of [['events-null', (s) => { s.events = null; }], ['length-not-seq', (s) => { s.seq += 1; }], ['max-not-binding', (s) => { s.max = 4; }], ['event-seq-gap', (s) => { s.events[0].seq = 5; }]]) {
      const c = await makeCase(`a21-cur-${name}`, { submits: ['ok'] }, null, { historical: 1 });
      const st = readState(c.task);
      mutate(st);
      stamp(st);
      fs.writeFileSync(stateFile(c.task), JSON.stringify(st, null, 2));
      const before = fileSha(stateFile(c.task));
      const s1 = await c.status();
      const l1 = await c.login();
      const m = await runIt(migrateArgs(c));
      check(`A21-current-schema-structure-${name}`, 'behavior', `schema 2 state with valid integrity but broken structure (${name})`, 'status/login/migrate: state_corrupt, no internal_error, bytes unchanged, zero browser calls', { status: s1.final?.result, login: l1.final?.result, migrate: m.final?.result, same: fileSha(stateFile(c.task)) === before, calls: c.calls().length }, [s1, l1, m].every((r) => r.final?.result === 'state_corrupt' && !/internal_error/.test(r.stdout + r.stderr)) && fileSha(stateFile(c.task)) === before && c.calls().length === 0 && JSON.stringify(fs.readdirSync(dirOf(c.task)).sort()) === JSON.stringify(['state.json']));
    }
    // 结构合法、仅事件回放计数与 used 不一致：保守迁移（不补额度），不等同结构损坏；迁移后同 runner 可读
    for (const [name, delta] of [['used-above-replay', +1], ['used-below-replay', -1]]) {
      const c = await makeCase(`a21-replay-${name}`, { submits: ['ok'] }, null, { historical: 0 });
      const st = legacyState(c.task, BINDING_OBJ, baseSteps);
      const replayUsed = st.used;
      st.used = replayUsed + delta; // 事件日志结构完整，但计数与回放不一致（used 仍 ≥ historical 且 ≤ max）
      stamp(st);
      putLegacy(c, st);
      const m = await runIt(migrateArgs(c));
      const afterStatus = await c.status();
      const s = c.state();
      check(`A21-replay-mismatch-conservative-${name}`, 'behavior', `structurally valid legacy state whose used (${st.used}) disagrees with its event replay (${replayUsed})`, 'migrated without credit (used_mismatch, reset false); used and total stay at the stored value; same runner then reads it (status ok); not treated as corruption', { migrate: [m.final?.result, m.final?.reason, m.final?.reset], used: s.used, total: s.total, status: afterStatus.final?.result, files: fs.readdirSync(dirOf(c.task)).sort() }, m.final?.result === 'migrated' && m.final.reason === 'used_mismatch' && m.final.reset === false && s.used === st.used && s.total === st.used && afterStatus.final?.result === 'status' && afterStatus.code === 0);
    }
    // 每条成功迁移路径之后，同一 runner 的 status 必须正常可读（迁移结果满足完整字段约束）
    for (const [name, steps, opts] of [['trusted-reset', ['intent', 'outcome', 'verifyOK'], {}], ['no-success', ['intent', 'outcome'], {}], ['pending', ['intent', 'outcome', 'verifyOK', 'intent', 'unknownOutcome'], { pending: true }], ['blocked', ['intent', 'outcome', 'verifyOK', 'blockedEvent'], { blocked: true }]]) {
      const c = await makeCase(`a21-readable-${name}`, { submits: ['ok'] }, null, { historical: 0 });
      putLegacy(c, legacyState(c.task, BINDING_OBJ, steps, opts));
      const m = await runIt(migrateArgs(c));
      const stat = await c.status();
      check(`A21-migrated-state-readable-${name}`, 'behavior', `migrate (${name}) then status with the same runner`, 'migrated, then status exit 0 with result status (no state_corrupt)', { migrate: m.final?.result, status: stat.final?.result, code: stat.code }, m.final?.result === 'migrated' && stat.final?.result === 'status' && stat.code === 0);
    }
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
    check('L1-score-boundary', 'behavior', 'score 0.79 rejected, next round 0.80 accepted', 'one rejection then one submission (total 3, round closed)', { outcome: r.final.result, rejected: r.final.ocrRejected, used: r.final.used, total: r.final.total }, r.final.result === 'login_verified' && r.final.ocrRejected === 1 && r.final.total === 3 && r.final.used === 0);
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
    check('L2-concurrent-exclusive', 'behavior', 'two logins on the same task at once', 'exactly one proceeds (login_verified), the other is locked; one submission, total 2->3 and the round closed', { outcomes, used: c.state().used, total: c.state().total, submits: c.page().submits }, outcomes.join(',') === 'locked,login_verified' && c.state().total === 3 && c.state().used === 0 && c.page().submits === 1);
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
    const needed = ['Ego', '.codex\\skills\\localvault-credentials', 'init', 'historical-used', 'reconcile', 'blocked-reviewed', 'blocked_reconcile_required', 'userinfo', '--account-selector', '--business-selector', '不回退', 'stdin', '0.8', '最多 5', 'not-covered', '每轮', '累计', 'total', 'roundsCompleted', 'migrate', 'migration_required', 'round_reset', 'state.schema1-', 'already_migrated'].filter((s) => !docs.includes(s));
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
