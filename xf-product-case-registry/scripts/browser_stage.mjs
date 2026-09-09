import { createHash, randomUUID } from "node:crypto";
import { lstat, mkdir, realpath, rename, writeFile } from "node:fs/promises";
import path from "node:path";

const stages = new Set(["LIST", "DETAIL", "PACKAGE_SELECTION", "NAVIGATION", "FILTER"]);
const transitional = new Set([
  "SOURCE_LOADING", "SOURCE_PAGINATION_NOT_COMMITTED", "SOURCE_PREVIOUS_PAGE_STILL_VISIBLE",
  "SOURCE_DETAIL_ROUTE_CHANGED", "SOURCE_ROUTE_CHANGED", "SOURCE_DETAIL_IDENTITY_NOT_READY",
  "SOURCE_DETAIL_TABLES_NOT_READY", "SOURCE_DOCUMENT_DIRECTORY_NOT_READY", "SOURCE_NOT_READY",
  "SOURCE_PACKAGE_SELECTION_NOT_READY", "SOURCE_CONTAINER_NOT_READY",
]);
const digest = (value) => createHash("sha256").update(JSON.stringify(value)).digest("hex");
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function validateCheckpoint(value, stage, bindingDigest) {
  if (value?.schemaVersion !== "SourceStageV1" || value.stage !== stage ||
      value.bindingDigest !== bindingDigest || !/^[a-f0-9-]{36}$/.test(value.recordId ?? "") ||
      typeof value.actionIssued !== "boolean" ||
      !["RUNNING", "WAITING", "PAUSED", "BLOCKED", "COMPLETED"].includes(value.status) ||
      ![60000, 120000].includes(value.waitLimitMs)) {
    throw new Error("SOURCE_STAGE_CHECKPOINT_MISMATCH");
  }
  for (const key of ["processingMs", "networkWaitMs", "manualPauseMs", "observations"]) {
    if (!Number.isFinite(value[key]) || value[key] < 0) throw new Error("SOURCE_STAGE_CHECKPOINT_INVALID");
  }
  if (value.manualPauseStartedMs !== null &&
      (!Number.isFinite(value.manualPauseStartedMs) || value.manualPauseStartedMs < 0)) {
    throw new Error("SOURCE_STAGE_CHECKPOINT_INVALID");
  }
  return structuredClone(value);
}

/**
 * One explicitly supplied action at most, with a durable pre-action receipt.
 * Call again with the returned checkpoint to continue observation, not replay clicks.
 * All operations finish inside the call; no browser promises survive a returned receipt.
 */
export async function advanceSourceStage({
  stage, identity, read, validate, action, checkpoint = null, onCheckpoint,
  budgetMs = 45000, now = Date.now, sleep = delay,
}) {
  if (!stages.has(stage) || !identity || typeof identity !== "object" ||
      typeof read !== "function" || typeof validate !== "function" ||
      !Number.isInteger(budgetMs) || budgetMs < 1 || budgetMs > 55000 ||
      (action !== undefined && typeof action !== "function") ||
      (action && typeof onCheckpoint !== "function")) {
    throw new Error("SOURCE_STAGE_ARGUMENT_INVALID");
  }
  const bindingDigest = digest({ stage, identity });
  const state = checkpoint ? validateCheckpoint(checkpoint, stage, bindingDigest) : {
    schemaVersion: "SourceStageV1", recordId: randomUUID(), stage, bindingDigest,
    status: "RUNNING", actionIssued: false, waitLimitMs: 60000, observations: 0,
    startedAt: new Date(now()).toISOString(), processingMs: 0, networkWaitMs: 0,
    manualPauseMs: 0, manualPauseStartedMs: null, reason: null,
  };
  const wasCompleted = state.status === "COMPLETED";
  const callStarted = now();
  let previousDigest = null;
  let lastSummary = null;
  let lastBusy = false;
  const emit = async (status, reason = null) => {
    state.status = status;
    state.reason = reason;
    state.updatedAt = new Date(now()).toISOString();
    if (onCheckpoint) await onCheckpoint(structuredClone(state));
    return { status, reason, summary: status === "COMPLETED" ? lastSummary : null,
      checkpoint: structuredClone(state),
      timings: { processingSeconds: state.processingMs / 1000,
        networkWaitSeconds: state.networkWaitMs / 1000, manualPauseSeconds: state.manualPauseMs / 1000 } };
  };
  while (true) {
    let observation;
    const beforeRead = now();
    try {
      observation = await read();
    } catch {
      state.processingMs += Math.max(0, now() - beforeRead);
      return emit("WAITING", "SOURCE_CONTROL_READ_UNAVAILABLE");
    }
    state.processingMs += Math.max(0, now() - beforeRead);
    state.observations += 1;
    if (state.manualPauseStartedMs !== null) {
      state.manualPauseMs += Math.max(0, now() - state.manualPauseStartedMs);
      state.manualPauseStartedMs = null;
    }
    if (observation?.loginRequired || observation?.reason === "SOURCE_LOGIN_REQUIRED") {
      state.manualPauseStartedMs = now();
      return emit("PAUSED", "SOURCE_LOGIN_REQUIRED");
    }
    if (observation?.userBusy === true) {
      state.manualPauseStartedMs = now();
      return emit("PAUSED", "SOURCE_USER_EDITING");
    }
    lastBusy = observation?.busy === true || observation?.reason === "SOURCE_LOADING";
    let reason;
    try {
      lastSummary = validate(observation);
      const currentDigest = digest(lastSummary);
      if (currentDigest === previousDigest) return emit("COMPLETED");
      previousDigest = currentDigest;
    } catch (error) {
      reason = /^[A-Z][A-Z0-9_]{1,100}$/.test(error?.message ?? "") ? error.message : "SOURCE_VALIDATION_FAILED";
      previousDigest = null;
      lastSummary = null;
      if (!transitional.has(reason)) return emit("BLOCKED", reason);
      if (action && !state.actionIssued && !wasCompleted) {
        state.actionIssued = true;
        // The callback must atomically persist this before any click can be issued.
        await emit("RUNNING", "SOURCE_ACTION_ISSUED");
        const beforeAction = now();
        try { await action(); }
        catch { state.reason = "SOURCE_ACTION_OUTCOME_UNKNOWN"; }
        finally { state.processingMs += Math.max(0, now() - beforeAction); }
        // Even a thrown action may have succeeded. Always observe; never issue it again.
        if (now() - callStarted < budgetMs) continue;
      }
    }
    const elapsed = state.processingMs + state.networkWaitMs;
    if (elapsed >= state.waitLimitMs) {
      if (state.waitLimitMs === 60000 && lastBusy) state.waitLimitMs = 120000;
      else return emit("BLOCKED", "SOURCE_STAGE_WAIT_EXPIRED");
    }
    const remaining = budgetMs - (now() - callStarted);
    if (remaining <= 0) return emit("WAITING", reason ?? "SOURCE_STABILITY_READ_PENDING");
    const beforeWait = now();
    await sleep(Math.min(500, remaining));
    state.networkWaitMs += Math.max(0, now() - beforeWait);
  }
}

/** Store only stage metadata; identity fields, page text and credentials are not serialized. */
export async function saveSourceStageCheckpoint(evidenceDir, checkpoint) {
  if (!path.isAbsolute(evidenceDir)) throw new Error("SOURCE_STAGE_EVIDENCE_PATH_INVALID");
  const state = validateCheckpoint(checkpoint, checkpoint.stage, checkpoint.bindingDigest);
  if (!stages.has(state.stage) || !/^[a-f0-9]{64}$/.test(state.bindingDigest)) {
    throw new Error("SOURCE_STAGE_CHECKPOINT_INVALID");
  }
  await mkdir(evidenceDir, { recursive: true });
  const canonical = await realpath(evidenceDir);
  if (path.resolve(canonical).toLowerCase() !== path.resolve(evidenceDir).toLowerCase()) {
    throw new Error("SOURCE_STAGE_EVIDENCE_REPARSE_POINT");
  }
  const destination = path.join(evidenceDir, `阶段_${state.stage}_${state.bindingDigest.slice(0, 16)}_${state.recordId}.json`);
  const temporary = destination + ".tmp";
  for (const candidate of [destination, temporary]) {
    try { if ((await lstat(candidate)).isSymbolicLink()) throw new Error("SOURCE_STAGE_EVIDENCE_REPARSE_POINT"); }
    catch (error) { if (error.code !== "ENOENT") throw error; }
  }
  const keys = ["schemaVersion", "recordId", "stage", "bindingDigest", "status", "actionIssued", "waitLimitMs",
    "observations", "startedAt", "updatedAt", "processingMs", "networkWaitMs", "manualPauseMs",
    "manualPauseStartedMs", "reason"];
  const clean = Object.fromEntries(keys.map((key) => [key, state[key] ?? null]));
  await writeFile(temporary, JSON.stringify(clean, null, 2) + "\n", "utf8");
  await rename(temporary, destination);
  return destination;
}
