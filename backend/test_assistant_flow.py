"""
End-to-end test of the spec workflow: Stream -> Remember -> Ask -> Answer.

A simulated phone walks past scripted scenes (EXIT sign, door "ROOM 12",
blank wall, then faces a CAFE sign). The real backend must select,
store and analyse frames on its own; then questions are asked about
the current view and about the past, in English, Lebanese and French.

Usage: .venv\\Scripts\\python test_assistant_flow.py
Uses ~4 Gemini analysis requests, ~7 answers, a few embeddings (paced < 15/min).
"""

import asyncio
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from av import VideoFrame
from PIL import Image, ImageDraw

from test_helpers import foreign_script, wipe
from test_memory import font

HERE = Path(__file__).resolve().parent
RESULTS = []


def record(name, ok, detail=""):
    RESULTS.append(ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  -> ' + detail) if detail else ''}", flush=True)


def draw_scene(kind: str, jitter: int) -> Image.Image:
    img = Image.new("RGB", (800, 600), (120, 118, 112))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 470, 800, 600], fill=(80, 70, 60))                 # floor
    if kind == "exit":
        d.rectangle([220 + jitter, 120, 580 + jitter, 260], fill=(20, 120, 40), outline="white", width=6)
        d.text((270 + jitter, 150), "EXIT", fill="white", font=font(90))
    elif kind == "door":
        d.rectangle([260 + jitter, 60, 540 + jitter, 470], fill=(110, 70, 40), outline=(40, 30, 20), width=8)
        d.rectangle([300 + jitter, 110, 500 + jitter, 190], fill="white", outline="black", width=4)
        d.text((315 + jitter, 120), "ROOM 12", fill="black", font=font(48))
        d.ellipse([500 + jitter, 260, 520 + jitter, 280], fill=(200, 180, 60))
    elif kind == "wall":
        pass
    elif kind == "cafe":
        d.rectangle([180 + jitter, 140, 620 + jitter, 300], fill=(150, 30, 30), outline="white", width=6)
        d.text((230 + jitter, 170), "CAFE", fill="white", font=font(100))
    return img


class Walk(VideoStreamTrack):
    """Scripted walk: (scene, seconds)."""

    SCRIPT = [("exit", 7), ("door", 7), ("wall", 4), ("cafe", 9999)]

    def __init__(self):
        super().__init__()
        self.start = time.time()

    def scene(self):
        t = time.time() - self.start
        for kind, seconds in self.SCRIPT:
            if t < seconds:
                return kind
            t -= seconds
        return "cafe"

    async def recv(self):
        pts, time_base = await self.next_timestamp()
        kind = self.scene()
        frame = VideoFrame.from_image(draw_scene(kind, int(time.time() * 3) % 6))
        frame.pts, frame.time_base = pts, time_base
        return frame


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


async def ask(c, base, question, language=""):
    r = await c.post(base + "/ask", data={"question": question, "question_language": language, "speak": "false"})
    if r.status_code != 200:
        return r.status_code, {"detail": r.text}
    data = r.json()
    # "١٢" (Arabic-Indic digits) is the same number as "12".
    data["answer_check"] = data["answer"].translate(ARABIC_DIGITS)
    return r.status_code, data


async def main():
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    log_path = HERE / "test_assistant_server.log"
    log = open(log_path, "w", encoding="utf-8")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "WV_MEMORY_ANALYZE_EVERY": "6"}
    # Use a separate database so the test never touches real memory.
    test_store = HERE / "storage" / "memory_test"
    env["WV_MEMORY_DIR"] = str(test_store)
    wipe(test_store)                             # never test on a previous run's memory
    server = subprocess.Popen(
        [str(HERE / ".venv" / "Scripts" / "python.exe"), "-m", "uvicorn", "main:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=HERE, stdout=log, stderr=subprocess.STDOUT, env=env)
    try:
        async with httpx.AsyncClient(timeout=90) as c:
            for _ in range(80):
                try:
                    await c.get(base + "/health")
                    break
                except httpx.HTTPError:
                    await asyncio.sleep(0.25)

            code, body = await ask(c, base, "What is in front of me?")
            record("question before any stream -> clean 409", code == 409, str(body)[:90])

            print("\n[walk] phone streams a scripted walk (EXIT -> door ROOM 12 -> wall -> CAFE)")
            phone = RTCPeerConnection(RTCConfiguration(iceServers=[]))
            phone.addTrack(Walk())
            offer = await phone.createOffer()
            await phone.setLocalDescription(offer)
            r = await c.post(base + "/stream/publish",
                             json={"sdp": phone.localDescription.sdp, "type": phone.localDescription.type})
            answer = r.json()
            await phone.setRemoteDescription(RTCSessionDescription(sdp=answer["sdp"], type=answer["type"]))
            walk_started = time.time()

            await asyncio.sleep(24)               # walk past EXIT, door, wall; now facing CAFE
            for _ in range(30):                   # let the analyser finish pending frames
                stats = (await c.get(base + "/memory/stats")).json()
                if stats["store"].get("pending") in (0, None) and stats["store"].get("analyzed"):
                    break
                await asyncio.sleep(2)
            sel = stats["selection"]
            record("memory kept only useful frames", 3 <= sel["kept"] <= 12 and sel["kept"] < sel["seen"],
                   f"seen {sel['seen']}, kept {sel['kept']}, duplicates {sel['duplicate']}, "
                   f"uninformative {sel['uninformative']}, blurry {sel['blurry']}")
            frames = (await c.get(base + "/memory/frames?minutes=5")).json()["frames"]
            texts = " | ".join(f["text"] for f in frames if f["text"])
            record("frames analysed: OCR found EXIT, ROOM 12 and CAFE",
                   all(w in texts.upper().replace(" ", "") for w in ("EXIT", "ROOM12", "CAFE")), texts[:120])
            # Quota protection: at most one analysis request per interval (6 s here).
            elapsed = time.time() - walk_started
            record("analysis rate limited (<= 1 request per 6 s interval)",
                   stats["analysis"]["requests"] <= elapsed / 6 + 1,
                   f"{stats['analysis']['requests']} requests in {elapsed:.0f} s for "
                   f"{stats['store'].get('analyzed')} frames")

            print("\n[ask] questions")
            cases = [
                ("current view (en)", "What does the sign in front of me say?", "english",
                 lambda a: "CAFE" in a["answer"].upper() and a["current_view"] and a["evidence"] == "strong"),
                ("memory by time (en)", "What was written on the door I just passed?", "english",
                 lambda a: re.search(r"ROOM\s*12|12", a["answer"]) and a["memory_frames"]),
                ("memory by meaning (en)", "Where is the emergency exit?", "english",
                 lambda a: "EXIT" in a["answer"].upper() or "exit" in a["answer"].lower()),
                ("not seen -> 'I'm not sure' (en)", "What colour is the car parked outside?", "english",
                 lambda a: a["evidence"] in ("weak", "none") and re.search(r"not sure|can't|cannot|don't see|do not see|no car", a["answer"], re.I)),
                ("memory, Lebanese", "شو كان مكتوب عالباب يلي مرقنا حدّو؟", "arabic",
                 lambda a: "12" in a["answer_check"] and a["answer_language"] in ("ar", "ar-LB")),
                ("memory, French", "Qu'est-ce qui était écrit sur la porte ?", "french",
                 lambda a: "12" in a["answer_check"] and a["answer_language"] == "fr"),
                ("memory, formal Arabic", "ماذا كان مكتوبًا على الباب الذي مررت بجانبه قبل قليل؟", "arabic",
                 lambda a: "12" in a["answer_check"] and a["answer_language"] in ("ar", "ar-LB")),
                ("current view, mixed English + Lebanese", "Can you tell me شو مكتوب عاللافتة اللي قدامي؟", "english",
                 lambda a: "CAFE" in a["answer_check"].upper() or "كافيه" in a["answer_check"]),
            ]
            latencies = []
            for label, question, lang, check in cases:
                await asyncio.sleep(5)            # stay under 15 Gemini requests/minute
                code, a = await ask(c, base, question, lang)
                ok = code == 200 and bool(check(a))
                odd = foreign_script(a.get("answer", "")) if code == 200 else set()
                if odd:
                    ok = False
                    a["answer"] = f"FOREIGN SCRIPT {sorted(odd)}: " + a["answer"]
                if code == 200:
                    latencies.append(a["timings"]["total"])
                record(f"{label}", ok,
                       f"[{a.get('evidence')}/{a.get('answer_language')}] {a.get('answer', a.get('detail', ''))[:110]} "
                       f"(memory {len(a.get('memory_frames', []))}, {a.get('timings', {}).get('total')} s)")

            qa = (await c.get(base + "/memory/qa")).json()["qa"]
            record("questions and answers stored for the dashboard", len(qa) == len(cases),
                   f"{len(qa)} rows")
            img = await c.get(base + frames[0]["image_url"])
            record("timeline image served", img.status_code == 200 and img.content[:2] == b"\xff\xd8")
            if latencies:
                latencies.sort()
                print(f"\n  answer latency: median {latencies[len(latencies) // 2]:.2f} s, max {latencies[-1]:.2f} s")
            await phone.close()
    finally:
        server.terminate()
        server.wait(10)
        log.close()
        import shutil
        wipe(test_store)
    bad = [l for l in log_path.read_text(encoding="utf-8", errors="replace").splitlines()
           if "Traceback" in l or "ERROR" in l]
    record("server log: no tracebacks", not bad, "; ".join(bad[:2])[:200])
    print(f"\n{sum(RESULTS)}/{len(RESULTS)} end-to-end checks passed")
    sys.exit(0 if all(RESULTS) else 1)


if __name__ == "__main__":
    asyncio.run(main())
