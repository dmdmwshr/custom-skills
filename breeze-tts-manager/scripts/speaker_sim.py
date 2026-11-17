#!/usr/bin/env python3
"""用本机 CAM++ 声纹模型比较“是不是同一个人的声音”（离线、CPU）。

模型：CosyVoice 自带的 campplus.onnx（80 维 fbank -> 192 维说话人向量）。
输出余弦相似度：同一说话人通常明显高于不同说话人，但阈值要用本机实测基线定，不是通用标准。
用 ComfyUI 的 Python 运行（有 torchaudio、onnxruntime、soundfile）：

  python speaker_sim.py --files a.flac b.flac c.wav [--out result.json]
  python speaker_sim.py --groups groups.json --out result.json
      groups.json: {"甲": ["a.flac", ...], "乙": [...]} -> 组内平均相似度、组间平均相似度、每个文件到本组中心的相似度
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path

DEFAULT_MODEL = r"D:\aimodels\speech\tts\wschuan-tts\pretrained_models\campplus.onnx"
_SESSION = None


def _session(model_path: str):
    global _SESSION
    if _SESSION is None:
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 2
        _SESSION = ort.InferenceSession(model_path, sess_options=opts, providers=["CPUExecutionProvider"])
    return _SESSION


def embed_file(path: str, model_path: str = DEFAULT_MODEL):
    import numpy as np
    import soundfile as sf
    import torch
    import torchaudio
    import torchaudio.compliance.kaldi as kaldi

    audio, rate = sf.read(path, dtype="float32", always_2d=True)
    wave = torch.from_numpy(audio.mean(axis=1))
    if rate != 16000:
        wave = torchaudio.functional.resample(wave, rate, 16000)
    feat = kaldi.fbank(wave.unsqueeze(0) * 32768, num_mel_bins=80, dither=0.0, sample_frequency=16000)
    feat = feat - feat.mean(dim=0, keepdim=True)
    session = _session(model_path)
    name = session.get_inputs()[0].name
    out = session.run(None, {name: feat.unsqueeze(0).numpy().astype("float32")})[0][0]
    return out / (np.linalg.norm(out) + 1e-9)


def cosine(a, b) -> float:
    return float((a * b).sum())


def pairwise(paths: list[str], model_path: str = DEFAULT_MODEL) -> dict:
    vectors = {p: embed_file(p, model_path) for p in paths}
    matrix = {a: {b: round(cosine(vectors[a], vectors[b]), 3) for b in paths} for a in paths}
    return {"files": paths, "matrix": matrix}


def groups_report(groups: dict[str, list[str]], model_path: str = DEFAULT_MODEL) -> dict:
    import numpy as np
    vecs = {g: [embed_file(p, model_path) for p in ps] for g, ps in groups.items()}
    report = {"groups": {}, "between": {}}
    for g, vs in vecs.items():
        pairs = [cosine(a, b) for a, b in itertools.combinations(vs, 2)]
        centre = np.mean(vs, axis=0)
        centre = centre / (np.linalg.norm(centre) + 1e-9)
        report["groups"][g] = {
            "n": len(vs),
            "within_mean": round(float(np.mean(pairs)), 3) if pairs else None,
            "within_min": round(float(np.min(pairs)), 3) if pairs else None,
            "to_centre": {p: round(cosine(v, centre), 3) for p, v in zip(groups[g], vs)},
        }
    for g1, g2 in itertools.combinations(groups, 2):
        sims = [cosine(a, b) for a in vecs[g1] for b in vecs[g2]]
        report["between"][f"{g1} | {g2}"] = {"mean": round(float(np.mean(sims)), 3), "max": round(float(np.max(sims)), 3)}
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--files", nargs="+")
    parser.add_argument("--groups", help="JSON：{组名: [文件...]}")
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    if bool(args.files) == bool(args.groups):
        print("错误：--files 与 --groups 二选一。", file=sys.stderr)
        return 2
    if not Path(args.model).is_file():
        print(f"错误：找不到声纹模型 {args.model}", file=sys.stderr)
        return 2
    result = pairwise(args.files, args.model) if args.files else groups_report(
        json.loads(Path(args.groups).read_text(encoding="utf-8-sig")), args.model)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
