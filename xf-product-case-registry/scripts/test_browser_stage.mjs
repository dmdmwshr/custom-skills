import assert from "node:assert/strict";
import { mkdtemp, readFile, readdir, realpath, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { advanceSourceStage, saveSourceStageCheckpoint } from "./browser_stage.mjs";

const validate = (value) => { if (!value.ready) throw new Error(value.reason ?? "SOURCE_NOT_READY"); return { digest: value.digest }; };
function rig() {
  let clock = 0;
  const saved = [];
  return { args: { stage: "LIST", identity: { page: 2 }, validate,
    now: () => clock, sleep: async (ms) => { clock += ms; },
    onCheckpoint: async (state) => { saved.push(structuredClone(state)); } },
    saved, advance: (ms) => { clock += ms; } };
}

{
  const { args } = rig();
  let actions = 0, reads = 0;
  const result = await advanceSourceStage({ ...args, read: async () => { reads++; return { ready: true, digest: "current" }; }, action: async () => { actions++; } });
  assert.equal(result.status, "COMPLETED");
  assert.equal(reads, 2);
  assert.equal(actions, 0);
}
{
  const { args, saved } = rig();
  let actions = 0, reads = 0;
  const read = async () => ++reads < 3 ? { ready: false, reason: "SOURCE_PREVIOUS_PAGE_STILL_VISIBLE" } : { ready: true, digest: "new-page" };
  const result = await advanceSourceStage({ ...args, read, action: async () => { actions++; assert.equal(saved.at(-1).actionIssued, true); throw new Error("timeout with private response"); } });
  assert.equal(result.status, "COMPLETED");
  assert.equal(actions, 1);
  assert.ok(!JSON.stringify(result).includes("private response"));
}
{
  const { args } = rig();
  let actions = 0;
  const pending = await advanceSourceStage({ ...args, budgetMs: 1000,
    read: async () => ({ ready: false, reason: "SOURCE_LOADING" }), action: async () => { actions++; } });
  assert.equal(pending.status, "WAITING");
  const done = await advanceSourceStage({ ...args, checkpoint: pending.checkpoint,
    read: async () => ({ ready: true, digest: "done" }), action: async () => { actions++; } });
  assert.equal(done.status, "COMPLETED");
  assert.equal(actions, 1);
  await assert.rejects(advanceSourceStage({ ...args, identity: { page: 3 }, checkpoint: pending.checkpoint, read: async () => ({}) }), /CHECKPOINT_MISMATCH/);
}
{
  const { args, advance } = rig();
  let actions = 0;
  const paused = await advanceSourceStage({ ...args,
    read: async () => ({ ready: false, loginRequired: true }), action: async () => { actions++; } });
  assert.equal(paused.status, "PAUSED");
  assert.equal(actions, 0);
  advance(10000);
  const resumed = await advanceSourceStage({ ...args, checkpoint: paused.checkpoint,
    read: async () => ({ ready: true, digest: "logged-in" }) });
  assert.equal(resumed.status, "COMPLETED");
  assert.equal(resumed.timings.manualPauseSeconds, 10);
}
{
  const { args } = rig();
  const mismatch = await advanceSourceStage({ ...args,
    read: async () => ({ ready: false, reason: "CASE_IDENTITY_CHAIN_MISMATCH" }),
    action: async () => { assert.fail("must not click through identity mismatch"); } });
  assert.equal(mismatch.status, "BLOCKED");
}
{
  const { args } = rig();
  let checkpoint;
  for (let index = 0; index < 3; index++) {
    const result = await advanceSourceStage({ ...args, budgetMs: 50000, checkpoint,
      read: async () => ({ ready: false, reason: "SOURCE_LOADING", busy: true }) });
    checkpoint = result.checkpoint;
  }
  assert.equal(checkpoint.status, "BLOCKED");
  assert.equal(checkpoint.waitLimitMs, 120000);
  assert.equal(checkpoint.networkWaitMs, 120000);
}
{
  const { args } = rig();
  let checkpoint;
  for (let index = 0; index < 2; index++) {
    checkpoint = (await advanceSourceStage({ ...args, budgetMs: 50000, checkpoint,
      read: async () => ({ ready: false, reason: "SOURCE_PREVIOUS_PAGE_STILL_VISIBLE" }) })).checkpoint;
  }
  assert.equal(checkpoint.status, "BLOCKED");
  assert.equal(checkpoint.waitLimitMs, 60000);
  assert.equal(checkpoint.networkWaitMs, 60000);
}
{
  const { args } = rig();
  let actions = 0;
  await assert.rejects(advanceSourceStage({ ...args, onCheckpoint: async () => { throw new Error("disk full"); },
    read: async () => ({ ready: false, reason: "SOURCE_NOT_READY" }), action: async () => { actions++; } }), /disk full/);
  assert.equal(actions, 0);
}
{
  const root = await realpath(os.tmpdir());
  const temporary = await mkdtemp(path.join(root, "source-stage-test-"));
  try {
    const { args } = rig();
    const result = await advanceSourceStage({ ...args, read: async () => ({ ready: true, digest: "ready" }) });
    const file = await saveSourceStageCheckpoint(temporary, { ...result.checkpoint, cookie: "must-not-save", body: "must-not-save" });
    const text = await readFile(file, "utf8");
    assert.ok(!text.includes("must-not-save"));
    assert.equal(JSON.parse(text).status, "COMPLETED");
    const secondRound = await advanceSourceStage({ ...args, read: async () => ({ ready: true, digest: "ready" }) });
    const nextFile = await saveSourceStageCheckpoint(temporary, secondRound.checkpoint);
    assert.notEqual(nextFile, file);
    assert.equal((await readdir(temporary)).length, 2);
    assert.equal(await saveSourceStageCheckpoint(temporary, result.checkpoint), file);
    await assert.rejects(advanceSourceStage({ ...args, checkpoint: { ...result.checkpoint, manualPauseStartedMs: "bad" },
      read: async () => ({ ready: true, digest: "ready" }) }), /CHECKPOINT_INVALID/);
  } finally {
    assert.equal(path.dirname(await realpath(temporary)), root);
    assert.ok(path.basename(temporary).startsWith("source-stage-test-"));
    await rm(temporary, { recursive: true });
  }
}
console.log("browser stage: single action, stable reads, bounded recovery, pause and safe receipts passed");
