"""离线 RapidOCR 普通图片验证码识别 worker（仅 Windows 受管 Python 使用）。

从标准输入读取一张验证码图片：`data:image/...;base64,...` 文本或原始图片字节。
只向标准输出写一行 JSON：{"ok","code","score","accepted","ocrMs"}，失败时 {"ok":false,"error":<分类>}。
识别码、图片和 base64 只在进程内存中出现：不写文件、不打印异常原文、不保留缓存。
依赖 rapidocr_onnxruntime（含随包 ONNX 模型，纯 CPU 离线），不联网、不安装依赖。
"""
import base64
import json
import re
import sys
import time

sys.dont_write_bytecode = True

MIN_SCORE = 0.8
CODE_PATTERN = re.compile(r"^[0-9]{4}$")  # 仅 ASCII 四位数字
MAX_INPUT_BYTES = 1_000_000


def emit(payload, exit_code=0):
    sys.stdout.write(json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n")
    sys.stdout.flush()
    sys.exit(exit_code)


def read_image_bytes():
    raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if not raw:
        emit({"ok": False, "error": "empty_input"}, 2)
    if len(raw) > MAX_INPUT_BYTES:
        emit({"ok": False, "error": "input_too_large"}, 2)
    head = raw[:16].lstrip()
    if head.startswith(b"data:image/"):
        try:
            return base64.b64decode(raw.strip().split(b",", 1)[1], validate=False)
        except Exception:
            emit({"ok": False, "error": "bad_data_url"}, 2)
    return raw


def main():
    image = read_image_bytes()
    started = time.perf_counter()
    try:
        from rapidocr_onnxruntime import RapidOCR
    except Exception:
        emit({"ok": False, "error": "dependency_unavailable"}, 3)
    try:
        engine = RapidOCR(intra_op_num_threads=1, inter_op_num_threads=1)
        result = engine(image, use_det=False, use_cls=False)
        rows = result[0]
        text, score = (rows[0][0], float(rows[0][1])) if rows else ("", 0.0)
    except Exception:
        emit({"ok": False, "error": "ocr_failed"}, 1)
    finally:
        image = None
    code = "".join(str(text).split())
    accepted = bool(CODE_PATTERN.match(code)) and score >= MIN_SCORE
    emit({
        "ok": True,
        "code": code if accepted else "",
        "score": round(score, 4),
        "accepted": accepted,
        "reason": "ok" if accepted else ("format" if not CODE_PATTERN.match(code) else "score"),
        "ocrMs": round((time.perf_counter() - started) * 1000),
    })


if __name__ == "__main__":
    main()
