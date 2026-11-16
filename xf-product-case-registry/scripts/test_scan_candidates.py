"""Offline tests for the scan/text-layer recognition candidates (fake OCR engine, synthetic images and PDFs).

依赖 numpy/Pillow/pdfplumber/reportlab(受管文档环境)；缺依赖时整体跳过。本机可用：
PYTHONPATH=<文档环境 site-packages> <case-registry 环境>/bin/python -m pytest scripts/test_scan_candidates.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
Image = pytest.importorskip("PIL.Image")
ImageDraw = pytest.importorskip("PIL.ImageDraw")
pytest.importorskip("pdfplumber")
pytest.importorskip("pypdf")

import scripts.scan_candidates as sc  # noqa: E402

W, H = 1200, 900
VX = [100, 260, 420, 580, 640, 700, 900, 1100]          # 8 条竖线 → 7 列
HY = [100, 300, 500, 650]                                  # 表头行 100-300，数据行 300-500，备注行 500-650
COLS = ["name", "producer", "location", "base", "qty", "market", "quality"]
HEADERS = {"name": "产品名称及规格型号", "producer": "标称生产者", "location": "产品所在部位", "base": "检查基数", "qty": "检查数量", "market": "市场准入检查情况", "quality": "产品质量现场检查情况"}


def _table_png(path: Path) -> Path:
    im = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(im)
    for x in VX:
        d.line([(x, HY[0]), (x, HY[-1])], fill=0, width=3)
    for y in HY:
        d.line([(VX[0], y), (VX[-1], y)], fill=0, width=3)
    im.save(path)
    return path


def _pdf_from_png(png: Path, pdf: Path) -> Path:
    Image.open(png).convert("RGB").save(pdf)       # 图片 PDF：没有文字层，走 scan 路线
    return pdf


def _tsv(words):
    """words: (text, left, top, width, height, line_no)"""
    head = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
    rows = [f"5\t1\t1\t1\t{ln}\t{i}\t{l}\t{t}\t{w}\t{h}\t90\t{tx}" for i, (tx, l, t, w, h, ln) in enumerate(words, start=1)]
    return "\n".join([head, *rows]) + "\n"


class FakeEngine:
    """按文件名/参数返回预先写好的 OCR 文字；记录调用，便于断言。stage2 读数可按字段覆盖。"""

    def __init__(self, stage1=None, stage2=None, fail_stage2=False):
        self.stage1 = stage1 or {"name": "过滤式消防自救呼吸器TZL30", "producer": "广州正邦应急消防科技有限公司", "location": "微型消防站器材箱内", "quality": "未发现不合格现象"}
        self.stage2 = stage2 or dict(self.stage1)
        self.fail_stage2 = fail_stage2
        self.calls = []

    def render(self, pdf, work):
        png = work / "pg-1.png"
        _table_png(png)
        return [png]

    def ocr(self, img, psm, lang, tsv=False):
        self.calls.append((Path(img).name, psm, lang, tsv))
        name = Path(img).name
        if name.startswith("orient-"):
            return "监督检查记录 产品名称 标称生产者 所在部位 检查基数 市场准入 现场检查 被检查单位 消防救援局"
        if name.startswith("hdr-1-"):
            return HEADERS[COLS[int(name.split("-")[2].split(".")[0])]]
        if name.startswith("cell-1-"):
            if self.fail_stage2:
                raise RuntimeError("stage2 broke")
            return self.stage2[name.split("-")[3].split(".")[0]]
        if name.startswith(("documentNo-", "inspectionDate-")):
            return "编号：〔2026〕第0012号" if name.startswith("documentNo") else "监督检查时间：2026年05月19日"
        if tsv:      # 整页 stage1：表头行（一行里有多个表头词）、数据行文字（每个单元格一行，字序打乱的单字不会出现，保持整词）
            words = [("编号：〔2026〕第0012号", 700, 40, 300, 30, 1), ("监督检查时间：2026年05月19日", 100, 60, 400, 30, 2)]
            for i, col in enumerate(COLS):
                xa, xb = VX[i], VX[i + 1]
                words.append((HEADERS[col], xa + 10, 150, max(20, xb - xa - 20), 40, 3))
            for i, col in enumerate(COLS):
                if col in self.stage1:
                    xa, xb = VX[i], VX[i + 1]
                    words.append((self.stage1[col], xa + 10, 350, max(20, xb - xa - 20), 40, 10 + i))
            return _tsv(words)
        return ""


@pytest.fixture()
def engine(tmp_path):
    eng = FakeEngine()
    sc.set_engine(eng.ocr, eng.render)
    yield eng
    sc.set_engine()


def _scan_pdf(tmp_path: Path) -> Path:
    return _pdf_from_png(_table_png(tmp_path / "t.png"), tmp_path / "scan.pdf")


def test_back_to_page_inverts_every_rotation(tmp_path):
    """旋转 PIL.rotate(rot, expand=True) 后量到的方块位置，映射回原页坐标必须等于原位置（pt，400dpi）。"""
    w, h = 400, 300
    im = Image.new("L", (w, h), 255)
    ImageDraw.Draw(im).rectangle([60, 40, 99, 69], fill=0)          # 原页上的方块
    for rot in (0, 270, 90, 180):
        r = im.rotate(rot, expand=True)
        ys, xs = np.where(np.array(r) < 128)
        box = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
        got = sc.back_to_page(box, rot, w, h)
        k = 72.0 / sc.DPI
        want = [60 * k, 40 * k, 100 * k, 70 * k]
        assert all(abs(a - b) < 0.5 for a, b in zip(got, want)), (rot, got, want)


def test_two_stage_gate_accepts_agreeing_reads_and_keeps_both_reads_when_they_differ(tmp_path, engine):
    pdf = _scan_pdf(tmp_path)
    ok = sc.recognize(pdf, tmp_path / "w1")
    assert ok["route"] == "scan" and ok["status"] == "partial" and "all-candidates-need-human-confirmation" in ok["needsReview"]
    f = ok["fields"]
    assert f["productName"]["value"] == "过滤式消防自救呼吸器" and f["productName"]["status"] == "accepted-candidate"
    assert f["modelSpec"]["value"] == "TZL30" and f["nominalProducer"]["status"] == "accepted-candidate" and f["location"]["status"] == "accepted-candidate"
    assert f["fieldResultSentence"]["value"] == "未发现不合格现象"
    cit = f["productName"]["citation"]
    assert cit["page"] == 1 and cit["table"] == {"index": 1, "row": 1, "col": 1} and cit["bboxPt"][2] > cit["bboxPt"][0] and cit["rotationApplied"] == 0
    assert 0 <= cit["bboxPt"][0] and cit["bboxPt"][2] <= W * 72 / sc.DPI + 1             # 在页内
    engine.stage2["producer"] = "广州正邦应急消防科技有限公司X"                              # stage2 读数不同
    bad = sc.recognize(pdf, tmp_path / "w2")
    p = bad["fields"]["nominalProducer"]
    assert p["status"] == "uncertain" and p["stage1"] != p["stage2"] and "不择一" in p["reason"] and "nominalProducer" in bad["needsReview"]
    assert "nominalProducer" not in bad["acceptedCandidates"] and "productName" in bad["acceptedCandidates"]


def test_stage2_failure_keeps_stage1_reading_as_uncertain_and_does_not_raise(tmp_path, engine):
    engine.fail_stage2 = True
    result = sc.recognize(_scan_pdf(tmp_path), tmp_path / "w")
    assert result["status"] == "partial" and any(x["flag"] == "stage2-failed" for x in result["flags"])
    assert result["fields"]["productName"]["status"] == "uncertain" and result["fields"]["productName"]["stage1"]
    assert result["acceptedCandidates"] == [] or "productName" not in result["acceptedCandidates"]


def test_unreadable_header_leaves_product_fields_blank_with_a_reason(tmp_path, engine):
    engine.ocr_orig = engine.ocr
    original = engine.ocr

    def blank_headers(img, psm, lang, tsv=False):
        name = Path(img).name
        if name.startswith("hdr-1-"):
            return ""
        text = original(img, psm, lang, tsv)
        return "\n".join(line for line in text.splitlines() if not any(h in line for h in HEADERS.values())) if tsv and not name.startswith(("orient-",)) else text

    sc.set_engine(blank_headers, engine.render)
    result = sc.recognize(_scan_pdf(tmp_path), tmp_path / "w")
    assert "productName" not in result["fields"] and any(x["flag"] in ("header-not-recognized", "header-line-not-found", "no-product-table-found") for x in result["flags"])


def test_cache_key_is_digest_plus_parser_version_and_failures_are_not_cached(tmp_path, engine):
    pdf = _scan_pdf(tmp_path)
    cache = tmp_path / "cache"
    first = sc.recognize_cached(pdf, cache, tmp_path / "w")
    again = sc.recognize_cached(pdf, cache, tmp_path / "w")
    assert not first.get("reused") and again["reused"] is True and {k: v for k, v in again.items() if k not in ("reused", "elapsedSec")} == {k: v for k, v in first.items() if k != "elapsedSec"}
    assert len(engine.calls) > 0
    calls = len(engine.calls)
    sc.recognize_cached(pdf, cache, tmp_path / "w")
    assert len(engine.calls) == calls                                                     # 命中缓存不再 OCR
    other = sc.recognize_cached(pdf, cache, tmp_path / "w2", version="scan-candidates-vX")
    assert other["parser"] == "scan-candidates-vX" and not other.get("reused") and len(list(cache.glob("*.json"))) == 2
    def boom(*a, **k):
        raise RuntimeError("engine down")
    sc.set_engine(boom, engine.render)
    failed = sc.recognize_cached(pdf, cache, tmp_path / "w3", version="scan-candidates-vY")
    assert failed["status"] == "unknown" and failed["reason"] == "stage-exception" and failed["previousVersionResult"].endswith(".json")
    assert len(list(cache.glob("*.json"))) == 2                                           # 失败不写缓存，旧缓存仍在


def test_damaged_and_encrypted_files_are_blocked_with_reasons(tmp_path, engine):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.4 garbage")
    assert sc.recognize(bad, tmp_path / "w")["status"] == "blocked"
    pypdf = pytest.importorskip("pypdf")
    src = _scan_pdf(tmp_path)
    writer = pypdf.PdfWriter(clone_from=str(src))
    writer.encrypt("pw")
    enc = tmp_path / "enc.pdf"
    with enc.open("wb") as out:
        writer.write(out)
    result = sc.recognize(enc, tmp_path / "w2")
    assert result["status"] == "blocked" and result["reason"] == "password-required"


def test_text_layer_pdf_routes_without_ocr_and_cites_cells(tmp_path, engine):
    rl = pytest.importorskip("reportlab.platypus")
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    style = ParagraphStyle("s", fontName="STSong-Light", fontSize=11)
    header = ["产品名称及规格型号", "标称生产者", "产品所在部位", "检查基数", "检查数量", "市场准入检查情况", "产品质量现场检查情况"]
    row = ["手提式干粉灭火器MF/ABC4", "虚构消防器材有限公司", "一层", "5", "2", "未发现不合格现象", "未发现不合格现象"]
    pdf = tmp_path / "text.pdf"
    doc = rl.SimpleDocTemplate(str(pdf), pagesize=A4)
    table = rl.Table([header, row], style=[("GRID", (0, 0), (-1, -1), 0.5, colors.black), ("FONTNAME", (0, 0), (-1, -1), "STSong-Light")])
    doc.build([rl.Paragraph("消防产品监督检查记录 编号：〔2026〕第 0034 号 监督检查时间： 2026 年 05 月 18 日 以下为产品检查情况表，共一行产品记录，供测试使用的文字层内容。", style), table])
    result = sc.recognize(pdf, tmp_path / "w")
    assert result["route"] == "text-layer" and result["status"] == "ok" and not engine.calls
    assert result["fields"]["documentNo"]["value"] == "〔2026〕第0034号" and result["fields"]["inspectionDate"]["value"] == "2026-05-18"
    cells = {c["field"]: c for c in result["fields"]["products"]["cells"]}
    assert cells["name"]["citation"]["table"] == {"index": 1, "row": 2, "col": 1} and cells["producer"]["text"] == "虚构消防器材有限公司"
    assert cells["location"]["citation"]["page"] == 1 and len(cells["location"]["citation"]["bboxPt"]) == 4
