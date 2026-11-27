"""
Listen to and benchmark natural-voice providers side by side.

For every configured provider/voice, synthesizes the SAME
answers (Lebanese, English, French, mixed), measures time to
first audio and generation speed, and writes a local listening
page:  backend/voice_compare/index.html  (open it in a browser).

The approved Fola reference (saved recording) is always included
at zero quota. Live Gemini costs 4 requests, so it is only run
with --with-gemini.

Configure providers in backend/.env first (all free tiers):
  AZURE_SPEECH_KEY=...         AZURE_SPEECH_REGION=westeurope
  ELEVENLABS_API_KEY=...       ELEVENLABS_VOICE_ID=...
  ELEVENLABS_COMPARE_VOICES=id1,id2     (optional, extra voices)

Usage: .venv\\Scripts\\python compare_voices.py [--with-gemini]
"""

import asyncio
import html
import os
import shutil
import sys
import time
import wave
from pathlib import Path

import config
import tts_providers
import tts_service

OUT = Path(__file__).resolve().parent / "voice_compare"

SAMPLES = [
    ("ar-LB", "Lebanese",
     "في قدامك لابتوب مفتوح عالطاولة، والشاشة شغّالة. حدّ اللابتوب عاليمين في كباية قهوة وتلفون أسود. "
     "مش واضح إذا في شي مكتوب عالورقة، فيك تقرّب الكاميرا شوي؟"),
    ("en", "English",
     "In front of you there is an open laptop on a wooden table. To its right are a coffee mug and a "
     "black phone. I can't read the paper clearly; could you move the camera a little closer?"),
    ("fr", "French",
     "Devant vous, il y a un ordinateur portable ouvert sur une table en bois. À droite, une tasse de "
     "café et un téléphone noir. Je ne peux pas lire la feuille ; pouvez-vous rapprocher la caméra ?"),
    ("ar-LB", "Mixed (Lebanese + English)",
     "في قدامك laptop مفتوح، وعالـ screen في email مفتوح. حدّو في phone أسود و charger أبيض."),
]


def variants() -> list[tuple[str, str, dict]]:
    """(provider, label, settings) for every voice to compare."""
    found = []
    if config.AZURE_SPEECH_KEY and config.AZURE_SPEECH_REGION:
        found.append(("azure", "Azure ar-LB-RamiNeural (male)", {"WV_AZURE_VOICE_AR_LB": "ar-LB-RamiNeural"}))
        found.append(("azure", "Azure ar-LB-LaylaNeural (female)", {"WV_AZURE_VOICE_AR_LB": "ar-LB-LaylaNeural"}))
    if config.ELEVENLABS_API_KEY and config.ELEVENLABS_VOICE_ID:
        voices = [config.ELEVENLABS_VOICE_ID] + [
            v.strip() for v in os.getenv("ELEVENLABS_COMPARE_VOICES", "").split(",") if v.strip()
        ]
        for voice in dict.fromkeys(voices):
            for model in ("eleven_multilingual_v2", "eleven_flash_v2_5"):
                found.append(("elevenlabs", f"ElevenLabs {voice[:8]} {model}", {"voice": voice, "model": model}))
    if "--with-gemini" in sys.argv:
        found.append(("gemini", f"Gemini Fola ({config.TTS_MODEL}) LIVE", {}))
    return found


async def synthesize(provider: str, settings: dict, language: str, text: str) -> dict:
    # Apply per-variant settings.
    if provider == "azure":
        config.AZURE_VOICE_OVERRIDES["ar-LB"] = settings["WV_AZURE_VOICE_AR_LB"]
    if provider == "elevenlabs":
        config.ELEVENLABS_VOICE_ID = settings["voice"]
        config.ELEVENLABS_MODEL = settings["model"]

    start = time.perf_counter()
    first = None
    pcm = bytearray()
    try:
        async for piece in tts_providers.stream_pcm(text, language, provider=provider):
            first = first or time.perf_counter() - start
            pcm.extend(piece)
    except tts_service.TTSError as error:
        return {"error": f"{error.status} {error.message}"}
    total = time.perf_counter() - start
    audio = len(pcm) / tts_service.BYTES_PER_SECOND
    return {
        "pcm": bytes(pcm),
        "first_audio_s": round(first or 0, 2),
        "total_s": round(total, 2),
        "audio_s": round(audio, 2),
        "rate": round(audio / max(0.01, total - (first or 0)), 2),
    }


def write_wav(path: Path, pcm: bytes) -> None:
    with wave.open(str(path), "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(tts_service.SAMPLE_RATE)
        file.writeframes(pcm)


async def main() -> None:
    OUT.mkdir(exist_ok=True)
    rows = []

    reference = Path(__file__).resolve().parent / "test_audio" / "fola_sample_ar-LB.wav"
    if reference.exists():
        shutil.copy(reference, OUT / "reference_fola_lebanese.wav")

    found = variants()
    if not found:
        print("No alternative provider is configured yet. Add keys to backend/.env (see docstring).")

    for index, (provider, label, settings) in enumerate(found):
        cells = []
        for s_index, (language, sample_label, text) in enumerate(SAMPLES):
            result = await synthesize(provider, settings, language, text)
            name = f"v{index}_s{s_index}.wav"
            if "pcm" in result:
                write_wav(OUT / name, result.pop("pcm"))
                result["file"] = name
            cells.append((sample_label, result))
            print(f"{label:45s} {sample_label:28s} {result}", flush=True)
        rows.append((label, cells))

    await tts_service.close_http_client()

    # ---------- listening page ----------
    parts = [
        "<!doctype html><meta charset='utf-8'><title>World Voice AI - voice comparison</title>",
        "<style>body{font-family:system-ui,sans-serif;margin:24px;max-width:1200px}"
        "table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:8px;vertical-align:top}"
        "th{background:#f3f3f3}small{color:#555}audio{width:230px}</style>",
        "<h1>Voice comparison</h1>",
        "<p>Same text for every voice. <b>first</b> = time to first audio, "
        "<b>speed</b> = audio seconds generated per second (above 1.0 = faster than playback).</p>",
        "<h2>Approved reference: Gemini Fola (saved recording, Lebanese)</h2>",
        "<audio controls src='reference_fola_lebanese.wav'></audio>",
        "<h2>Candidates</h2><table><tr><th>Voice</th>"
        + "".join(f"<th>{html.escape(s[1])}</th>" for s in SAMPLES) + "</tr>",
    ]
    for label, cells in rows:
        parts.append(f"<tr><td><b>{html.escape(label)}</b></td>")
        for _, result in cells:
            if "file" in result:
                parts.append(
                    f"<td><audio controls src='{result['file']}'></audio><br>"
                    f"<small>first {result['first_audio_s']} s | speed x{result['rate']} | "
                    f"{result['audio_s']} s audio</small></td>"
                )
            else:
                parts.append(f"<td><small>{html.escape(result.get('error', 'failed'))}</small></td>")
        parts.append("</tr>")
    parts.append("</table><h2>Texts</h2><ol>")
    parts += [f"<li><b>{html.escape(l)}</b>: {html.escape(t)}</li>" for _, l, t in SAMPLES]
    parts.append("</ol>")
    (OUT / "index.html").write_text("\n".join(parts), encoding="utf-8")
    print(f"\nOpen: {OUT / 'index.html'}")


if __name__ == "__main__":
    asyncio.run(main())
