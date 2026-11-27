"""
Rami naturalness investigation (comparison samples only; the app is NOT changed).

Isolates the cause of disconnected, word-by-word Arabic speech:
  1. our processing  : raw Azure REST vs app provider code vs full speech job
                       (byte comparison)
  2. word endings    : Lebanese pausal endings (sukun on final letters)
  3. punctuation     : same text without commas
  4. speaking rate   : prosody rate +8 %
  5. the voice model : Layla, Syrian Laith, Jordanian Taim, Turbo multilingual

Measures speech directly (NOT via transcription):
  * micro-pauses between words per second of speech (disconnectedness)
  * pitch variation in semitones (intonation / melody)
  * speaking rate (characters per second of voiced speech)
and compares every variant with the approved Fola recording of the SAME text.

Output: voice_compare/rami/*.wav and voice_compare/rami/index.html
Usage:  .venv\\Scripts\\python compare_rami_naturalness.py   (~30 Azure requests, free F0)
"""

import asyncio
import html
import math
import re
import statistics
import wave
from pathlib import Path
from xml.sax.saxutils import escape

import httpx

import config
import speech_jobs
import tts_providers

HERE = Path(__file__).resolve().parent
OUT = HERE / "voice_compare" / "rami"
URL = f"https://{config.AZURE_SPEECH_REGION}.tts.speech.microsoft.com/cognitiveservices/v1"
RATE = 24000

FOLA_TEXT = (
    "في قدامك لابتوب مفتوح عالطاولة، والشاشة شغّالة وعليها صفحة كتابة. "
    "حدّ اللابتوب عاليمين في كباية قهوة وتلفون أسود. "
    "ورا الطاولة في شبّاك والضو جايي منّو، فالصورة واضحة منيحة. "
    "ما في شي مكتوب واضح بقدر إقراه من هالبعد."
)
SENTENCES = [
    ("fola_text", FOLA_TEXT),
    ("everyday", "هلّق في قدامك باب خشب، وعاليمين في كباية مي. خلّيك ماشي شوي لقدّام بس انتبه عالدرج."),
]


# ---------------- text variants ----------------

def pausal_endings(text: str) -> str:
    """Spoken Lebanese drops formal case endings: put sukun on each word's last letter."""
    def fix(word: re.Match) -> str:
        w = word.group(0)
        if len(w) < 3 or w[-1] in "اوىيةءآإأ" or w[-1] in "ًٌٍَُِّْ":
            return w
        return w + "ْ"
    return re.sub(r"[ء-يّ]+", fix, text)


def no_commas(text: str) -> str:
    return text.replace("،", "").replace(",", "")


def ssml(voice: str, locale: str, text: str, rate: str | None = None) -> str:
    body = escape(text)
    if rate:
        body = f"<prosody rate='{rate}'>{body}</prosody>"
    return (f"<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='{locale}'>"
            f"<voice name='{voice}'>{body}</voice></speak>")


VARIANTS = [
    ("rami_raw", "Rami, raw Azure (no processing)", lambda t: ssml("ar-LB-RamiNeural", "ar-LB", t)),
    ("rami_endings", "Rami + Lebanese pausal endings", lambda t: ssml("ar-LB-RamiNeural", "ar-LB", pausal_endings(t))),
    ("rami_nocommas", "Rami, no commas", lambda t: ssml("ar-LB-RamiNeural", "ar-LB", no_commas(t))),
    ("rami_faster", "Rami, rate +8 %", lambda t: ssml("ar-LB-RamiNeural", "ar-LB", t, "+8%")),
    ("rami_rate15", "Rami, rate +15 %", lambda t: ssml("ar-LB-RamiNeural", "ar-LB", t, "+15%")),
    ("rami_rate25", "Rami, rate +25 % (about Fola's tempo)", lambda t: ssml("ar-LB-RamiNeural", "ar-LB", t, "+25%")),
    ("rami_rate25_endings", "Rami, rate +25 % + pausal endings",
     lambda t: ssml("ar-LB-RamiNeural", "ar-LB", pausal_endings(t), "+25%")),
    ("layla", "Layla (ar-LB, female)", lambda t: ssml("ar-LB-LaylaNeural", "ar-LB", t)),
    ("laith_sy", "Laith (Syrian, Levantine)", lambda t: ssml("ar-SY-LaithNeural", "ar-SY", t)),
    ("taim_jo", "Taim (Jordanian, Levantine)", lambda t: ssml("ar-JO-TaimNeural", "ar-JO", t)),
    ("onyx_turbo", "Onyx Turbo multilingual (reads Arabic)",
     lambda t: ssml("en-US-OnyxTurboMultilingualNeural", "ar-SA", t)),
]


# ---------------- audio analysis (no transcription) ----------------

def frames(pcm: bytes, size: int):
    samples = memoryview(pcm).cast("h")
    for start in range(0, len(samples) - size, size):
        yield samples[start:start + size]


def speech_metrics(pcm: bytes) -> dict:
    """Micro-pauses, pitch variation and rate, from the waveform itself."""
    frame = int(RATE * 0.01)                      # 10 ms
    energy = [sum(abs(s) for s in f[::2]) / (len(f) / 2) for f in frames(pcm, frame)]
    if not energy:
        return {}
    peak = sorted(energy)[int(len(energy) * 0.95)] or 1
    voiced = [e > 0.06 * peak for e in energy]
    first = voiced.index(True) if True in voiced else 0
    last = len(voiced) - 1 - voiced[::-1].index(True) if True in voiced else 0
    inner = voiced[first:last + 1]

    gaps, run = [], 0
    for v in inner:
        if not v:
            run += 1
        elif run:
            gaps.append(run)
            run = 0
    micro = [g for g in gaps if 4 <= g <= 25]      # 40-250 ms: gaps between words
    long_ = [g for g in gaps if g > 25]            # > 250 ms: phrase pauses
    speech_s = len(inner) / 100

    # Pitch (F0) by autocorrelation on 40 ms frames, decimated to 8 kHz.
    samples = memoryview(pcm).cast("h")[::3]
    sr, win = 8000, 320
    f0 = []
    for start in range(0, len(samples) - win, win // 2):
        seg = samples[start:start + win]
        e = sum(s * s for s in seg)
        if e < 1e6 * win:
            continue
        best, lag_best = 0.0, 0
        for lag in range(sr // 320, sr // 70):     # 70-320 Hz
            c = sum(seg[i] * seg[i + lag] for i in range(0, win - lag, 2))
            if c > best:
                best, lag_best = c, lag
        if lag_best and best > 0.35 * e / 2:
            f0.append(sr / lag_best)
    semis = [12 * math.log2(f / statistics.median(f0)) for f in f0] if len(f0) > 10 else []
    return {
        "speech_s": round(speech_s, 2),
        "micro_pauses_per_s": round(len(micro) / speech_s, 2) if speech_s else 0,
        "phrase_pauses": len(long_),
        "pitch_variation_st": round(statistics.pstdev(semis), 2) if semis else None,
        "pitch_range_st": round(max(semis) - min(semis), 1) if semis else None,
    }


def read_wav(path: Path) -> bytes:
    with wave.open(str(path), "rb") as f:
        return f.readframes(f.getnframes())


def write_wav(path: Path, pcm: bytes) -> None:
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(RATE)
        f.writeframes(pcm)


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    headers = {"Ocp-Apim-Subscription-Key": config.AZURE_SPEECH_KEY,
               "Content-Type": "application/ssml+xml",
               "X-Microsoft-OutputFormat": "raw-24khz-16bit-mono-pcm", "User-Agent": "WorldVoiceAI"}
    rows = []

    fola = read_wav(HERE / "test_audio" / "fola_sample_ar-LB.wav")
    write_wav(OUT / "fola_reference.wav", fola)
    rows.append(("fola_text", "Fola (approved reference)", "fola_reference.wav", speech_metrics(fola),
                 len(FOLA_TEXT)))

    async with httpx.AsyncClient(timeout=40) as client:
        # 1. Is OUR processing changing the audio? (pure Arabic sentence)
        raw = (await client.post(URL, headers=headers,
                                 content=ssml("ar-LB-RamiNeural", "ar-LB", FOLA_TEXT).encode())).content
        app = bytearray()
        async for piece in tts_providers.stream_pcm(FOLA_TEXT, "ar-LB", provider="azure"):
            app.extend(piece)
        config.TTS_PROVIDERS = ["azure"]
        config.TTS_SPLIT = 1
        job = speech_jobs.create_job(FOLA_TEXT, "ar-LB")
        await job.task
        same_provider = bytes(app) == raw
        same_job = bytes(job.pcm) == raw
        print(f"processing check: raw {len(raw)} B | app provider identical={same_provider} | "
              f"speech job identical={same_job}")
        # Azure output can vary slightly between requests: also compare sizes.
        processing_note = (
            f"raw Azure {len(raw) / 48000:.2f} s; app provider {'IDENTICAL' if same_provider else f'{len(app) / 48000:.2f} s'}; "
            f"speech job {'IDENTICAL' if same_job else f'{len(job.pcm) / 48000:.2f} s'}")

        for key, text in SENTENCES:
            for name, label, build in VARIANTS:
                await asyncio.sleep(3.2)                 # F0: 20 requests / minute
                r = await client.post(URL, headers=headers, content=build(text).encode("utf-8"))
                if r.status_code != 200:
                    print(f"{key} {name}: HTTP {r.status_code} {r.text[:100]}")
                    rows.append((key, label, None, {"error": r.status_code}, len(text)))
                    continue
                file = f"{key}_{name}.wav"
                write_wav(OUT / file, r.content)
                m = speech_metrics(r.content)
                rows.append((key, label, file, m, len(text)))
                print(f"{key:9s} {label:42s} {m}", flush=True)

    await tts_providers.tts_service.close_http_client()

    # ---------------- listening page ----------------
    parts = [
        "<!doctype html><meta charset='utf-8'><title>Rami naturalness</title>",
        "<style>body{font-family:system-ui,sans-serif;margin:24px;max-width:1100px}"
        "table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:6px}"
        "th{background:#f3f3f3}audio{width:260px}.n{text-align:right}</style>",
        "<h1>Rami naturalness: where does the choppiness come from?</h1>",
        f"<p><b>Our processing:</b> {html.escape(processing_note)}</p>",
        "<p><b>chars/s</b>: speaking tempo (Fola 12.8; Rami raw 9.9 = about 23 % slower, "
        "the clearest measured difference). <b>Micro-pauses/s</b>: short silences (natural "
        "speech has them too: Fola 1.49). <b>Pitch variation</b>: melody in semitones "
        "(Fola 4.1). Numbers only guide your ears; listening decides.</p>",
    ]
    for key, text in SENTENCES:
        parts.append(f"<h2>{html.escape(text)}</h2><table><tr><th>Voice / variant</th><th>Listen</th>"
                     "<th>chars/s</th><th>micro-pauses /s</th><th>phrase pauses</th>"
                     "<th>pitch variation (st)</th><th>pitch range (st)</th></tr>")
        for k, label, file, m, chars in rows:
            if k != key:
                continue
            if file is None:
                parts.append(f"<tr><td>{html.escape(label)}</td><td colspan=6>not available ({m})</td></tr>")
                continue
            tempo = round(chars / m["speech_s"], 1) if m.get("speech_s") else None
            parts.append(
                f"<tr><td>{html.escape(label)}</td><td><audio controls src='{file}'></audio></td>"
                f"<td class=n>{tempo}</td>"
                f"<td class=n>{m.get('micro_pauses_per_s')}</td><td class=n>{m.get('phrase_pauses')}</td>"
                f"<td class=n>{m.get('pitch_variation_st')}</td><td class=n>{m.get('pitch_range_st')}</td></tr>")
        parts.append("</table>")
    (OUT / "index.html").write_text("\n".join(parts), encoding="utf-8")
    print(f"\nListening page: {OUT / 'index.html'}")


if __name__ == "__main__":
    asyncio.run(main())
