"""
Language-verification experiments on test_audio/asr.

1. Whisper forced to Arabic vs forced to English: does the
   average log-probability reliably pick the SPOKEN language?
2. Gemini audio transcription (verbatim, no translation):
   does it keep dialect, English and code-switching?

Uses Groq (2 requests per file) and Gemini vision quota
(1 request per file). Usage: .venv\\Scripts\\python bench_asr_verify.py
"""

import asyncio
import io
import time
import wave
from pathlib import Path

from google import genai
from google.genai import types

import config
import whisper_service as w

FOLDER = Path(__file__).resolve().parent / "test_audio" / "asr"

GEMINI_PROMPT = (
    "Transcribe this speech exactly as spoken, word for word. "
    "Keep every word in the language it was spoken in: Lebanese Arabic in "
    "Arabic script, English and French in Latin script, and mixed sentences "
    "mixed. Never translate. Output only the transcript."
)


def mean_logprob(result) -> float:
    segments = getattr(result, "segments", None) or []
    values = [s["avg_logprob"] if isinstance(s, dict) else s.avg_logprob for s in segments]
    return sum(values) / len(values) if values else -9.0


def duration(path: Path) -> float:
    with wave.open(str(path), "rb") as file:
        return file.getnframes() / file.getframerate()


async def whisper(audio: bytes, language: str, prompt: str | None):
    options = {
        "model": config.WHISPER_MODEL,
        "file": ("r.wav", audio, "audio/wav"),
        "response_format": "verbose_json",
        "temperature": 0.0,
        "language": language,
    }
    if prompt:
        options["prompt"] = prompt
    start = time.perf_counter()
    result = await w._client().audio.transcriptions.create(**options)  # noqa: SLF001
    return result, time.perf_counter() - start


async def main() -> None:
    gemini = genai.Client(api_key=config.GEMINI_API_KEY)
    files = sorted(p for p in FOLDER.glob("*.wav") if not p.name.startswith("_"))

    for path in files:
        audio = path.read_bytes()
        (ar, t_ar), (en, t_en) = await asyncio.gather(
            whisper(audio, "ar", w.NATURAL_PROMPT),
            whisper(audio, "en", None),
        )
        lp_ar, lp_en = mean_logprob(ar), mean_logprob(en)
        winner = "ar" if lp_ar > lp_en else "en"

        start = time.perf_counter()
        response = await gemini.aio.models.generate_content(
            model=config.VISION_MODEL,
            contents=[GEMINI_PROMPT, types.Part.from_bytes(data=audio, mime_type="audio/wav")],
            config=types.GenerateContentConfig(
                temperature=0.0,
                thinking_config=types.ThinkingConfig(thinking_level="minimal"),
            ),
        )
        t_gem = time.perf_counter() - start

        print(f"\n## {path.stem} ({duration(path):.1f} s audio)")
        print(f"   forced ar  lp={lp_ar:6.3f} {t_ar:4.2f}s :: {ar.text.strip()[:110]}")
        print(f"   forced en  lp={lp_en:6.3f} {t_en:4.2f}s :: {en.text.strip()[:110]}")
        print(f"   => confidence picks: {winner}")
        print(f"   gemini           {t_gem:4.2f}s :: {(response.text or '').strip()[:140]}")

    await w.close_groq_client()


if __name__ == "__main__":
    asyncio.run(main())
