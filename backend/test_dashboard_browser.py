"""
The web dashboard in a REAL browser (headless Microsoft Edge).

Starts the backend on port 8000 (the dashboard's address) with a test
memory, serves the built dashboard (vite preview, port 5173), streams
a simulated phone walk, asks two questions, then loads the dashboard in
Edge and checks the rendered page: stream status, live video connected,
timeline with OCR text and images, Q&A history with the frames used.
Also saves a screenshot: test_dashboard_browser.png.

Requires: ports 8000 and 5173 free, `npm run build` done in dashboard/.
Usage: .venv\\Scripts\\python test_dashboard_browser.py
Quota: ~4 Gemini analysis requests + 2 answers.
"""

import asyncio
import os
import re
import subprocess
import sys
from pathlib import Path

import httpx
from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription

from test_assistant_flow import Walk
from test_helpers import wipe

HERE = Path(__file__).resolve().parent
DASHBOARD = HERE.parent / "dashboard"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
results: list[tuple[bool, str, str]] = []


def check(ok, name: str, detail: str = "") -> None:
    results.append((bool(ok), name, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}  -> {detail}", flush=True)


def edge(args: list[str], timeout: int = 90) -> str:
    profile = HERE / "storage" / "edge_profile_test"
    out = subprocess.run([EDGE, "--headless=new", "--disable-gpu", "--no-first-run",
                          f"--user-data-dir={profile}", "--window-size=1400,3200", *args],
                         capture_output=True, timeout=timeout)
    return out.stdout.decode("utf-8", errors="replace")


async def main() -> None:
    store = HERE / "storage" / "memory_browser_test"
    wipe(store)
    log = open(HERE / "test_browser_server.log", "w", encoding="utf-8")
    server = subprocess.Popen(
        [str(HERE / ".venv" / "Scripts" / "python.exe"), "-m", "uvicorn", "main:app",
         "--host", "127.0.0.1", "--port", "8000"],
        cwd=HERE, stdout=log, stderr=subprocess.STDOUT,
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "WV_MEMORY_DIR": str(store),
             "WV_MEMORY_ANALYZE_EVERY": "6"})
    preview = subprocess.Popen("npx vite preview --port 5173 --strictPort --host 127.0.0.1", cwd=DASHBOARD,
                               shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    phone = None
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            for _ in range(120):
                try:
                    await c.get("http://127.0.0.1:8000/health")
                    await c.get("http://127.0.0.1:5173/")
                    break
                except httpx.HTTPError:
                    await asyncio.sleep(0.25)
            phone = RTCPeerConnection(RTCConfiguration(iceServers=[]))
            phone.addTrack(Walk())
            await phone.setLocalDescription(await phone.createOffer())
            a = (await c.post("http://127.0.0.1:8000/stream/publish", json={
                "sdp": phone.localDescription.sdp, "type": phone.localDescription.type,
                "device_id": "phone-test"})).json()
            await phone.setRemoteDescription(RTCSessionDescription(sdp=a["sdp"], type=a["type"]))
            print("phone streaming the walk (32 s) ...", flush=True)
            await asyncio.sleep(32)
            for q in ("What does the sign in front of me say?", "What was written on the door I just passed?"):
                r = await c.post("http://127.0.0.1:8000/ask", data={"question": q, "device_id": "phone-test"})
                d = r.json()
                print(f"  asked: {q} -> {d.get('answer', r.text)[:70]}", flush=True)
                if d.get("qa_id"):                       # what the phone reports after the first word
                    await c.post(f"http://127.0.0.1:8000/ask/{d['qa_id']}/first-word", json={"first_word_s": 3.9})
                await asyncio.sleep(3)

            def page(tab: str, shot: bool = False) -> tuple[str, str]:
                url = f"http://127.0.0.1:5173/#{tab}"
                dom = edge(["--virtual-time-budget=20000", "--dump-dom", url])
                if shot:
                    edge(["--virtual-time-budget=20000", f"--screenshot={HERE / f'test_dashboard_{tab}.png'}", url])
                return dom, re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", dom))

            print("loading the dashboard in Edge (each tab) ...", flush=True)
            dom, text = await asyncio.to_thread(page, "live", True)
            check("Phone live" in text and "Backend online" in text, "header: backend online, phone live",
                  (re.search(r"Phone (live|stalled|not streaming)[^A]{0,12}", text) or [""])[0])
            video = re.search(r'aria-label="(Live video[^"]*)"', dom)
            check(video and video.group(1) == "Live video from the phone camera",
                  "live video connected in the browser (WebRTC)", video.group(1) if video else "canvas not found")
            check("Latest answer" in text and "first word 3.9 s" in text and "12" in text,
                  "latest answer with its measured first-word latency", "")

            dom, text = await asyncio.to_thread(page, "memory", True)
            check(all(f"Text: {w}" in text for w in ("EXIT", "ROOM 12", "CAFE")),
                  "memory: stored frames with their OCR text",
                  ", ".join(w for w in ("EXIT", "ROOM 12", "CAFE") if f"Text: {w}" in text))
            imgs = re.findall(r'<img[^>]+src="(http://127\.0\.0\.1:8000/memory/frames/[0-9a-f]+\.jpg)"', dom)
            check(len(imgs) >= 3 and re.search(r"Frames stored\s*\d+", text),
                  "memory: thumbnails from the backend and statistics", f"{len(imgs)} images")

            dom, text = await asyncio.to_thread(page, "conversations", True)
            check("What was written on the door I just passed?" in text and "first word 3.9 s" in text,
                  "Q&A: questions, answers and first-word latency", "")
            check('class="thumbs"' in dom, "Q&A: the stored frames each answer used", "")

            dom, text = await asyncio.to_thread(page, "system", True)
            check("gemini-3.5-flash-lite" in text and "Connect a phone" in text and ":8000" in text,
                  "AI / voice / network: model chain, quota and phone addresses", "")

            dom, text = await asyncio.to_thread(page, "warnings")
            check("Obstacle warnings" in text, "warnings tab rendered", "")
    finally:
        if phone:
            await phone.close()
        server.terminate()
        server.wait(10)
        subprocess.run(f"taskkill /PID {preview.pid} /T /F", shell=True, capture_output=True)
        log.close()
        wipe(store)
        wipe(HERE / "storage" / "edge_profile_test")

    tracebacks = "Traceback" in (HERE / "test_browser_server.log").read_text(encoding="utf-8")
    check(not tracebacks, "server log: no tracebacks", "")
    passed = sum(ok for ok, _, _ in results)
    print(f"\n{passed}/{len(results)} browser checks passed (screenshots: test_dashboard_<tab>.png)")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
