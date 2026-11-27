"""
Azure Rami pronunciation test (real Azure + Groq Whisper as the
independent listener). Compares plain SSML with the production
SSML builder (Lebanese loanword aliases) on mixed sentences.

Usage: .venv\\Scripts\\python test_azure_pronunciation.py
Uses ~10 Azure requests (free F0) and ~10 Groq requests.
"""

import asyncio
import difflib
import io
import re
import sys
import wave
from xml.sax.saxutils import escape

import httpx

import config
import tts_providers as p
import whisper_service as w

SENTENCES = [
    "في قدامك laptop مفتوح، وعالـ screen في email مفتوح. حدّو في phone أسود و charger أبيض.",
    "خبرني شو في عالـ table حد اللابتوب.",
    "شوف إذا الـ charger موصول بالـ laptop.",
    "فيك تقرالي شو مكتوب عالـ document en français?",
    "الـ camera منيحة، بس الـ battery خالصة وبدّا charger.",
]

PLAIN = {k: re.sub(r"[ً-ْ]", "", v) for k, v in p.LEBANESE_LOANWORDS.items()}


def to_arabic(text: str) -> str:
    """Write known loanwords in Arabic so Latin/Arabic spelling is not penalised."""
    text = re.sub(r"([؀-ۿ]*ال)ـ?\s+([A-Za-zÀ-ÿ]+)",
                  lambda m: m.group(1) + PLAIN.get(m.group(2).lower(), m.group(2)), text)
    return re.sub(r"[A-Za-zÀ-ÿ]+", lambda m: PLAIN.get(m.group(0).lower(), m.group(0)), text)


def norm(s: str) -> str:
    s = re.sub(r"[ً-ْـ]", "", s)
    for a, b in (("أ", "ا"), ("إ", "ا"), ("آ", "ا"), ("ة", "ه"), ("ى", "ي")):
        s = s.replace(a, b)
    return re.sub(r"[^\w]", "", s.lower())


def similarity(expected: str, heard: str) -> float:
    return difflib.SequenceMatcher(None, norm(to_arabic(expected)), norm(to_arabic(heard))).ratio()


def wav(pcm: bytes) -> bytes:
    b = io.BytesIO()
    with wave.open(b, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(24000)
        f.writeframes(pcm)
    return b.getvalue()


async def plain_azure(text: str) -> bytes:
    url = f"https://{config.AZURE_SPEECH_REGION}.tts.speech.microsoft.com/cognitiveservices/v1"
    ssml = (f"<speak version='1.0' xml:lang='ar-LB'><voice name='ar-LB-RamiNeural'>"
            f"{escape(text)}</voice></speak>")
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(url, content=ssml.encode("utf-8"), headers={
            "Ocp-Apim-Subscription-Key": config.AZURE_SPEECH_KEY,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": "raw-24khz-16bit-mono-pcm",
            "User-Agent": "WorldVoiceAI"})
        r.raise_for_status()
        return r.content


async def production_azure(text: str) -> bytes:
    pcm = bytearray()
    async for piece in p.stream_pcm(text, "ar-LB", provider="azure"):
        pcm.extend(piece)
    return bytes(pcm)


async def main() -> None:
    config.ASR_HEDGE_AFTER_SECONDS = 0
    totals = {"plain": [], "aliases": []}
    for sentence in SENTENCES:
        print(f"\n{sentence}")
        for label, synth in (("plain", plain_azure), ("aliases", production_azure)):
            heard = (await w.transcribe_bytes(wav(await synth(sentence)), "audio/wav"))["transcript"]
            score = similarity(sentence, heard)
            totals[label].append(score)
            print(f"  {label:8s} {score:.2f}  heard: {heard}")
            await asyncio.sleep(3.2)        # Groq: 20 requests/minute
    await w.close_groq_client()
    import tts_service
    await tts_service.close_http_client()

    mean = {k: sum(v) / len(v) for k, v in totals.items()}
    better = sum(a >= b for a, b in zip(totals["aliases"], totals["plain"]))
    print(f"\nmean similarity: plain {mean['plain']:.3f} -> aliases {mean['aliases']:.3f}; "
          f"aliases equal or better in {better}/{len(SENTENCES)} sentences")
    sys.exit(0 if mean["aliases"] >= mean["plain"] else 1)


if __name__ == "__main__":
    asyncio.run(main())
