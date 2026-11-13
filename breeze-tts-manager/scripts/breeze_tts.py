#!/usr/bin/env python3
"""Breeze TTS 2 工作流构建与本机 ComfyUI 提交（仅回环地址，仅标准库）。

只做四件事：
  build  生成 API 格式工作流 JSON（design / clone / direction / multi）；
  run    检查本机 ComfyUI 健康 -> 提交 -> 等待历史 -> 把音频取回新的运行目录并写回执；
  ingest 把成功的运行交给媒体中心 ingest_dialogue，生成 48k 单声道 24bit 候选；
  info   打印模型位置、许可和默认参数。

不启动或重启 ComfyUI，不下载模型（工作流中 download_if_missing 固定为 false），
不自动重试：提交结果不明确时写 result_unknown 并退出，由人按 prompt_id 对账。
权重许可为 BreezeBlue Research and Non-Commercial License，run 必须显式声明
--purpose，商用被拒绝。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import re
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

DEFAULT_URL = "http://127.0.0.1:8188"
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
MODEL_LABELS = {
    "hybrid": "int8 hybrid (recommended)",
    "bf16": "bf16 (best quality)",
    "int8": "int8 (smallest, slower)",
    "int8-te": "int8 text encoder only",
}
LICENSE_NAME = "BreezeBlue Research and Non-Commercial License"
ALLOWED_PURPOSES = {"evaluation", "noncommercial"}
MODES = ("design", "clone", "direction", "multi")
REFERENCE_SUBFOLDER = "breeze_refs"
MODEL_DIR_HINT = r"C:\Users\12070\Documents\ComfyUI\models\breezetts2\drbaph_Breeze-TTS-2-comfyui"


class BreezeError(RuntimeError):
    """可向用户直接说明的失败。"""


class BreezeHttpError(BreezeError):
    """ComfyUI 明确返回了 HTTP 错误状态（请求已被服务端处理并拒绝）。"""

    def __init__(self, code: int, body: str):
        super().__init__(f"HTTP {code}: {body}")
        self.code = code


def normalize_url(value: str) -> str:
    parsed = urlparse(value.strip().rstrip("/"))
    if parsed.scheme not in {"http", "https"} or (parsed.hostname or "").lower() not in LOOPBACK_HOSTS:
        raise BreezeError("只允许 http(s) 回环地址（127.0.0.1、localhost、::1）。")
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise BreezeError("ComfyUI 地址不能包含凭据、路径、查询或片段。")
    return f"{parsed.scheme}://{parsed.netloc}"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --------------------------------------------------------------------------- build


def _controls(seed: int, max_new_tokens: int) -> dict:
    """API 格式要求写全所有 required 输入；取值与节点默认值一致。"""
    return {
        "max_new_tokens": max_new_tokens, "temperature": 0.9, "top_k": 50, "top_p": 1.0,
        "repetition_penalty": 1.1, "depth_temperature": 0.9, "depth_top_k": 50, "depth_top_p": 1.0,
        "seed": seed,
    }


_FULLWIDTH_SPEAKER = re.compile(r"^(\s*\[?[^:：\[\]\n]{1,24}\]?)：", re.MULTILINE)


def normalize_script(text: str) -> str:
    """节点只认 ASCII 冒号的 `名字: 台词`；把行首“名字：”改成 ASCII 冒号，JSON 脚本不动。"""
    if text.lstrip().startswith("["):
        return text
    return _FULLWIDTH_SPEAKER.sub(lambda m: m.group(1) + ":", text)


def build_workflow(
    mode: str,
    *,
    text: str,
    instruction: str = "",
    reference_name: str | None = None,
    reference_text: str = "",
    cast: list[dict] | None = None,
    model: str = "hybrid",
    seed: int = 42,
    cfg_scale: float | None = None,
    max_new_tokens: int = 1500,
    pause_between_speakers: float = 0.3,
    prefix: str = "audio/breeze/run",
) -> dict:
    """返回 API 格式工作流。参数缺省值沿用节点默认值，只覆盖必要项。"""
    if mode not in MODES:
        raise BreezeError(f"未知模式：{mode}")
    if model not in MODEL_LABELS:
        raise BreezeError(f"未知模型：{model}")
    if not text.strip():
        raise BreezeError("台词不能为空。")
    if mode in {"design", "direction"} and not instruction.strip():
        raise BreezeError("design/direction 需要 instruction（音色或表演说明）。")
    if mode in {"clone", "direction"} and (not reference_name or not reference_text.strip()):
        raise BreezeError("clone/direction 需要参考音频与其逐字转写。")

    graph: dict[str, dict] = {
        "1": {
            "class_type": "BreezeTTS2LoadModel",
            "inputs": {
                "model": MODEL_LABELS[model],
                "dtype": "auto",
                "device": "auto",
                "attention": "sdpa",
                "decode_mode": "eager",
                "download_if_missing": False,
            },
        }
    }
    controls = _controls(seed, max_new_tokens)
    if mode == "design":
        graph["2"] = {
            "class_type": "BreezeTTS2VoiceDesign",
            "inputs": {"breeze_model": ["1", 0], "text": text, "instruction": instruction,
                       "cfg_scale": cfg_scale or 4.0, **controls},
        }
        audio_ref = ["2", 0]
    elif mode in {"clone", "direction"}:
        graph["2"] = {"class_type": "LoadAudio", "inputs": {"audio": reference_name}}
        inputs = {"breeze_model": ["1", 0], "text": text, "reference_audio": ["2", 0],
                  "reference_text": reference_text, **controls}
        if mode == "clone":
            inputs["cfg_scale"] = cfg_scale or 1.0
            graph["3"] = {"class_type": "BreezeTTS2VoiceClone", "inputs": inputs}
        else:
            inputs.update({"instruction": instruction, "cfg_scale": cfg_scale or 4.0, "stitch_reference": "none"})
            graph["3"] = {"class_type": "BreezeTTS2VoiceDirection", "inputs": inputs}
        audio_ref = ["3", 0]
    else:
        if not cast:
            raise BreezeError("multi 需要 cast 说明（至少一个说话人）。")
        if len(cast) > 8:
            raise BreezeError("多人对白最多 8 个说话人。")
        multi_inputs = {"breeze_model": ["1", 0], "text": normalize_script(text),
                        "pause_between_speakers": pause_between_speakers, **controls}
        next_id = 2
        for slot, member in enumerate(cast, start=1):
            speaker_inputs = {
                "name": member["name"],
                "audio": "none",
                "reference_text": member.get("reference_text", ""),
                "auto_transcribe_if_empty": False,  # 避免触发 Whisper 联网下载
                "instruction": member.get("instruction", ""),
                "cfg_scale": member.get("cfg_scale", 4.0 if not member.get("reference_name") else 1.0),
            }
            if member.get("reference_name"):
                load_id = str(next_id)
                next_id += 1
                graph[load_id] = {"class_type": "LoadAudio", "inputs": {"audio": member["reference_name"]}}
                speaker_inputs["reference_audio"] = [load_id, 0]
                if not speaker_inputs["reference_text"].strip():
                    raise BreezeError(f"说话人 {member['name']} 的参考音频需要逐字转写。")
            elif not speaker_inputs["instruction"].strip():
                raise BreezeError(f"说话人 {member['name']} 需要 instruction 或参考音频。")
            speaker_id = str(next_id)
            next_id += 1
            graph[speaker_id] = {"class_type": "BreezeTTS2Speaker", "inputs": speaker_inputs}
            multi_inputs[f"speaker_{slot}"] = [speaker_id, 0]
        multi_id = str(next_id)
        next_id += 1
        graph[multi_id] = {"class_type": "BreezeTTS2MultiSpeaker", "inputs": multi_inputs}
        audio_ref = [multi_id, 0]
        graph[str(next_id)] = {"class_type": "SaveAudio",
                               "inputs": {"audio": audio_ref, "filename_prefix": prefix}}
        return graph
    graph[str(len(graph) + 1)] = {"class_type": "SaveAudio",
                                  "inputs": {"audio": audio_ref, "filename_prefix": prefix}}
    return graph


# --------------------------------------------------------------------------- http


def _http_json(url: str, *, data: bytes | None = None, headers: dict | None = None, timeout: int = 15):
    request = Request(url, data=data, headers=headers or {})
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - 仅回环
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:800]
        raise BreezeHttpError(exc.code, body) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise BreezeError(f"无法访问 {url}: {exc}") from exc


def _http_bytes(url: str, timeout: int = 60) -> bytes:
    try:
        with urlopen(Request(url), timeout=timeout) as response:  # noqa: S310
            return response.read()
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise BreezeError(f"下载输出失败 {url}: {exc}") from exc


def upload_reference(base: str, path: Path) -> str:
    """把参考音频上传到 ComfyUI input/breeze_refs/，返回 LoadAudio 使用的名称。"""
    boundary = uuid.uuid4().hex
    body = bytearray()

    def field(name: str, value: str) -> None:
        body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())

    field("type", "input")
    field("subfolder", REFERENCE_SUBFOLDER)
    field("overwrite", "false")
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    body.extend(
        f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{path.name}"\r\n'
        f"Content-Type: {mime}\r\n\r\n".encode()
    )
    body.extend(path.read_bytes())
    body.extend(f"\r\n--{boundary}--\r\n".encode())
    result = _http_json(f"{base}/upload/image", data=bytes(body),
                        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, timeout=60)
    name = result.get("name")
    if not name:
        raise BreezeError(f"上传参考音频未返回文件名：{result}")
    sub = result.get("subfolder") or ""
    return f"{sub}/{name}" if sub else name


def probe_audio(path: Path) -> dict:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return {"probe": "ffprobe_unavailable"}
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "stream=sample_rate,channels,codec_name:format=duration",
             "-of", "json", str(path)], capture_output=True, text=True, timeout=30, check=True).stdout
        data = json.loads(out)
        stream = (data.get("streams") or [{}])[0]
        return {"duration_s": float(data["format"]["duration"]), "sample_rate": int(stream.get("sample_rate", 0)),
                "channels": stream.get("channels"), "codec": stream.get("codec_name")}
    except Exception as exc:  # noqa: BLE001 - 探测失败不影响音频本体
        return {"probe": f"failed: {type(exc).__name__}"}


def _write_json(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def run_workflow(base: str, workflow: dict, run_dir: Path, *, purpose: str, timeout_s: int,
                 node_commit: str, meta: dict, text: str = "") -> dict:
    if purpose not in ALLOWED_PURPOSES:
        raise BreezeError(
            f"权重许可为 {LICENSE_NAME}，--purpose 只能是 {sorted(ALLOWED_PURPOSES)}；"
            "商用须先取得 BreezeBlue/RESONIA 书面授权，不得用本入口提交。")
    run_dir.mkdir(parents=True, exist_ok=False)  # 目录必须全新，保留旧结果
    receipt_path = run_dir / "receipt.json"
    receipt: dict = {
        "engine": "breeze-tts-2", "license": LICENSE_NAME, "purpose": purpose, "node_commit": node_commit,
        "workflow_sha256": sha256_bytes(json.dumps(workflow, sort_keys=True).encode()),
        "started_at": datetime.now(timezone.utc).isoformat(), "status": "preparing", **meta,
    }
    _write_json(run_dir / "workflow.api.json", workflow)
    if text:
        (run_dir / "text.txt").write_text(text, encoding="utf-8", newline="")
    _write_json(receipt_path, receipt)
    try:
        health = _http_json(f"{base}/system_stats")
    except BreezeError as exc:
        receipt.update(status="not_submitted", error=str(exc))
        _write_json(receipt_path, receipt)
        raise BreezeError(f"ComfyUI 不可用，未提交（不会自动启动）：{exc}") from exc
    receipt["comfyui"] = {"version": health.get("system", {}).get("comfyui_version"),
                          "devices": [d.get("name") for d in health.get("devices", [])]}
    client_id = uuid.uuid4().hex
    receipt.update(status="submitting", client_id=client_id)
    _write_json(receipt_path, receipt)
    try:
        response = _http_json(f"{base}/prompt", data=json.dumps({"prompt": workflow, "client_id": client_id}).encode(),
                              headers={"Content-Type": "application/json"}, timeout=30)
    except BreezeHttpError as exc:
        if exc.code == 400:  # 校验失败：服务端明确拒绝，未入队
            receipt.update(status="rejected", error=str(exc))
            _write_json(receipt_path, receipt)
            raise BreezeError(f"ComfyUI 明确拒绝了工作流（未入队）：{exc}") from exc
        receipt.update(status="result_unknown", error=str(exc))
        _write_json(receipt_path, receipt)
        raise BreezeError(f"提交结果不明确，已写 result_unknown，不重放：{exc}") from exc
    except BreezeError as exc:
        receipt.update(status="result_unknown", error=str(exc))
        _write_json(receipt_path, receipt)
        raise BreezeError(f"提交结果不明确，已写 result_unknown，不重放：{exc}") from exc
    prompt_id = response.get("prompt_id")
    if not prompt_id or response.get("node_errors"):
        receipt.update(status="rejected", response=response)
        _write_json(receipt_path, receipt)
        raise BreezeError(f"ComfyUI 拒绝工作流：{json.dumps(response, ensure_ascii=False)[:800]}")
    receipt.update(status="queued", prompt_id=prompt_id)
    _write_json(receipt_path, receipt)

    deadline = time.monotonic() + timeout_s
    started = time.monotonic()
    entry = None
    while time.monotonic() < deadline:
        history = _http_json(f"{base}/history/{quote(prompt_id)}")
        entry = history.get(prompt_id)
        if entry and entry.get("status", {}).get("completed") is not None:
            break
        time.sleep(2)
    else:
        receipt.update(status="result_unknown", error=f"等待超过 {timeout_s}s，未取消任务；请按 prompt_id 对账")
        _write_json(receipt_path, receipt)
        raise BreezeError(receipt["error"])
    receipt["elapsed_s"] = round(time.monotonic() - started, 2)
    status = entry.get("status", {})
    if status.get("status_str") == "error" or not status.get("completed"):
        receipt.update(status="failed", comfy_status=status)
        _write_json(receipt_path, receipt)
        raise BreezeError(f"ComfyUI 任务失败：{json.dumps(status, ensure_ascii=False)[:1200]}")
    outputs = []
    for node_id, node_out in (entry.get("outputs") or {}).items():
        for item in node_out.get("audio", []):
            url = (f"{base}/view?filename={quote(item['filename'])}&subfolder={quote(item.get('subfolder', ''))}"
                   f"&type={quote(item.get('type', 'output'))}")
            data = _http_bytes(url)
            target = run_dir / Path(item["filename"]).name
            target.write_bytes(data)
            outputs.append({"node": node_id, "file": target.name, "bytes": len(data),
                            "sha256": sha256_bytes(data), **probe_audio(target)})
    if not outputs:
        receipt.update(status="failed", error="历史中没有音频输出")
        _write_json(receipt_path, receipt)
        raise BreezeError(receipt["error"])
    receipt.update(status="succeeded_pending_listening", outputs=outputs, approved=False, voice_locked=False,
                   finished_at=datetime.now(timezone.utc).isoformat())
    _write_json(receipt_path, receipt)
    return receipt


# --------------------------------------------------------------------------- ingest


def ingest_run(run_dir: Path, *, media_center_root: Path, shot_id: str, speaker: str, out_dir: Path,
               role: str) -> dict:
    """把一次成功的 Breeze 运行交给媒体中心的 ingest_dialogue（48k 单声道 24bit WAV 候选）。"""
    receipt = json.loads((run_dir / "receipt.json").read_text(encoding="utf-8"))
    if receipt.get("status") != "succeeded_pending_listening" or not receipt.get("outputs"):
        raise BreezeError(f"运行状态为 {receipt.get('status')}，没有可接收的成功输出。")
    if len(receipt["outputs"]) != 1:
        raise BreezeError("一次接收只处理一个输出文件；请显式拆分。")
    text_path = run_dir / "text.txt"
    if role == "final_dialogue" and not text_path.is_file():
        raise BreezeError("缺少 text.txt，无法登记逐字台词。")
    src_dir = media_center_root / "src"
    if not src_dir.is_dir():
        raise BreezeError(f"媒体中心 src 不存在：{src_dir}")
    sys.path.insert(0, str(src_dir))
    try:
        from media_center import audio as mc_audio  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001
        raise BreezeError(f"无法导入媒体中心 audio 模块：{exc}") from exc
    output = run_dir / receipt["outputs"][0]["file"]
    if sha256_file(output) != receipt["outputs"][0]["sha256"]:
        raise BreezeError("输出文件与回执哈希不一致，拒绝接收。")
    metadata = {
        "engine": "breeze-tts-2", "license": LICENSE_NAME, "purpose": receipt.get("purpose"),
        "breeze_receipt": str(run_dir / "receipt.json"),
        "breeze_receipt_sha256": sha256_file(run_dir / "receipt.json"),
        "node_commit": receipt.get("node_commit"), "generation_mode": receipt.get("mode"),
        "commercial_use_allowed": False,
    }
    text = text_path.read_text(encoding="utf-8") if text_path.is_file() else ""
    return mc_audio.ingest_dialogue(output, text, shot_id, speaker, out_dir, mc_audio.DEFAULT_FFMPEG,
                                    mc_audio.DEFAULT_FFPROBE, role=role, metadata=metadata)


# --------------------------------------------------------------------------- cli


def _read_text(value: str | None, file_value: str | None) -> str:
    if file_value:
        return Path(file_value).read_text(encoding="utf-8-sig").strip()
    return (value or "").strip()


def _add_build_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--mode", choices=MODES, required=True)
    parser.add_argument("--text")
    parser.add_argument("--text-file")
    parser.add_argument("--instruction")
    parser.add_argument("--instruction-file")
    parser.add_argument("--reference", help="本地参考音频（run 时上传）或 input 目录内已有名称（build）")
    parser.add_argument("--reference-text")
    parser.add_argument("--reference-text-file")
    parser.add_argument("--cast-json", help="multi 模式：[{name, instruction, reference_name, reference_text, cfg_scale}]")
    parser.add_argument("--model", choices=sorted(MODEL_LABELS), default="hybrid")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--cfg-scale", type=float)
    parser.add_argument("--max-new-tokens", type=int, default=1500)
    parser.add_argument("--prefix", default="audio/breeze/run")


def _build_from_args(args: argparse.Namespace, reference_name: str | None) -> dict:
    cast = json.loads(Path(args.cast_json).read_text(encoding="utf-8-sig")) if args.cast_json else None
    return build_workflow(
        args.mode, text=_read_text(args.text, args.text_file),
        instruction=_read_text(args.instruction, args.instruction_file),
        reference_name=reference_name, reference_text=_read_text(args.reference_text, args.reference_text_file),
        cast=cast, model=args.model, seed=args.seed, cfg_scale=args.cfg_scale,
        max_new_tokens=args.max_new_tokens, prefix=args.prefix)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="只生成工作流 JSON")
    _add_build_args(build)
    build.add_argument("--out", required=True)
    run = sub.add_parser("run", help="提交并取回音频")
    _add_build_args(run)
    run.add_argument("--url", default=DEFAULT_URL)
    run.add_argument("--run-dir", required=True, help="必须是尚不存在的新目录")
    run.add_argument("--purpose", required=True, help="evaluation 或 noncommercial；商用被拒绝")
    run.add_argument("--timeout", type=int, default=900)
    run.add_argument("--node-commit", default="53d899bfaa1ea37badc58f901900c068a9755116")
    run.add_argument("--yes", action="store_true", help="确认本次提交")
    ingest = sub.add_parser("ingest", help="把成功的运行交给媒体中心配音接收流程")
    ingest.add_argument("--run-dir", required=True)
    ingest.add_argument("--media-center-root", default=r"D:\ws\04-content\04-01-ai-imaging\comfy-workspace\media-center")
    ingest.add_argument("--shot-id", required=True)
    ingest.add_argument("--speaker", required=True)
    ingest.add_argument("--out", required=True, help="项目 audio 目录")
    ingest.add_argument("--role", choices=["final_dialogue", "voice_reference"], default="final_dialogue")
    sub.add_parser("info", help="打印模型与许可信息")
    args = parser.parse_args(argv)

    try:
        if args.command == "info":
            print(json.dumps({"license": LICENSE_NAME, "model_dir": MODEL_DIR_HINT, "models": MODEL_LABELS,
                              "purposes": sorted(ALLOWED_PURPOSES)}, ensure_ascii=False, indent=2))
            return 0
        if args.command == "ingest":
            record = ingest_run(Path(args.run_dir), media_center_root=Path(args.media_center_root),
                                shot_id=args.shot_id, speaker=args.speaker, out_dir=Path(args.out), role=args.role)
            print(json.dumps(record, ensure_ascii=False, indent=2, default=str)[:3000])
            return 0
        if args.command == "build":
            workflow = _build_from_args(args, args.reference)
            Path(args.out).write_text(json.dumps(workflow, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"已生成 {args.out}")
            return 0
        if not args.yes:
            raise BreezeError("run 会占用 GPU 并写入输出，请加 --yes 确认。")
        base = normalize_url(args.url)
        reference_name = None
        if args.reference and args.mode in {"clone", "direction"}:
            ref_path = Path(args.reference)
            reference_name = upload_reference(base, ref_path) if ref_path.is_file() else args.reference
        workflow = _build_from_args(args, reference_name)
        receipt = run_workflow(base, workflow, Path(args.run_dir), purpose=args.purpose, timeout_s=args.timeout,
                               node_commit=args.node_commit,
                               meta={"mode": args.mode, "model": args.model, "seed": args.seed,
                                     "text_sha256": sha256_bytes(_read_text(args.text, args.text_file).encode())},
                               text=_read_text(args.text, args.text_file))
        print(json.dumps({"status": receipt["status"], "run_dir": args.run_dir, "outputs": receipt["outputs"],
                          "elapsed_s": receipt.get("elapsed_s")}, ensure_ascii=False, indent=2))
        return 0
    except BreezeError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
