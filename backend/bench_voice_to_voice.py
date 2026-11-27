"""
Voice-to-voice benchmark in three operating scenarios: from the moment the
user stops speaking to the FIRST AUDIBLE WORD (phone rule: playback starts
with 1.0 s of audio buffered), plus answer correctness.

  normal   : full chain (Gemini first; override the first model with --primary)
  gemini_out: no Gemini model usable -> Groq (Whisper + Qwen 3.8 27B)
  offline  : no internet (all cloud requests go to a dead proxy) -> local
             faster-whisper + local Qwen + Piper. Needs start_local_ai.ps1 running.

Usage: .venv\\Scripts\\python bench_voice_to_voice.py normal|gemini_out|offline [--primary MODEL]
Same spoken questions in every scenario (cached real audio).
"""

import asyncio
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

import httpx
from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription

from test_assistant_flow import Walk
from test_helpers import wipe
from test_live_latency import QDIR, first_sound, free_port, make_questions

HERE = Path(__file__).resolve().parent
QUESTIONS = [("q_en_now", "CAFE"), ("q_lb_now", "CAFE"), ("q_fr_now", "CAFE"),
             ("q_en_past", "12"), ("q_lb_past", "12")]


def correct(answer: str, expected: str) -> bool:
    a = answer.upper().replace("كافيه", "CAFE").replace("١٢", "12")
    return expected in a


def scenario_env(name: str, primary: str | None) -> dict:
    env = {}
    if name == "normal" and primary:
        env["WV_VISION_MODELS"] = primary
    if name == "gemini_out":
        env["WV_VISION_MODELS"] = "gemini-unavailable-test"       # no Gemini model can answer
        env["WV_GEMINI_RPD_GEMINI_UNAVAILABLE_TEST"] = "0"
    if name == "offline":
        dead = "http://127.0.0.1:9"                               # nothing listens: like no internet
        env.update({"HTTPS_PROXY": dead, "HTTP_PROXY": dead, "https_proxy": dead, "http_proxy": dead,
                    "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
                    # Piper only as the measurement voice (the app's voices are unchanged);
                    # no local background analysis: 0.8B misreads Arabic (bench_local_vlm.py).
                    "WV_TTS_PROVIDERS": "gemini,azure,piper", "WV_LOCAL_BACKGROUND": "0"})
    return env


async def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else "normal"
    primary = sys.argv[sys.argv.index("--primary") + 1] if "--primary" in sys.argv else None
    await make_questions()
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    store = HERE / "storage" / f"memory_v2v_{name}"
    wipe(store)
    log = open(HERE / f"bench_v2v_{name}.log", "w", encoding="utf-8")
    server = subprocess.Popen(
        [str(HERE / ".venv" / "Scripts" / "python.exe"), "-m", "uvicorn", "main:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=HERE, stdout=log, stderr=subprocess.STDOUT,
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "WV_MEMORY_DIR": str(store),
             "WV_QUOTA_STATE": str(store / "quota_state.json"), "WV_MEMORY_ANALYZE_EVERY": "6",
             **scenario_env(name, primary)})
    rows = []
    try:
        async with httpx.AsyncClient(timeout=120) as c:
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
            await asyncio.sleep(40 if name == "offline" else 32)
            frames = (await c.get(base + "/memory/frames")).json()["frames"]
            print(f"[{name}] memory: " + " | ".join(f"{f['status']} {f.get('text') or '-'}" for f in frames), flush=True)
            for q, expected in QUESTIONS:
                await asyncio.sleep(20 if name == "gemini_out" else 6)   # Groq: 7,000 tokens/min
                t0 = time.perf_counter()
                r = await c.post(base + "/ask", data={"question": "", "speak": "true"},
                                 files={"question_audio": (f"{q}.wav", (QDIR / f"{q}.wav").read_bytes(), "audio/wav")})
                ready = time.perf_counter() - t0
                if r.status_code != 200:
                    rows.append((q, False, ready, None, "-", r.text[:60]))
                    print(f"{q:10s} HTTP {r.status_code} {r.text[:80]}", flush=True)
                    continue
                d = r.json()
                sound, voice = await first_sound(c, base, d["speech"], t0) if d.get("speech") else (None, "-")
                ok = correct(d["answer"], expected)
                rows.append((q, ok, ready, sound, voice, d["answer"]))
                print(f"{'OK  ' if ok else 'BAD '}{q:10s} answer {ready:5.2f} s | first word "
                      f"{(f'{sound:5.2f} s' if sound else '  n/a')} | model {d['timings'].get('model')} | voice {voice} "
                      f"| {d['answer'][:70]}", flush=True)
            await phone.close()
    finally:
        server.terminate()
        server.wait(10)
        log.close()
        wipe(store)
    sounds = [r[3] for r in rows if r[3]]
    print(f"\n[{name}] correct {sum(r[1] for r in rows)}/{len(rows)} | first word median "
          f"{statistics.median(sounds):.2f} s, slowest {max(sounds):.2f} s, within 5 s "
          f"{sum(s <= 5 for s in sounds)}/{len(sounds)}" if sounds else f"\n[{name}] no answer was spoken")


if __name__ == "__main__":
    asyncio.run(main())
