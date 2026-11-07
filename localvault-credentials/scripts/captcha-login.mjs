#!/usr/bin/env node
// 授权普通图片验证码登录运行器：现有 Ego 页面 + 本地离线 OCR + 持久提交计数。
// 只能经 Manage-LocalVaultCredential.ps1 -Action Invoke 由标准输入接收一次性 {username,password}。
// 凭据、验证码图片/识别码只在内存中；日志与状态文件只含白名单字段。见 references/captcha-login.md。
import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const IS_WIN = process.platform === 'win32';
const SCHEMA = 2; // 2 = 每轮连续计数 + 累计分列；1 = 旧“整任务累计”，只读/对账/迁移可识别，login/verify 要求先 migrate
const LEGACY_SCHEMA = 1;
const MIN_SCORE = 0.8;
const CODE_RE = /^[0-9]{4}$/;
const TASK_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
const SECRET_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;

const PROFILES = {
  // 消防监督系统登录页（Element UI），选择器取自已验证的真实成功登录。
  'fire-supervision': {
    selectors: {
      account: 'input[placeholder="请输入账号"]',
      password: 'input[type="password"]',
      captcha: 'input[placeholder="请输入验证码"]',
      captchaImg: 'img.login-code-img',
      submit: 'button.login-submit',
      messages: '.el-message,.el-notification,[role=alert],.el-form-item__error',
    },
  },
};
// 错误提示分类关键字：只在页面内匹配，文本本身不离开页面、不入日志。
const MESSAGE_PATTERNS = {
  captcha: '验证码|驗證碼',
  credential: '账号|帐号|账户|用户名|密码|口令',
  restricted: '锁定|限制|频繁|过多|禁止|冻结|次数|稍后再试|稍后重试',
};

const EXIT = { ok: 0, stop: 10, pending: 20, usage: 30, state: 31, lock: 32 };
class Stop extends Error {
  constructor(outcome, exit, extra = {}) {
    super(outcome);
    this.outcome = outcome;
    this.exit = exit;
    this.extra = extra;
  }
}

const secretsInMemory = new Set();
const isoNow = () => new Date().toISOString();
const sha256 = (data) => crypto.createHash('sha256').update(data).digest('hex');
const canonical = (value) => JSON.stringify(value, (_k, v) => (v && typeof v === 'object' && !Array.isArray(v)
  ? Object.fromEntries(Object.keys(v).sort().map((k) => [k, v[k]])) : v));

// 唯一输出口：序列化后再扫一次内存中的凭据，命中即抹掉。
function emit(obj) {
  let text = JSON.stringify(obj);
  for (const secret of secretsInMemory) {
    if (secret.length >= 2 && text.includes(secret)) text = text.split(secret).join('[REDACTED]');
  }
  process.stdout.write(text + '\n');
}

function parseArgs(argv) {
  const args = { _: [], 'ego-arg': [], 'ocr-flag': [] };
  const multi = new Set(['ego-arg', 'ocr-flag']);
  for (let i = 0; i < argv.length; i += 1) {
    const token = argv[i];
    if (!token.startsWith('--')) { args._.push(token); continue; }
    const eq = token.indexOf('=');
    const key = eq === -1 ? token.slice(2) : token.slice(2, eq);
    if (/^(password|username|passwd|captcha-code|code|secret)$/.test(key)) throw new Stop('usage_error', EXIT.usage, { reason: 'credential_argument_refused' });
    let value;
    if (eq !== -1) value = token.slice(eq + 1);
    else {
      value = argv[i + 1];
      if (value === undefined || value.startsWith('--')) throw new Stop('usage_error', EXIT.usage, { reason: 'missing_value', option: key.slice(0, 40) });
      i += 1;
    }
    if (multi.has(key)) args[key].push(value); else args[key] = value;
  }
  return args;
}

function intArg(args, key, { min, max, fallback }) {
  const raw = args[key];
  if (raw === undefined) {
    if (fallback === undefined) throw new Stop('usage_error', EXIT.usage, { reason: 'missing_option', option: key });
    return fallback;
  }
  if (!/^[0-9]{1,6}$/.test(raw) || Number(raw) < min || Number(raw) > max) throw new Stop('usage_error', EXIT.usage, { reason: 'bad_number', option: key });
  return Number(raw);
}

function requireText(args, key, max = 256) {
  const value = args[key];
  if (typeof value !== 'string' || !value.trim() || value.length > max) throw new Stop('usage_error', EXIT.usage, { reason: 'missing_option', option: key });
  return value;
}

function taskIdOf(args) {
  const id = requireText(args, 'task-id', 64);
  if (!TASK_RE.test(id)) throw new Stop('usage_error', EXIT.usage, { reason: 'bad_task_id' });
  return id;
}

// CSS 选择器（非秘密）：必须显式给出，且不能是会命中整页的泛选择器。
function selectorArg(args, key) {
  const value = requireText(args, key, 200).trim();
  if (/[\u0000-\u001f]/.test(value) || /^(\*|html|body|:root|document)$/i.test(value)) throw new Stop('usage_error', EXIT.usage, { reason: 'bad_selector', option: key });
  return value;
}

// ---- 绑定：同一任务固定目标/账号/密钥名/上限，参数变化不能重置计数 ----
function bindingOf(args) {
  const profile = args.profile ?? 'fire-supervision';
  if (!PROFILES[profile]) throw new Stop('usage_error', EXIT.usage, { reason: 'unknown_profile' });
  const targetUrl = requireText(args, 'target-url', 512);
  let parsed;
  try { parsed = new URL(targetUrl); } catch { throw new Stop('usage_error', EXIT.usage, { reason: 'bad_target_url' }); }
  const loginShaped = /\/login\/?$/.test(parsed.pathname) || /^#\/login(?:[/?]|$)/.test(parsed.hash);
  if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password || !loginShaped) throw new Stop('usage_error', EXIT.usage, { reason: 'bad_target_url' });
  const secretName = requireText(args, 'secret-name', 128);
  if (!SECRET_RE.test(secretName)) throw new Stop('usage_error', EXIT.usage, { reason: 'bad_secret_name' });
  // 账号与业务入口的控件定位必须由来源 Windows 负责人明确提供并纳入任务绑定；不提供就失败关闭，绝不回退到整页文本。
  const accountSelector = selectorArg(args, 'account-selector');
  const businessSelector = selectorArg(args, 'business-selector');
  return {
    profile,
    targetUrl,
    expectedAccount: requireText(args, 'expected-account', 64),
    businessMarker: requireText(args, 'business-marker', 64),
    accountSelector,
    businessSelector,
    secretName,
    maxSubmissions: intArg(args, 'max-submissions', { min: 1, max: 20 }),
  };
}

// ---- 状态：固定外部根 + 确定任务路径，原子写、fsync、排他锁 ----
function stateRoot() {
  const override = process.env.CAPTCHA_LOGIN_STATE_ROOT;
  if (override) {
    if (process.env.CAPTCHA_LOGIN_ALLOW_TEST_ROOT !== '1') throw new Stop('usage_error', EXIT.usage, { reason: 'state_root_override_refused' });
    return { root: path.resolve(override), kind: 'test-override' };
  }
  const base = IS_WIN ? process.env.LOCALAPPDATA : path.join(os.homedir(), '.local', 'state');
  if (!base) throw new Stop('usage_error', EXIT.usage, { reason: 'no_state_base' });
  return { root: path.join(base, IS_WIN ? 'LocalVaultCredentials' : 'localvault-credentials', 'captcha-login-state'), kind: 'default' };
}

function paths(taskId) {
  const { root, kind } = stateRoot();
  const dir = path.join(root, taskId);
  return { dir, state: path.join(dir, 'state.json'), lock: path.join(dir, 'lock'), kind };
}

function fsyncDir(dir) {
  if (IS_WIN) return; // Windows 不支持对目录 fsync
  const fd = fs.openSync(dir, 'r');
  try { fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
}

function atomicWrite(file, text) {
  const tmp = `${file}.tmp-${process.pid}-${crypto.randomBytes(4).toString('hex')}`;
  const fd = fs.openSync(tmp, 'wx', 0o600);
  try { fs.writeSync(fd, text); fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
  fs.renameSync(tmp, file);
  fsyncDir(path.dirname(file));
}

function stampIntegrity(state) {
  const { integrity: _drop, ...body } = state;
  state.integrity = sha256(canonical(body));
  return state;
}

function saveState(p, state) {
  stampIntegrity(state);
  atomicWrite(p.state, JSON.stringify(state, null, 2));
}

function pushEvent(state, kind, fields = {}) {
  state.seq += 1;
  state.events.push({ seq: state.seq, at: isoNow(), kind, ...fields });
}

function requireStateExists(p) {
  if (!fs.existsSync(p.state)) throw new Stop('state_missing', EXIT.state, { hint: 'run init explicitly; state is never rebuilt silently' });
}

const isCount = (v) => Number.isInteger(v) && v >= 0;
const intentNo = (intent) => { const m = /#(\d+)$/.exec(String(intent ?? '')); return m ? Number(m[1]) : null; };

function checkFields(state) {
  const bad = (reason) => { throw new Stop('state_corrupt', EXIT.state, { reason }); };
  const plain = (v) => v && typeof v === 'object' && !Array.isArray(v);
  if (!plain(state) || !plain(state.binding) || !plain(state.historical) || !isCount(state.historical.used) || !isCount(state.used) || !isCount(state.max)) bad('fields');
  if (state.used > state.max || state.max !== state.binding.maxSubmissions || sha256(canonical(state.binding)) !== state.bindingSha256) bad('fields');
  // 结构校验对 schema 1 / 2 一视同仁，且必须发生在任何写入（含 schema 1 备份）之前：事件日志是数组、seq 为合法计数且与长度一致、事件序号连续。
  if (!isCount(state.seq) || !Array.isArray(state.events) || state.events.length !== state.seq) bad('events');
  for (let i = 0; i < state.events.length; i += 1) {
    const e = state.events[i];
    if (!plain(e) || e.seq !== i + 1 || typeof e.kind !== 'string') bad('events');
  }
  for (const key of ['pending', 'blocked']) if (state[key] != null && !plain(state[key])) bad('fields');
  if (state.schema === LEGACY_SCHEMA) {
    if (state.used < state.historical.used) bad('fields');
    return;
  }
  // schema 2：used = 本轮连续已用；total = 累计实际提交；intentSeq 单调不退，保证意图编号唯一。
  if (!isCount(state.total) || !isCount(state.intentSeq) || !isCount(state.roundsCompleted)) bad('fields');
  if (state.used > state.total || state.total < state.historical.used || state.intentSeq < state.total) bad('fields');
  if (state.pending && (intentNo(state.pending.intent) === null || intentNo(state.pending.intent) > state.intentSeq || state.used < 1)) bad('pending');
}

// legacy: true 的命令（status / reconcile / migrate）可读取 schema 1；login / verify 遇到 schema 1 要求先 migrate，绝不静默套用新语义。
function loadState(p, binding, { legacy = false } = {}) {
  requireStateExists(p);
  let state;
  try { state = JSON.parse(fs.readFileSync(p.state, 'utf8')); } catch { throw new Stop('state_corrupt', EXIT.state, { reason: 'unparseable' }); }
  const { integrity, ...body } = state ?? {};
  if (![LEGACY_SCHEMA, SCHEMA].includes(state?.schema) || typeof integrity !== 'string' || integrity !== sha256(canonical(body))) throw new Stop('state_corrupt', EXIT.state, { reason: 'integrity' });
  checkFields(state);
  if (binding && state.bindingSha256 !== sha256(canonical(binding))) throw new Stop('binding_mismatch', EXIT.state, { hint: 'task id is bound to a fixed target/account/secret/max' });
  if (state.schema === LEGACY_SCHEMA && !legacy) throw new Stop('migration_required', EXIT.state, { schema: LEGACY_SCHEMA, hint: 'state uses the old whole-task cumulative count; run `migrate` once (same binding arguments) before login/verify' });
  return state;
}

function lockInfo(p) {
  if (!fs.existsSync(p.lock)) return null;
  try {
    const info = JSON.parse(fs.readFileSync(p.lock, 'utf8'));
    let alive = false;
    try { process.kill(info.pid, 0); alive = true; } catch (e) { alive = e.code === 'EPERM'; }
    return { pid: info.pid, since: info.since, alive };
  } catch { return { pid: null, since: null, alive: null }; }
}

function acquireLock(p) {
  fs.mkdirSync(p.dir, { recursive: true });
  let fd;
  try { fd = fs.openSync(p.lock, 'wx', 0o600); } catch (e) {
    if (e.code === 'EEXIST') throw new Stop('locked', EXIT.lock, { holder: lockInfo(p), hint: 'never cleared automatically; use reconcile after the holder is gone' });
    throw e;
  }
  try { fs.writeSync(fd, JSON.stringify({ pid: process.pid, since: isoNow() })); fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
  let released = false;
  const release = () => { if (!released) { released = true; try { fs.unlinkSync(p.lock); } catch { /* 已被对账清理 */ } } };
  process.once('exit', release);
  for (const sig of ['SIGINT', 'SIGTERM']) process.once(sig, () => { release(); process.exit(sig === 'SIGINT' ? 130 : 143); });
  return release;
}

function checkEvidence(args) {
  const ref = requireText(args, 'evidence-ref', 1024);
  const expected = requireText(args, 'evidence-sha256', 64).toLowerCase();
  let stat;
  try { stat = fs.statSync(ref); } catch { throw new Stop('usage_error', EXIT.usage, { reason: 'evidence_missing' }); }
  if (!stat.isFile() || stat.size > 32 * 1024 * 1024) throw new Stop('usage_error', EXIT.usage, { reason: 'evidence_not_file' });
  if (sha256(fs.readFileSync(ref)) !== expected) throw new Stop('usage_error', EXIT.usage, { reason: 'evidence_sha_mismatch' });
  return { ref, sha256: expected };
}

// ---- 子进程：Ego 脚本与本地 OCR，只解析白名单结果，不回显任何原文 ----
// 按行只收集以指定标记开头的行（有界）；其余内容既不保留也不回显。stdout/stderr 分开收集。
function markerScanner(markers) {
  const MAX_LINE = 4_000_000;
  const MAX_TOTAL = 4_000_000;
  let buffer = '';
  let discarding = false; // 正在丢弃一条超长行的剩余部分：其尾段即使恰好以标记开头也不能采信
  let total = 0;
  const lines = [];
  const take = (raw) => {
    const line = raw.endsWith('\r') ? raw.slice(0, -1) : raw;
    if (markers.some((m) => line.startsWith(m)) && total + line.length <= MAX_TOTAL) { lines.push(line); total += line.length; }
  };
  return {
    lines,
    push(chunk) {
      buffer += chunk;
      for (let i = buffer.indexOf('\n'); i !== -1; i = buffer.indexOf('\n')) {
        if (discarding) discarding = false; else take(buffer.slice(0, i));
        buffer = buffer.slice(i + 1);
      }
      if (buffer.length > MAX_LINE) { buffer = ''; discarding = true; } // 超长单行：丢弃，宁可没有结果也不截断解析
      else if (discarding) buffer = '';
    },
    end() { if (buffer && !discarding) take(buffer); buffer = ''; discarding = false; },
  };
}

function runChild(command, argv, input, timeoutMs, markers = []) {
  return new Promise((resolve) => {
    let stdout = '';
    let stderrBytes = 0;
    let settled = false;
    let timedOut = false;
    const scan = { stdout: markerScanner(markers), stderr: markerScanner(markers) };
    const marked = () => { scan.stdout.end(); scan.stderr.end(); return { stdout: scan.stdout.lines, stderr: scan.stderr.lines }; };
    const done = (value) => { if (!settled) { settled = true; clearTimeout(timer); resolve({ ...value, marked: marked() }); } };
    let child;
    try {
      child = spawn(command, argv, { stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true, env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1', PYTHONIOENCODING: 'utf-8' } });
    } catch { return done({ spawnFailed: true, stdout: '', code: null, timedOut: false, stderrBytes: 0 }); }
    const timer = setTimeout(() => { timedOut = true; child.kill('SIGKILL'); }, timeoutMs);
    child.stdout.setEncoding('utf8');
    child.stderr.setEncoding('utf8');
    child.stdout.on('data', (chunk) => { if (stdout.length < 4_000_000) stdout += chunk; scan.stdout.push(chunk); });
    child.stderr.on('data', (chunk) => { stderrBytes += chunk.length; scan.stderr.push(chunk); });
    child.on('error', () => done({ spawnFailed: true, stdout, code: null, timedOut, stderrBytes }));
    child.on('close', (code) => done({ spawnFailed: false, stdout, code, timedOut, stderrBytes }));
    child.stdin.on('error', () => { /* 子进程提前退出 */ });
    child.stdin.end(input, 'utf8');
  });
}

// 从某一路流的标记行里取最后一个合法结果：必须是带布尔 ok 的普通对象，其余（噪声、非 JSON、数组）一律忽略。
function lastResult(lines, marker) {
  for (let i = lines.length - 1; i >= 0; i -= 1) {
    if (!lines[i].startsWith(marker)) continue;
    try {
      const value = JSON.parse(lines[i].slice(marker.length));
      return value && typeof value === 'object' && !Array.isArray(value) && typeof value.ok === 'boolean' ? value : null;
    } catch { return null; }
  }
  return null;
}

// Ego 的 console 输出实际落在 stderr：两路都认，但两路都有结果且不一致时视为冲突，不采信。
function egoResult(marked) {
  const out = lastResult(marked.stdout, '@@R@@');
  const err = lastResult(marked.stderr, '@@R@@');
  if (out && err) return canonical(out) === canonical(err) ? out : null;
  return out ?? err;
}

const clickMarked = (marked) => [...marked.stdout, ...marked.stderr].some((line) => line === '@@STAGE@@click-begin');

function makeEgo(args, binding) {
  const profile = PROFILES[binding.profile];
  const base = {
    space: intArg(args, 'space', { min: 0, max: 1_000_000 }),
    page: requireText(args, 'page', 16),
    loginUrl: binding.targetUrl,
    origin: new URL(binding.targetUrl).origin,
    account: binding.expectedAccount,
    marker: binding.businessMarker,
    accountSel: binding.accountSelector,
    markerSel: binding.businessSelector,
    sel: profile.selectors,
    re: MESSAGE_PATTERNS,
  };
  if (!/^p[0-9]{1,3}$/.test(base.page)) throw new Stop('usage_error', EXIT.usage, { reason: 'bad_page_label' });
  const bin = args['ego-bin'] ?? (IS_WIN ? 'C:/Users/12070/.local/share/ego/active_version_dir/ego-browser.exe' : 'ego-browser');
  const prefix = args['ego-arg'];
  const timeoutMs = intArg(args, 'step-timeout-ms', { min: 1000, max: 120000, fallback: 30000 });

  // 只操作调用方精确指定的现有 TaskSpace/Page；不 claim、不新建、不关闭其他页。
  const wrap = (step, extra = {}) => `const P = ${JSON.stringify({ ...base, ...extra })};
const out = (v) => console.log('@@R@@' + JSON.stringify(v));
// URL 一律解析后精确比较源（含端口），拒绝 userinfo，登录页要求路径/哈希精确匹配，不用字符串前缀。
const parse = (u) => { try { return new URL(u); } catch { return null; } };
const T = parse(P.loginUrl);
const sameOrigin = (x) => !!x && x.origin === T.origin && !x.username && !x.password;
const onLogin = (u) => { const x = parse(u); return sameOrigin(x) && x.pathname === T.pathname && (x.hash === T.hash || x.hash.startsWith(T.hash + '?')); };
const onBusiness = (u) => { const x = parse(u); return sameOrigin(x) && !/\\/login/.test(x.pathname + x.hash); };
async function main() {
  const task = await taskSpace(P.space);
  if (['user', 'inactive', 'unassigned'].includes(String(task.ownership))) return out({ ok: false, reason: 'space_not_agent_owned', ownership: String(task.ownership) });
  const pages = await task.pages();
  if (!pages.some((x) => x.label === P.page)) return out({ ok: false, reason: 'page_missing' });
  const p = task.page(P.page);
${step}
}
try { await main(); } catch (e) { out({ ok: false, reason: 'script_error', clickInvoked: globalThis.__clickInvoked === true }); }
`;
  const fields = `const fields = await p.evaluate((s) => ({ a: !!document.querySelector(s.account).value, p: !!document.querySelector(s.password).value }), P.sel);`;
  const steps = {
    preflight: () => wrap(`  const url = await p.url();
  const kind = onLogin(url) ? 'login' : (onBusiness(url) ? 'business' : 'other');
  out({ ok: true, ownership: String(task.ownership), kind });`),
    fill: (cred) => wrap(`  if (!onLogin(await p.url())) return out({ ok: false, reason: 'not_on_login' });
  await p.fill(P.sel.account, P.cred.username);
  await p.fill(P.sel.password, P.cred.password);
  ${fields}
  out({ ok: true, fields });`, { cred }),
    refresh: () => wrap(`  if (!onLogin(await p.url())) return out({ ok: false, reason: 'not_on_login' });
  ${fields}
  const read = () => p.evaluate((s) => document.querySelector(s.captchaImg).src, P.sel);
  const old = await read();
  await p.click(P.sel.captchaImg);
  let src = old;
  for (let i = 0; i < 30 && src === old; i += 1) { await p.waitForTimeout(50); src = await read(); }
  const refreshed = src !== old && src.startsWith('data:image/');
  out({ ok: true, fields, refreshed, image: refreshed ? src : '' });`),
    submit: (code) => wrap(`  if (!onLogin(await p.url())) return out({ ok: false, reason: 'not_on_login', clickInvoked: false });
  // 只把“点击之后新增”的提示当作本次提交的反馈：先记基线（已有提示最多等约 4 秒消失，覆盖常见 3 秒 toast），事后按类别比较增量。
  const count = () => p.evaluate((a) => {
    const res = Object.fromEntries(Object.entries(a.re).map(([k, v]) => [k, new RegExp(v)]));
    const texts = Array.from(document.querySelectorAll(a.sel)).map((x) => x.innerText || '').filter(Boolean);
    const n = { captcha: 0, credential: 0, restricted: 0, other: 0 };
    for (const t of texts) {
      const hits = Object.keys(res).filter((k) => res[k].test(t));
      if (!hits.length) n.other += 1;
      for (const k of hits) n[k] += 1;
    }
    return n;
  }, { re: P.re, sel: P.sel.messages });
  let before = await count();
  for (let i = 0; i < 27 && Object.values(before).some((n) => n > 0); i += 1) { await p.waitForTimeout(150); before = await count(); }
  await p.fill(P.sel.captcha, P.code);
  console.log('@@STAGE@@click-begin');
  globalThis.__clickInvoked = true;
  await p.click(P.sel.submit);
  let leftLogin = false;
  let flags = { captcha: false, credential: false, restricted: false, other: 0 };
  for (let i = 0; i < 30; i += 1) {
    await p.waitForTimeout(150);
    leftLogin = onBusiness(await p.url());
    const after = await count();
    flags = { captcha: after.captcha > before.captcha, credential: after.credential > before.credential, restricted: after.restricted > before.restricted, other: Math.max(0, after.other - before.other) };
    if (leftLogin || flags.captcha || flags.credential || flags.restricted) break;
  }
  out({ ok: true, clickInvoked: true, leftLogin, flags, staleAtClick: Object.values(before).some((n) => n > 0) });`, { code }),
    verify: () => wrap(`  await p.reload();
  let v = {};
  let urlOk = false;
  for (let i = 0; i < 12; i += 1) {
    // 账号与业务入口只在调用方明确配置的控件上判断：选择器必须恰好命中一个可见元素，且其规范化文本与期望值完全相等；不使用整页文本。
    v = await p.evaluate((a) => {
      const norm = (s) => String(s ?? '').replace(/\\s+/g, ' ').trim();
      const visible = (el) => {
        const s = getComputedStyle(el);
        return s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0' && el.getClientRects().length > 0 && (typeof el.checkVisibility !== 'function' || el.checkVisibility());
      };
      const check = (selector, expected) => {
        let nodes = [];
        try { nodes = Array.from(document.querySelectorAll(selector)).filter(visible); } catch { return 'invalid'; }
        if (nodes.length === 0) return 'missing';
        if (nodes.length > 1) return 'ambiguous';
        const text = norm(typeof nodes[0].innerText === 'string' ? nodes[0].innerText : nodes[0].textContent);
        return text === norm(expected) ? 'ok' : 'mismatch';
      };
      return { href: location.href, loginForm: !!document.querySelector('input[type="password"]'), account: check(a.accountSel, a.account), marker: check(a.markerSel, a.marker) };
    }, { accountSel: P.accountSel, markerSel: P.markerSel, account: P.account, marker: P.marker });
    urlOk = onBusiness(String(v.href));
    if (urlOk && !v.loginForm && v.account === 'ok' && v.marker === 'ok') break;
    await p.waitForTimeout(250);
  }
  out({ ok: true, urlOk, loginForm: v.loginForm, accountState: v.account, markerState: v.marker });`),
  };

  async function call(name, arg) {
    const script = steps[name](arg);
    const res = await runChild(bin, [...prefix, 'nodejs'], script, timeoutMs, ['@@R@@', '@@STAGE@@']);
    // 仅当进程正常退出（非超时、退出码 0）时才采信输出；超时/非零/缺结构一律视为没有结果，不回显原文。
    const result = !res.spawnFailed && !res.timedOut && res.code === 0 ? egoResult(res.marked) : null;
    return {
      result,
      clickBegun: clickMarked(res.marked),
      spawnFailed: res.spawnFailed,
      timedOut: res.timedOut,
      exitCode: res.code,
    };
  }
  return { call };
}

async function recognize(args, dataUrl) {
  const python = args.python ?? (IS_WIN ? 'C:/Users/12070/AppData/Local/Programs/Python/Python312/python.exe' : 'python3');
  const script = args['ocr-script'] ?? path.join(HERE, 'captcha-ocr.py');
  const res = await runChild(python, [...(args['ocr-flag'].length ? args['ocr-flag'] : ['-B']), script], dataUrl, intArg(args, 'ocr-timeout-ms', { min: 1000, max: 60000, fallback: 10000 }));
  let parsed = null;
  try { parsed = JSON.parse(res.stdout.trim().split(/\r?\n/).pop() ?? ''); } catch { /* 视为失败 */ }
  if (res.spawnFailed || res.timedOut) return { kind: 'failed', error: res.timedOut ? 'ocr_timeout' : 'ocr_failed' };
  // worker 只在失败分类时允许非零退出；ok:true 伴随非零退出码不被采信。
  if (res.code !== 0) return { kind: 'failed', error: parsed?.ok === false && parsed.error === 'dependency_unavailable' ? 'dependency_unavailable' : 'ocr_failed' };
  if (!parsed || !parsed.ok) return { kind: 'failed', error: parsed?.error === 'dependency_unavailable' ? 'dependency_unavailable' : 'ocr_failed' };
  const code = typeof parsed.code === 'string' ? parsed.code : '';
  const score = Number(parsed.score);
  const accepted = parsed.accepted === true && CODE_RE.test(code) && Number.isFinite(score) && score >= MIN_SCORE;
  return { kind: 'ok', accepted, code: accepted ? code : '', score, ocrMs: Number(parsed.ocrMs) || 0, reason: accepted ? 'ok' : (CODE_RE.test(code) ? 'score' : 'format') };
}

// ---- 子命令 ----
// used / max / remaining 是“本轮连续”口径；total 是累计实际提交（旧 schema 1 里 used 本身就是累计，显示为 total 的同值）。
function snapshot(state) {
  return {
    taskId: state.taskId, schema: state.schema, used: state.used, max: state.max, remaining: state.max - state.used,
    total: state.total ?? state.used, roundsCompleted: state.roundsCompleted ?? null, historicalUsed: state.historical.used,
  };
}

// 完整刷新成功后结束当前一轮：连续计数归零，累计、历史、事件全部保留。pending / blocked 存在时绝不归零。
function closeRoundIfVerified(state, via) {
  if (state.used === 0 || state.pending || state.blocked) return false;
  const roundSubmissions = state.used;
  state.used = 0;
  state.roundsCompleted += 1;
  pushEvent(state, 'round_reset', { via, roundSubmissions, total: state.total, roundsCompleted: state.roundsCompleted });
  return true;
}

async function cmdInit(args) {
  const taskId = taskIdOf(args);
  const binding = bindingOf(args);
  const historicalUsed = intArg(args, 'historical-used', { min: 0, max: binding.maxSubmissions });
  const evidence = checkEvidence(args);
  const p = paths(taskId);
  const release = acquireLock(p);
  try {
    if (fs.existsSync(p.state)) throw new Stop('state_exists', EXIT.state, { hint: 'init never overwrites an existing task state' });
    const state = {
      schema: SCHEMA, taskId, binding, bindingSha256: sha256(canonical(binding)), max: binding.maxSubmissions,
      used: historicalUsed, total: historicalUsed, intentSeq: historicalUsed, roundsCompleted: 0,
      historical: { used: historicalUsed, evidenceRef: evidence.ref, evidenceSha256: evidence.sha256, at: isoNow() },
      pending: null, blocked: null, seq: 0, events: [],
    };
    pushEvent(state, 'init', { historicalUsed, max: state.max });
    saveState(p, state);
    return { outcome: 'initialized', exit: EXIT.ok, ...snapshot(state), stateRootKind: p.kind };
  } finally { release(); }
}

async function cmdStatus(args) {
  const taskId = taskIdOf(args);
  const p = paths(taskId);
  const state = loadState(p, null, { legacy: true }); // 严格只读：不迁移、不写任何文件
  const legacy = state.schema === LEGACY_SCHEMA ? { migrationRequired: true, migrationWouldReset: assessLegacy(state).eligible } : {};
  return { outcome: 'status', exit: EXIT.ok, ...snapshot(state), ...legacy, pending: state.pending, blocked: state.blocked ?? null, lock: lockInfo(p), bindingSha256: state.bindingSha256, stateRootKind: p.kind };
}

// ---- schema 1 → 2 迁移：只据旧事件里完整、可信的最后一次刷新成功才归零，其余一律原样延续连续消耗 ----
const isFullSuccess = (e) => e.kind === 'verify' && e.outcome === 'login_verified' && e.urlOk === true && e.loginForm === false && e.accountState === 'ok' && e.markerState === 'ok';
const NON_CONSUMING = new Set(['ocr_rejected', 'ocr_failed', 'captcha_not_refreshed', 'stale_lock_cleared']);

function assessLegacy(state) {
  const none = (reason) => ({ eligible: false, reason, completed: 0, maxIntent: state.historical.used, verifySeq: null });
  const events = state.events;
  if (!Array.isArray(events) || events.length !== state.seq) return none('event_log_inconsistent');
  let replay = state.historical.used;
  let open = false;
  let completed = 0;
  let maxIntent = state.historical.used;
  let lastSuccess = -1;
  for (let i = 0; i < events.length; i += 1) {
    const e = events[i];
    if (!e || typeof e !== 'object' || e.seq !== i + 1 || typeof e.kind !== 'string') return none('event_log_inconsistent');
    if (e.kind === 'submit_intent') { replay += 1; open = true; maxIntent = Math.max(maxIntent, intentNo(e.intent) ?? 0); }
    else if (e.kind === 'intent_released_pre_click' || (e.kind === 'reconciled' && e.resolution === 'not-submitted')) replay -= 1;
    else if (isFullSuccess(e)) { if (open) { completed += 1; open = false; } lastSuccess = i; }
  }
  const base = { completed, maxIntent, verifySeq: lastSuccess >= 0 ? events[lastSuccess].seq : null };
  if (replay !== state.used) return { ...base, eligible: false, reason: 'used_mismatch' };
  if (lastSuccess < 0) return { ...base, eligible: false, reason: 'no_trusted_success' };
  if (state.pending) return { ...base, eligible: false, reason: 'pending_kept' };
  if (state.blocked) return { ...base, eligible: false, reason: 'blocked_kept' };
  for (let i = lastSuccess + 1; i < events.length; i += 1) {
    if (!NON_CONSUMING.has(events[i].kind) && !isFullSuccess(events[i])) return { ...base, eligible: false, reason: 'unfinished_after_success' };
  }
  return { ...base, eligible: true, reason: 'trusted_success_no_later_consumption' };
}

async function cmdMigrate(args) {
  const taskId = taskIdOf(args);
  const binding = bindingOf(args); // 绑定不符 → binding_mismatch，不写任何文件
  const p = paths(taskId);
  requireStateExists(p);
  const release = acquireLock(p);
  try {
    const rawBytes = fs.readFileSync(p.state);
    const state = loadState(p, binding, { legacy: true }); // 完整性 / 字段 / 绑定校验失败 → state_corrupt / binding_mismatch，保持原文件
    if (state.schema === SCHEMA) return { outcome: 'already_migrated', exit: EXIT.ok, ...snapshot(state), migration: state.migration ?? null, stateRootKind: p.kind };
    const a = assessLegacy(state);
    if (a.reason === 'event_log_inconsistent') throw new Stop('state_corrupt', EXIT.state, { reason: 'events' }); // 结构已在 loadState 校验；这里是防御，绝不带着坏事件日志继续落盘
    const previousFileSha256 = sha256(rawBytes);
    const backupName = `state.schema1-${previousFileSha256.slice(0, 16)}.json`;
    const backup = path.join(p.dir, backupName);
    const migrated = {
      schema: SCHEMA, taskId: state.taskId, binding: state.binding, bindingSha256: state.bindingSha256, max: state.max,
      used: a.eligible ? 0 : state.used, total: state.used,
      intentSeq: Math.max(state.used, a.maxIntent, intentNo(state.pending?.intent) ?? 0), roundsCompleted: a.completed,
      historical: state.historical, pending: state.pending ?? null, blocked: state.blocked ?? null, seq: state.seq, events: [...state.events],
      migration: { fromSchema: LEGACY_SCHEMA, at: isoNow(), previousIntegrity: state.integrity, previousFileSha256, backup: backupName, previousUsed: state.used, previousSeq: state.seq, reset: a.eligible, reason: a.reason, trustedVerifySeq: a.verifySeq },
    };
    pushEvent(migrated, 'migrated_schema', { fromSchema: LEGACY_SCHEMA, toSchema: SCHEMA, previousIntegrity: state.integrity, previousFileSha256, reset: a.eligible, reason: a.reason, trustedVerifySeq: a.verifySeq, usedBefore: state.used, usedAfter: migrated.used, total: migrated.total });
    checkFields(migrated); // 新状态必须满足同一 runner 的完整字段约束，否则不写备份也不换状态（失败 → state_corrupt）
    if (fs.existsSync(backup)) { // 上次迁移写了备份但没来得及换状态：只接受字节完全相同的备份，不覆盖
      if (sha256(fs.readFileSync(backup)) !== previousFileSha256) throw new Stop('state_corrupt', EXIT.state, { reason: 'backup_conflict' });
    } else {
      const fd = fs.openSync(backup, 'wx', 0o600);
      try { fs.writeSync(fd, rawBytes); fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
      fsyncDir(p.dir);
    }
    saveState(p, migrated);
    return { outcome: 'migrated', exit: EXIT.ok, ...snapshot(migrated), reset: a.eligible, reason: a.reason, backup: backupName, previousFileSha256, pending: migrated.pending, blocked: migrated.blocked, stateRootKind: p.kind };
  } finally { release(); }
}

// 对账命令之间的互斥：同一时刻只有一个 reconcile，且清锁、取锁、读状态都在其保护内完成。
function acquireGuard(p) {
  const guard = path.join(p.dir, 'reconcile.lock');
  let fd;
  try { fd = fs.openSync(guard, 'wx', 0o600); } catch (e) {
    if (e.code === 'EEXIST') throw new Stop('locked', EXIT.lock, { holder: 'reconcile', hint: 'another reconcile holds reconcile.lock; it is never cleared automatically' });
    throw e;
  }
  try { fs.writeSync(fd, JSON.stringify({ pid: process.pid, since: isoNow() })); fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
  let released = false;
  const release = () => { if (!released) { released = true; try { fs.unlinkSync(guard); } catch { /* 已不存在 */ } } };
  process.once('exit', release);
  return release;
}

const RESOLUTIONS = ['not-submitted', 'submitted', 'blocked-reviewed', 'clear-stale-lock'];

// 仅测试：在取 reconcile 互斥之前停顿，用来确定性复现“先读状态、后取锁”的竞态；只有同时设置了测试根许可才生效。
async function testPauseBeforeReconcileLock() {
  const ms = Number(process.env.CAPTCHA_LOGIN_TEST_PAUSE_BEFORE_RECONCILE_LOCK_MS);
  if (process.env.CAPTCHA_LOGIN_ALLOW_TEST_ROOT === '1' && Number.isFinite(ms) && ms > 0 && ms <= 10000) await new Promise((r) => setTimeout(r, ms));
}

async function cmdReconcile(args) {
  const taskId = taskIdOf(args);
  const resolution = requireText(args, 'resolution', 32);
  if (!RESOLUTIONS.includes(resolution)) throw new Stop('usage_error', EXIT.usage, { reason: 'bad_resolution' });
  const evidence = resolution === 'clear-stale-lock' ? null : checkEvidence(args);
  const p = paths(taskId);
  requireStateExists(p);
  await testPauseBeforeReconcileLock();
  const releaseGuard = acquireGuard(p);
  try {
    const holder = lockInfo(p);
    if (holder) {
      if (holder.alive !== false) throw new Stop('locked', EXIT.lock, { holder, hint: 'holder process is alive or unknown; wait or verify it is gone' });
      // 仅清理已确认退出的持有者；删除前再读一遍原文，内容变化则视为被替换，不删除。
      const raw = fs.readFileSync(p.lock, 'utf8');
      if (lockInfo(p)?.pid !== holder.pid || fs.readFileSync(p.lock, 'utf8') !== raw) throw new Stop('locked', EXIT.lock, { hint: 'lock changed while reconciling; not removed' });
      fs.unlinkSync(p.lock);
    } else if (resolution === 'clear-stale-lock') throw new Stop('usage_error', EXIT.usage, { reason: 'no_lock' });
    const release = acquireLock(p);
    try {
      const state = loadState(p, null, { legacy: true }); // 取得排他锁之后才读取最新状态
      if (resolution === 'clear-stale-lock') {
        if (state.pending) throw new Stop('pending_reconcile_required', EXIT.pending, { hint: 'resolve the pending submission first' });
        pushEvent(state, 'stale_lock_cleared', { holderPid: holder.pid ?? null });
      } else if (resolution === 'blocked-reviewed') {
        if (!state.blocked) throw new Stop('usage_error', EXIT.usage, { reason: 'nothing_blocked' });
        pushEvent(state, 'unblocked', { blockedReason: state.blocked.reason, intent: state.blocked.intent, evidenceSha256: evidence.sha256, used: state.used });
        state.blocked = null; // 计数不退款、不复位
      } else {
        if (!state.pending) throw new Stop('usage_error', EXIT.usage, { reason: 'nothing_pending' });
        if (resolution === 'not-submitted') {
          // 只退还这次待对账、已证未点击的意图；它一定属于当前一轮，不会跨成功轮退款。
          if (state.schema === LEGACY_SCHEMA) {
            if (state.used - 1 < state.historical.used) throw new Stop('state_corrupt', EXIT.state, { reason: 'refund_below_history' });
            state.used -= 1;
          } else {
            if (state.used < 1 || state.total - 1 < state.historical.used) throw new Stop('state_corrupt', EXIT.state, { reason: 'refund_below_history' });
            state.used -= 1;
            state.total -= 1;
          }
        }
        pushEvent(state, 'reconciled', { resolution, intent: state.pending.intent, evidenceSha256: evidence.sha256, used: state.used, ...(state.schema === SCHEMA ? { total: state.total } : {}) });
        state.pending = null;
      }
      saveState(p, state);
      return { outcome: 'reconciled', exit: EXIT.ok, resolution, ...snapshot(state) };
    } finally { release(); }
  } finally { releaseGuard(); }
}

async function readCredentials() {
  if (process.stdin.isTTY) throw new Stop('usage_error', EXIT.usage, { reason: 'credentials_must_come_from_stdin' });
  process.stdin.setEncoding('utf8');
  let raw = '';
  for await (const chunk of process.stdin) { raw += chunk; if (raw.length > 65536) break; }
  let cred;
  try { cred = JSON.parse(raw.replace(/^\uFEFF/, '')); } catch { throw new Stop('usage_error', EXIT.usage, { reason: 'credentials_stdin_invalid' }); }
  raw = '';
  if (!cred || typeof cred.username !== 'string' || typeof cred.password !== 'string' || !cred.username || !cred.password) throw new Stop('usage_error', EXIT.usage, { reason: 'credentials_stdin_invalid' });
  secretsInMemory.add(cred.username);
  secretsInMemory.add(cred.password);
  return { username: cred.username, password: cred.password };
}

const CHECK_STATES = new Set(['ok', 'missing', 'ambiguous', 'mismatch', 'invalid']);
const stateOf = (x) => (CHECK_STATES.has(x) ? x : 'invalid');

async function verifySession(ego) {
  const v = await ego.call('verify');
  const r = v.result;
  if (!r || r.ok !== true) return { outcome: 'verify_failed', exit: EXIT.stop, reason: r?.reason ?? 'no_result' };
  const flags = { urlOk: r.urlOk === true, loginForm: r.loginForm === true, accountState: stateOf(r.accountState), markerState: stateOf(r.markerState) };
  if (flags.urlOk && !flags.loginForm && flags.accountState === 'ok' && flags.markerState === 'ok') return { outcome: 'login_verified', exit: EXIT.ok, verify: flags };
  if (flags.urlOk && !flags.loginForm && flags.markerState === 'ok' && flags.accountState !== 'ok') return { outcome: 'account_mismatch', exit: EXIT.stop, verify: flags };
  return { outcome: flags.loginForm ? 'not_logged_in' : 'verify_failed', exit: EXIT.stop, verify: flags };
}

function egoGate(res, what) {
  const r = res.result;
  if (r && r.ok === true) return r;
  if (res.spawnFailed) throw new Stop('ego_unavailable', EXIT.stop, { step: what });
  const reason = r?.reason;
  if (reason === 'space_not_agent_owned') throw new Stop('ego_space_not_agent_owned', EXIT.stop, { step: what, ownership: r.ownership });
  if (reason === 'page_missing') throw new Stop('ego_page_missing', EXIT.stop, { step: what });
  if (reason === 'not_on_login') throw new Stop('unexpected_page', EXIT.stop, { step: what });
  throw new Stop('ego_error', EXIT.stop, { step: what, reason: r?.reason ?? (res.timedOut ? 'timeout' : 'no_result'), exitCode: res.exitCode });
}

async function cmdLogin(args) {
  const taskId = taskIdOf(args);
  const binding = bindingOf(args);
  const maxRounds = intArg(args, 'max-ocr-rounds', { min: 1, max: 20, fallback: 8 });
  const runCap = intArg(args, 'run-submission-cap', { min: 1, max: 20, fallback: 3 });
  const cred = await readCredentials();
  const ego = makeEgo(args, binding);
  const p = paths(taskId);
  requireStateExists(p);
  const release = acquireLock(p);
  const run = { submissions: 0, rounds: 0, rejected: 0 };
  try {
    const state = loadState(p, binding);
    if (state.pending) throw new Stop('pending_reconcile_required', EXIT.pending, { pending: state.pending, hint: 'previous submission result is unknown; reconcile with evidence, never auto-retry' });
    if (state.blocked) throw new Stop('blocked_reconcile_required', EXIT.pending, { blocked: state.blocked, hint: 'a definitive failure (credential/restriction/verification) blocks further submissions until reviewed with evidence via reconcile --resolution blocked-reviewed' });
    const pre = egoGate(await ego.call('preflight'), 'preflight');
    if (pre.kind === 'business') {
      const verdict = await verifySession(ego);
      pushEvent(state, 'verify', { via: 'already-on-business-page', outcome: verdict.outcome, ...(verdict.verify ?? {}) });
      if (verdict.outcome === 'login_verified') closeRoundIfVerified(state, 'already-on-business-page'); // 已有正确会话：有未归零的本轮消耗才归零，已是 0 则不重复记轮
      saveState(p, state);
      return { ...verdict, outcome: verdict.outcome === 'login_verified' ? 'already_logged_in_verified' : verdict.outcome, submissionsThisRun: 0, ...snapshot(state) };
    }
    if (pre.kind !== 'login') throw new Stop('unexpected_page', EXIT.stop, { step: 'preflight' });
    if (state.used >= state.max) throw new Stop('submission_budget_exhausted', EXIT.stop, { ...snapshot(state), before: 'fill' });

    const fill = async () => {
      const r = egoGate(await ego.call('fill', cred), 'fill');
      if (!r.fields?.a || !r.fields?.p) throw new Stop('fill_failed', EXIT.stop, { step: 'fill' });
    };
    await fill();
    let refreshFailures = 0;
    for (; run.rounds < maxRounds;) {
      run.rounds += 1;
      const refreshed = egoGate(await ego.call('refresh'), 'refresh');
      if (!refreshed.refreshed) {
        refreshFailures += 1;
        pushEvent(state, 'captcha_not_refreshed', { round: run.rounds });
        saveState(p, state);
        if (refreshFailures >= 2) throw new Stop('captcha_refresh_failed', EXIT.stop, { rounds: run.rounds });
        continue;
      }
      refreshFailures = 0;
      if (!refreshed.fields?.a || !refreshed.fields?.p) await fill();
      const ocrStarted = Date.now();
      let ocr = await recognize(args, refreshed.image);
      refreshed.image = '';
      if (ocr.kind === 'failed') {
        pushEvent(state, 'ocr_failed', { round: run.rounds, error: ocr.error });
        saveState(p, state);
        throw new Stop(ocr.error === 'dependency_unavailable' ? 'ocr_unavailable' : 'ocr_failed', EXIT.stop, { error: ocr.error, rounds: run.rounds });
      }
      if (!ocr.accepted) {
        run.rejected += 1;
        pushEvent(state, 'ocr_rejected', { round: run.rounds, reason: ocr.reason, score: ocr.score, ocrMs: ocr.ocrMs });
        saveState(p, state);
        ocr = null;
        continue; // 低置信或格式不符：刷新重识别，绝不提交
      }
      if (state.used >= state.max) throw new Stop('submission_budget_exhausted', EXIT.stop, snapshot(state));
      if (run.submissions >= runCap) throw new Stop('run_submission_cap_reached', EXIT.stop, { cap: runCap });

      // 先持久化提交意图（计数+1、待对账），再做不可逆的点击。
      state.used += 1;
      state.total += 1;
      state.intentSeq += 1;
      run.submissions += 1;
      const intent = `${taskId}#${state.intentSeq}`; // 单调唯一：本轮归零或退款都不会复用编号
      state.pending = { intent, at: isoNow(), stage: 'intent' };
      pushEvent(state, 'submit_intent', { intent, used: state.used, total: state.total, max: state.max, score: ocr.score, ocrMs: ocr.ocrMs });
      saveState(p, state);
      const launchedMs = Date.now() - ocrStarted;
      const submitStarted = Date.now();
      const res = await ego.call('submit', ocr.code);
      ocr = null;
      const r = res.result;
      const preClick = (res.spawnFailed && !res.clickBegun)
        || (r && r.ok === false && (r.clickInvoked === false || ['space_not_agent_owned', 'page_missing', 'not_on_login'].includes(r.reason)));
      if (preClick) { // 脚本明确声明点击未发生：释放这次计数
        state.used -= 1;
        state.total -= 1;
        state.pending = null;
        pushEvent(state, 'intent_released_pre_click', { intent, used: state.used, total: state.total });
        saveState(p, state);
        throw new Stop(res.spawnFailed ? 'ego_unavailable' : 'fill_failed', EXIT.stop, { step: 'submit', released: true, ...snapshot(state) });
      }
      const timing = { ocrToSubmitLaunchMs: launchedMs, submitStepMs: Date.now() - submitStarted };
      if (!r || r.ok !== true || r.clickInvoked !== true) {
        state.pending = { ...state.pending, stage: 'unknown' };
        pushEvent(state, 'submit_unknown', { intent, timedOut: res.timedOut, clickBegun: res.clickBegun, ...timing });
        saveState(p, state);
        throw new Stop('submit_result_unknown', EXIT.pending, { intent, ...snapshot(state) });
      }
      const f = r.flags ?? {};
      const stopCategory = f.restricted ? 'access_restricted' : f.credential ? 'credential_error' : null;
      const classification = stopCategory
        ?? (r.leftLogin ? 'left_login' : (f.captcha && !f.other ? 'captcha_error' : 'unknown'));
      pushEvent(state, 'submit_outcome', { intent, classification, used: state.used, total: state.total, ...timing });
      if (classification === 'unknown') {
        state.pending = { ...state.pending, stage: 'unknown' };
        saveState(p, state);
        throw new Stop('submit_result_unknown', EXIT.pending, { intent, ...snapshot(state) });
      }
      state.pending = null;
      saveState(p, state);
      emit({ event: 'submission', intent, classification, ...snapshot(state), ...timing });
      if (classification === 'captcha_error') continue; // 唯一允许刷新重试的明确分类
      if (classification !== 'left_login') {
        // 明确的账号密码错误/访问限制：持久阻断，新进程不会自动再点；计数不退款。
        state.blocked = { reason: classification, intent, at: isoNow() };
        pushEvent(state, 'blocked', { reason: classification, intent });
        saveState(p, state);
        throw new Stop(classification, EXIT.stop, { blocked: true, ...snapshot(state) });
      }
      const verdict = await verifySession(ego);
      pushEvent(state, 'verify', { outcome: verdict.outcome, ...(verdict.verify ?? {}) });
      let roundClosed = false;
      if (verdict.outcome !== 'login_verified') { // 点击后核验失败/错误账号同样不默认获得下一次提交权限，也不归零
        state.blocked = { reason: verdict.outcome, intent, at: isoNow() };
        pushEvent(state, 'blocked', { reason: verdict.outcome, intent });
      } else roundClosed = closeRoundIfVerified(state, 'login'); // 完整刷新核验成功：本轮结束，连续计数归零（与 verify 事件同一次原子写）
      saveState(p, state);
      return { ...verdict, submissionsThisRun: run.submissions, rounds: run.rounds, ocrRejected: run.rejected, roundClosed, ...snapshot(state) };
    }
    throw new Stop('ocr_rounds_exhausted', EXIT.stop, { rounds: run.rounds, ocrRejected: run.rejected, submissionsThisRun: run.submissions, ...snapshot(state) });
  } finally { release(); }
}

async function cmdVerify(args) {
  const taskId = taskIdOf(args);
  const binding = bindingOf(args);
  const ego = makeEgo(args, binding);
  const p = paths(taskId);
  requireStateExists(p);
  const release = acquireLock(p);
  try {
    const state = loadState(p, binding);
    egoGate(await ego.call('preflight'), 'preflight');
    const verdict = await verifySession(ego);
    pushEvent(state, 'verify', { outcome: verdict.outcome, ...(verdict.verify ?? {}) });
    const roundClosed = verdict.outcome === 'login_verified' ? closeRoundIfVerified(state, 'verify') : false; // 同样要求完整核验；pending / blocked 存在时不归零
    saveState(p, state);
    return { ...verdict, submissionsThisRun: 0, roundClosed, ...snapshot(state) };
  } finally { release(); }
}

const COMMANDS = { init: cmdInit, status: cmdStatus, migrate: cmdMigrate, reconcile: cmdReconcile, login: cmdLogin, verify: cmdVerify };

async function main() {
  let result;
  try {
    const args = parseArgs(process.argv.slice(2));
    const command = COMMANDS[args._[0]];
    if (!command) throw new Stop('usage_error', EXIT.usage, { reason: 'unknown_command', commands: Object.keys(COMMANDS) });
    result = await command(args);
  } catch (error) {
    if (error instanceof Stop) result = { outcome: error.outcome, exit: error.exit, ...error.extra };
    else result = { outcome: 'internal_error', exit: EXIT.usage, error: String(error?.code ?? error?.name ?? 'Error').slice(0, 40) };
  }
  const { exit, ...rest } = result;
  emit({ result: rest.outcome, ...rest, exit });
  process.exitCode = exit;
}

await main();
