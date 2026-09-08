const METHODS = [
  "Network.responseReceived", "Network.dataReceived", "Network.loadingFinished", "Network.loadingFailed",
  "Page.downloadWillBegin", "Page.downloadProgress", "Browser.downloadWillBegin", "Browser.downloadProgress",
];
const DOWNLOAD_PATH = "/api/blade-fileserver/api/fileserver/usualDownloadFile";
const MAX_BYTES = 2 * 1024 * 1024 * 1024;

/** Keep only the current business download's metadata. Never retain request or authentication headers. */
export class SourceDownloadObservation {
  constructor(origin) {
    const url = new URL(origin);
    if (!/^https?:$/.test(url.protocol) || url.username || url.password || url.search || url.hash || url.pathname !== "/") {
      throw new Error("DOWNLOAD_ORIGIN_INVALID");
    }
    this.origin = url.origin;
    this.response = null;
    this.begin = null;
    this.progress = null;
    this.failure = null;
    this.finished = false;
    this.dataBytes = 0;
  }

  accept(event) {
    const p = event.params ?? {};
    if (event.method === "Network.responseReceived") {
      let url;
      try { url = new URL(p.response?.url); } catch { return; }
      if (url.origin !== this.origin || url.pathname !== DOWNLOAD_PATH) return;
      if (this.response && this.response.requestId !== p.requestId) { this.failure = "DOWNLOAD_RESPONSE_AMBIGUOUS"; return; }
      const length = Object.entries(p.response.headers ?? {}).find(([key]) => key.toLowerCase() === "content-length")?.[1];
      const bytes = /^\d+$/.test(String(length ?? "")) ? Number(length) : null;
      this.response = {
        requestId: p.requestId, status: p.response.status, mimeType: p.response.mimeType,
        contentLength: Number.isSafeInteger(bytes) && bytes > 0 && bytes <= MAX_BYTES ? bytes : null,
      };
      if (p.response.status !== 200) this.failure = p.response.status === 401 ? "SOURCE_AUTH_EXPIRED" : "DOWNLOAD_HTTP_FAILED";
      else if (!["application/zip", "application/x-zip-compressed", "application/octet-stream"].includes(p.response.mimeType)) {
        this.failure = "DOWNLOAD_MIME_INVALID";
      }
    } else if (event.method === "Network.dataReceived" && this.response?.requestId === p.requestId) {
      if (!Number.isSafeInteger(p.dataLength) || p.dataLength < 0 || this.dataBytes + p.dataLength > MAX_BYTES) {
        this.failure = "DOWNLOAD_DATA_LENGTH_INVALID";
      } else this.dataBytes += p.dataLength;
    } else if (event.method === "Network.loadingFinished" && this.response?.requestId === p.requestId) {
      this.finished = true;
    } else if (event.method === "Network.loadingFailed" && this.response?.requestId === p.requestId) {
      this.failure = "DOWNLOAD_NETWORK_FAILED";
    } else if (["Page.downloadWillBegin", "Browser.downloadWillBegin"].includes(event.method)) {
      if (typeof p.url !== "string" || !p.url.startsWith(`blob:${this.origin}/`)) return;
      if (this.begin && this.begin.guid !== p.guid) { this.failure = "DOWNLOAD_EVENT_AMBIGUOUS"; return; }
      // These identifiers remain only in the current controlled browser session, never in a manifest.
      this.begin = { guid: p.guid, suggestedFilename: p.suggestedFilename };
    } else if (["Page.downloadProgress", "Browser.downloadProgress"].includes(event.method) && this.begin?.guid === p.guid) {
      this.progress = { state: p.state, receivedBytes: p.receivedBytes, totalBytes: p.totalBytes };
    }
  }

  result() {
    const { response, begin, progress, finished, dataBytes } = this;
    if (this.failure) return { status: "FAILED", code: this.failure, response, begin, progress, finished, dataBytes };
    if (!response || !begin || !finished || !["canceled", "completed"].includes(progress?.state)) return null;
    const expected = progress.totalBytes;
    if (!Number.isSafeInteger(expected) || expected <= 0 || expected > MAX_BYTES || dataBytes !== expected ||
        (response.contentLength !== null && response.contentLength !== expected)) {
      return { status: "FAILED", code: "DOWNLOAD_LENGTH_EVIDENCE_MISMATCH", response, begin, progress, finished, dataBytes };
    }
    if (progress.state === "canceled" && progress.receivedBytes !== 0) {
      return { status: "FAILED", code: "DOWNLOAD_PARTIAL_DELIVERY", response, begin, progress, finished, dataBytes };
    }
    if (progress.state === "completed" && progress.receivedBytes !== expected) {
      return { status: "FAILED", code: "DOWNLOAD_PARTIAL_DELIVERY", response, begin, progress, finished, dataBytes };
    }
    return { status: progress.state === "completed" ? "NATIVE_COMPLETED" : "BLOB_CANCELED_ZERO", response, begin, progress, finished, dataBytes };
  }
}

/** Start before the one permitted click and drain continuously. The caller MUST await done in the SAME CUA call. */
export async function beginSourceDownloadObservation(cdp, { origin, timeoutMs = 120000 } = {}) {
  if (!cdp?.readEvents || !Number.isSafeInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 1800000) {
    throw new Error("DOWNLOAD_OBSERVER_ARGUMENT_INVALID");
  }
  const observation = new SourceDownloadObservation(origin);
  const seed = await cdp.readEvents({ methods: METHODS, limit: 1000 });
  let cursor = seed.cursor;
  let stopped = false;
  const deadline = Date.now() + timeoutMs;
  const done = (async () => {
    try {
      while (!stopped && Date.now() < deadline) {
        const batch = await cdp.readEvents({ methods: METHODS, afterSequence: cursor, limit: 1000, timeoutMs: Math.min(1000, Math.max(1, deadline - Date.now())) });
        if (batch.truncated) return { status: "FAILED", code: "DOWNLOAD_RESPONSE_EVIDENCE_EVICTED" };
        cursor = batch.cursor;
        for (const event of batch.events) observation.accept(event);
        const result = observation.result();
        if (result) return result;
      }
      return { status: stopped ? "STOPPED" : "WAITING", response: observation.response, finished: observation.finished, dataBytes: observation.dataBytes };
    } catch {
      return { status: "FAILED", code: "DOWNLOAD_OBSERVER_UNAVAILABLE" };
    }
  })();
  return { done, stop: () => { stopped = true; } };
}

/** Supported CUA entry point: do not leave a browser readEvents promise pending across REPL calls. */
export async function observeSourceDownloadAction(cdp, { origin, action, timeoutMs = 45000 } = {}) {
  if (typeof action !== "function" || timeoutMs > 55000) throw new Error("DOWNLOAD_ACTION_ARGUMENT_INVALID");
  const observer = await beginSourceDownloadObservation(cdp, { origin, timeoutMs });
  try {
    await action();
    return await observer.done;
  } finally {
    observer.stop();
    await observer.done;
  }
}
