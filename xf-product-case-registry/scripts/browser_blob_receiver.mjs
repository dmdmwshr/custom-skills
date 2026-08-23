import { createHash, randomUUID } from "node:crypto";
import { link, stat, unlink, writeFile } from "node:fs/promises";
import path from "node:path";

const MAX_BROWSER_RESPONSE_BYTES = 2 * 1024 * 1024 * 1024;
const BASE64 = /^[A-Za-z0-9+/]*={0,2}$/;
const SHA256 = /^[a-f0-9]{64}$/i;

export class BrowserBlobReceiverError extends Error {}

function requireSafeZipName(value) {
  if (
    typeof value !== "string" ||
    !value ||
    value.length > 180 ||
    path.basename(value) !== value ||
    !value.toLowerCase().endsWith(".zip") ||
    /[<>:"/\\|?*\u0000]/.test(value)
  ) {
    throw new BrowserBlobReceiverError("浏览器建议文件名不是安全的直接 ZIP 文件名");
  }
  return value;
}

function requireBase64(value) {
  if (typeof value !== "string" || !value || value.length % 4 !== 0 || !BASE64.test(value)) {
    throw new BrowserBlobReceiverError("浏览器响应包体不是有效的 Base64 数据");
  }
  const size = Buffer.byteLength(value, "base64");
  if (!size || size > MAX_BROWSER_RESPONSE_BYTES) {
    throw new BrowserBlobReceiverError("浏览器响应包体大小不在允许范围内");
  }
  return value;
}

async function requireEmptyDestination(destination) {
  try {
    await stat(destination);
  } catch (error) {
    if (error && error.code === "ENOENT") return;
    throw error;
  }
  throw new BrowserBlobReceiverError("浏览器恢复目标已存在，拒绝覆盖");
}

/**
 * 从同一受控页面已经完成的 Blob 下载响应原子恢复 ZIP。
 * 调用方必须先验证 Page.downloadProgress 为 canceled/0 bytes，且 Network.loadingFinished
 * 与同一轮 Page.downloadWillBegin 相对应；本函数不读取会话、Cookie 或来源地址。
 */
export async function receiveBrowserBlobZip({
  base64Body,
  downloadDir,
  suggestedFilename,
  expectedBytes,
  expectedSha256,
}) {
  const filename = requireSafeZipName(suggestedFilename);
  const body = requireBase64(base64Body);
  if (!path.isAbsolute(downloadDir)) {
    throw new BrowserBlobReceiverError("下载目录必须是绝对路径");
  }
  const root = path.resolve(downloadDir);
  const rootStat = await stat(root);
  if (!rootStat.isDirectory()) {
    throw new BrowserBlobReceiverError("下载目录不存在");
  }
  const destination = path.resolve(root, filename);
  if (path.dirname(destination) !== root) {
    throw new BrowserBlobReceiverError("恢复文件必须位于已配置下载目录的直接层级");
  }
  if (
    expectedBytes !== undefined &&
    (!Number.isSafeInteger(expectedBytes) || expectedBytes <= 0 || expectedBytes > MAX_BROWSER_RESPONSE_BYTES)
  ) {
    throw new BrowserBlobReceiverError("预期下载大小不合法");
  }
  if (expectedSha256 !== undefined && (typeof expectedSha256 !== "string" || !SHA256.test(expectedSha256))) {
    throw new BrowserBlobReceiverError("预期下载哈希不合法");
  }

  const payload = Buffer.from(body, "base64");
  if (payload.length < 4 || payload.subarray(0, 2).toString("ascii") !== "PK") {
    throw new BrowserBlobReceiverError("浏览器响应不是 ZIP 文件头");
  }
  if (expectedBytes !== undefined && payload.length !== expectedBytes) {
    throw new BrowserBlobReceiverError("浏览器响应长度与下载事件不一致");
  }
  const sha256 = createHash("sha256").update(payload).digest("hex");
  if (expectedSha256 !== undefined && sha256 !== expectedSha256.toLowerCase()) {
    throw new BrowserBlobReceiverError("浏览器响应哈希与预期不一致");
  }

  await requireEmptyDestination(destination);
  const temporary = path.join(root, `.${filename}.${randomUUID()}.iab-recovery.part`);
  let temporaryExists = false;
  try {
    await writeFile(temporary, payload, { flag: "wx" });
    temporaryExists = true;
    const written = await stat(temporary);
    if (written.size !== payload.length) {
      throw new BrowserBlobReceiverError("浏览器响应写入长度不一致");
    }
    // 同目录硬链接在目标存在时会失败，避免 rename 的覆盖语义。
    await link(temporary, destination);
    await unlink(temporary);
    temporaryExists = false;
  } catch (error) {
    if (temporaryExists) await unlink(temporary).catch(() => undefined);
    if (error instanceof BrowserBlobReceiverError) throw error;
    throw new BrowserBlobReceiverError(`浏览器响应原子落盘失败：${String(error)}`);
  }
  return { path: destination, sizeBytes: payload.length, sha256: `sha256:${sha256}` };
}
