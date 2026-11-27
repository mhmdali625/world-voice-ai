"""
Objective first filter for the voice comparison: Whisper transcribes each
sample and the transcript is compared with the intended sentence
(word-sequence similarity 0-1). It does NOT judge naturalness - only the
user's ears can - but a voice Whisper cannot follow will not sound right.

Usage: .venv\\Scripts\\python check_voice_intelligibility.py   (Groq Whisper, ~16 requests)
"""

import asyncio
import difflib
import json
import re
from pathlib import Path

import whisper_service

OUT = Path(__file__).resolve().parent / "voice_compare" / "final"


def words(text: str) -> list[str]:
    text = text.lower().replace("ـ", "")
    text = re.sub(r"[ً-ْ]", "", text)        # Arabic diacritics (shadda, sukun...)
    return re.sub(r"[^\w\s]", " ", text).split()


async def main() -> None:
    rows = json.loads((OUT / "results.json").read_text(encoding="utf-8"))
    totals: dict[str, list[float]] = {}
    for r in rows:
        line = f"{r['name']:11s}"
        for voice in ("fola", "azure", "piper"):
            path = OUT / f"{r['name']}_{voice}.wav"
            if not path.exists():
                continue
            try:
                heard = await whisper_service.transcribe_bytes(path.read_bytes(), "audio/wav")
                score = difflib.SequenceMatcher(None, words(r["text"]), words(heard["transcript"])).ratio()
                totals.setdefault(voice, []).append(score)
                line += f" | {voice} {score:.2f} {heard['transcript'][:40]!r}"
            except Exception as error:  # noqa: BLE001
                line += f" | {voice} ERROR {type(error).__name__}"
        print(line, flush=True)
    for voice, scores in totals.items():
        print(f"{voice}: mean similarity {sum(scores) / len(scores):.2f} over {len(scores)} sentences")


if __name__ == "__main__":
    asyncio.run(main())
