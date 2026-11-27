"""
End-to-end response time of the Live Assistant, measured like the phone.

For each spoken question (real audio, generated once with Azure voices):
  t0 = question audio sent (the moment the user finished speaking)
  -> /ask: server-side speech recognition + current view + memory + Gemini
  -> speech job ready -> audio streamed -> 1.0 s buffered (ExoPlayer start)
  = time to FIRST SOUND

Uses the real configuration from backend/.env (voices: WV_TTS_PROVIDERS).
Usage: .venv\\Scripts\\python test_live_latency.py
Quota: ~6 Azure TTS (question audio, cached) + per question: 1-2 Groq,
1 Gemini answer, 1 embedding, 1 TTS answer.
"""

import asyncio
import os
import socket
import statistics
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
from test_helpers import foreign_script, wipe

HERE = Path(__file__).resolve().parent
QDIR = HERE / "test_audio" / "questions"

QUESTIONS = [
    ("q_en_now", "en-US-AndrewNeural", "en-US", "What does the sign in front of me say?"),
    ("q_en_past", "en-US-AndrewNeural", "en-US", "What was written on the door I just passed?"),
    ("q_lb_now", "ar-LB-RamiNeural", "ar-LB", "شو مكتوب عاللافتة اللي قدامي؟"),
    ("q_lb_past", "ar-LB-RamiNeural", "ar-LB", "شو كان مكتوب عالباب يلي مرقنا حدّو؟"),
    ("q_fr_now", "fr-FR-HenriNeural", "fr-FR", "Qu'est-ce qui est écrit sur le panneau devant moi ?"),
    ("q_en_exit", "en-US-AndrewNeural", "en-US", "Where is the emergency exit?"),
]


async def make_questions() -> None:
    QDIR.mkdir(parents=True, exist_ok=True)
    url = f"https://{config.AZURE_SPEECH_REGION}.tts.speech.microsoft.com/cognitiveservices/v1"
    async with httpx.AsyncClient(timeout=30) as c:
        for name, voice, locale, text in QUESTIONS:
            path = QDIR / f"{name}.wav"
            if path.exists():
                continue
            ssml = (f"<speak version='1.0' xml:lang='{locale}'><voice name='{voice}'>"
                    f"{escape(text)}</voice></speak>")
            r = await c.post(url, content=ssml.encode("utf-8"), headers={
                "Ocp-Apim-Subscription-Key": config.AZURE_SPEECH_KEY,
                "Content-Type": "application/ssml+xml",
                "X-Microsoft-OutputFormat": "raw-16khz-16bit-mono-pcm", "User-Agent": "WorldVoiceAI"})
            r.raise_for_status()
            with wave.open(str(path), "wb") as f:
                f.setnchannels(1)
                f.setsampwidth(2)
                f.setframerate(16000)
                f.writeframes(r.content)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def first_sound(c, base, speech, t0) -> tuple[float | None, str]:
    """Phone behaviour: poll every 350 ms, open stream when ready, start at 1.0 s buffered."""
    while True:
        s = (await c.get(base + speech["status_path"])).json()
        if s["error"] and not s["audio_seconds"]:
            return None, f"speech error {s['error']['status']}"
        if s["ready_to_start"] or s["done"]:
            break
        await asyncio.sleep(0.2)
    received = 0
    async with c.stream("GET", base + speech["audio_path"]) as r:
        async for chunk in r.aiter_bytes():
            received += len(chunk)
            if received - 44 >= 48000 or (s["done"] and received - 44 >= s["audio_seconds"] * 48000 - 2):
                return time.perf_counter() - t0, s.get("provider", "?")
    return time.perf_counter() - t0, s.get("provider", "?")


async def main():
    await make_questions()
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    store = HERE / "storage" / "memory_latency_test"
    wipe(store)                                  # never measure on a previous run's memory
    log = open(HERE / "test_latency_server.log", "w", encoding="utf-8")
    server = subprocess.Popen(
        [str(HERE / ".venv" / "Scripts" / "python.exe"), "-m", "uvicorn", "main:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=HERE, stdout=log, stderr=subprocess.STDOUT,
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "WV_MEMORY_DIR": str(store),
             "WV_MEMORY_ANALYZE_EVERY": "6", "WV_DEBUG_ASK": "1"})
    rows = []
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
            offer = await phone.createOffer()
            await phone.setLocalDescription(offer)
            a = (await c.post(base + "/stream/publish",
                              json={"sdp": phone.localDescription.sdp, "type": phone.localDescription.type})).json()
            await phone.setRemoteDescription(RTCSessionDescription(sdp=a["sdp"], type=a["type"]))
            print("walking past EXIT -> door ROOM 12 -> wall -> CAFE ...", flush=True)
            await asyncio.sleep(30)
            frames = (await c.get(base + "/memory/frames")).json()["frames"]
            stats = (await c.get(base + "/memory/stats")).json()
            print("memory after the walk: " + " | ".join(
                f"{int(f['age_s'])}s {f['status']} {f.get('text') or '-'} ({f['keep_reason']})" for f in frames), flush=True)
            print(f"selection {stats['selection']} | analysis {stats['analysis']}", flush=True)

            for name, _, _, text in QUESTIONS:
                await asyncio.sleep(4)                       # user pause between questions
                audio = (QDIR / f"{name}.wav").read_bytes()
                t0 = time.perf_counter()
                r = await c.post(base + "/ask", data={"question": "", "speak": "true"},
                                 files={"question_audio": (f"{name}.wav", audio, "audio/wav")})
                answered = time.perf_counter() - t0
                if r.status_code != 200:
                    rows.append((name, None, None, f"HTTP {r.status_code} {r.text[:80]}"))
                    print(f"{name:10s} FAILED HTTP {r.status_code} {r.text[:100]}", flush=True)
                    continue
                d = r.json()
                sound, provider = await first_sound(c, base, d["speech"], t0) if d.get("speech") else (None, "-")
                if sound and d.get("qa_id"):          # what the phone reports to the dashboard
                    await c.post(base + f"/ask/{d['qa_id']}/first-word", json={"first_word_s": sound})
                rows.append((name, answered, sound, d["answer"]))
                tm = d["timings"]
                print(f"{name:10s} answer {answered:4.2f} s (asr {tm.get('asr')}, retrieval {tm.get('retrieval')}, "
                      f"gemini {tm.get('gemini')}, images {tm.get('images')}) | FIRST SOUND "
                      f"{(f'{sound:4.2f} s' if sound else 'n/a')} via {provider} | [{d['evidence']}] {d['answer'][:70]}",
                      flush=True)
                print(f"           heard: {d.get('question_heard')!r} | retrieval: {d.get('retrieval')}", flush=True)
            qa = (await c.get(base + "/memory/qa")).json()["qa"]
            print(f"dashboard Q&A rows with the measured first word: "
                  f"{sum(1 for q in qa if q.get('first_word_s'))}/{len(qa)}", flush=True)
            await phone.close()
    finally:
        server.terminate()
        server.wait(10)
        log.close()
        wipe(store)

    odd = [(r[0], sorted(foreign_script(r[3]))) for r in rows if r[1] and foreign_script(r[3])]
    print(f"answers with foreign-script letters: {len(odd)} {odd if odd else ''}")
    sounds = [r[2] for r in rows if r[2]]
    answers = [r[1] for r in rows if r[1]]
    print(f"\nquestions: {len(rows)}, answered: {len(answers)}, spoken: {len(sounds)}")
    if answers:
        print(f"answer ready : median {statistics.median(answers):.2f} s, max {max(answers):.2f} s")
    if sounds:
        within = sum(s <= 5.0 for s in sounds)
        print(f"FIRST SOUND  : median {statistics.median(sounds):.2f} s, max {max(sounds):.2f} s, "
              f"within 5 s: {within}/{len(sounds)}")
    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
