import assert from "node:assert/strict";
import { mkdtemp, readFile, readdir, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import {
  BrowserBlobReceiverError,
  receiveBrowserBlobZip,
  receiveBrowserStreamZip,
  receiveEdgeCdpZip,
} from "./browser_blob_receiver.mjs";

const root = await mkdtemp(path.join(os.tmpdir(), "xf-browser-blob-receiver-"));
try {
  const payload = Buffer.from("PK\x03\x04fixture-package", "binary");
  const first = await receiveBrowserBlobZip({
    base64Body: payload.toString("base64"),
    downloadDir: root,
    suggestedFilename: "fixture-source.zip",
    expectedBytes: payload.length,
  });
  assert.equal(first.sizeBytes, payload.length);
  assert.match(first.sha256, /^sha256:[a-f0-9]{64}$/);
  assert.deepEqual(await readFile(first.path), payload);
  await assert.rejects(
    () =>
      receiveBrowserBlobZip({
        base64Body: payload.toString("base64"),
        downloadDir: root,
        suggestedFilename: "fixture-source.zip",
      }),
    BrowserBlobReceiverError,
  );
  await assert.rejects(
    () =>
      receiveBrowserBlobZip({
        base64Body: payload.toString("base64"),
        downloadDir: root,
        suggestedFilename: "bad-size.zip",
        expectedBytes: payload.length + 1,
      }),
    BrowserBlobReceiverError,
  );

  const streamPayload = Buffer.from("PK\x03\x04streamed-fixture-package", "binary");
  async function* streamChunks() {
    yield streamPayload.subarray(0, 1);
    yield streamPayload.subarray(1, 7);
    yield streamPayload.subarray(7);
  }
  const streamed = await receiveBrowserStreamZip({
    chunks: streamChunks(),
    downloadDir: root,
    suggestedFilename: "stream-source.zip",
    expectedBytes: streamPayload.length,
  });
  assert.equal(streamed.sizeBytes, streamPayload.length);
  assert.deepEqual(await readFile(streamed.path), streamPayload);

  async function* shortChunks() {
    yield streamPayload.subarray(0, -1);
  }
  await assert.rejects(
    () =>
      receiveBrowserStreamZip({
        chunks: shortChunks(),
        downloadDir: root,
        suggestedFilename: "short-stream.zip",
        expectedBytes: streamPayload.length,
      }),
    BrowserBlobReceiverError,
  );
  assert.equal((await readdir(root)).some((name) => name.endsWith(".edge-stream.part")), false);

  async function* longChunks() {
    yield streamPayload;
    yield Buffer.from("extra");
  }
  await assert.rejects(
    () =>
      receiveBrowserStreamZip({
        chunks: longChunks(),
        downloadDir: root,
        suggestedFilename: "long-stream.zip",
        expectedBytes: streamPayload.length,
      }),
    BrowserBlobReceiverError,
  );
  assert.equal((await readdir(root)).some((name) => name.endsWith(".edge-stream.part")), false);

  const cdpPayload = Buffer.from("PK\x03\x04cdp-fixture-package", "binary");
  const cdpCalls = [];
  const encodedParts = [cdpPayload.subarray(0, 5), cdpPayload.subarray(5)];
  const cdpSession = {
    async send(method, params) {
      cdpCalls.push({ method, params });
      if (method === "Fetch.takeResponseBodyAsStream") return { stream: "fixture-stream" };
      if (method === "IO.read") {
        const value = encodedParts.shift();
        return {
          base64Encoded: true,
          data: value ? value.toString("base64") : "",
          eof: encodedParts.length === 0,
        };
      }
      if (method === "IO.close" || method === "Fetch.failRequest") return {};
      throw new Error(`unexpected CDP method: ${method}`);
    },
  };
  const progress = [];
  const cdp = await receiveEdgeCdpZip({
    cdpSession,
    fetchRequestId: "fetch-fixture",
    downloadDir: root,
    suggestedFilename: "cdp-source.zip",
    expectedBytes: cdpPayload.length,
    onProgress: (value) => progress.push(value.receivedBytes),
  });
  assert.deepEqual(await readFile(cdp.path), cdpPayload);
  assert.deepEqual(progress, [5, cdpPayload.length]);
  assert.equal(cdpCalls.at(-2).method, "IO.close");
  assert.equal(cdpCalls.at(-1).method, "Fetch.failRequest");
  console.log("browser_blob_receiver: ok");
} finally {
  await rm(root, { recursive: true, force: true });
}
