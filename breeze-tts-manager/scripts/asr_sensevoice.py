#!/usr/bin/env python3
"""用本机 FunASR SenseVoiceSmall 离线转写若干 16 kHz 单声道 WAV（CPU，不联网）。

由 dub.py 用 FunASR 自己的解释器调用：
  <funasr python> asr_sensevoice.py --model-dir <SenseVoiceSmall> --out result.json a.wav b.wav
输出 JSON：{ "<wav 路径>": {"text": "转写文字（已去掉表情符号）"} }。
转写只用于核对台词，不代表音色或情绪合格。
"""

import argparse
import json
import os
import re

os.environ["CUDA_VISIBLE_DEVICES"] = ""
os.environ["HF_HUB_OFFLINE"] = "1"

_EMOJI = re.compile(r"[\U0001F300-\U0001FAFF☀-➿]")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("wavs", nargs="+")
    args = parser.parse_args()
    from funasr import AutoModel
    from funasr.utils.postprocess_utils import rich_transcription_postprocess

    model = AutoModel(model=args.model_dir, device="cpu", disable_update=True, hub="ms")
    result = {}
    for wav in args.wavs:
        raw = model.generate(input=wav, language="zh", use_itn=True)
        result[wav] = {"text": _EMOJI.sub("", rich_transcription_postprocess(raw[0]["text"])).strip()}
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
