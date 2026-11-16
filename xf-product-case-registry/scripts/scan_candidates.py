"""扫描/文字层初查记录的识别候选（只读，本地）：RecognitionCandidatesV1。

选路按文档特征：每页文字层字符数 >=20 走 text-layer（只给编号、日期与产品表单元格及其引用，不跑 OCR），否则走 scan：
页方向按“锚词得分”选（0/270/90/180），按表格外框估计倾斜，横竖线切单元格；stage1 整页 OCR(psm4)，stage2 单元格裁剪重读(psm6 chi_sim+eng)，
**两阶段规范化后一致才标 accepted-candidate，否则 uncertain 并保留两种读数，不择一**。扫描件结果一律是候选，须人工确认，不当已核验值，不写飞书。
每个候选带页码、表格行列和 bbox（原 PDF 页面坐标，pt，已映射回旋转/倾斜前）；缓存键为文件 sha256 + 解析器版本；任一阶段异常落回 partial/blocked/unknown 并写原因。
依赖 numpy、Pillow、pdfplumber、pypdf、pdftoppm、tesseract(chi_sim)，在受管文档环境里运行（经 document_tools exec）。
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pdfplumber
from PIL import Image

SCHEMA = "RecognitionCandidatesV1"
TEXT_TARGETS = ("documentNo", "inspectionDate", "products")  # 电子记录文字层应能读到的目标结构
VERSION = "scan-candidates-v2.4"
ANCHORS = ["监督检查", "检查记录", "产品名称", "标称生产者", "所在部位", "检查基数", "检查数量", "市场准入", "现场检查", "被检查单位", "消防救援"]
ROT_ORDER = (0, 270, 90, 180)
DPI = 400
HEADER_KEYS = {"name": ("产品名称", "规格型号"), "producer": ("生产者", "标称"), "location": ("部位", "所在"), "base": ("基数",), "qty": ("数量",), "market": ("市场准入",), "quality": ("现场检查", "质量")}

norm = lambda s: re.sub(r"[\s\|\[\]\(\)〔〕（）【】:：,，。.、_\-;；'\"`‘’“”<>]", "", s or "")


def sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tess(img: Path, psm: int, lang: str, tsv: bool = False) -> str:
    """默认 OCR：本机 tesseract。测试可用 set_engine 注入假实现。"""
    return _ENGINE["ocr"](img, psm, lang, tsv)


def _tesseract(img: Path, psm: int, lang: str, tsv: bool = False) -> str:
    cmd = ["tesseract", str(img), "stdout", "-l", lang, "--psm", str(psm)] + (["tsv"] if tsv else [])
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise RuntimeError(f"tesseract-failed:{r.stderr[-200:]}")
    return r.stdout


def _pdftoppm(pdf: Path, workdir: Path) -> list[Path]:
    subprocess.run(["pdftoppm", "-r", str(DPI), "-png", str(pdf), str(workdir / "pg")], check=True, capture_output=True, timeout=600)
    return sorted(workdir.glob("pg-*.png"))


_ENGINE: dict[str, Any] = {"ocr": _tesseract, "render": _pdftoppm}


def set_engine(ocr: Callable | None = None, render: Callable | None = None) -> None:
    """仅供测试：替换 OCR 与整页渲染。"""
    _ENGINE["ocr"] = ocr or _tesseract
    _ENGINE["render"] = render or _pdftoppm


def tsv_rows(text: str):
    rows = list(csv.DictReader(io.StringIO(text), delimiter="\t", quoting=csv.QUOTE_NONE))
    out = []
    for r in rows:
        try:
            if (r.get("text") or "").strip() and float(r["conf"]) >= 0:
                out.append({"t": r["text"], "l": int(r["left"]), "tp": int(r["top"]), "w": int(r["width"]), "h": int(r["height"]), "line": (r["block_num"], r["par_num"], r["line_num"])})
        except (ValueError, KeyError):
            pass
    return out


def back_to_page(box, rot, W, H):
    """旋转图(PIL rotate(rot, expand))坐标 → 原页 400dpi 像素坐标；box=(x0,y0,x1,y1)。W,H 为原页像素尺寸。"""
    x0, y0, x1, y1 = box
    pts = [(x0, y0), (x1, y0), (x0, y1), (x1, y1)]
    def one(x, y):
        if rot == 0: return x, y
        if rot == 270: return y, H - x        # 逆 CW90
        if rot == 90: return W - y, x         # 逆 CCW90
        return W - x, H - y
    q = [one(*p) for p in pts]
    xs, ys = [a for a, _ in q], [b for _, b in q]
    k = 72.0 / DPI
    return [round(min(xs) * k, 1), round(min(ys) * k, 1), round(max(xs) * k, 1), round(max(ys) * k, 1)]


def longest_run(col: np.ndarray) -> int:
    if not col.any():
        return 0
    d = np.diff(np.concatenate(([0], col.astype(np.int8), [0])))
    s, e = np.where(d == 1)[0], np.where(d == -1)[0]
    return int((e - s).max())


def cluster(idx, gap=3):
    out, cur = [], []
    for i in idx:
        if cur and i - cur[-1] > gap:
            out.append(cur); cur = []
        cur.append(i)
    if cur:
        out.append(cur)
    return [int(np.mean(c)) for c in out]


def deskew_angle(img: Image.Image, xr=None) -> float:
    """倾斜角：在 -2°..2° 步长 0.25° 内取行投影方差最大者（同值取绝对值小者），与已知答案无关。"""
    g = img.convert("L")
    if xr:   # v2.1：有竖线时只在表格外框的 x 范围内按横线锐度估计倾斜（避免手写/正文干扰）
        g = g.crop((xr[0], 0, xr[1], g.height))
        g.thumbnail((4000, 4000)); step = 0.1
    else:
        g.thumbnail((1000, 1000)); step = 0.25
    best, ba = -1.0, 0.0
    for a in np.arange(-2.0, 2.0001, step):
        arr = np.array(g.rotate(float(a), fillcolor=255)) < 150
        v = float(arr.sum(1).astype(np.float64).var())
        if abs(a) < 1e-9:
            v0 = v
        if v > best + 1e-9 or (abs(v - best) <= 1e-9 and abs(a) < abs(ba)):
            best, ba = v, float(a)
    # 无明显峰值（提升<10%）或落在搜索边界时不校正，避免文字页被误判
    if best <= 1.10 * v0 or abs(ba) >= 2.0:
        return 0.0
    return round(ba, 2)


def unskew_pt(x, y, a, cx, cy):
    """去偏后图(rotate(a))上的点 → 偏斜前(R)图上的点。"""
    import math
    t = math.radians(-a)
    return cx + (x - cx) * math.cos(t) + (y - cy) * math.sin(t), cy - (x - cx) * math.sin(t) + (y - cy) * math.cos(t)


def grid_lines(img: Image.Image, f=4):
    a = np.array(img.convert("L"))
    h4, w4 = a.shape[0] // f * f, a.shape[1] // f * f
    dark = a[:h4, :w4].reshape(h4 // f, f, w4 // f, f).min(axis=(1, 3)) < 150   # 块最小值：细线不被平均掉
    H, W = dark.shape
    vs = cluster([x for x in range(W) if longest_run(dark[:, x]) >= 0.10 * H])
    if len(vs) < 3:
        return [x * f for x in vs], []
    x0, x1 = vs[0], vs[-1]   # v2.1：横线按表格外框跨度内的暗像素占比>=50%判定（容忍断线），不是整页宽
    fr = dark[:, x0:x1 + 1].mean(1)
    hs = [y for y in range(H) if fr[y] >= 0.50]
    # 竖线底端：各竖线最长游程的末端中位数，用来补最后一行的下边界
    ends = []
    for x in vs:
        col = dark[:, x]; d = np.diff(np.concatenate(([0], col.astype(np.int8), [0]))); s_, e_ = np.where(d == 1)[0], np.where(d == -1)[0]
        ends.append(int(e_[(e_ - s_).argmax()]))
    lines = [y * f for y in cluster(hs)]
    return [x * f for x in vs], lines + ([int(np.median(ends)) * f] if lines and (int(np.median(ends)) * f - lines[-1]) > 60 else [])


def orient(page_img: Path, work: Path) -> tuple[int, dict]:
    base = Image.open(page_img).convert("L")
    scores = {}
    for rot in ROT_ORDER:
        im = base.rotate(rot, expand=True)
        im.thumbnail((2000, 2000))
        f = work / f"orient-{page_img.stem}-{rot}.png"; im.save(f)
        text = norm(tess(f, 4, "chi_sim"))
        scores[str(rot)] = sum(1 for a in ANCHORS if a in text)   # 键用字符串：JSON 往返后不变
    best = max(ROT_ORDER, key=lambda r: (scores[str(r)], -ROT_ORDER.index(r)))
    return best, scores


def split_name_spec(cell: str) -> tuple[str, str]:
    """“产品名称及规格型号”合并单元：名称是中文起头的部分，规格型号从第一个字母/数字起。"""
    cell = "".join((cell or "").split())
    m = re.search(r"[A-Za-z0-9]", cell)
    if not m or m.start() == 0:
        return cell, ""
    return cell[: m.start()], cell[m.start():]


def split_ns(text: str):
    n, s = split_name_spec(text or "")
    return norm(n or ""), norm(s or "")


def unbox(box, skew, cxy):
    if not skew or not box:
        return box
    q = [unskew_pt(x, y, skew, *cxy) for x, y in ((box[0], box[1]), (box[2], box[1]), (box[0], box[3]), (box[2], box[3]))]
    xs, ys = [a for a, _ in q], [b for _, b in q]
    return (min(xs), min(ys), max(xs), max(ys))


def cand(value, stage1, stage2, page, rot, W, H, box, table=None, status=None, reason=None):
    ok = bool(stage1) and norm(stage1) == norm(stage2)
    return {"value": value, "status": status or ("accepted-candidate" if ok else "uncertain"), "stage1": stage1, "stage2": stage2, "reason": reason or (None if ok else "stage1 与 stage2 读数不一致（可能手写或扫描质量不足），保留两种读数，不择一"),
            "citation": {"page": page, "rotationApplied": rot, "table": table, "bboxPt": back_to_page(box, rot, W, H) if box else None}}


def scan_route(path: Path, work: Path, inject=None) -> dict:
    out = {"route": "scan", "fields": {}, "pages": [], "flags": []}
    work.mkdir(parents=True, exist_ok=True)
    pages = _ENGINE["render"](path, work)
    if not pages:
        raise RuntimeError("render-empty")
    for pno, pimg in enumerate(pages, start=1):
        orig = Image.open(pimg); W, H = orig.size
        rot, scores = orient(pimg, work)
        R0 = orig.convert("L").rotate(rot, expand=True)
        vx0, _h0 = grid_lines(R0)
        skew = deskew_angle(R0, (vx0[0], vx0[-1]) if len(vx0) >= 3 else None)
        R = R0.rotate(skew, fillcolor=255) if skew else R0
        Rf = work / f"rot-{pno}.png"; R.save(Rf)
        cxy = (R.width / 2, R.height / 2)
        rows = tsv_rows(tess(Rf, 4, "chi_sim", tsv=True))
        lines = {}
        for r in rows:
            lines.setdefault(r["line"], []).append(r)
        ltext = {k: "".join(w["t"] for w in sorted(v, key=lambda w: w["l"])) for k, v in lines.items()}
        pinfo = {"page": pno, "rotationApplied": rot, "deskewDeg": skew, "orientScores": scores, "image": f"{W}x{H}"}
        out["pages"].append(pinfo)
        # 页面级字段：编号、日期（整页行 + 该行裁剪重读）
        for key, anchor, rx in (("documentNo", "编号", r"[\[〔【(]?(\d{4})[\]〕】)]?第([0-9?]{3,5})号"), ("inspectionDate", "监督检查时间", r"(\d{4})年0?(\d{1,2})月0?(\d{1,2})日")):
            for lk, lt in ltext.items():
                if anchor in lt:
                    ws = lines[lk]; box = (min(w["l"] for w in ws), min(w["tp"] for w in ws), max(w["l"] + w["w"] for w in ws), max(w["tp"] + w["h"] for w in ws))
                    cf = work / f"{key}-{pno}.png"; Image.open(Rf).crop((box[0] - 8, box[1] - 8, box[2] + 8, box[3] + 8)).save(cf)
                    t2 = tess(cf, 7, "chi_sim+eng")
                    m1, m2 = re.search(rx, norm(lt)), re.search(rx, norm(t2))
                    def val(m):
                        if not m: return None
                        g = m.groups()
                        if key == "documentNo": return f"〔{g[0]}〕第{g[1]}号" if "?" not in g[1] else None
                        return f"{g[0]}-{int(g[1]):02d}-{int(g[2]):02d}"
                    v1, v2 = val(m1), val(m2)
                    if v1 and v1 == v2 and key not in out["fields"]:
                        out["fields"][key] = cand(v1, v1, v2, pno, rot, W, H, unbox(box, skew, cxy), status="accepted-candidate")
                    elif key not in out["fields"]:
                        out["fields"][key] = {"value": v1 or v2, "status": "uncertain", "stage1": ltext[lk], "stage2": t2.strip(), "reason": "整页读数与该行裁剪重读不一致或有无法辨认字符",
                                              "citation": {"page": pno, "rotationApplied": rot, "table": None, "bboxPt": back_to_page(unbox(box, skew, cxy), rot, W, H)}}
                    break
        # 表格：定位表头行并切格
        hk = ("产品名称", "标称生产者", "所在部位", "检查基数", "市场准入", "现场检查")
        hdr = [w for lk, ws in lines.items() if sum(1 for k in hk if k in ltext[lk]) >= 2 for w in ws]
        if not hdr:
            if any(a in norm("".join(ltext.values())) for a in ("产品名称", "标称生产者")):
                out["flags"].append({"page": pno, "flag": "header-line-not-found", "action": "表头行未按整行识别：产品字段缺项，不猜"})
            else:
                out["flags"].append({"page": pno, "flag": "no-product-table-found", "action": "本页没有识别到产品表表头(可能不是产品表页)；产品字段缺项，不猜"})
            continue
        vx, hy = grid_lines(R)
        hy_t = min(r["tp"] for r in hdr); hy_b = max(r["tp"] + r["h"] for r in hdr)
        rows_y = [y for y in hy]
        # 表头行：包含表头词的横线间区间
        band = [(a, b) for a, b in zip(rows_y, rows_y[1:]) if a <= hy_t + 20 and b >= hy_b - 20]
        if not band or len(vx) < 3:
            out["flags"].append({"page": pno, "flag": "table-grid-not-found", "action": "表格线未识别：产品字段缺项，不猜"})
            continue
        h_top, h_bot = band[0]
        data_bands = [(a, b) for a, b in zip(rows_y, rows_y[1:]) if a >= h_bot - 10]
        cols = list(zip(vx, vx[1:]))
        out["pages"][-1]["gridDebug"] = {"vx": vx, "hy": rows_y, "headerBand": [h_top, h_bot]}
        def words_in(box):
            return [w for w in rows if box[0] <= w["l"] + w["w"] / 2 <= box[2] and box[1] <= w["tp"] + w["h"] / 2 <= box[3]]
        def ordered(ws):
            """按行聚类（行内按 x 排）：y 中心与当前行均值之差 > 0.6×字高中位数就换行。"""
            if not ws:
                return ""
            mh = float(np.median([w["h"] for w in ws])); ws = sorted(ws, key=lambda w: w["tp"] + w["h"] / 2)
            lines_, cur, cy = [], [ws[0]], ws[0]["tp"] + ws[0]["h"] / 2
            for w in ws[1:]:
                c = w["tp"] + w["h"] / 2
                if c - cy > 0.6 * mh:
                    lines_.append(cur); cur = [w]; cy = c
                else:
                    cur.append(w); cy = (cy * len(cur) + c) / (len(cur) + 1)
            lines_.append(cur)
            return "".join(w["t"] for ln in lines_ for w in sorted(ln, key=lambda w: w["l"]))
        colmap = {}
        sets = {"name": "产品名称及规格型号", "producer": "标称生产者", "location": "产品所在部位", "base": "检查基数", "qty": "检查数量", "market": "市场准入检查情况", "quality": "产品质量现场检查情况"}
        scores = []
        for ci, (xa, xb) in enumerate(cols):
            chars = set("".join(w["t"] for w in words_in((xa, h_top, xb, h_bot))))   # 竖排表头的字序不可靠，用字符集合覆盖度
            try:   # v2.2：再加上该表头单元格单独裁剪重读的字符（整页读数在表头处不稳定）
                hc = R.crop((xa + 4, h_top + 4, xb - 4, h_bot - 4)); hc = hc.resize((int(hc.width * 1.5), int(hc.height * 1.5))); hf = work / f"hdr-{pno}-{ci}.png"; hc.save(hf)
                chars |= set(tess(hf, 6, "chi_sim"))
            except Exception:  # noqa: BLE001
                pass
            for fld, st in sets.items():
                sc = len(chars & set(st))
                if sc >= 3:
                    scores.append((sc, -ci, fld, ci))
        used_c, used_f = set(), set()
        for sc, _, fld, ci in sorted(scores, reverse=True):
            if ci not in used_c and fld not in used_f:
                colmap[fld] = ci; used_c.add(ci); used_f.add(fld)
        need = ("name", "producer", "location")
        if not all(n in colmap for n in need):
            out["flags"].append({"page": pno, "flag": "header-not-recognized", "found": list(colmap), "action": "表头列语义未全识别：产品字段缺项，不按位置提取"})
            continue
        prod_rows = []
        for ri, (ya, yb) in enumerate(data_bands):
            first = ordered(words_in((cols[0][0], ya, cols[0][1], yb)))
            if not first or norm(first).startswith("备注"):
                continue
            cells = {}
            for fld, ci in colmap.items():
                xa, xb = cols[ci]; box = (xa + 4, ya + 4, xb - 4, yb - 4)
                s1 = ordered(words_in(box))
                cf = work / f"cell-{pno}-{ri}-{fld}.png"
                c = R.crop(box); c = c.resize((int(c.width * 1.5), int(c.height * 1.5))); c.save(cf)
                try:
                    if inject == "stage2-error":
                        raise RuntimeError("injected-stage2-failure")
                    s2 = tess(cf, 6, "chi_sim+eng").replace("\n", "")
                except Exception as e:  # noqa: BLE001
                    s2 = None; out["flags"].append({"page": pno, "flag": "stage2-failed", "cell": [ri, fld], "detail": str(e)[:80], "action": "该格只保留 stage1 读数，标 uncertain"})
                cells[fld] = (s1, s2, box, ci)
            prod_rows.append((ri, cells))
        for ri, cells in prod_rows:
            tbl = lambda fld: {"index": 1, "row": ri + 1, "col": cells[fld][3] + 1}
            n1, sp1 = split_ns(cells["name"][0]); n2, sp2 = split_ns(cells["name"][1] or "")
            def put(key, v1, v2, fld, status_override=None):
                s1, s2, box, ci = cells[fld]
                c = cand(v1, v1, v2, pno, rot, W, H, unbox(box, skew, cxy), table=tbl(fld))
                if s2 is None: c["status"], c["reason"] = "uncertain", "stage2 失败"
                out["fields"].setdefault(key, c)
            put("productName", n1, n2, "name"); put("modelSpec", sp1, sp2, "name")
            put("nominalProducer", norm(cells["producer"][0]), norm(cells["producer"][1] or ""), "producer")
            put("location", norm(cells["location"][0]), norm(cells["location"][1] or ""), "location")
            if "quality" in cells:
                q1 = "未发现不合格现象" if "未发现不合格现象" in norm(cells["quality"][0]) else norm(cells["quality"][0])
                q2 = "未发现不合格现象" if "未发现不合格现象" in norm(cells["quality"][1] or "") else norm(cells["quality"][1] or "")
                put("fieldResultSentence", q1, q2, "quality")
            break  # 本次只取第一个产品行（单产品样本）；多产品行不在本版范围
    return out


def text_route(path: Path) -> dict:
    """有文字层：不跑 OCR，只给编号、日期与产品表单元格（带页码、表格行列、bbox）。产品行的拆分与校验仍走现有电子记录解析。"""
    fields: dict[str, Any] = {}
    with pdfplumber.open(path) as pdf:
        for pno, pg in enumerate(pdf.pages, start=1):
            for key, rx in (("documentNo", r"编号[：:]\s*〔(\d{4})〕第\s*(\d+)\s*号"), ("inspectionDate", r"监督检查时间[：:]\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")):
                if key in fields:
                    continue
                hit = pg.search(rx, regex=True)
                if hit:
                    h = hit[0]
                    g = re.search(rx, h["text"])
                    value = f"〔{g.group(1)}〕第{int(g.group(2)):04d}号" if key == "documentNo" else f"{g.group(1)}-{int(g.group(2)):02d}-{int(g.group(3)):02d}"
                    fields[key] = {"value": value, "status": "text-layer", "citation": {"page": pno, "table": None, "bboxPt": [round(h["x0"], 1), round(h["top"], 1), round(h["x1"], 1), round(h["bottom"], 1)]}}
            if "products" in fields:
                continue
            cells = []
            for ti, t in enumerate(pg.find_tables(), start=1):
                ex = t.extract()
                for ri, row in enumerate(ex):
                    if len(row) >= 7 and (row[0] or "").strip() and "产品名称" not in norm(row[0]) and not norm(row[0]).startswith("备注"):
                        for fld, ci in (("name", 0), ("producer", 1), ("location", 2)):
                            bb = t.rows[ri].cells[ci]
                            cells.append({"field": fld, "text": norm(row[ci]), "citation": {"page": pno, "table": {"index": ti, "row": ri + 1, "col": ci + 1}, "bboxPt": [round(v, 1) for v in bb]}})
            if cells:
                fields["products"] = {"status": "text-layer", "cells": cells}
    return {"route": "text-layer", "fields": fields, "note": "文字层记录：数值来自 PDF 文字层本身，仍按现有电子记录解析与本地核验确认"}


def recognize(path: Path, work: Path, inject: str | None = None) -> dict:
    """解析一份 PDF；任何异常转成状态与原因，不向外抛。"""
    out: dict[str, Any] = {"file": path.name, "sha256": sha(path) if path.is_file() else None, "parser": VERSION}
    try:
        try:
            import pypdf
            if pypdf.PdfReader(str(path)).is_encrypted:
                return {**out, "status": "blocked", "reason": "password-required", "detail": "is_encrypted；不索要或猜测密码"}
        except Exception:  # noqa: BLE001 - 交给下面的 pdfplumber 判定
            pass
        try:
            pdf = pdfplumber.open(path)
        except Exception as e:  # noqa: BLE001
            nm = type(e).__name__
            return {**out, "status": "blocked", "reason": "password-required" if ("Password" in nm or "Encrypt" in nm) else "file-damaged", "detail": nm}
        with pdf:
            chars = [len((p.extract_text() or "").strip()) for p in pdf.pages]
        text_diag = None
        if chars and all(c >= 20 for c in chars):
            t = text_route(path)
            found = [k for k in TEXT_TARGETS if k in t["fields"]]
            if len(found) == len(TEXT_TARGETS):
                return {**out, **t, "status": "ok"}
            if found:
                missing = [k for k in TEXT_TARGETS if k not in found]
                return {**out, **t, "status": "partial", "reason": "text-layer-incomplete", "detail": f"文字层只含 {found}，缺 {missing}",
                        "needsReview": sorted(set(missing + ["all-candidates-need-human-confirmation"]))}
            # 有文字层但读不到任何目标结构（编号、检查时间、产品表）：文字层不可信（常见于扫描图带劣质识别文字），回退本地扫描路线
            text_diag = {"usable": False, "reason": "no-target-structure-found", "charsPerPage": chars, "targets": list(TEXT_TARGETS)}
        try:
            s = scan_route(path, work, inject=inject)
        except Exception as e:  # noqa: BLE001
            if text_diag is None:
                raise
            return {**out, "status": "unknown", "reason": "scan-fallback-failed", "detail": f"{type(e).__name__}:{str(e)[:120]}", "textLayer": text_diag,
                    "action": "文字层不可用且扫描回退失败；保留诊断，先回读再决定，不盲目重跑"}
        if text_diag is not None:
            s["flags"].append({"flag": "text-layer-unusable-fell-back-to-scan", "detail": text_diag["reason"], "action": "回退发生不等于识别成功，结果仍是候选"})
            s["textLayer"] = text_diag
        unc = sorted(k for k, v in s["fields"].items() if v["status"] != "accepted-candidate")
        res = {**out, **s, "status": "partial", "needsReview": sorted(set(unc + ["all-candidates-need-human-confirmation"])),
               "acceptedCandidates": sorted(k for k, v in s["fields"].items() if v["status"] == "accepted-candidate"),
               "note": "扫描件结果一律是候选；accepted-candidate 只表示两阶段读数一致，不等于已核验"}
        if not any(v.get("value") not in (None, "") for v in s["fields"].values()):
            res["reason"] = "scan-no-candidate-values"
            res["detail"] = "扫描路线没有读出任何字段值" + ("（文字层不可用后回退）" if text_diag else "")
        return res
    except Exception as e:  # noqa: BLE001
        return {**out, "status": "unknown", "reason": "stage-exception", "detail": f"{type(e).__name__}:{str(e)[:120]}", "action": "保留已有结果，先回读再决定，不盲目重跑"}


def recognize_cached(path: Path, cache_dir: Path, work: Path, version: str | None = None, inject: str | None = None) -> dict:
    """缓存键=文件 sha256 前 16 位 + 解析器版本；版本变化不使用旧缓存且旧文件保留；失败(unknown)不写缓存，新版本失败时指向旧版本缓存。"""
    global VERSION
    ver, old = version or VERSION, VERSION
    VERSION = ver
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        digest = sha(path)
        cache_file = cache_dir / f"{digest[:16]}-{ver}.json"
        if cache_file.exists():
            return {**json.loads(cache_file.read_text(encoding="utf-8")), "reused": True}
        previous = sorted(p.name for p in cache_dir.glob(f"{digest[:16]}-*.json"))
        started = time.time()
        result = recognize(path, work / f"{digest[:16]}-{ver}", inject=inject)
        result["schema"] = SCHEMA
        result["elapsedSec"] = round(time.time() - started, 2)
        if result["status"] == "unknown":
            if previous:
                result["previousVersionResult"] = previous[-1]
                result["note"] = "新版本解析失败：保留旧版本缓存文件，未覆盖"
        else:
            cache_file.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        return result
    finally:
        VERSION = old


def initial_record_path(manifest: dict[str, Any], generation_dir: Path) -> tuple[Path | None, str | None]:
    """清单里初查记录(电子或扫描)指向的 PDF：按清单摘要找回同一份文件。"""
    files = {item.get("clientRef"): item for item in manifest.get("files", []) if isinstance(item, dict)}
    ref = next((v.get("fileRef") for slot in manifest.get("documentSlots", []) if slot.get("slotCode") == "INITIAL_INSPECTION_RECORD" for v in slot.get("versions", [])), None)
    entry = files.get(ref)
    if entry is None:
        return None, "清单没有初查监督检查记录"
    want = str(entry.get("sha256") or "").removeprefix("sha256:")
    if not re.fullmatch(r"[0-9a-f]{64}", want):
        return None, "清单没有该记录的摘要"
    for path in sorted(generation_dir.rglob("*.pdf")):
        if sha(path) == want:
            return path, None
    return None, "找不到与清单摘要一致的初查记录"
