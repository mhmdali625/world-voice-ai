"""
Reliability of spoken questions about the PAST (fast path), repeated.

One scripted walk (EXIT -> door ROOM 12 -> wall -> CAFE), then each
spoken past question is asked N times; prints what Gemini heard and
answered, and the share of correct answers (must mention ROOM 12).

Usage: .venv\\Scripts\\python bench_past_questions.py [N]
Quota: ~4 Gemini analysis requests + 2 x N answers (no TTS).
"""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import httpx
from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription

from test_assistant_flow import Walk
from test_helpers import wipe
from test_live_latency import QDIR, free_port, make_questions

HERE = Path(__file__).resolve().parent
N = int(sys.argv[1]) if len(sys.argv) > 1 else 5
QUESTIONS = ["q_lb_past", "q_en_past"]


def correct(answer: str) -> bool:
    a = answer.upper().replace("١٢", "12").replace("روم", "ROOM")
    return "12" in a and "CAFE" not in a and "كافيه" not in answer


async def main() -> None:
    await make_questions()
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    store = HERE / "storage" / "memory_past_bench"
    wipe(store)
    log = open(HERE / "bench_past_server.log", "w", encoding="utf-8")
    server = subprocess.Popen(
        [str(HERE / ".venv" / "Scripts" / "python.exe"), "-m", "uvicorn", "main:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=HERE, stdout=log, stderr=subprocess.STDOUT,
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "WV_MEMORY_DIR": str(store),
             "WV_MEMORY_ANALYZE_EVERY": "6", "WV_DEBUG_ASK": "1"})
    score = {q: 0 for q in QUESTIONS}
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
            frames = (await c.get(base + "/memory/frames")).json()["frames"]
            print("memory: " + " | ".join(f"{f['status']} {f.get('text') or '-'}" for f in frames), flush=True)
            for i in range(N):
                for q in QUESTIONS:
                    await asyncio.sleep(4)
                    r = await c.post(base + "/ask", data={"question": ""},
                                     files={"question_audio": ("q.wav", (QDIR / f"{q}.wav").read_bytes(), "audio/wav")})
                    d = r.json()
                    ok = r.status_code == 200 and correct(d["answer"])
                    score[q] += ok
                    print(f"{i + 1} {q:10s} {'OK ' if ok else 'BAD'} {d.get('timings', {}).get('gemini')} s | "
                          f"heard {d.get('question_heard')!r} | used {d.get('used')} | {d.get('answer', r.text)[:80]}",
                          flush=True)
            await phone.close()
    finally:
        server.terminate()
        server.wait(10)
        log.close()
        wipe(store)
    for q in QUESTIONS:
        print(f"{q}: {score[q]}/{N} correct")


if __name__ == "__main__":
    asyncio.run(main())
