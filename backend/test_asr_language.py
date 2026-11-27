"""
Validates the Whisper language check on test_audio/asr.

Run 1 "normal": the real pipeline as deployed.
Run 2 "misdetect": the main request is FORCED to Arabic,
reproducing the real-device bug (Whisper deciding "Arabic"
for English speech). The language check must recover the
spoken language.

Uses Groq only (about 2 requests per file per run).
Usage: .venv\\Scripts\\python test_asr_language.py
"""

import asyncio
import re
import time
from pathlib import Path

import config
import whisper_service as w

FOLDER = Path(__file__).resolve().parent / "test_audio" / "asr"
ARABIC = re.compile(r"[؀-ۿ]")
LATIN = re.compile(r"[A-Za-zÀ-ÿ]{2,}")


def spoken_language_kept(expected: str, text: str) -> bool:
    has_ar, has_lat = bool(ARABIC.search(text)), len(LATIN.findall(text)) >= 2
    if expected in ("en", "fr"):
        return has_lat and not has_ar
    if expected == "ar":
        return has_ar and not has_lat
    return has_ar and has_lat          # mixed must keep both


async def run(label: str, force_arabic: bool) -> None:
    client = w._client()  # noqa: SLF001
    original = client.audio.transcriptions.create

    async def patched(**options):
        if force_arabic and "language" not in options:
            options = {**options, "language": "ar"}   # simulate wrong detection
        return await original(**options)

    client.audio.transcriptions.create = patched
    rows = [l.split("\t") for l in (FOLDER / "expected.tsv").read_text(encoding="utf-8").splitlines() if l.strip()]

    ok = 0
    print(f"\n===== {label} =====")
    for name, expected, _ in rows:
        start = time.perf_counter()
        result = await w.transcribe_bytes((FOLDER / f"{name}.wav").read_bytes(), "audio/wav")
        elapsed = time.perf_counter() - start
        good = spoken_language_kept(expected, result["transcript"])
        ok += good
        print(f"{'OK ' if good else 'BAD'} {name:16s} {elapsed:4.2f}s "
              f"lang={result['detected_language']!s:8s} "
              f"[{result['language_check']['reason']}] :: {result['transcript'][:90]}")
    print(f"--> {ok}/{len(rows)} kept the spoken language")
    client.audio.transcriptions.create = original


async def main() -> None:
    config.ASR_HEDGE_AFTER_SECONDS = 0
    await run("normal pipeline", force_arabic=False)
    await run("simulated misdetection (main request forced to Arabic)", force_arabic=True)
    await w.close_groq_client()


if __name__ == "__main__":
    asyncio.run(main())
