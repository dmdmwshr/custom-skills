import { createHash, randomUUID } from "node:crypto";
import { link, open, stat, unlink, writeFile } from "node:fs/promises";
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

async function writeBufferFully(handle, buffer) {
  let offset = 0;
  while (offset < buffer.length) {
    const { bytesWritten } = await handle.write(buffer, offset, buffer.length - offset);
    if (!Number.isSafeInteger(bytesWritten) || bytesWritten <= 0) {
      throw new BrowserBlobReceiverError("浏览器响应流写入未取得进展");
    }
    offset += bytesWritten;
  }
}

function requireExpectedBytes(value) {
  if (!Number.isSafeInteger(value) || value <= 0 || value > MAX_BROWSER_RESPONSE_BYTES) {
    throw new BrowserBlobReceiverError("预期下载大小不合法");
  }
  return value;
}

function requireExpectedSha256(value) {
  if (value !== undefined && (typeof value !== "string" || !SHA256.test(value))) {
    throw new BrowserBlobReceiverError("预期下载哈希不合法");
  }
  return value?.toLowerCase();
}

async function resolveDestination(downloadDir, suggestedFilename) {
  const filename = requireSafeZipName(suggestedFilename);
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
  return { destination, filename, root };
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
  const { destination, filename, root } = await resolveDestination(downloadDir, suggestedFilename);
  const body = requireBase64(base64Body);
  if (expectedBytes !== undefined) requireExpectedBytes(expectedBytes);
  const normalizedExpectedSha256 = requireExpectedSha256(expectedSha256);

  const payload = Buffer.from(body, "base64");
  if (payload.length < 4 || payload.subarray(0, 2).toString("ascii") !== "PK") {
    throw new BrowserBlobReceiverError("浏览器响应不是 ZIP 文件头");
  }
  if (expectedBytes !== undefined && payload.length !== expectedBytes) {
    throw new BrowserBlobReceiverError("浏览器响应长度与下载事件不一致");
  }
  const sha256 = createHash("sha256").update(payload).digest("hex");
  if (normalizedExpectedSha256 !== undefined && sha256 !== normalizedExpectedSha256) {
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

/**
 * 把受控浏览器网络响应的顺序字节流原子写入下载目录。
 * 调用方必须先验证该流只属于当前案卷、当前点击和精确下载接口；本函数不读取请求头、
 * Cookie、令牌、浏览器配置或来源页面状态。
 */
export async function receiveBrowserStreamZip({
  chunks,
  downloadDir,
  suggestedFilename,
  expectedBytes,
  expectedSha256,
}) {
  if (!chunks || typeof chunks[Symbol.asyncIterator] !== "function") {
    throw new BrowserBlobReceiverError("浏览器响应流不是异步可迭代字节流");
  }
  const sizeLimit = requireExpectedBytes(expectedBytes);
  const normalizedExpectedSha256 = requireExpectedSha256(expectedSha256);
  const { destination, filename, root } = await resolveDestination(downloadDir, suggestedFilename);
  await requireEmptyDestination(destination);

  const temporary = path.join(root, `.${filename}.${randomUUID()}.edge-stream.part`);
  const sha256 = createHash("sha256");
  let handle;
  let temporaryExists = false;
  let destinationCreated = false;
  let sizeBytes = 0;
  let prefix = Buffer.alloc(0);
  try {
    handle = await open(temporary, "wx");
    temporaryExists = true;
    for await (const value of chunks) {
      const chunk = Buffer.isBuffer(value)
        ? value
        : value instanceof Uint8Array
          ? Buffer.from(value)
          : null;
      if (chunk === null) {
        throw new BrowserBlobReceiverError("浏览器响应流包含非字节数据");
      }
      if (!chunk.length) continue;
      if (sizeBytes + chunk.length > sizeLimit) {
        throw new BrowserBlobReceiverError("浏览器响应流超过预期下载大小");
      }
      if (prefix.length < 4) {
        prefix = Buffer.concat([prefix, chunk.subarray(0, 4 - prefix.length)]);
      }
      await writeBufferFully(handle, chunk);
      sha256.update(chunk);
      sizeBytes += chunk.length;
    }
    await handle.sync();
    await handle.close();
    handle = undefined;

    if (prefix.length < 4 || prefix.subarray(0, 2).toString("ascii") !== "PK") {
      throw new BrowserBlobReceiverError("浏览器响应流不是 ZIP 文件头");
    }
    if (sizeBytes !== sizeLimit) {
      throw new BrowserBlobReceiverError("浏览器响应流长度与响应头不一致");
    }
    const digest = sha256.digest("hex");
    if (normalizedExpectedSha256 !== undefined && digest !== normalizedExpectedSha256) {
      throw new BrowserBlobReceiverError("浏览器响应流哈希与预期不一致");
    }
    const written = await stat(temporary);
    if (written.size !== sizeBytes) {
      throw new BrowserBlobReceiverError("浏览器响应流写入长度不一致");
    }
    await link(temporary, destination);
    destinationCreated = true;
    await unlink(temporary);
    temporaryExists = false;
    return { path: destination, sizeBytes, sha256: `sha256:${digest}` };
  } catch (error) {
    if (handle) await handle.close().catch(() => undefined);
    if (destinationCreated) await unlink(destination).catch(() => undefined);
    if (temporaryExists) await unlink(temporary).catch(() => undefined);
    if (error instanceof BrowserBlobReceiverError) throw error;
    throw new BrowserBlobReceiverError(`浏览器响应流原子落盘失败：${String(error)}`);
  }
}

/**
 * 从 Fetch.takeResponseBodyAsStream 返回的 CDP 流接收 ZIP。读取完成后主动取消原页面请求，
 * 使网页退出加载态；请求体不会回传给页面，也不会读取或记录请求认证信息。
 */
export async function receiveEdgeCdpZip({
  cdpSession,
  fetchRequestId,
  downloadDir,
  suggestedFilename,
  expectedBytes,
  expectedSha256,
  readSize = 1024 * 1024,
  onProgress,
}) {
  if (!cdpSession || typeof cdpSession.send !== "function") {
    throw new BrowserBlobReceiverError("缺少受控 Edge CDP 会话");
  }
  if (typeof fetchRequestId !== "string" || !fetchRequestId) {
    throw new BrowserBlobReceiverError("缺少当前响应的 Fetch 请求标识");
  }
  if (!Number.isSafeInteger(readSize) || readSize < 64 * 1024 || readSize > 8 * 1024 * 1024) {
    throw new BrowserBlobReceiverError("CDP 顺序读取块大小不合法");
  }
  if (onProgress !== undefined && typeof onProgress !== "function") {
    throw new BrowserBlobReceiverError("CDP 进度回调不合法");
  }

  let stream;
  let receivedBytes = 0;
  try {
    ({ stream } = await cdpSession.send("Fetch.takeResponseBodyAsStream", {
      requestId: fetchRequestId,
    }));
    if (typeof stream !== "string" || !stream) {
      throw new BrowserBlobReceiverError("CDP 未返回有效响应流");
    }
    async function* readChunks() {
      while (true) {
        const part = await cdpSession.send("IO.read", { handle: stream, size: readSize });
        if (typeof part?.data !== "string") {
          throw new BrowserBlobReceiverError("CDP 响应流返回了无效数据块");
        }
        if (part.data && part.base64Encoded !== true) {
          throw new BrowserBlobReceiverError("CDP 二进制响应流未使用 Base64 编码");
        }
        const chunk = Buffer.from(part.data, "base64");
        if (chunk.length) {
          receivedBytes += chunk.length;
          onProgress?.({ receivedBytes, expectedBytes });
          yield chunk;
        }
        if (part.eof) break;
      }
    }
    return await receiveBrowserStreamZip({
      chunks: readChunks(),
      downloadDir,
      suggestedFilename,
      expectedBytes,
      expectedSha256,
    });
  } catch (error) {
    if (error instanceof BrowserBlobReceiverError) throw error;
    throw new BrowserBlobReceiverError(`Edge CDP 响应流接收失败：${String(error)}`);
  } finally {
    if (stream) await cdpSession.send("IO.close", { handle: stream }).catch(() => undefined);
    await cdpSession
      .send("Fetch.failRequest", { requestId: fetchRequestId, errorReason: "Aborted" })
      .catch(() => undefined);
  }
}
