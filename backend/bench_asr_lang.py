"""
Benchmark Whisper language detection with different prompts.

Runs every recording in test_audio/asr (see make_asr_testset.py)
through each prompt mode and reports the detected language,
whether the transcript is in the SPOKEN language (not
translated), and latency. Uses Groq Whisper only
(1 request per recording per mode).

Usage: .venv\\Scripts\\python bench_asr_lang.py [modes...]
"""

import asyncio
import re
import sys
import time
from pathlib import Path

import config
import whisper_service as w

FOLDER = Path(__file__).resolve().parent / "test_audio" / "asr"

ARABIC = re.compile(r"[؀-ۿ]")
LATIN = re.compile(r"[A-Za-zÀ-ÿ]")


def script_of(text: str) -> str:
    arabic = len(ARABIC.findall(text))
    latin = len(LATIN.findall(text))
    if arabic and latin and min(arabic, latin) / max(arabic, latin) > 0.15:
        return "ar+en"
    return "ar" if arabic > latin else "en"


def correct_language(expected: str, transcript: str) -> bool:
    got = script_of(transcript)
    if expected in ("en", "fr"):
        return got == "en"            # Latin script, not translated to Arabic
    if expected == "ar":
        return got == "ar"
    return got == "ar+en"             # code-switch must keep BOTH languages


async def main() -> None:
    modes = sys.argv[1:] or ["natural", "none", "legacy", "multi"]
    config.ASR_HEDGE_AFTER_SECONDS = 0      # measure single requests

    rows = [
        line.split("\t")
        for line in (FOLDER / "expected.tsv").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    score = {m: 0 for m in modes}
    times = {m: [] for m in modes}
    results: dict[tuple[str, str], dict] = {}

    for name, expected_lang, expected_text in rows:
        audio = (FOLDER / f"{name}.wav").read_bytes()
        print(f"\n## {name} (spoken: {expected_lang})\n   expected: {expected_text}")
        for mode in modes:
            config.WHISPER_PROMPT_MODE = mode
            start = time.perf_counter()
            try:
                r = await w.transcribe_bytes(audio, "audio/wav")
                text, detected = r["transcript"], r["detected_language"]
            except Exception as error:  # noqa: BLE001
                text, detected = f"ERROR {error}", None
            elapsed = time.perf_counter() - start
            ok = correct_language(expected_lang, text)
            score[mode] += ok
            times[mode].append(elapsed)
            results[(name, mode)] = {"text": text, "detected": detected, "ok": ok}
            print(f"   [{mode:7s}] {'OK ' if ok else 'BAD'} detected={detected!s:8s} "
                  f"{elapsed:4.2f}s  {text}")

    await w.close_groq_client()

    print("\n=== SUMMARY: transcript in the spoken language ===")
    for mode in modes:
        median = sorted(times[mode])[len(times[mode]) // 2]
        print(f"{mode:8s} {score[mode]}/{len(rows)} correct | median {median:.2f} s")


if __name__ == "__main__":
    asyncio.run(main())
