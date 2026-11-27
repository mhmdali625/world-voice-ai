"""
Live Gemini TTS speed test. USES EXACTLY 3 TTS REQUESTS
(of the 10 per day per model on the free tier).

Answers the one question offline tests cannot: does the free
tier slow requests down when two run IN PARALLEL?

  A. one request with the whole answer          (1 request)
  B. the same answer as 2 parallel parts        (2 requests)

Prints time to first audio and real-time factor for each, and
the recommended WV_TTS_SPLIT. Audio is saved for listening.

Usage: .venv\\Scripts\\python bench_tts_live.py [model]
"""

import asyncio
import os
import sys
import time
import wave

import config
import speech_jobs
import tts_service as t

TEXT = (
    "في قدامك لابتوب مفتوح عالطاولة، والشاشة شغّالة وعليها صفحة كتابة. "
    "حدّ اللابتوب عاليمين في كباية قهوة وتلفون أسود. "
    "ورا الطاولة في شبّاك والضو جايي منّو، فالصورة واضحة منيحة. "
    "ما في شي مكتوب واضح بقدر إقراه من هالبعد."
)


async def measure(text: str, model: str, label: str) -> dict:
    start = time.perf_counter()
    first = None
    pcm = bytearray()
    async for piece in t._stream_pcm(text, "ar-LB", False, model):  # noqa: SLF001
        first = first or time.perf_counter() - start
        pcm.extend(piece)
    total = time.perf_counter() - start
    audio = len(pcm) / t.BYTES_PER_SECOND
    after_first = max(0.01, total - (first or 0))
    out = os.path.join(os.environ.get("TEMP", "."), f"wv_live_{label}.wav")
    with wave.open(out, "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(t.SAMPLE_RATE)
        file.writeframes(bytes(pcm))
    return {
        "label": label,
        "first_audio_s": round(first or 0, 2),
        "total_s": round(total, 2),
        "audio_s": round(audio, 2),
        "rate_after_first_audio": round(audio / after_first, 2),
        "saved": out,
    }


async def main() -> None:
    model = sys.argv[1] if len(sys.argv) > 1 else config.TTS_MODEL
    if t.blocked_for(model):
        sys.exit(f"{model} is quota-blocked right now.")

    print(f"Model {model}, voice {config.TTS_VOICE}. Using 3 requests.\n")

    single = await measure(TEXT, model, "single")
    print("A. single request:", single, flush=True)

    parts = speech_jobs.split_into_parts(TEXT, 2)
    results = await asyncio.gather(
        *(measure(p, model, f"part{i + 1}") for i, p in enumerate(parts))
    )
    for r in results:
        print("B. parallel part:", r, flush=True)

    single_rate = single["rate_after_first_audio"]
    parallel_rate = sum(r["rate_after_first_audio"] for r in results)
    print(
        f"\nSingle request: x{single_rate}. Two in parallel: x{parallel_rate:.2f} combined "
        f"(x{min(r['rate_after_first_audio'] for r in results):.2f} slowest part)."
    )
    if single_rate >= 1.0:
        print("=> Already real time: keep WV_TTS_SPLIT=1 (no extra quota needed).")
    elif parallel_rate > single_rate * 1.3:
        print("=> Parallel helps: set WV_TTS_SPLIT=2 in backend/.env (2 requests per long answer).")
    else:
        print("=> The free tier throttles parallel requests: keep WV_TTS_SPLIT=1.")

    await t.close_http_client()


if __name__ == "__main__":
    asyncio.run(main())
