"""
Answer language with real spoken questions, native and ACCENTED.

The phone is simulated (walk: EXIT -> door ROOM 12 -> wall -> CAFE); each
question is real audio generated once with Azure voices (cached), asked
exactly like the phone does (/ask with the recording only).

Checked per answer:
  * English / French question -> answer in that language, no Arabic words;
  * formal Arabic (MSA) -> formal Arabic (answer_language 'ar', no Lebanese words);
  * Lebanese -> Lebanese ('ar-LB');
  * mixed sentence -> one of the languages the user used, nothing added;
  * content right (CAFE now / ROOM 12 for the door passed).

Usage: .venv\\Scripts\\python bench_language.py
Quota: Azure TTS for new question audio (once), ~4 Gemini analysis + 1 answer per question.
"""

import asyncio
import os
import re
import subprocess
import sys
import time
import wave
from pathlib import Path
from xml.sax.saxutils import escape

import httpx
from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription

import config
from test_assistant_flow import Walk
from test_helpers import wipe
from test_live_latency import QDIR, free_port, make_questions

HERE = Path(__file__).resolve().parent

# Single-language Azure voices cannot speak another language (an Arabic voice
# reading French produced Arabic-sounding nonsense, verified with Whisper), so
# accented French comes from a multilingual voice; Arabic-accented French is
# left to the phone test with a real speaker.
# name, ssml voice, locale, text (or SSML body), expected language(s), expected content
QUESTIONS = [
    ("q_en_now", None, None, None, {"en"}, "CAFE"),
    ("l_en_arabic_accent", "ar-LB-LaylaNeural", "ar-LB", "What is written on the sign in front of me?", {"en"}, "CAFE"),
    ("l_en_french_accent", "fr-FR-DeniseNeural", "fr-FR", "What does the sign in front of me say?", {"en"}, "CAFE"),
    ("q_fr_now", None, None, None, {"fr"}, "CAFE"),
    # French from an English-native multilingual speaker (non-native French).
    ("l_fr_nonnative_past", "en-US-AndrewMultilingualNeural", "en-US",
     "<lang xml:lang='fr-FR'>Qu'est-ce qui était écrit sur la porte que je viens de passer ?</lang>", {"fr"}, "12"),
    # A slow, hesitant French speaker.
    ("l_fr_slow_now", "fr-FR-DeniseNeural", "fr-FR",
     "<prosody rate='-30%'>Euh... qu'est-ce qui est écrit, sur le panneau, devant moi ?</prosody>", {"fr"}, "CAFE"),
    ("l_msa_now", "ar-SA-HamedNeural", "ar-SA", "ماذا كُتِبَ على اللافتة التي أمامي؟", {"ar"}, "CAFE"),
    ("l_msa_past_egypt", "ar-EG-SalmaNeural", "ar-EG", "ماذا كان مكتوبًا على الباب الذي مررتُ بجانبه للتو؟", {"ar"}, "12"),
    ("q_lb_now", None, None, None, {"ar-LB"}, "CAFE"),
    ("q_lb_past", None, None, None, {"ar-LB"}, "12"),
    ("l_mix_en_lb", "en-US-AndrewMultilingualNeural", "en-US",
     "Can you tell me <lang xml:lang='ar-EG'>شو مكتوب عاللافتة اللي قدامي؟</lang>", {"en", "ar-LB", "ar"}, "CAFE"),
    ("l_mix_fr_en", "en-US-AvaMultilingualNeural", "fr-FR",
     "<lang xml:lang='fr-FR'>Est-ce que tu peux me dire</lang> what the sign in front of me says?", {"fr", "en"}, "CAFE"),
]

LEBANESE = re.compile(r"(^|\s)(شو|هيدا|هيدي|مش|هلّق|هلق|شي|يلّي|يلي|قدّامك|قدامك|مرقت|مرقنا|عال\S+)(?=\s|$|[،.؟])")


async def make_language_questions() -> None:
    url = f"https://{config.AZURE_SPEECH_REGION}.tts.speech.microsoft.com/cognitiveservices/v1"
    async with httpx.AsyncClient(timeout=30) as c:
        for name, voice, locale, text, _, _ in QUESTIONS:
            path = QDIR / f"{name}.wav"
            if voice is None or path.exists():
                continue
            body = text if text.lstrip().startswith("<") or "<lang" in text else escape(text)
            ssml = f"<speak version='1.0' xml:lang='{locale}' xmlns:mstts='http://www.w3.org/2001/mstts'>" \
                   f"<voice name='{voice}'>{body}</voice></speak>"
            r = await c.post(url, content=ssml.encode("utf-8"), headers={
                "Ocp-Apim-Subscription-Key": config.AZURE_SPEECH_KEY, "Content-Type": "application/ssml+xml",
                "X-Microsoft-OutputFormat": "raw-16khz-16bit-mono-pcm", "User-Agent": "WorldVoiceAI"})
            r.raise_for_status()
            with wave.open(str(path), "wb") as f:
                f.setnchannels(1)
                f.setsampwidth(2)
                f.setframerate(16000)
                f.writeframes(r.content)


def judge(d: dict, expected_langs: set[str], content: str) -> tuple[bool, str]:
    answer = d["answer"]
    problems = []
    if d["answer_language"] not in expected_langs:
        problems.append(f"language {d['answer_language']} not in {sorted(expected_langs)}")
    arabic_words = [w for w in answer.split() if re.search(r"[؀-ۿ]", w)]
    if expected_langs <= {"en", "fr"} and arabic_words:
        problems.append(f"Arabic words in a {'/'.join(sorted(expected_langs))} answer: {arabic_words}")
    if d["answer_language"] == "ar" and LEBANESE.search(answer):
        problems.append(f"Lebanese words in a formal-Arabic answer: {LEBANESE.findall(answer)}")
    a = answer.upper().replace("١٢", "12").replace("كافيه", "CAFE").replace("كافيه", "CAFE")
    if content not in a:
        problems.append(f"content: expected {content}")
    return not problems, "; ".join(problems)


async def main() -> None:
    await make_questions()
    await make_language_questions()
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    store = HERE / "storage" / "memory_language_bench"
    wipe(store)
    log = open(HERE / "bench_language_server.log", "w", encoding="utf-8")
    server = subprocess.Popen(
        [str(HERE / ".venv" / "Scripts" / "python.exe"), "-m", "uvicorn", "main:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=HERE, stdout=log, stderr=subprocess.STDOUT,
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "WV_MEMORY_DIR": str(store),
             "WV_MEMORY_ANALYZE_EVERY": "6"})
    good, total, gemini_times, fixes = 0, 0, [], 0
    try:
        async with httpx.AsyncClient(timeout=90) as c:
            for _ in range(80):
                try:
                    await c.get(base + "/health")
                    break
                except httpx.HTTPError:
                    await asyncio.sleep(0.25)
            phone = RTCPeerConnection(RTCConfiguration(iceServers=[]))
            phone.addTrack(Walk())
            await phone.setLocalDescription(await phone.createOffer())
            a = (await c.post(base + "/stream/publish", json={
                "sdp": phone.localDescription.sdp, "type": phone.localDescription.type})).json()
            await phone.setRemoteDescription(RTCSessionDescription(sdp=a["sdp"], type=a["type"]))
            await asyncio.sleep(32)
            for name, voice, _, _, langs, content in QUESTIONS:
                await asyncio.sleep(4.5)                 # stay under 15 Gemini requests / minute
                r = await c.post(base + "/ask", data={"question": "", "device_id": "phone"},
                                 files={"question_audio": ("q.wav", (QDIR / f"{name}.wav").read_bytes(), "audio/wav")})
                total += 1
                if r.status_code != 200:
                    print(f"FAIL {name:20s} HTTP {r.status_code} {r.text[:80]}", flush=True)
                    continue
                d = r.json()
                ok, why = judge(d, langs, content)
                good += ok
                gemini_times.append(d["timings"].get("gemini", 0) + d["timings"].get("language_fix", 0))
                fixes += "language_fix" in d["timings"]
                print(f"{'OK  ' if ok else 'FAIL'} {name:20s} heard {d['question_heard'][:45]!r} | spoken "
                      f"{d['question_language']} -> answer {d['answer_language']}"
                      f"{' (corrected)' if 'language_fix' in d['timings'] else ''} | {d['answer'][:70]}"
                      f"{'' if ok else '  <-- ' + why}", flush=True)
            await phone.close()
    finally:
        server.terminate()
        server.wait(10)
        log.close()
        wipe(store)
    print(f"\n{good}/{total} answers in the right language with the right content; "
          f"language corrections needed: {fixes}; Gemini time median "
          f"{sorted(gemini_times)[len(gemini_times) // 2]:.2f} s" if gemini_times else "")
    sys.exit(0 if good == total else 1)


if __name__ == "__main__":
    asyncio.run(main())
