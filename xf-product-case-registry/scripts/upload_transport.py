"""One bounded, cancellable upload using the caller's authenticated session.

No body retries. Progress measures bytes passed to the transport, never server
acceptance; only the server receipt can advance the durable upload checkpoint.
"""

from __future__ import annotations

import asyncio
import sys
import time
from typing import Any

import httpx

UPLOAD_TOTAL_SECONDS = 210.0
UPLOAD_IDLE_SECONDS = 60.0
PROGRESS_INTERVAL_SECONDS = 2.0


def upload_request(client: httpx.Client, method: str, url: str, **kwargs: Any) -> httpx.Response:
    completed, count = kwargs.pop("upload_progress", (0, 1))
    filename = kwargs["files"]["file"][0]
    # Keep control characters out of terminal progress; never print headers or URLs.
    label = "".join(c for c in str(filename) if c.isprintable())[:120]
    request = client.build_request(method, url, **kwargs)
    request.extensions["timeout"] = httpx.Timeout(UPLOAD_IDLE_SECONDS).as_dict()
    chunks = iter(request.stream)
    total = int(request.headers.get("content-length", "0"))
    started = last_progress = time.monotonic()
    last_report = getattr(client, "_xfpcr_last_upload_progress", float("-inf"))
    sent = 0

    def report() -> None:
        nonlocal last_report
        now = time.monotonic()
        if now - last_report >= PROGRESS_INTERVAL_SECONDS:
            print(
                f"上传 {label} | 已接收 {completed}/{count} 个 | "
                f"已发送 {sent}/{total} 字节 | 耗时 {now - started:.0f} 秒（等待接收回执）",
                file=sys.stderr,
                flush=True,
            )
            last_report = now
            client._xfpcr_last_upload_progress = now  # type: ignore[attr-defined]

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            nonlocal sent, last_progress
            for chunk in chunks:
                yield chunk
                sent += len(chunk)
                last_progress = time.monotonic()
                report()
                await asyncio.sleep(0)

    async def run() -> httpx.Response:
        # Production clients use HTTPX's standard environment proxy/TLS settings.
        # MockTransport supports both APIs and keeps synthetic contract tests offline.
        transport = getattr(client, "_transport", None)
        options = {"transport": transport} if isinstance(transport, httpx.MockTransport) else {}
        request.stream = Body()
        async with httpx.AsyncClient(follow_redirects=False, **options) as sender:
            task = asyncio.create_task(sender.send(request))
            try:
                while not task.done():
                    await asyncio.wait({task}, timeout=min(1.0, UPLOAD_IDLE_SECONDS / 4))
                    report()
                    now = time.monotonic()
                    if not task.done() and now - started >= UPLOAD_TOTAL_SECONDS:
                        raise httpx.TimeoutException(
                            "单文件上传达到总期限，已停止发送", request=request
                        )
                    if not task.done() and now - last_progress >= UPLOAD_IDLE_SECONDS:
                        raise httpx.TimeoutException(
                            "单文件上传连续无进展，已停止发送", request=request
                        )
                response = await task
                client.cookies.update(sender.cookies)
                return response
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    report()
    return asyncio.run(run())
