"""
Several phones streaming at the same time to one real server.

Each phone walks past its OWN door and then stops at its OWN sign:
    phone-a : door "ROOM 12" -> sign "CAFE"
    phone-b : door "ROOM 47" -> sign "PHARMACY"
    phone-c : door "LAB 3"   -> sign "LIBRARY"

Checks: all streams live at once with their own status; frames
selected and stored per phone (no mixing); timeline / Q&A / search
limited per phone; questions asked AT THE SAME TIME by the three phones
are answered from each phone's own view and memory (spoken and typed,
current and past); one phone reconnecting does not disturb the others;
the 4-phone limit; server CPU and RAM while 3 streams are decoded.

Usage: .venv\\Scripts\\python test_multi_device.py
Quota: ~8 Gemini analysis requests + 6 answers + embeddings (no TTS).
"""

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from av import VideoFrame
from PIL import Image, ImageDraw

from test_helpers import ProcessMeter, folder_size, foreign_script, server_pid, wipe
from test_live_latency import QDIR, free_port, make_questions
from test_memory import font

HERE = Path(__file__).resolve().parent
PHONES = {
    "phone-a": ("ROOM 12", "CAFE"),
    "phone-b": ("ROOM 47", "PHARMACY"),
    "phone-c": ("LAB 3", "LIBRARY"),
}
ALL_WORDS = {w for pair in PHONES.values() for w in pair}
results: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    results.append((bool(ok), name, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}  -> {detail}", flush=True)


def draw(kind: str, text: str, jitter: int) -> Image.Image:
    img = Image.new("RGB", (800, 600), (120, 118, 112))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 470, 800, 600], fill=(80, 70, 60))
    if kind == "door":
        d.rectangle([250 + jitter, 60, 550 + jitter, 470], fill=(110, 70, 40), outline=(40, 30, 20), width=8)
        d.rectangle([280 + jitter, 110, 520 + jitter, 190], fill="white", outline="black", width=4)
        d.text((295 + jitter, 120), text, fill="black", font=font(48))
        d.ellipse([500 + jitter, 280, 525 + jitter, 305], fill=(200, 180, 60))
    else:
        d.rectangle([120 + jitter, 140, 680 + jitter, 300], fill=(30, 60, 150), outline="white", width=6)
        d.text((150 + jitter, 170), text, fill="white", font=font(90))
    return img


class PhoneWalk(VideoStreamTrack):
    def __init__(self, door: str, sign: str) -> None:
        super().__init__()
        self.door, self.sign, self.start = door, sign, time.time()

    async def recv(self):
        pts, time_base = await self.next_timestamp()
        at_door = time.time() - self.start < 9
        image = draw("door" if at_door else "sign", self.door if at_door else self.sign, int(time.time() * 3) % 6)
        frame = VideoFrame.from_image(image)
        frame.pts, frame.time_base = pts, time_base
        return frame


async def connect(c: httpx.AsyncClient, base: str, device: str, track) -> tuple[RTCPeerConnection, httpx.Response]:
    pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    pc.addTrack(track)
    await pc.setLocalDescription(await pc.createOffer())
    r = await c.post(base + "/stream/publish", json={
        "sdp": pc.localDescription.sdp, "type": pc.localDescription.type, "device_id": device})
    if r.status_code == 200:
        a = r.json()
        await pc.setRemoteDescription(RTCSessionDescription(sdp=a["sdp"], type=a["type"]))
    return pc, r


def only_own(answer: str, own: str) -> tuple[bool, set[str]]:
    """(own text present, foreign words or foreign-script letters found)"""
    # Same text written differently: Arabic-Indic digits, and "ROOM"
    # spelled in Arabic script inside an Arabic answer ("روم 12").
    upper = (answer.upper().replace("١٢", "12").replace("٤٧", "47").replace("٣", "3")
             .replace("روم", "ROOM"))
    foreign = {w for w in ALL_WORDS - {own} if w in upper and not (w == "ROOM 12" and own == "ROOM 47")}
    return own in upper, foreign | {f"script:{c}" for c in foreign_script(answer)}


async def main() -> None:
    await make_questions()
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    store = HERE / "storage" / "memory_multi_test"
    wipe(store)
    log = open(HERE / "test_multi_server.log", "w", encoding="utf-8")
    server = subprocess.Popen(
        [str(HERE / ".venv" / "Scripts" / "python.exe"), "-m", "uvicorn", "main:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=HERE, stdout=log, stderr=subprocess.STDOUT,
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "WV_MEMORY_DIR": str(store),
             "WV_MEMORY_ANALYZE_EVERY": "5"})
    pcs: dict[str, RTCPeerConnection] = {}
    try:
        async with httpx.AsyncClient(timeout=90) as c:
            for _ in range(80):
                try:
                    await c.get(base + "/health")
                    break
                except httpx.HTTPError:
                    await asyncio.sleep(0.25)
            meter = ProcessMeter(server_pid(server.pid))

            print("[1] three phones start streaming at the same time", flush=True)
            connected = await asyncio.gather(*(connect(c, base, d, PhoneWalk(*PHONES[d])) for d in PHONES))
            for d, (pc, r) in zip(PHONES, connected):
                pcs[d] = pc
            await asyncio.sleep(5)
            st = (await c.get(base + "/stream/status")).json()
            live = {s["device"]: s for s in st["devices"] if s["live"]}
            check(set(live) == set(PHONES) and all((s["fps"] or 0) >= 10 for s in live.values()),
                  "3 phones live at once, each with its own status",
                  ", ".join(f"{d} {s['fps']} fps" for d, s in sorted(live.items())))
            sessions = {d: s["session_id"] for d, s in live.items()}

            print("[2] walking: door, then sign (35 s) ...", flush=True)
            for _ in range(7):
                await asyncio.sleep(5)
                meter.sample()
            cpu_streaming = meter.cpu_percent()

            print("[3] memory per phone", flush=True)
            for d, (door, sign) in PHONES.items():
                frames = (await c.get(base + f"/memory/frames?device={d}")).json()["frames"]
                texts = " | ".join(f["text"] for f in frames if f["status"] == "analyzed")
                foreign = {w for w in ALL_WORDS - {door, sign} if w in texts.upper()
                           and not (w == "ROOM 12" and door == "ROOM 47")}
                check(bool(frames) and all(f["device"] == d for f in frames) and door in texts.upper()
                      and sign in texts.upper() and not foreign,
                      f"{d}: own frames only, OCR found its door and sign",
                      f"{len(frames)} frames: {texts[:80]}" + (f" FOREIGN {foreign}" if foreign else ""))
            stats = (await c.get(base + "/memory/stats")).json()
            check(sorted(stats["store"]["devices"]) == sorted(PHONES), "stats list the three phones",
                  f"{stats['store']['devices']}, {stats['store']['frames']} frames, "
                  f"selection {stats['selection']}")

            print("[4] the three phones ask AT THE SAME TIME", flush=True)
            spoken_now = (QDIR / "q_en_now.wav").read_bytes()

            async def ask(device: str, **kw):
                t = time.perf_counter()
                if "audio" in kw:
                    r = await c.post(base + "/ask", data={"question": "", "device_id": device},
                                     files={"question_audio": ("q.wav", kw["audio"], "audio/wav")})
                else:
                    r = await c.post(base + "/ask", data={"question": kw["text"], "device_id": device})
                return r, time.perf_counter() - t

            now_answers = await asyncio.gather(
                ask("phone-a", audio=spoken_now),
                ask("phone-b", text="What does the sign in front of me say?"),
                ask("phone-c", text="Qu'est-ce qui est écrit sur le panneau devant moi ?"))
            for (d, (door, sign)), (r, secs) in zip(PHONES.items(), now_answers):
                ans = r.json().get("answer", r.text) if r.status_code == 200 else r.text
                has, foreign = only_own(ans, sign)
                check(r.status_code == 200 and has and not foreign and r.json()["device"] == d,
                      f"{d}: current sign answered from its own view ({secs:.1f} s)", ans[:90])

            await asyncio.sleep(4)
            spoken_past = (QDIR / "q_lb_past.wav").read_bytes()
            past_answers = await asyncio.gather(
                ask("phone-a", audio=spoken_past),
                ask("phone-b", text="What was written on the door I just passed?"),
                ask("phone-c", text="What was written on the door I passed before?"))
            for (d, (door, sign)), (r, secs) in zip(PHONES.items(), past_answers):
                ans = r.json().get("answer", r.text) if r.status_code == 200 else r.text
                has, foreign = only_own(ans, door)
                check(r.status_code == 200 and has and not foreign,
                      f"{d}: past door answered from its own memory ({secs:.1f} s)", ans[:90])

            for d in PHONES:
                qa = (await c.get(base + f"/memory/qa?device={d}")).json()["qa"]
                check(len(qa) == 2 and all(q["device"] == d for q in qa), f"{d}: Q&A history has only its 2 questions",
                      f"{len(qa)} rows")
            ev = now_answers[0][0].json().get("evidence_frames") if now_answers[0][0].status_code == 200 else None
            past_ev = past_answers[1][0].json().get("evidence_frames") if past_answers[1][0].status_code == 200 else None
            check(past_ev is not None and any("ROOM 47" in (e.get("text") or "").upper() for e in past_ev),
                  "answer reports the stored frame it relied on (phone-b door)",
                  f"{[(e['label'], e['text']) for e in past_ev or []]} | current-view answer: {len(ev or [])} memory frames")

            print("[5] one phone reconnects", flush=True)
            await pcs["phone-b"].close()
            pcs["phone-b"], r = await connect(c, base, "phone-b", PhoneWalk("ROOM 47", "PHARMACY"))
            await asyncio.sleep(4)
            st = {s["device"]: s for s in (await c.get(base + "/stream/status")).json()["devices"]}
            check(r.status_code == 200 and st["phone-b"]["live"] and st["phone-b"]["session_id"] != sessions["phone-b"]
                  and st["phone-a"]["session_id"] == sessions["phone-a"] and st["phone-a"]["live"]
                  and st["phone-c"]["session_id"] == sessions["phone-c"] and st["phone-c"]["live"],
                  "phone-b reconnects with a new session; phone-a and phone-c undisturbed",
                  ", ".join(f"{d}: live={s['live']}" for d, s in sorted(st.items())))
            frames_b = (await c.get(base + "/memory/frames?device=phone-b")).json()["frames"]
            check(any("ROOM 47" in f["text"].upper() for f in frames_b), "phone-b keeps its memory after reconnecting",
                  f"{len(frames_b)} frames")

            print("[6] limit: at most 4 phones", flush=True)
            pcs["phone-d"], r4 = await connect(c, base, "phone-d", PhoneWalk("ROOM 1", "EXIT"))
            pcs["phone-e"], r5 = await connect(c, base, "phone-e", PhoneWalk("ROOM 2", "EXIT"))
            check(r4.status_code == 200 and r5.status_code == 503, "4th phone accepted, 5th refused cleanly (503)",
                  f"4th HTTP {r4.status_code}, 5th HTTP {r5.status_code} {r5.text[:60]}")
            await asyncio.sleep(3)
            meter.sample()

            r = await c.delete(base + "/memory?device=phone-c")
            left_c = (await c.get(base + "/memory/frames?device=phone-c")).json()["frames"]
            left_a = (await c.get(base + "/memory/frames?device=phone-a")).json()["frames"]
            check(r.status_code == 200 and not left_c and left_a, "clearing phone-c's memory keeps phone-a's",
                  f"phone-c {len(left_c)} frames, phone-a {len(left_a)} frames")

            print(f"\n  server: CPU {cpu_streaming:.0f} % of one core while 3 phones streamed and were "
                  f"analysed, peak RAM {meter.peak_rss / 2 ** 20:.0f} MB, storage {folder_size(store) / 2 ** 20:.1f} MB",
                  flush=True)
    finally:
        for pc in pcs.values():
            await pc.close()
        server.terminate()
        server.wait(10)
        log.close()
        wipe(store)

    tracebacks = "Traceback" in (HERE / "test_multi_server.log").read_text(encoding="utf-8")
    check(not tracebacks, "server log: no tracebacks", "")
    passed = sum(ok for ok, _, _ in results)
    print(f"\n{passed}/{len(results)} multi-phone checks passed")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
