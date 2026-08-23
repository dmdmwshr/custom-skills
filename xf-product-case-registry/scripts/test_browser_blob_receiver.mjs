import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import { BrowserBlobReceiverError, receiveBrowserBlobZip } from "./browser_blob_receiver.mjs";

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
  console.log("browser_blob_receiver: ok");
} finally {
  await rm(root, { recursive: true, force: true });
}
