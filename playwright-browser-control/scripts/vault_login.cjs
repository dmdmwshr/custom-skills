/* Credentials enter through stdin only. The official CLI runs in this process. */
'use strict';
const fs = require('node:fs');
const path = require('node:path');

function validateRequest(r, credential) {
  const allowed = new Set(['browser','session','origin','loginUrlPrefix','username','usernamePlaceholder','passwordPlaceholder','submitText','successText','captchaPlaceholder','captchaValue']);
  if (Object.keys(r).some(key => !allowed.has(key))) throw new Error('Unexpected request field');
  if (!['chrome', 'edge'].includes(r.browser) || !/^[a-z0-9][a-z0-9-]{0,47}$/.test(r.session || '')) throw new Error('Invalid browser/session');
  const target = new URL(r.loginUrlPrefix);
  if (!['http:', 'https:'].includes(target.protocol) || target.origin !== r.origin || target.username || target.password) throw new Error('Invalid target binding');
  for (const key of ['username', 'usernamePlaceholder', 'passwordPlaceholder', 'submitText', 'successText']) {
    if (typeof r[key] !== 'string' || !r[key].trim()) throw new Error('Missing non-secret login selector');
  }
  if (credential.username !== r.username || typeof credential.password !== 'string' || !credential.password) throw new Error('Credential account mismatch');
  if (Boolean(r.captchaPlaceholder) !== Boolean(r.captchaValue)) throw new Error('Captcha must be supplied before credential fill');
  return r;
}

function buildCode(r, credential) {
  validateRequest(r, credential);
  const submitPattern = '^' + r.submitText.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '$';
  return `async (page) => {
    const request = ${JSON.stringify(r)};
    if (!page.url().startsWith(request.origin + '/')) return {state:'TARGET_MISMATCH',submitted:false};
    const success = page.getByText(request.successText, {exact:true});
    if (!page.url().startsWith(request.loginUrlPrefix)) {
      return {state:await success.isVisible().catch(()=>false) ? 'ALREADY_LOGGED_IN' : 'TARGET_MISMATCH',submitted:false};
    }
    const password = page.getByPlaceholder(request.passwordPlaceholder, {exact:true});
    let submitted = false;
    try {
      if (await password.getAttribute('type') !== 'password') return {state:'PASSWORD_NOT_MASKED',submitted:false};
      await page.getByPlaceholder(request.usernamePlaceholder, {exact:true}).fill(request.username);
      if (request.captchaPlaceholder) await page.getByPlaceholder(request.captchaPlaceholder, {exact:true}).fill(request.captchaValue);
      await password.fill(${JSON.stringify(credential.password)});
      submitted = true;
      // Keyboard activation also works when browser zoom makes the extension's
      // pointer coordinates differ from the visible page. Submit exactly once.
      await page.locator('button:visible').filter({hasText: new RegExp(${JSON.stringify(submitPattern)})}).press('Enter');
      await success.waitFor({state:'visible',timeout:15000});
      if (page.url().startsWith(request.loginUrlPrefix)) return {state:'LOGIN_NOT_CONFIRMED',submitted};
      return {state:'LOGIN_VERIFIED',submitted};
    } catch { return {state:'LOGIN_NOT_CONFIRMED',submitted}; }
    finally {
      if (page.url().startsWith(request.loginUrlPrefix)) await password.fill('').catch(()=>{});
    }
  }`;
}

function parseResult(output) {
  const match = output.match(/### Result\s*\r?\n(\{[^\r\n]+\})/);
  if (!match || output.includes('### Error')) return {state:'RESULT_UNKNOWN',submitted:null};
  try {
    const result = JSON.parse(match[1]);
    if (!['ALREADY_LOGGED_IN','LOGIN_VERIFIED','TARGET_MISMATCH','PASSWORD_NOT_MASKED','LOGIN_NOT_CONFIRMED'].includes(result.state)) throw new Error();
    return {state:result.state,submitted:result.submitted};
  } catch { return {state:'RESULT_UNKNOWN',submitted:null}; }
}

function main() {
  const request = JSON.parse(fs.readFileSync(process.argv[2], 'utf8').replace(/^\uFEFF/, ''));
  const credential = JSON.parse(fs.readFileSync(0, 'utf8'));
  const code = buildCode(request, credential);
  const runtime = JSON.parse(fs.readFileSync(path.join(process.env.LOCALAPPDATA, 'CodexBrowser/playwright/runtime.json'), 'utf8'));
  const actual = JSON.parse(fs.readFileSync(path.join(path.dirname(runtime.cli_entry), 'package.json'), 'utf8')).version;
  if (actual !== runtime.expected_cli_version || actual !== '0.1.21') throw new Error('Pinned CLI version mismatch');
  for (const key of Object.keys(process.env)) if (/^(DEBUG|PWTEST_|PLAYWRIGHT_MCP_|PLAYWRIGHT_CLI_)/.test(key)) delete process.env[key];
  process.env.NO_UPDATE_NOTIFIER = '1';
  let output = '';
  const write = process.stdout.write.bind(process.stdout);
  const suppress = (chunk, enc, cb) => { output += String(chunk); if (typeof enc === 'function') enc(); if (cb) cb(); return true; };
  process.stdout.write = suppress;
  process.stderr.write = suppress;
  process.on('exit', () => {
    const result = parseResult(output);
    write(JSON.stringify(result) + '\n');
    if (!['ALREADY_LOGGED_IN','LOGIN_VERIFIED'].includes(result.state)) process.exitCode = 1;
    output = '';
  });
  credential.password = '';
  // Never spawn a process with these arguments: this argv is memory-only.
  process.argv = [process.execPath, runtime.cli_entry, '-s=' + request.browser + '-' + request.session, 'run-code', code];
  require(runtime.cli_entry);
}

module.exports = {validateRequest, buildCode, parseResult};
if (require.main === module) { try { main(); } catch { process.stdout.write('{"state":"LOCAL_VALIDATION_FAILED","submitted":false}\n'); process.exitCode = 1; } }
