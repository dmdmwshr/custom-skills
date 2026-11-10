"""Faithful PNG/JPEG -> single-page PDF normalization with exact source association.

The image bytes are never re-drawn or re-sampled. JPEG data is embedded as-is (DCTDecode); PNG
data without an alpha channel keeps its original zlib/IDAT stream (FlateDecode + PNG predictor);
only PNG alpha is split into a separate soft mask (a lossless re-compression of the same
samples). Interlaced, tRNS, exotic or unreadable images are rejected instead of guessed.
A JPEG whose entropy-verified stream ends at its own EOI marker may carry vendor trailer bytes
(phone camera watermark/metadata blocks) after it: only the verified part is embedded, and the
trailer length and digest are recorded in the mapping. A trailer that starts another image is
rejected.

``verify_pdf`` re-reads the produced PDF structure and compares the embedded pixel data with the
source file, so a swapped image, a swapped PDF or tampered pixels are rejected. It does not depend
on zlib producing identical bytes across platforms.
"""

from __future__ import annotations

import hashlib
import re
import struct
import zlib
from typing import Any

CONVERTER = {"name": "xf-image-to-pdf", "version": "1"}
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_PNG_DEPTHS = {0: {1, 2, 4, 8, 16}, 2: {8, 16}, 3: {1, 2, 4, 8}, 4: {8, 16}, 6: {8, 16}}
_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
_ROTATE = {1: 0, 3: 180, 6: 90, 8: 270}


class ImageNormalizationError(RuntimeError):
    pass


def sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def sniff(data: bytes) -> str:
    if data.startswith(PNG_SIGNATURE):
        return "PNG"
    if data.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    raise ImageNormalizationError("不是受支持的 PNG/JPEG 图片")


# ----------------------------------------------------------------------------- PNG


def _png_chunks(data: bytes) -> list[tuple[bytes, bytes]]:
    position, chunks, ended = 8, [], False
    while position + 12 <= len(data):
        size = struct.unpack(">I", data[position : position + 4])[0]
        kind = data[position + 4 : position + 8]
        end = position + 8 + size
        if end + 4 > len(data):
            raise ImageNormalizationError("PNG 数据块被截断")
        body = data[position + 8 : end]
        if zlib.crc32(kind + body) != struct.unpack(">I", data[end : end + 4])[0]:
            raise ImageNormalizationError("PNG 数据块校验失败")
        chunks.append((kind, body))
        position = end + 4
        if kind == b"IEND":
            ended = True
            break
    if not ended or position != len(data):
        raise ImageNormalizationError("PNG 缺少 IEND 或末尾含有多余数据")
    return chunks


def decodable_png_size(data: bytes) -> tuple[int, int]:
    """Lenient proof that a PNG really decodes (any colour type/interlace); returns (w, h)."""
    chunks = _png_chunks(data)
    if not chunks or chunks[0][0] != b"IHDR" or len(chunks[0][1]) != 13:
        raise ImageNormalizationError("PNG 缺少合法 IHDR")
    width, height, depth, color, _c, _f, interlace = struct.unpack(">IIBBBBB", chunks[0][1])
    if width < 1 or height < 1 or color not in _PNG_DEPTHS or depth not in _PNG_DEPTHS[color]:
        raise ImageNormalizationError("PNG 尺寸、颜色类型或位深不合法")
    idat = b"".join(body for kind, body in chunks if kind == b"IDAT")
    if not idat:
        raise ImageNormalizationError("PNG 缺少像素数据")
    if interlace == 0:
        _check_png_pixels(idat, width, height, depth, color)
    else:
        decoder = zlib.decompressobj()
        try:
            decoder.decompress(idat)
            decoder.flush()
        except zlib.error as error:
            raise ImageNormalizationError("PNG 像素数据无法完整解码（IDAT 损坏）") from error
        if not decoder.eof or decoder.unused_data:
            raise ImageNormalizationError("PNG 像素数据压缩流不完整或含多余数据")
    return width, height


def inspect_png(data: bytes) -> dict[str, Any]:
    chunks = _png_chunks(data)
    if not chunks or chunks[0][0] != b"IHDR" or len(chunks[0][1]) != 13:
        raise ImageNormalizationError("PNG 缺少合法 IHDR")
    width, height, depth, color, compression, filtering, interlace = struct.unpack(
        ">IIBBBBB", chunks[0][1]
    )
    if color not in _PNG_DEPTHS or depth not in _PNG_DEPTHS[color]:
        raise ImageNormalizationError("PNG 颜色类型或位深不受支持")
    if compression != 0 or filtering != 0:
        raise ImageNormalizationError("PNG 压缩或过滤方式不受支持")
    if interlace != 0:
        raise ImageNormalizationError("隔行扫描 PNG 不受支持，不做猜测转换")
    if width < 1 or height < 1:
        raise ImageNormalizationError("PNG 像素尺寸不合法")
    kinds = [kind for kind, _ in chunks]
    if b"tRNS" in kinds:
        raise ImageNormalizationError("带透明色 tRNS 的 PNG 不受支持，不做猜测转换")
    idat = b"".join(body for kind, body in chunks if kind == b"IDAT")
    if not idat:
        raise ImageNormalizationError("PNG 缺少像素数据")
    palette = next((body for kind, body in chunks if kind == b"PLTE"), None)
    if color == 3 and (palette is None or len(palette) % 3 or not 3 <= len(palette) <= 768):
        raise ImageNormalizationError("索引色 PNG 缺少合法调色板")
    _check_png_pixels(idat, width, height, depth, color)
    dpi = None
    for kind, body in chunks:
        if kind == b"pHYs":
            if len(body) != 9:
                raise ImageNormalizationError("PNG pHYs 数据块长度不合法")
            x, y = struct.unpack(">II", body[:8])
            if x != y:
                raise ImageNormalizationError("PNG 像素非等距（pHYs 横纵密度不同），不做拉伸猜测")
            if body[8] == 1 and x:
                value = x * 0.0254
                dpi = value if 36 <= value <= 1200 else None
    return {
        "format": "PNG",
        "width": width,
        "height": height,
        "bitDepth": depth,
        "colorType": color,
        "idat": idat,
        "palette": palette,
        "dpi": dpi,
        "ancillaryChunks": sorted(
            {kind.decode("latin-1") for kind in kinds} - {"IHDR", "IDAT", "IEND", "PLTE"}
        ),
    }


def _check_png_pixels(idat: bytes, width: int, height: int, depth: int, color: int) -> None:
    """The image data must fully decode to exactly height scanlines of valid filter type."""
    stride = (width * _CHANNELS[color] * depth + 7) // 8
    expected = height * (stride + 1)
    decoder = zlib.decompressobj()
    try:
        raw = decoder.decompress(idat, expected + 1)
        raw += decoder.flush()
    except zlib.error as error:
        raise ImageNormalizationError("PNG 像素数据无法完整解码（IDAT 损坏）") from error
    if not decoder.eof or decoder.unused_data:
        raise ImageNormalizationError("PNG 像素数据压缩流不完整或含多余数据")
    if len(raw) != expected:
        raise ImageNormalizationError("PNG 扫描行数据长度与宽高/颜色/位深不符（扫描行不足或多余）")
    if any(raw[row * (stride + 1)] > 4 for row in range(height)):
        raise ImageNormalizationError("PNG 扫描行过滤类型不合法")


def _unfilter(raw: bytes, width: int, height: int, bpp: int, stride: int) -> bytes:
    if len(raw) != height * (stride + 1):
        raise ImageNormalizationError("PNG 像素数据长度与尺寸不符")
    out = bytearray(height * stride)
    previous = bytearray(stride)
    for row in range(height):
        base = row * (stride + 1)
        kind = raw[base]
        line = bytearray(raw[base + 1 : base + 1 + stride])
        if kind == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 255
        elif kind == 2:
            for i in range(stride):
                line[i] = (line[i] + previous[i]) & 255
        elif kind == 3:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + previous[i]) >> 1)) & 255
        elif kind == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b = previous[i]
                c = previous[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                predictor = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                line[i] = (line[i] + predictor) & 255
        elif kind != 0:
            raise ImageNormalizationError("PNG 行过滤类型不合法")
        out[row * stride : (row + 1) * stride] = line
        previous = line
    return bytes(out)


def _split_alpha(info: dict[str, Any]) -> tuple[bytes, bytes]:
    """Decode an alpha PNG into separate colour and alpha sample planes (lossless)."""
    color, depth = info["colorType"], info["bitDepth"]
    channels = _CHANNELS[color]
    sample = depth // 8
    bpp = channels * sample
    stride = info["width"] * bpp
    try:
        raw = zlib.decompress(info["idat"])
    except zlib.error as error:
        raise ImageNormalizationError("PNG 像素数据无法解压") from error
    pixels = _unfilter(raw, info["width"], info["height"], bpp, stride)
    color_bytes = bytearray()
    alpha_bytes = bytearray()
    color_width = (channels - 1) * sample
    for offset in range(0, len(pixels), bpp):
        color_bytes += pixels[offset : offset + color_width]
        alpha_bytes += pixels[offset + color_width : offset + bpp]
    return bytes(color_bytes), bytes(alpha_bytes)


# ---------------------------------------------------------------------------- JPEG


def inspect_jpeg(data: bytes) -> dict[str, Any]:
    if not data.startswith(b"\xff\xd8") or b"\xff\xd9" not in data:
        raise ImageNormalizationError("JPEG 缺少结束标记，疑似截断")
    position, sof, adobe, dpi, orientation = 2, None, None, None, 1
    while position + 4 <= len(data):
        if data[position] != 0xFF:
            raise ImageNormalizationError("JPEG 标记结构不合法")
        marker = data[position + 1]
        if marker == 0xFF:
            position += 1
            continue
        if marker in {0xD8, 0x01} or 0xD0 <= marker <= 0xD7:
            position += 2
            continue
        if marker == 0xDA:
            break
        size = struct.unpack(">H", data[position + 2 : position + 4])[0]
        body = data[position + 4 : position + 2 + size]
        if marker == 0xC2:
            raise ImageNormalizationError("渐进式 JPEG 的熵数据无法本地完整验证，明确不支持")
        if marker in {0xC0, 0xC1} and sof is None:
            sof = body
        elif 0xC3 <= marker <= 0xCF and marker not in {0xC4, 0xC8, 0xCC}:
            raise ImageNormalizationError("该 JPEG 编码方式（无损/算术/分层）不受支持")
        elif marker == 0xE0 and body[:5] == b"JFIF\0" and len(body) >= 12:
            x, y = struct.unpack(">HH", body[8:12])
            if x != y:
                raise ImageNormalizationError("JPEG 像素非等距（JFIF 横纵密度不同），不做拉伸猜测")
            if body[7] in {1, 2} and x:
                value = x if body[7] == 1 else x * 2.54
                dpi = value if 36 <= value <= 1200 else None
        elif marker == 0xEE and body[:5] == b"Adobe":
            adobe = body[11] if len(body) >= 12 else 0
        elif marker == 0xE1 and body[:6] == b"Exif\0\0":
            orientation = _exif_orientation(body[6:]) or 1
        position += 2 + size
    if sof is None or len(sof) < 6:
        raise ImageNormalizationError("JPEG 缺少帧头")
    precision, height, width, components = struct.unpack(">BHHB", sof[:6])
    if components == 4:
        raise ImageNormalizationError("CMYK/YCCK JPEG 的色彩转换未经验证，明确不支持")
    if precision != 8 or components not in {1, 3} or width < 1 or height < 1:
        raise ImageNormalizationError("JPEG 位深或分量数不受支持")
    if orientation not in _ROTATE:
        raise ImageNormalizationError("JPEG EXIF 方向为镜像类，不做猜测转换")
    end = _check_jpeg_scans(data)
    trailing = data[end:]
    if trailing.startswith(b"\xff\xd8"):
        raise ImageNormalizationError("JPEG 结束标记之后还有另一幅图像数据，不做猜测")
    return {
        "end": end,
        "trailingBytes": len(trailing),
        "trailingSha256": sha256(trailing) if trailing else None,
        "format": "JPEG",
        "width": width,
        "height": height,
        "components": components,
        "adobeTransform": adobe,
        "dpi": dpi,
        "orientation": orientation,
    }


_JPEG_MARKER_END = re.compile(rb"\xff+[^\x00\xd0-\xd7\xff]")
_JPEG_RESTART = re.compile(rb"\xff+([\xd0-\xd7])")


def _ceil_div(value: int, divisor: int) -> int:
    return -(-value // divisor)


def _huffman_lookup(counts: bytes, symbols: bytes) -> list[int]:
    """16-bit prefix table: entry = (code length << 8) | symbol, 0 for an unused prefix."""
    table = [0] * 65536
    code = index = 0
    for length in range(1, 17):
        for _ in range(counts[length - 1]):
            if index >= len(symbols) or code + 1 > (1 << length):
                raise ImageNormalizationError("JPEG 霍夫曼表不合法")
            low = code << (16 - length)
            high = (code + 1) << (16 - length)
            table[low:high] = [(length << 8) | symbols[index]] * (high - low)
            index += 1
            code += 1
        code <<= 1
    if index != len(symbols):
        raise ImageNormalizationError("JPEG 霍夫曼表不合法")
    return table


def _decode_jpeg_segment(
    buf: bytes, mcus: int, blocks: list[int], dc: list[list[int]], ac: list[list[int]]
) -> None:
    """Consume one restart segment's entropy-coded bits; any invalid code or overrun raises."""
    acc = nbits = pos = 0
    end = len(buf)
    for _ in range(mcus):
        for slot in blocks:
            while nbits < 16:
                acc = ((acc & ((1 << nbits) - 1)) << 8) | (buf[pos] if pos < end else 0)
                pos += 1
                nbits += 8
            entry = dc[slot][(acc >> (nbits - 16)) & 0xFFFF]
            size = entry & 255
            if not entry or size > 11:
                raise ImageNormalizationError("JPEG 扫描数据损坏（DC 编码无效）")
            nbits -= entry >> 8
            while nbits < 16:
                acc = ((acc & ((1 << nbits) - 1)) << 8) | (buf[pos] if pos < end else 0)
                pos += 1
                nbits += 8
            nbits -= size
            k = 1
            while k < 64:
                while nbits < 16:
                    acc = ((acc & ((1 << nbits) - 1)) << 8) | (buf[pos] if pos < end else 0)
                    pos += 1
                    nbits += 8
                entry = ac[slot][(acc >> (nbits - 16)) & 0xFFFF]
                if not entry:
                    raise ImageNormalizationError("JPEG 扫描数据损坏（AC 编码无效）")
                nbits -= entry >> 8
                size = entry & 15
                run = (entry >> 4) & 15
                if size == 0:
                    if run != 15:
                        break
                    k += 16
                    continue
                k += run + 1
                if k > 64 or size > 10:
                    raise ImageNormalizationError("JPEG 扫描数据损坏（系数越界）")
                while nbits < 16:
                    acc = ((acc & ((1 << nbits) - 1)) << 8) | (buf[pos] if pos < end else 0)
                    pos += 1
                    nbits += 8
                nbits -= size
            if k > 64:
                raise ImageNormalizationError("JPEG 扫描数据损坏（系数越界）")
    consumed = pos * 8 - nbits
    if consumed > end * 8:
        raise ImageNormalizationError("JPEG 扫描数据不足，疑似截断或损坏")
    if end * 8 - consumed >= 16:
        raise ImageNormalizationError("JPEG 扫描数据与帧尺寸不符，含多余数据，疑似损坏")


def _check_jpeg_scans(data: bytes) -> int:
    """Entropy-decode every baseline scan (no IDCT): the whole file must be consistently coded.

    Header markers alone cannot show a damaged scan stream, so each Huffman-coded block is read
    and the bit budget, restart markers, MCU counts and component coverage are checked.
    """
    position, frame, interval = 2, None, 0
    quant: set[int] = set()
    dc_tables: dict[int, list[int]] = {}
    ac_tables: dict[int, list[int]] = {}
    covered: set[int] = set()
    done = False
    try:
        while position + 2 <= len(data) and not done:
            if data[position] != 0xFF:
                raise ImageNormalizationError("JPEG 标记结构不合法")
            marker = data[position + 1]
            if marker == 0xFF:
                position += 1
                continue
            if marker == 0xD9:
                done = True
                position += 2
                continue
            if marker in {0xD8, 0x01} or 0xD0 <= marker <= 0xD7:
                position += 2
                continue
            size = struct.unpack(">H", data[position + 2 : position + 4])[0]
            body = data[position + 4 : position + 2 + size]
            if len(body) != size - 2:
                raise ImageNormalizationError("JPEG 段长度超出文件")
            position += 2 + size
            if marker == 0xDB:
                cursor = 0
                while cursor < len(body):
                    precision, table = body[cursor] >> 4, body[cursor] & 15
                    cursor += 1 + 64 * (2 if precision else 1)
                    if cursor > len(body):
                        raise ImageNormalizationError("JPEG 量化表不完整")
                    quant.add(table)
            elif marker == 0xC4:
                cursor = 0
                while cursor < len(body):
                    kind, table = body[cursor] >> 4, body[cursor] & 15
                    counts = body[cursor + 1 : cursor + 17]
                    total = sum(counts)
                    symbols = body[cursor + 17 : cursor + 17 + total]
                    if len(counts) != 16 or len(symbols) != total or kind > 1 or table > 3:
                        raise ImageNormalizationError("JPEG 霍夫曼表不完整")
                    (ac_tables if kind else dc_tables)[table] = _huffman_lookup(counts, symbols)
                    cursor += 17 + total
            elif marker == 0xDD:
                interval = struct.unpack(">H", body[:2])[0]
            elif marker in {0xC0, 0xC1}:
                height, width, count = struct.unpack(">HHB", body[1:6])
                comps = [
                    (body[6 + 3 * i], body[7 + 3 * i] >> 4, body[7 + 3 * i] & 15, body[8 + 3 * i])
                    for i in range(count)
                ]
                if any(not h or not v or h > 4 or v > 4 for _c, h, v, _q in comps):
                    raise ImageNormalizationError("JPEG 采样因子不合法")
                frame = (width, height, comps)
            elif marker == 0xDA:
                if frame is None:
                    raise ImageNormalizationError("JPEG 扫描出现在帧头之前")
                position = _check_one_jpeg_scan(
                    data, position, body, frame, quant, dc_tables, ac_tables, interval, covered
                )
    except (struct.error, IndexError) as error:
        raise ImageNormalizationError("JPEG 结构不完整或损坏") from error
    if not done or frame is None or covered != {c[0] for c in frame[2]}:
        raise ImageNormalizationError("JPEG 缺少结束标记或有分量未被扫描覆盖")
    return position


def _check_one_jpeg_scan(
    data: bytes,
    position: int,
    header: bytes,
    frame: tuple[int, int, list[tuple[int, int, int, int]]],
    quant: set[int],
    dc_tables: dict[int, list[int]],
    ac_tables: dict[int, list[int]],
    interval: int,
    covered: set[int],
) -> int:
    width, height, comps = frame
    count = header[0]
    if count < 1 or len(header) != 1 + 2 * count + 3:
        raise ImageNormalizationError("JPEG 扫描头不合法")
    spectral = header[1 + 2 * count :]
    if spectral != b"\x00\x3f\x00":
        raise ImageNormalizationError("JPEG 仅支持顺序（baseline）扫描")
    by_id = {c[0]: c for c in comps}
    hmax = max(c[1] for c in comps)
    vmax = max(c[2] for c in comps)
    blocks: list[int] = []
    dc: list[list[int]] = []
    ac: list[list[int]] = []
    chosen = []
    for index in range(count):
        cid, tables = header[1 + 2 * index], header[2 + 2 * index]
        comp = by_id.get(cid)
        if comp is None or cid in covered or comp[3] not in quant:
            raise ImageNormalizationError("JPEG 扫描分量或量化表不合法")
        if (tables >> 4) not in dc_tables or (tables & 15) not in ac_tables:
            raise ImageNormalizationError("JPEG 扫描引用了未定义的霍夫曼表")
        dc.append(dc_tables[tables >> 4])
        ac.append(ac_tables[tables & 15])
        chosen.append(comp)
    if count == 1:
        comp = chosen[0]
        across = _ceil_div(_ceil_div(width * comp[1], hmax), 8)
        down = _ceil_div(_ceil_div(height * comp[2], vmax), 8)
        mcus, blocks = across * down, [0]
    else:
        mcus = _ceil_div(width, 8 * hmax) * _ceil_div(height, 8 * vmax)
        for slot, comp in enumerate(chosen):
            blocks += [slot] * (comp[1] * comp[2])
    match = _JPEG_MARKER_END.search(data, position)
    if match is None:
        raise ImageNormalizationError("JPEG 扫描后缺少结束标记")
    scan = data[position : match.start()]
    parts = _JPEG_RESTART.split(scan)
    segments, restarts = parts[0::2], parts[1::2]
    if any(r[0] != 0xD0 + i % 8 for i, r in enumerate(restarts)):
        raise ImageNormalizationError("JPEG 重启标记顺序不合法")
    expected = _ceil_div(mcus, interval) if interval else 1
    if len(segments) != expected:
        raise ImageNormalizationError("JPEG 重启间隔与扫描段数不符，疑似损坏")
    if sum(len(s) for s in segments) * 8 < 2 * mcus * len(blocks):
        raise ImageNormalizationError("JPEG 扫描数据不足，疑似截断或损坏")
    remaining = mcus
    for segment in segments:
        take = min(interval or remaining, remaining)
        _decode_jpeg_segment(segment.replace(b"\xff\x00", b"\xff"), take, blocks, dc, ac)
        remaining -= take
    covered.update(c[0] for c in chosen)
    return match.start()


def _exif_orientation(tiff: bytes) -> int | None:
    if len(tiff) < 8 or tiff[:2] not in {b"II", b"MM"}:
        return None
    order = "<" if tiff[:2] == b"II" else ">"
    try:
        offset = struct.unpack(order + "I", tiff[4:8])[0]
        count = struct.unpack(order + "H", tiff[offset : offset + 2])[0]
        for index in range(count):
            entry = tiff[offset + 2 + index * 12 : offset + 14 + index * 12]
            tag, kind, _n = struct.unpack(order + "HHI", entry[:8])
            if tag == 0x0112 and kind == 3:
                return struct.unpack(order + "H", entry[8:10])[0]
    except struct.error:
        return None
    return None


# ----------------------------------------------------------------------------- PDF


def _number(value: float) -> str:
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return text or "0"


def _stream(dictionary: str, payload: bytes) -> bytes:
    return (
        f"<<{dictionary}/Length {len(payload)}>>\nstream\n".encode("ascii")
        + payload
        + b"\nendstream"
    )


def _plan(data: bytes) -> dict[str, Any]:
    """Describe the image object(s) to embed; pure, no PDF bytes yet."""
    kind = sniff(data)
    if kind == "JPEG":
        info = inspect_jpeg(data)
        space = {1: "/DeviceGray", 3: "/DeviceRGB", 4: "/DeviceCMYK"}[info["components"]]
        decode = (
            "/Decode[1 0 1 0 1 0 1 0]"
            if info["components"] == 4 and info["adobeTransform"] is not None
            else ""
        )
        info.update(
            method="EMBED_JPEG_RAW",
            image_dict=f"/Type/XObject/Subtype/Image/Width {info['width']}/Height {info['height']}"
            f"/ColorSpace {space}/BitsPerComponent 8/Filter/DCTDecode{decode}",
            payload=data[: info["end"]],
            smask=None,
        )
        return info
    info = inspect_png(data)
    color, depth, width = info["colorType"], info["bitDepth"], info["width"]
    base = f"/Type/XObject/Subtype/Image/Width {info['width']}/Height {info['height']}"
    if color in {0, 2, 3}:
        channels = _CHANNELS[color]
        if color == 3:
            palette = info["palette"]
            space = f"[/Indexed/DeviceRGB {len(palette) // 3 - 1}<{palette.hex()}>]"
        else:
            space = "/DeviceGray" if color == 0 else "/DeviceRGB"
        parms = f"/Predictor 15/Colors {channels}/BitsPerComponent {depth}/Columns {width}"
        info.update(
            method="EMBED_PNG_IDAT_RAW",
            image_dict=f"{base}/ColorSpace {space}/BitsPerComponent {depth}"
            f"/Filter/FlateDecode/DecodeParms<<{parms}>>",
            payload=info["idat"],
            smask=None,
        )
        return info
    color_plane, alpha_plane = _split_alpha(info)
    space = "/DeviceGray" if color == 4 else "/DeviceRGB"
    info.update(
        method="PNG_ALPHA_SPLIT_LOSSLESS",
        image_dict=f"{base}/ColorSpace {space}/BitsPerComponent {depth}/Filter/FlateDecode",
        payload=zlib.compress(color_plane, 9),
        smask={
            "dict": f"/Type/XObject/Subtype/Image/Width {info['width']}/Height {info['height']}"
            f"/ColorSpace/DeviceGray/BitsPerComponent {depth}/Filter/FlateDecode",
            "payload": zlib.compress(alpha_plane, 9),
            "plane": alpha_plane,
        },
        color_plane=color_plane,
    )
    return info


def build_pdf(data: bytes) -> tuple[bytes, dict[str, Any]]:
    """Return (PDF bytes, mapping record). Deterministic for the same input and zlib build."""
    plan = _plan(data)
    source_sha = sha256(data)
    dpi = plan.get("dpi") or 72.0
    width_pt = plan["width"] * 72.0 / dpi
    height_pt = plan["height"] * 72.0 / dpi
    rotate = _ROTATE.get(plan.get("orientation", 1), 0)
    page_extra = f"/Rotate {rotate}" if rotate else ""
    content = f"q {_number(width_pt)} 0 0 {_number(height_pt)} 0 0 cm /Im0 Do Q".encode("ascii")
    image_dict = plan["image_dict"] + ("/SMask 6 0 R" if plan["smask"] else "")
    objects: list[bytes] = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        (
            f"<</Type/Page/Parent 2 0 R/MediaBox[0 0 {_number(width_pt)} {_number(height_pt)}]"
            f"/Resources<</XObject<</Im0 5 0 R>>>>/Contents 4 0 R{page_extra}>>"
        ).encode("ascii"),
        _stream("", content),
        _stream(image_dict, plan["payload"]),
    ]
    if plan["smask"]:
        objects.append(_stream(plan["smask"]["dict"], plan["smask"]["payload"]))
    info_index = len(objects) + 1
    objects.append(
        (
            f"<</Producer({CONVERTER['name']}/{CONVERTER['version']})"
            f"/XfSourceSha256({source_sha})/XfSourceFormat({plan['format']})>>"
        ).encode("ascii")
    )
    body = bytearray(b"%PDF-1.5\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, payload in enumerate(objects, 1):
        offsets.append(len(body))
        body += f"{number} 0 obj\n".encode("ascii") + payload + b"\nendobj\n"
    xref = len(body)
    body += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii")
    for offset in offsets:
        body += f"{offset:010d} 00000 n \n".encode("ascii")
    identity = hashlib.sha256(data).hexdigest()[:32]
    body += (
        f"trailer\n<</Size {len(objects) + 1}/Root 1 0 R/Info {info_index} 0 R"
        f"/ID[<{identity}><{identity}>]>>\n"
        f"startxref\n{xref}\n%%EOF\n"
    ).encode("ascii")
    mapping = {
        "sourceSha256": source_sha,
        "sourceSizeBytes": len(data),
        "sourceFormat": plan["format"],
        "pixelWidth": plan["width"],
        "pixelHeight": plan["height"],
        "pageSizePt": [round(width_pt, 4), round(height_pt, 4)],
        "rotate": rotate,
        "method": plan["method"],
        "densityDpi": round(dpi, 4),
        "pageCount": 1,
        "converter": dict(CONVERTER),
        **{key: plan[key] for key in ("bitDepth", "colorType", "ancillaryChunks") if key in plan},
        **{key: plan[key] for key in ("components", "orientation") if key in plan},
        **(
            {key: plan[key] for key in ("trailingBytes", "trailingSha256")}
            if plan.get("trailingBytes")
            else {}
        ),
    }
    return bytes(body), mapping


def _objects(pdf: bytes) -> dict[int, bytes]:
    """Object bodies located through the xref table (binary streams are never scanned)."""
    tail = pdf.rfind(b"startxref")
    if tail < 0:
        return {}
    try:
        xref = int(pdf[tail + 9 :].split()[0])
        lines = pdf[xref:tail].split(b"\n")
        count = int(lines[1].split()[1])
        offsets = [int(line.split()[0]) for line in lines[3 : 2 + count]]
    except (ValueError, IndexError):
        return {}
    ends = [*offsets[1:], xref]
    result: dict[int, bytes] = {}
    for number, (start, end) in enumerate(zip(offsets, ends, strict=True), 1):
        chunk = pdf[start:end]
        header = f"{number} 0 obj\n".encode("ascii")
        if not chunk.startswith(header) or not chunk.endswith(b"\nendobj\n"):
            return {}
        result[number] = chunk[len(header) : -len(b"\nendobj\n")]
    return result


def _stream_payload(body: bytes) -> bytes:
    match = re.match(rb"<<(.*?)/Length (\d+)>>\nstream\n", body, re.S)
    if not match:
        raise ImageNormalizationError("PDF 图片流结构不是本工具生成的结构")
    start = match.end()
    return body[start : start + int(match.group(2))]


def verify_pdf(pdf: bytes, source: bytes, mapping: dict[str, Any]) -> list[str]:
    """Return mismatch reasons; empty means the PDF still faithfully carries exactly this source."""
    reasons: list[str] = []
    try:
        plan = _plan(source)
    except ImageNormalizationError as error:
        return [f"源图片无法读取：{error}"]
    if sha256(source) != mapping.get("sourceSha256") or len(source) != mapping.get(
        "sourceSizeBytes"
    ):
        reasons.append("源图片摘要或大小与转换记录不一致")
    if plan["width"] != mapping.get("pixelWidth") or plan["height"] != mapping.get("pixelHeight"):
        reasons.append("源图片像素尺寸与转换记录不一致")
    objects = _objects(pdf)
    if not pdf.startswith(b"%PDF-1.5\n") or 5 not in objects:
        return reasons + ["PDF 不是本工具生成的单页图片结构"]
    if f"/XfSourceSha256({mapping.get('sourceSha256')})".encode() not in pdf:
        reasons.append("PDF 内嵌的源摘要与转换记录不一致")
    try:
        embedded = _stream_payload(objects[5])
    except ImageNormalizationError as error:
        return reasons + [str(error)]
    image_dict = objects[5].split(b"stream\n", 1)[0].decode("latin-1")
    if f"/Width {plan['width']}/Height {plan['height']}" not in image_dict:
        reasons.append("PDF 图片像素尺寸与源图片不一致")
    if plan["method"] in {"EMBED_JPEG_RAW", "EMBED_PNG_IDAT_RAW"}:
        if embedded != plan["payload"]:
            reasons.append("PDF 内嵌图像数据与源图片不一致（疑似换图）")
    else:
        try:
            color_ok = zlib.decompress(embedded) == plan["color_plane"]
            smask = _stream_payload(objects[6]) if 6 in objects else b""
            alpha_ok = bool(smask) and zlib.decompress(smask) == plan["smask"]["plane"]
        except (zlib.error, ImageNormalizationError, KeyError):
            color_ok = alpha_ok = False
        if not color_ok or not alpha_ok:
            reasons.append("PDF 内嵌像素或透明通道与源图片不一致（疑似换图）")
    media = re.search(rb"/MediaBox\[0 0 ([0-9.]+) ([0-9.]+)\]", objects.get(3, b""))
    expected = mapping.get("pageSizePt") or []
    if not media or len(expected) != 2 or [round(float(v), 4) for v in media.groups()] != expected:
        reasons.append("PDF 页面尺寸与转换记录不一致")
    return reasons
