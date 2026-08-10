#!/usr/bin/env bash
set -euo pipefail

mode=apply
if [ "$#" -gt 1 ]; then
  echo "用法：meifu-download-cache-gc [--dry-run]" >&2
  exit 2
fi
if [ "$#" -eq 1 ]; then
  if [ "$1" != "--dry-run" ]; then
    echo "用法：meifu-download-cache-gc [--dry-run]" >&2
    exit 2
  fi
  mode=dry-run
fi

stale_hours=$(printenv MEIFU_DOWNLOAD_CACHE_STALE_HOURS 2>/dev/null || printf '72')
min_free_gib=$(printenv MEIFU_DOWNLOAD_CACHE_MIN_FREE_GIB 2>/dev/null || printf '8')
if [ -z "$stale_hours" ]; then stale_hours=72; fi
if [ -z "$min_free_gib" ]; then min_free_gib=8; fi

python3 - "$stale_hours" "$min_free_gib" "$mode" <<'PY'
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

ROOTS = (
    Path("/root/.cache/comfyui-models"),
    Path("/root/.cache/meifu-downloads"),
)
ACTIVE_GRACE_SECONDS = 20 * 60


def emit(status: str, **fields: object) -> None:
    print(json.dumps({"status": status, **fields}, ensure_ascii=False, sort_keys=True))


def latest_activity(job: Path) -> float:
    newest = job.stat().st_mtime
    for candidate in job.rglob("*"):
        if candidate.is_symlink():
            continue
        try:
            newest = max(newest, candidate.stat().st_mtime)
        except FileNotFoundError:
            continue
    return newest


def collect_jobs() -> list[tuple[Path, Path, float]]:
    jobs: list[tuple[Path, Path, float]] = []
    for root in ROOTS:
        if not root.exists():
            emit("cache_root_absent", cache_root=str(root))
            continue
        if root.is_symlink():
            emit("cache_root_skipped", cache_root=str(root), reason="symbolic_link")
            continue
        resolved_root = root.resolve()
        for job in root.iterdir():
            if job.is_symlink() or not job.is_dir():
                continue
            try:
                if job.parent.resolve() != resolved_root:
                    continue
                jobs.append((resolved_root, job, latest_activity(job)))
            except FileNotFoundError:
                continue
    return jobs


def remove_job(root: Path, job: Path, latest: float, reason: str, dry_run: bool) -> bool:
    if job.is_symlink() or job.parent.resolve() != root.resolve():
        emit("cache_job_skipped", task=job.name, reason="path_safety_check")
        return False
    payload = {
        "cache_root": str(root),
        "task": job.name,
        "reason": reason,
        "last_activity_epoch": int(latest),
    }
    if dry_run:
        emit("would_remove", **payload)
        return True
    shutil.rmtree(job)
    emit("removed", **payload)
    return True


try:
    stale_hours = float(sys.argv[1])
    min_free_gib = float(sys.argv[2])
except ValueError as exc:
    raise SystemExit(f"缓存参数必须是数字：{exc}")
if stale_hours <= 0 or min_free_gib < 0:
    raise SystemExit("缓存参数超出安全范围。")

dry_run = sys.argv[3] == "dry-run"
now = time.time()
stale_before = now - stale_hours * 3600
jobs = collect_jobs()
removed = set()

for root, job, latest in sorted(jobs, key=lambda item: item[2]):
    if latest >= stale_before:
        continue
    if remove_job(root, job, latest, "stale", dry_run):
        removed.add(job)

def free_bytes() -> int:
    return shutil.disk_usage("/").free

minimum_free_bytes = int(min_free_gib * 1024**3)
for root, job, latest in sorted(jobs, key=lambda item: item[2]):
    if job in removed:
        continue
    if free_bytes() >= minimum_free_bytes:
        break
    if latest >= now - ACTIVE_GRACE_SECONDS:
        emit("cache_job_skipped", task=job.name, reason="recent_activity")
        continue
    if remove_job(root, job, latest, "low_free_space", dry_run):
        removed.add(job)

emit(
    "summary",
    dry_run=dry_run,
    stale_hours=stale_hours,
    min_free_gib=min_free_gib,
    observed_jobs=len(jobs),
    selected_jobs=len(removed),
    free_bytes=free_bytes(),
)
PY
