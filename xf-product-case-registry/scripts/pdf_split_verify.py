"""Read-only, pixel-exact verification of the PDFs produced by the formal split command."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

if __package__:
    from .registry_cli import (
        RegistryError,
        enforce_local_workspace_preflight,
        inside,
        pdf_info,
        read_json,
        safe_relative,
    )
else:
    from registry_cli import (
        RegistryError,
        enforce_local_workspace_preflight,
        inside,
        pdf_info,
        read_json,
        safe_relative,
    )


def render_page(renderer: str, path: Path, page: int) -> bytes:
    # Some Windows Poppler builds intermittently leave stdout's final block
    # unflushed even with exit code 0. A completed file avoids false mismatches.
    with tempfile.TemporaryDirectory(prefix="xfpcr-page-check-") as temporary:
        prefix = Path(temporary) / "page"
        result = subprocess.run(
            [
                renderer,
                "-r",
                "50",
                "-f",
                str(page),
                "-l",
                str(page),
                "-singlefile",
                str(path),
                str(prefix),
            ],
            capture_output=True,
            timeout=60,
            check=False,
        )
        output = prefix.with_suffix(".ppm")
        if result.returncode != 0 or not output.is_file():
            raise RegistryError("PDF 页面渲染失败；未把原始错误或正文写入报告")
        rendered = output.read_bytes()
        header = re.match(rb"P6\s+(\d+)\s+(\d+)\s+255\s", rendered)
        if header is None or len(rendered) - header.end() != (int(header[1]) * int(header[2]) * 3):
            raise RegistryError("PDF 页面渲染结果字节数不完整，不能判断原件差异")
        return rendered


def verify_split(work: Path, renderer: str) -> dict[str, Any]:
    work = work.resolve()
    inventory = read_json(work / "inventory.json")
    index = read_json(work / "split-index.json")
    source_root = Path(inventory["sourceRoot"]).resolve()
    by_relative = {item["relativePath"]: item for item in inventory["files"]}
    normalized = (work / "normalized").resolve()
    items = index.get("items")
    if not isinstance(items, list) or not items:
        raise RegistryError("缺少正式拆分记录")
    verified = []
    for item in items:
        source_record = by_relative.get(safe_relative(item["sourceRelativePath"]))
        if not source_record:
            raise RegistryError("拆分来源不在清点记录中")
        source = Path(source_record["absolutePath"]).resolve()
        target = (work / safe_relative(item["relativePath"])).resolve()
        if not inside(source, source_root) or not inside(target, normalized) or source == target:
            raise RegistryError("PDF 核验路径越界")
        source_sha, source_pages = pdf_info(source)
        target_sha, target_pages = pdf_info(target)
        start, end = item["pageStart"], item["pageEnd"]
        if source_sha != source_record["sha256"] or target_sha != item["sha256"]:
            raise RegistryError("PDF 哈希已变化")
        if (
            not isinstance(start, int)
            or not isinstance(end, int)
            or not 1 <= start <= end <= source_pages
            or target_pages != end - start + 1
        ):
            raise RegistryError("PDF 拆分页码不闭合")
        for offset, page in enumerate(range(start, end + 1), 1):
            # Raw PPM has no PNG timestamps/metadata; compare rendered pixels.
            if render_page(renderer, source, page) != render_page(renderer, target, offset):
                raise RegistryError(
                    f"规范 PDF 页面与原件不一致：{item['relativePath']} 第 {offset} 页"
                )
        if pdf_info(source)[0] != source_sha or pdf_info(target)[0] != target_sha:
            raise RegistryError("核验期间 PDF 发生变化")
        verified.append(
            {
                "relativePath": item["relativePath"],
                "pages": target_pages,
                "sha256": target_sha,
                "pixelsEqual": True,
            }
        )
    return {
        "schemaVersion": "PdfSplitVerificationV1",
        "files": verified,
        "fileCount": len(verified),
        "pageCount": sum(item["pages"] for item in verified),
        "allPagesEqual": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--work-root")
    parser.add_argument("--workspace-config")
    parser.add_argument("--renderer", default=shutil.which("pdftoppm"))
    parser.set_defaults(workspace_required=True)
    args = parser.parse_args()
    work = Path(args.work_dir).resolve()
    enforce_local_workspace_preflight(args, work)
    if not args.renderer or not Path(args.renderer).is_file():
        raise RegistryError("缺少 Poppler 的 pdftoppm 渲染入口")
    print(json.dumps(verify_split(work, args.renderer), ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (RegistryError, subprocess.TimeoutExpired) as error:
        raise SystemExit(f"PDF 拆分核验未通过：{error}") from None
