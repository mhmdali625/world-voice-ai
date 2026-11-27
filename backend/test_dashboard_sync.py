"""
Dashboard synchronisation: every request the web dashboard makes,
sent from the dashboard's origin (http://localhost:5173) to a real
server while a simulated phone streams.

Checks: stream status goes live, the timeline lists analysed frames
with OCR text, thumbnails load, the Q&A history shows the question,
memory and quota stats are consistent, CORS allows the browser to read
every response (including the DELETE preflight of "Clear Memory"),
clearing really deletes frames, files and questions, and status
returns to "not live" after the phone stops.

Usage: .venv\\Scripts\\python test_dashboard_sync.py
Quota: ~4 Gemini analysis requests + 1 answer.
"""

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription

from test_assistant_flow import Walk
from test_helpers import wipe
from test_live_latency import free_port

HERE = Path(__file__).resolve().parent
ORIGIN = "http://localhost:5173"
results: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    results.append((ok, name, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}  -> {detail}", flush=True)


def cors_ok(r: httpx.Response) -> bool:
    return r.headers.get("access-control-allow-origin") in (ORIGIN, "*")


async def main() -> None:
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    store = HERE / "storage" / "memory_dashboard_test"
    wipe(store)
    log = open(HERE / "test_dashboard_server.log", "w", encoding="utf-8")
    server = subprocess.Popen(
        [str(HERE / ".venv" / "Scripts" / "python.exe"), "-m", "uvicorn", "main:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=HERE, stdout=log, stderr=subprocess.STDOUT,
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "WV_MEMORY_DIR": str(store),
             "WV_MEMORY_ANALYZE_EVERY": "6"})
    h = {"Origin": ORIGIN}
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            for _ in range(80):
                try:
                    await c.get(base + "/health")
                    break
                except httpx.HTTPError:
                    await asyncio.sleep(0.25)

            s = (await c.get(base + "/stream/status", headers=h)).json()
            check(not s["live"], "status before streaming: not live", f"live={s['live']}")

            phone = RTCPeerConnection(RTCConfiguration(iceServers=[]))
            phone.addTrack(Walk())
            offer = await phone.createOffer()
            await phone.setLocalDescription(offer)
            a = (await c.post(base + "/stream/publish", json={
                "sdp": phone.localDescription.sdp, "type": phone.localDescription.type})).json()
            await phone.setRemoteDescription(RTCSessionDescription(sdp=a["sdp"], type=a["type"]))
            await asyncio.sleep(4)
            r = await c.get(base + "/stream/status", headers=h)
            s = r.json()
            check(s["live"] and cors_ok(r), "status while streaming: live, readable by the browser",
                  f"live={s['live']} fps={s['fps']} res={s['resolution']} cors={cors_ok(r)}")

            await asyncio.sleep(30)                      # walk + background analysis
            r = await c.post(base + "/ask", data={"question": "What does the sign in front of me say?"},
                             headers=h)
            check(r.status_code == 200 and cors_ok(r), "question answered", r.json().get("answer", r.text)[:80])

            r = await c.get(base + "/memory/frames?minutes=30&limit=60", headers=h)
            frames = r.json()["frames"]
            analysed = [f for f in frames if f["status"] == "analyzed"]
            texts = " | ".join(f["text"] for f in analysed if f["text"])
            check(cors_ok(r) and len(frames) >= 3 and all(w in texts.upper() for w in ("EXIT", "ROOM 12", "CAFE")),
                  "timeline: stored frames with OCR text", f"{len(frames)} frames, {len(analysed)} analysed: {texts}")
            newest_first = all(frames[i]["captured_at"] >= frames[i + 1]["captured_at"]
                               for i in range(len(frames) - 1))
            check(newest_first, "timeline ordered newest first", "")
            fields = {"id", "captured_at", "age_s", "status", "image_url", "caption", "text", "objects", "hazards",
                      "keep_reason", "enhanced"}
            missing = fields - set(frames[0]) if frames else fields
            check(not missing, "frame rows have every field the dashboard shows", f"missing={sorted(missing)}")

            img = await c.get(base + frames[0]["image_url"], headers=h)
            check(img.status_code == 200 and img.headers["content-type"] == "image/jpeg" and img.content[:2] == b"\xff\xd8",
                  "thumbnail served as JPEG", f"HTTP {img.status_code}, {len(img.content)} bytes")

            r = await c.get(base + "/memory/qa?limit=30", headers=h)
            qa = r.json()["qa"]
            check(cors_ok(r) and len(qa) == 1 and qa[0]["answer"] and "sign" in (qa[0]["question"] or "").lower(),
                  "Q&A history shows the question and answer",
                  f"{len(qa)} rows: {qa[0]['question']!r} -> {qa[0]['answer'][:50]!r}" if qa else "empty")

            r = await c.get(base + "/memory/stats", headers=h)
            st = r.json()
            consistent = (st["store"]["frames"] == len(frames) and st["store"]["questions"] == 1
                          and st["selection"]["kept"] == len(frames)
                          and st["gemini_budget"]["per_minute"] > 0)
            check(cors_ok(r) and consistent, "stats agree with timeline and history",
                  f"store={st['store']['frames']} kept={st['selection']['kept']} timeline={len(frames)} "
                  f"questions={st['store']['questions']} gemini={st['gemini_budget']['used_last_minute']}"
                  f"/{st['gemini_budget']['per_minute']}")

            pre = await c.options(base + "/memory", headers={
                "Origin": ORIGIN, "Access-Control-Request-Method": "DELETE"})
            check(pre.status_code == 200 and "DELETE" in pre.headers.get("access-control-allow-methods", ""),
                  "Clear Memory: browser preflight allows DELETE",
                  f"HTTP {pre.status_code} methods={pre.headers.get('access-control-allow-methods')}")

            await phone.close()                          # stop the stream before clearing
            await asyncio.sleep(1)
            r = await c.delete(base + "/memory", headers=h)
            deleted = r.json().get("deleted_frames")
            files = list((store / "frames").glob("*.jpg")) if (store / "frames").exists() else []
            after_f = (await c.get(base + "/memory/frames", headers=h)).json()["frames"]
            after_q = (await c.get(base + "/memory/qa", headers=h)).json()["qa"]
            gone = await c.get(base + frames[0]["image_url"], headers=h)
            check(cors_ok(r) and deleted == len(frames) and not files and not after_f and not after_q
                  and gone.status_code == 404,
                  "Clear Memory deletes frames, image files and questions",
                  f"deleted={deleted}, files left={len(files)}, timeline={len(after_f)}, qa={len(after_q)}, "
                  f"old image HTTP {gone.status_code}")

            deadline = time.time() + 15
            s = {}
            while time.time() < deadline:
                s = (await c.get(base + "/stream/status", headers=h)).json()
                if not s["live"]:
                    break
                await asyncio.sleep(1)
            check(not s.get("live"), "status after the phone stops: not live",
                  f"live={s.get('live')} stalled={s.get('stalled')}")
    finally:
        server.terminate()
        server.wait(10)
        log.close()
        wipe(store)

    tracebacks = "Traceback" in (HERE / "test_dashboard_server.log").read_text(encoding="utf-8")
    check(not tracebacks, "server log: no tracebacks", "")
    passed = sum(ok for ok, _, _ in results)
    print(f"\n{passed}/{len(results)} dashboard checks passed")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
