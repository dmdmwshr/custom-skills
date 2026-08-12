import { chromium } from "playwright";

function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function readRequiredOption(name) {
  const index = process.argv.indexOf(name);
  if (index === -1 || index === process.argv.length - 1) {
    throw new Error(`缺少运行参数：${name}`);
  }
  return process.argv[index + 1];
}

function readBooleanOption(name) {
  const value = readRequiredOption(name).toLowerCase();
  if (value === "true") {
    return true;
  }
  if (value === "false") {
    return false;
  }
  throw new Error(`${name} 必须是 true 或 false。`);
}

function readNonNegativeIntegerOption(name) {
  const value = Number(readRequiredOption(name));
  if (!Number.isInteger(value) || value < 0) {
    throw new Error(`${name} 必须是非负整数。`);
  }
  return value;
}

async function readCredentialPayload() {
  let raw = "";
  process.stdin.setEncoding("utf8");
  for await (const chunk of process.stdin) {
    raw += chunk;
  }
  if (!raw.trim()) {
    throw new Error("没有收到一次性登录凭据。 ");
  }

  const payload = JSON.parse(raw);
  if (!payload.username || !payload.password) {
    throw new Error("登录凭据缺少账号或密码。 ");
  }
  return payload;
}

async function firstVisible(page, selectors) {
  for (const selector of selectors) {
    const candidates = page.locator(selector);
    const count = await candidates.count();
    for (let index = 0; index < count; index += 1) {
      const candidate = candidates.nth(index);
      if (await candidate.isVisible().catch(() => false)) {
        return candidate;
      }
    }
  }
  return null;
}

async function waitForManualCaptcha(captchaField, timeoutSeconds) {
  const deadline = Date.now() + timeoutSeconds * 1000;
  console.log(`请在新打开的浏览器窗口中手动输入验证码；脚本最多等待 ${timeoutSeconds} 秒。`);

  while (Date.now() < deadline) {
    const value = await captchaField.inputValue().catch(() => "");
    if (value.trim()) {
      return true;
    }
    await sleep(500);
  }
  return false;
}

async function main() {
  const url = readRequiredOption("--url");
  const parsedUrl = new URL(url);
  if (!['http:', 'https:'].includes(parsedUrl.protocol)) {
    throw new Error("登录地址必须是 HTTP 或 HTTPS。 ");
  }

  const configuration = {
    url,
    captcha: readBooleanOption("--captcha"),
    captchaTimeoutSeconds: readNonNegativeIntegerOption("--captcha-timeout-seconds"),
    keepBrowserOpenSeconds: readNonNegativeIntegerOption("--keep-browser-open-seconds")
  };
  const credential = await readCredentialPayload();

  let browser;
  let keepBrowserOpenSeconds = configuration.keepBrowserOpenSeconds;
  try {
    browser = await chromium.launch({ headless: false, channel: "msedge" });

    // 每次运行都使用临时上下文，不读取已有浏览器的配置、Cookie 或自动填充数据。
    const context = await browser.newContext({ locale: "zh-CN" });
    const page = await context.newPage();
    await page.goto(configuration.url, { waitUntil: "domcontentloaded", timeout: 60000 });
    await page.waitForTimeout(1500);

    const accountField = await firstVisible(page, [
      'input[placeholder*="账号"]',
      'input[placeholder*="用户名"]',
      'input[aria-label*="账号"]',
      'input[type="email"]',
      'input[type="text"]'
    ]);
    const passwordField = await firstVisible(page, ['input[type="password"]']);
    const captchaField = await firstVisible(page, [
      'input[placeholder*="验证码"]',
      'input[placeholder*="验证"]',
      'input[aria-label*="验证码"]'
    ]);

    if (accountField === null || passwordField === null) {
      throw new Error("没有识别到账号框或密码框，请检查网页结构。 ");
    }
    if (configuration.captcha && captchaField === null) {
      throw new Error("站点要求手动验证码，但没有识别到验证码输入框；未提交登录。 ");
    }

    await accountField.fill(credential.username);
    await passwordField.fill(credential.password);
    console.log("账号和密码已临时填入，不会显示或保存。 ");

    if (configuration.captcha) {
      const captchaReady = await waitForManualCaptcha(captchaField, configuration.captchaTimeoutSeconds);
      if (!captchaReady) {
        throw new Error("等待用户输入验证码超时，未提交登录。 ");
      }
    }

    const loginButton = await firstVisible(page, [
      'button:has-text("登录")',
      'input[type="submit"]',
      '[role="button"]:has-text("登录")'
    ]);
    if (loginButton === null) {
      throw new Error("没有识别到登录按钮，未提交登录。 ");
    }

    await loginButton.click();
    await page.waitForTimeout(2500);

    const passwordStillVisible = await firstVisible(page, ['input[type="password"]']);
    console.log(passwordStillVisible === null
      ? "页面已离开登录表单，请在浏览器中人工确认结果。"
      : "页面仍显示登录表单，请检查验证码或账号信息。 ");

    if (configuration.keepBrowserOpenSeconds === 0) {
      console.log("浏览器将保持打开；完成检查后请手动结束本次运行。 ");
      await new Promise(() => {});
    }
    else {
      await sleep(configuration.keepBrowserOpenSeconds * 1000);
    }
  }
  finally {
    if (browser && keepBrowserOpenSeconds !== 0) {
      await browser.close();
    }
  }
}

main().catch((error) => {
  console.error(`登录流程未完成：${error.message}`);
  process.exitCode = 1;
});
