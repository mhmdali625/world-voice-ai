"""
Speech recognition: local faster-whisper (CPU int8, via local_sidecar.py)
versus Groq Whisper large-v3 (cloud), on the same recorded questions with
known text (English, Lebanese, formal Arabic, French, accented, mixed).

Score = word-sequence similarity with the true text (0-1, diacritics and
punctuation ignored) + language detected + latency.

Usage: .venv\\Scripts\\python bench_local_asr.py      (sidecar must be running; ~13 Groq requests)
"""

import asyncio
import difflib
import re
import statistics
import time

import httpx

import whisper_service
from bench_language import QUESTIONS as LANG_QUESTIONS
from test_live_latency import QDIR, QUESTIONS as LATENCY_QUESTIONS

SIDECAR = "http://127.0.0.1:8092"


def words(text: str) -> list[str]:
    text = re.sub(r"[ً-ْـ]", "", text.lower())
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"[^\w\s]", " ", text).split()


def score(reference: str, heard: str) -> float:
    return difflib.SequenceMatcher(None, words(reference), words(heard)).ratio()


async def main() -> None:
    refs = {name: text for name, _, _, text in LATENCY_QUESTIONS}
    refs.update({name: text for name, _, _, text, _, _ in LANG_QUESTIONS if text})
    results = {"local": [], "groq": []}
    times = {"local": [], "groq": []}
    async with httpx.AsyncClient(timeout=60) as c:
        for name, reference in refs.items():
            path = QDIR / f"{name}.wav"
            if not path.exists():
                continue
            audio = path.read_bytes()
            t = time.perf_counter()
            r = (await c.post(f"{SIDECAR}/asr", content=audio)).json()
            local_s = time.perf_counter() - t
            t = time.perf_counter()
            try:
                g = await whisper_service.transcribe_bytes(audio, "audio/wav")
                groq_text, groq_lang = g["transcript"], g.get("detected_language")
            except Exception as error:  # noqa: BLE001
                groq_text, groq_lang = f"ERROR {type(error).__name__}", "-"
            groq_s = time.perf_counter() - t
            ls, gs = score(reference, r["text"]), score(reference, groq_text)
            results["local"].append(ls)
            results["groq"].append(gs)
            times["local"].append(local_s)
            times["groq"].append(groq_s)
            print(f"{name:20s} local {ls:.2f} {local_s:4.1f}s [{r['language']}] {r['text'][:42]!r}\n"
                  f"{'':20s} groq  {gs:.2f} {groq_s:4.1f}s [{groq_lang}] {groq_text[:42]!r}", flush=True)
            await asyncio.sleep(0.5)
    for k in results:
        print(f"{k:5s}: mean similarity {statistics.mean(results[k]):.2f}, median time "
              f"{statistics.median(times[k]):.2f} s, max {max(times[k]):.2f} s ({len(results[k])} questions)")


if __name__ == "__main__":
    asyncio.run(main())
