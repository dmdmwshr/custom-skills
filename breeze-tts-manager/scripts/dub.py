#!/usr/bin/env python3
"""Breeze TTS 2 批量配音流水线：台词清单 + 角色声线库 -> 多条候选 -> 机器预检 -> 人工选定 -> 接收。

目录约定（<项目>/audio/dubbing/）：
  cues.json       台词清单（谁、说什么、怎么说、期望语速）
  voices.json     角色声线库（音色描述、已锁定的参考音频）
  takes/<cue>/<take>/   每条候选的运行目录（回执、工作流、音频）
  takes.json      候选索引（时长、响度、尾部静音、转写、字错率、标记）
  REVIEW.md       给人试听用的候选表
  selection.json  人工选定结果

原则：AI 只做批量生成和预检，不替用户选声；选定、锁定声线都是显式命令，来自用户试听。
权重仅限研究/非商用，沿用 breeze_tts.py 的 --purpose 闸门。
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import breeze_tts as bt

DEFAULT_FFMPEG = r"D:\Program_Files\Common\ffmpeg\bin\ffmpeg.exe"
DEFAULT_FUNASR_PY = r"D:\Program_Files\FunASR-runtimes\1.4.16\.venv\Scripts\python.exe"
DEFAULT_SENSEVOICE = r"D:\aimodels\modelscope\iic\SenseVoiceSmall"
DEFAULT_SEEDS = [11, 23, 37, 41, 53, 67]
PACE_CPS = {"slow": (0.0, 3.4), "normal": (2.8, 5.8), "fast": (5.0, 99.0)}  # 每秒字数合理区间
CER_LIMIT = 0.15
QUIET_PEAK_DB = -9.0
QUIET_MEAN_DB = -25.0
TAIL_SILENCE_S = 0.4
DIRECTION_ONLY_NOTE = "本脚本只生成候选，不批准音色；选定与锁定声线必须来自用户试听。"


class DubError(bt.BreezeError):
    pass


# --------------------------------------------------------------------------- 基础读写


def dub_dir(project: Path) -> Path:
    return project / "audio" / "dubbing"


def load_json(path: Path, default=None):
    if not path.is_file():
        if default is not None:
            return default
        raise DubError(f"缺少 {path}")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def save_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------- 文本与评分


_TAG = re.compile(r"\[[^\]]*\]|\([^)]*\)|（[^）]*）")
_KEEP = re.compile(r"[^\u4e00-\u9fffA-Za-z0-9]")


def spoken_chars(text: str) -> str:
    """去掉事件标签、标点和表情，只留可读字符，用于字数与字错率。"""
    return _KEEP.sub("", _TAG.sub("", text)).lower()


def cer(reference: str, hypothesis: str) -> float:
    ref, hyp = spoken_chars(reference), spoken_chars(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    prev = list(range(len(hyp) + 1))
    for i, rc in enumerate(ref, 1):
        cur = [i]
        for j, hc in enumerate(hyp, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (rc != hc)))
        prev = cur
    return round(prev[-1] / len(ref), 3)


def flags_for(take: dict, cue: dict) -> list[str]:
    flags: list[str] = []
    if take.get("peak_db") is not None and take["peak_db"] < QUIET_PEAK_DB:
        flags.append("偏轻(峰值)")
    if take.get("mean_db") is not None and take["mean_db"] < QUIET_MEAN_DB:
        flags.append("偏轻(均值)")
    if (take.get("trailing_silence") or 0) > TAIL_SILENCE_S:
        flags.append("尾部静音长")
    pace = cue.get("pace")
    if pace in PACE_CPS and take.get("cps") is not None:
        low, high = PACE_CPS[pace]
        if take["cps"] < low:
            flags.append(f"比期望{pace}更慢")
        elif take["cps"] > high:
            flags.append(f"比期望{pace}更快")
    if take.get("cer") is not None and take["cer"] > CER_LIMIT:
        flags.append(f"转写差异{take['cer']}")
    return flags


# --------------------------------------------------------------------------- 音频度量


def measure(audio: Path, text: str, ffmpeg: str) -> dict:
    """用 ffmpeg 的 volumedetect/silencedetect 取响度和尾部静音；失败只返回已知字段。"""
    info = bt.probe_audio(audio)
    result = {"duration": info.get("duration_s"), "sample_rate": info.get("sample_rate")}
    if not Path(ffmpeg).is_file():
        result["measure_error"] = "ffmpeg 不可用"
        return result
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-nostats", "-i", str(audio), "-af", "volumedetect,silencedetect=n=-40dB:d=0.15",
         "-f", "null", "-"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    log = proc.stderr
    mean = re.search(r"mean_volume:\s*(-?[\d.]+) dB", log)
    peak = re.search(r"max_volume:\s*(-?[\d.]+) dB", log)
    result["mean_db"] = float(mean.group(1)) if mean else None
    result["peak_db"] = float(peak.group(1)) if peak else None
    starts = [float(x) for x in re.findall(r"silence_start:\s*(-?[\d.]+)", log)]
    ends = [float(x) for x in re.findall(r"silence_end:\s*(-?[\d.]+)", log)]
    dur = result.get("duration")
    trailing = 0.0
    if dur and starts and len(starts) > len(ends):  # 最后一段静音没有结束点：直到文件尾
        trailing = max(0.0, dur - starts[-1])
    result["trailing_silence"] = round(trailing, 2)
    chars = len(spoken_chars(text))
    result["cps"] = round(chars / dur, 2) if dur and chars else None
    return result


# --------------------------------------------------------------------------- 批量生成


def choose_mode(voice: dict, cue: dict) -> str:
    if voice.get("reference") and voice["reference"].get("path"):
        return "direction" if cue.get("direction") else "clone"
    return "design"


def take_dir(base: Path, cue_id: str, seed: int) -> Path:
    """每个种子一个新目录；已有失败/拒绝的目录保留，新尝试用 -r2、-r3。"""
    root = base / "takes" / cue_id
    candidate = root / f"s{seed}"
    n = 1
    while candidate.exists():
        n += 1
        candidate = root / f"s{seed}-r{n}"
    return candidate


def existing_success(base: Path, cue_id: str, seed: int) -> Path | None:
    root = base / "takes" / cue_id
    for path in sorted(root.glob(f"s{seed}*")) if root.is_dir() else []:
        receipt = path / "receipt.json"
        if receipt.is_file() and json.loads(receipt.read_text(encoding="utf-8")).get("status") == "succeeded_pending_listening":
            return path
    return None


def expand_cues(cues: list[dict], voices: dict, only: list[str] | None) -> list[dict]:
    chosen = [c for c in cues if not only or c["id"] in only]
    missing = [c["id"] for c in chosen if c["character"] not in voices]
    if missing:
        raise DubError(f"这些台词的角色不在 voices.json 里：{missing}")
    if only and len(chosen) != len(set(only)):
        raise DubError("指定的台词编号有不存在的项。")
    for c in chosen:
        if not str(c.get("text", "")).strip():
            raise DubError(f"台词 {c['id']} 文本为空。")
    return chosen


def run_batch(project: Path, *, cues: list[dict], voices: dict, seeds: list[int], base_url: str, purpose: str,
              ffmpeg: str, timeout_s: int, node_commit: str, model: str, runner=None, uploader=None) -> dict:
    base = dub_dir(project)
    index_path = base / "takes.json"
    index = load_json(index_path, {"takes": []})
    runner = runner or bt.run_workflow
    uploader = uploader or bt.upload_reference
    uploaded: dict[str, str] = {}
    summary = {"succeeded": 0, "skipped": 0, "failed": 0, "stopped_unknown": None}
    for cue in cues:
        voice = voices[cue["character"]]
        mode = choose_mode(voice, cue)
        for seed in seeds:
            if existing_success(base, cue["id"], seed):
                summary["skipped"] += 1
                continue
            reference_name = None
            if mode != "design":
                ref_path = Path(voice["reference"]["path"])
                if not ref_path.is_absolute():
                    ref_path = base / ref_path
                key = str(ref_path)
                if key not in uploaded:
                    uploaded[key] = uploader(base_url, ref_path)
                reference_name = uploaded[key]
            instruction = voice.get("instruction", "")
            if mode == "design" and cue.get("direction"):
                instruction = f"{instruction} 本句表演要求：{cue['direction']}".strip()
            elif mode == "direction":
                instruction = cue["direction"]
            workflow = bt.build_workflow(
                mode, text=cue["text"], instruction=instruction, reference_name=reference_name,
                reference_text=(voice.get("reference") or {}).get("text", ""), model=model, seed=seed,
                prefix=f"audio/breeze/dub/{cue['id']}")
            run_dir = take_dir(base, cue["id"], seed)
            run_dir.parent.mkdir(parents=True, exist_ok=True)
            entry = {"cue": cue["id"], "take": run_dir.name, "seed": seed, "mode": mode, "dir": str(run_dir),
                     "created_at": now()}
            try:
                receipt = runner(base_url, workflow, run_dir, purpose=purpose, timeout_s=timeout_s,
                                 node_commit=node_commit, meta={"mode": mode, "model": model, "seed": seed, "cue": cue["id"]},
                                 text=cue["text"])
            except bt.BreezeError as exc:
                status = "unknown"
                receipt_path = run_dir / "receipt.json"
                if receipt_path.is_file():
                    status = json.loads(receipt_path.read_text(encoding="utf-8")).get("status", "unknown")
                entry.update(status=status, error=str(exc)[:400])
                index["takes"].append(entry)
                save_json(index_path, index)
                if status == "result_unknown":
                    summary["stopped_unknown"] = {"cue": cue["id"], "take": run_dir.name}
                    return summary  # 结果不明确：停止整批，不重放，等人对账
                summary["failed"] += 1
                continue
            audio = run_dir / receipt["outputs"][0]["file"]
            entry.update(status=receipt["status"], audio=str(audio), audio_sha256=receipt["outputs"][0]["sha256"],
                         elapsed_s=receipt.get("elapsed_s"), **measure(audio, cue["text"], ffmpeg))
            entry["flags"] = flags_for(entry, cue)
            index["takes"].append(entry)
            save_json(index_path, index)
            summary["succeeded"] += 1
    return summary


# --------------------------------------------------------------------------- 转写预检


def asr_check(project: Path, cues: dict[str, dict], *, ffmpeg: str, funasr_py: str, model_dir: str) -> int:
    base = dub_dir(project)
    index = load_json(base / "takes.json")
    todo = [t for t in index["takes"] if t.get("audio") and t.get("asr") is None and t["status"] == "succeeded_pending_listening"]
    if not todo:
        return 0
    work = base / "takes" / "_asr"
    work.mkdir(parents=True, exist_ok=True)
    wavs = []
    for t in todo:
        wav = work / f"{t['cue']}__{t['take']}.wav"
        subprocess.run([ffmpeg, "-v", "error", "-y", "-i", t["audio"], "-ar", "16000", "-ac", "1", str(wav)], check=True, timeout=60)
        wavs.append(str(wav))
    out_json = work / "asr-result.json"
    worker = Path(__file__).with_name("asr_sensevoice.py")
    proc = subprocess.run([funasr_py, "-B", "-X", "utf8", str(worker), "--model-dir", model_dir, "--out", str(out_json), *wavs],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
    if proc.returncode != 0 or not out_json.is_file():
        raise DubError(f"转写失败（exit {proc.returncode}）：{proc.stderr[-400:]}")
    result = json.loads(out_json.read_text(encoding="utf-8"))
    for t, wav in zip(todo, wavs):
        text = result.get(wav, {}).get("text")
        if text is None:
            continue
        t["asr"] = text
        t["cer"] = cer(cues[t["cue"]]["text"], text)
        t["flags"] = flags_for(t, cues[t["cue"]])
    save_json(base / "takes.json", index)
    return len(todo)


def write_review(project: Path, cues: dict[str, dict]) -> Path:
    base = dub_dir(project)
    index = load_json(base / "takes.json", {"takes": []})
    selection = load_json(base / "selection.json", {})
    lines = ["# 配音候选表", "", DIRECTION_ONLY_NOTE, "",
             "机器转写和标记只帮助排除明显问题，不能代替试听。标记含义：偏轻=响度低需混音时加增益；比期望更慢/更快=与 cue 的 pace 不符；转写差异=SenseVoice 与台词不一致，可能是读错也可能是识别错。", ""]
    by_cue: dict[str, list[dict]] = {}
    for t in index["takes"]:
        by_cue.setdefault(t["cue"], []).append(t)
    for cue_id, takes in by_cue.items():
        cue = cues.get(cue_id, {})
        picked = selection.get(cue_id, {}).get("take")
        lines += [f"## {cue_id}（{cue.get('character', '?')}）{cue.get('text', '')}", "",
                  f"表演要求：{cue.get('direction') or '无'}；期望语速：{cue.get('pace') or '未设'}；已选：{picked or '未选'}", "",
                  "| 候选 | 模式 | 时长 s | 峰值 dB | 均值 dB | 尾静音 s | 字/秒 | 转写 | 标记 | 文件 |", "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
        for t in sorted(takes, key=lambda x: (len(x.get("flags") or []), x["take"])):
            lines.append("| {take} | {mode} | {dur} | {peak} | {mean} | {tail} | {cps} | {asr} | {flags} | `{audio}` |".format(
                take=t["take"] + (" ✔" if t["take"] == picked else ""), mode=t["mode"], dur=t.get("duration", "-"),
                peak=t.get("peak_db", "-"), mean=t.get("mean_db", "-"), tail=t.get("trailing_silence", "-"),
                cps=t.get("cps", "-"), asr=(t.get("asr") or "未转写").replace("|", "/"),
                flags="、".join(t.get("flags") or []) or "无" if t["status"] == "succeeded_pending_listening" else t["status"],
                audio=t.get("audio", "")))
        lines.append("")
    path = base / "REVIEW.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- 选定、锁定、接收


def find_take(project: Path, cue_id: str, take: str) -> dict:
    index = load_json(dub_dir(project) / "takes.json")
    for t in index["takes"]:
        if t["cue"] == cue_id and t["take"] == take and t["status"] == "succeeded_pending_listening":
            return t
    raise DubError(f"没有成功的候选 {cue_id}/{take}。")


def select_take(project: Path, cue_id: str, take: str, note: str, decided_by: str) -> dict:
    if not decided_by.strip():
        raise DubError("必须写明是谁听后决定的（--decided-by），AI 不能替用户选声。")
    t = find_take(project, cue_id, take)
    path = dub_dir(project) / "selection.json"
    selection = load_json(path, {})
    selection[cue_id] = {"take": take, "audio": t["audio"], "audio_sha256": t["audio_sha256"], "by": decided_by,
                         "note": note, "at": now()}
    save_json(path, selection)
    return selection[cue_id]


def lock_voice(project: Path, voice_name: str, cue_id: str, take: str, ref_text: str | None, decided_by: str) -> dict:
    """把用户听过并认可的一条候选固定为该角色的参考声线（之后同角色走 clone/direction）。"""
    if not decided_by.strip():
        raise DubError("必须写明是谁听后决定的（--decided-by），AI 不能替用户锁定声线。")
    base = dub_dir(project)
    t = find_take(project, cue_id, take)
    cues = {c["id"]: c for c in load_json(base / "cues.json")["cues"]}
    voices_doc = load_json(base / "voices.json")
    if voice_name not in voices_doc["voices"]:
        raise DubError(f"voices.json 里没有角色 {voice_name}")
    text = ref_text if ref_text is not None else t.get("asr")
    if not text:
        raise DubError("参考音频需要逐字转写：先运行 check 取得转写，或用 --ref-text 提供。")
    if ref_text is None and (t.get("cer") is None or t["cer"] > CER_LIMIT):
        raise DubError("该候选的转写与台词不一致或未转写，请核对后用 --ref-text 给出实际听到的逐字文本。")
    src = Path(t["audio"])
    dest_dir = base / "voices" / voice_name
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"ref-{bt.sha256_file(src)[:12]}{src.suffix}"
    if not dest.exists():
        shutil.copy2(src, dest)
    voices_doc["voices"][voice_name]["reference"] = {
        "path": str(dest.relative_to(base)).replace("\\", "/"), "text": spoken_text_for_reference(text),
        "sha256": bt.sha256_file(dest), "from_cue": cue_id, "from_take": take, "locked_at": now(), "locked_by": decided_by}
    voices_doc["voices"][voice_name]["locked"] = True
    save_json(base / "voices.json", voices_doc)
    return voices_doc["voices"][voice_name]["reference"]


def spoken_text_for_reference(text: str) -> str:
    return re.sub(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", "", text).strip()


def ingest_selected(project: Path, media_center_root: Path, out_dir: Path, role: str) -> list[dict]:
    base = dub_dir(project)
    selection = load_json(base / "selection.json", {})
    cues = {c["id"]: c for c in load_json(base / "cues.json")["cues"]}
    if not selection:
        raise DubError("还没有选定任何候选。")
    results = []
    for cue_id, sel in selection.items():
        t = find_take(project, cue_id, sel["take"])
        if bt.sha256_file(Path(t["audio"])) != sel["audio_sha256"]:
            raise DubError(f"{cue_id} 的音频与选定时的哈希不一致，拒绝接收。")
        record = bt.ingest_run(Path(t["dir"]), media_center_root=media_center_root,
                               shot_id=cues[cue_id].get("shot", cue_id), speaker=cues[cue_id]["character"],
                               out_dir=out_dir, role=role,
                               extra_metadata={"dub_cue": cue_id, "dub_take": sel["take"], "dub_decided_by": sel["by"],
                                               "dub_character": cues[cue_id]["character"]})
        results.append({"cue": cue_id, "audio_id": record.get("audio_id"), "audio": (record.get("audio") or {}).get("path")})
    save_json(base / "ingested.json", {"at": now(), "role": role, "items": results})
    return results


# --------------------------------------------------------------------------- CLI

EXAMPLE_CUES = {"project": "示例项目", "cues": [
    {"id": "S005-01", "shot": "S005", "character": "旁白", "text": "海雾漫过堤岸。信号送达，灯塔再次亮起。",
     "direction": "平静克制，像纪录片旁白", "pace": "slow"},
    {"id": "S005-02", "shot": "S005", "character": "方源", "text": "你听见了吗？灯塔又亮了。",
     "direction": "压低声音，带一点惊喜", "pace": "normal"}]}
EXAMPLE_VOICES = {"voices": {
    "旁白": {"instruction": "沉稳的中年男声，语速偏慢，像纪录片旁白。", "reference": None, "locked": False},
    "方源": {"instruction": "年轻男声，克制冷静，带一点少年感。", "reference": None, "locked": False}}}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--project", required=True, help="项目根目录（其下 audio/dubbing/）")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="创建示例 cues.json / voices.json（不覆盖已有）")
    b = sub.add_parser("batch", help="为台词批量生成多条候选（默认 3 条）")
    b.add_argument("--cue", action="append", help="只生成指定台词编号，可重复")
    b.add_argument("--takes", type=int, default=3)
    b.add_argument("--seeds", help="逗号分隔的种子，覆盖 --takes")
    b.add_argument("--url", default=bt.DEFAULT_URL)
    b.add_argument("--purpose", required=True, help="evaluation 或 noncommercial；商用被拒绝")
    b.add_argument("--model", choices=sorted(bt.MODEL_LABELS), default="hybrid")
    b.add_argument("--timeout", type=int, default=900)
    b.add_argument("--node-commit", default="53d899bfaa1ea37badc58f901900c068a9755116")
    b.add_argument("--ffmpeg", default=DEFAULT_FFMPEG)
    b.add_argument("--yes", action="store_true")
    c = sub.add_parser("check", help="转写预检并生成 REVIEW.md")
    c.add_argument("--ffmpeg", default=DEFAULT_FFMPEG)
    c.add_argument("--funasr-python", default=DEFAULT_FUNASR_PY)
    c.add_argument("--sensevoice-dir", default=DEFAULT_SENSEVOICE)
    c.add_argument("--no-asr", action="store_true", help="只刷新 REVIEW.md，不跑转写")
    s = sub.add_parser("select", help="记录用户试听后选定的候选")
    s.add_argument("--cue", required=True)
    s.add_argument("--take", required=True)
    s.add_argument("--note", default="")
    s.add_argument("--decided-by", required=True, help="如“用户试听”；测试或演练请写明“测试脚本”")
    lk = sub.add_parser("lock-voice", help="把用户认可的候选固定为角色参考声线")
    lk.add_argument("--voice", required=True)
    lk.add_argument("--cue", required=True)
    lk.add_argument("--take", required=True)
    lk.add_argument("--ref-text")
    lk.add_argument("--decided-by", required=True, help="如“用户试听”；测试或演练请写明“测试脚本”")
    ig = sub.add_parser("ingest", help="把选定的候选交给媒体中心配音接收流程")
    ig.add_argument("--media-center-root", default=r"D:\ws\04-content\04-01-ai-imaging\comfy-workspace\media-center")
    ig.add_argument("--role", choices=["final_dialogue", "voice_reference"], default="final_dialogue")
    sub.add_parser("status", help="汇总进度")
    args = p.parse_args(argv)
    project = Path(args.project)
    base = dub_dir(project)
    try:
        if args.cmd == "init":
            base.mkdir(parents=True, exist_ok=True)
            for name, payload in (("cues.json", EXAMPLE_CUES), ("voices.json", EXAMPLE_VOICES)):
                if not (base / name).exists():
                    save_json(base / name, payload)
            print(f"已创建 {base}（已有文件保持不变）")
            return 0
        if args.cmd == "batch":
            if args.purpose not in bt.ALLOWED_PURPOSES:
                raise DubError(f"权重许可为 {bt.LICENSE_NAME}；--purpose 只能是 {sorted(bt.ALLOWED_PURPOSES)}，商用须先取得授权。")
            if not args.yes:
                raise DubError("batch 会占用 GPU 并写入大量输出，请加 --yes。")
            seeds = [int(x) for x in args.seeds.split(",")] if args.seeds else DEFAULT_SEEDS[: args.takes]
            if not seeds or len(seeds) > len(DEFAULT_SEEDS) + 4:
                raise DubError("候选数量不合理。")
            cues_doc = load_json(base / "cues.json")["cues"]
            voices = load_json(base / "voices.json")["voices"]
            chosen = expand_cues(cues_doc, voices, args.cue)
            summary = run_batch(project, cues=chosen, voices=voices, seeds=seeds, base_url=bt.normalize_url(args.url),
                                purpose=args.purpose, ffmpeg=args.ffmpeg, timeout_s=args.timeout,
                                node_commit=args.node_commit, model=args.model)
            write_review(project, {c["id"]: c for c in cues_doc})
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 3 if summary["stopped_unknown"] else (1 if summary["failed"] else 0)
        cues_by_id = {c["id"]: c for c in load_json(base / "cues.json")["cues"]} if (base / "cues.json").is_file() else {}
        if args.cmd == "check":
            n = 0 if args.no_asr else asr_check(project, cues_by_id, ffmpeg=args.ffmpeg, funasr_py=args.funasr_python,
                                                model_dir=args.sensevoice_dir)
            print(f"转写 {n} 条；候选表：{write_review(project, cues_by_id)}")
            return 0
        if args.cmd == "select":
            print(json.dumps(select_take(project, args.cue, args.take, args.note, args.decided_by), ensure_ascii=False, indent=2))
            write_review(project, cues_by_id)
            return 0
        if args.cmd == "lock-voice":
            print(json.dumps(lock_voice(project, args.voice, args.cue, args.take, args.ref_text, args.decided_by), ensure_ascii=False, indent=2))
            return 0
        if args.cmd == "ingest":
            print(json.dumps(ingest_selected(project, Path(args.media_center_root), project / "audio", args.role),
                             ensure_ascii=False, indent=2))
            return 0
        if args.cmd == "status":
            index = load_json(base / "takes.json", {"takes": []})
            selection = load_json(base / "selection.json", {})
            voices = load_json(base / "voices.json", {"voices": {}})["voices"]
            done = {t["cue"] for t in index["takes"] if t["status"] == "succeeded_pending_listening"}
            print(json.dumps({"cues": len(cues_by_id), "cues_with_takes": len(done), "takes": len(index["takes"]),
                              "selected": len(selection), "locked_voices": [k for k, v in voices.items() if v.get("locked")],
                              "unlocked_voices": [k for k, v in voices.items() if not v.get("locked")]}, ensure_ascii=False, indent=2))
            return 0
    except bt.BreezeError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
