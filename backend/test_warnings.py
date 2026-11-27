"""
Spoken warnings (bonus) and answer-language checks.

Offline (no API): warning rules (obstacle vs ordinary object, frame too
old, camera moved, expiry, withdrawal at poll time, no repetition), the
messages in en / fr / formal Arabic / Lebanese with uncertainty, never
"safe"; the answer-language mismatch check.

Live (--live): a phone looks at a closed door ahead, then turns to a sign
on a wall, then back. The warning must come in the user's language
(French here), be withdrawn within seconds of turning away, never be
raised for the sign on the wall, and not be repeated on turning back.

Usage: .venv\\Scripts\\python test_warnings.py [--live]
Quota (--live): ~6 Gemini analysis requests + 1 answer.
"""

import asyncio
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import hazards as hz
from assistant_service import language_mismatch

HERE = Path(__file__).resolve().parent
results: list[tuple[bool, str, str]] = []
SAFE_WORDS = re.compile(r"\bsafe\b|\bsûr\b|\bsécurité\b|آمن|أمان|بأمان", re.IGNORECASE)


def check(ok, name: str, detail: str = "") -> None:
    results.append((bool(ok), name, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}  -> {detail}", flush=True)


DOOR = {"label_en": "closed door", "label_fr": "porte fermée", "label_ar": "باب مغلق", "label_lb": "باب مسكّر",
        "position": "ahead", "in_path": True, "certain": True}


def offline() -> None:
    print("\n[1] Warning rules", flush=True)
    book = hz.AlertBook()
    now = 1000.0
    frame = {"id": "f1", "device": "phone", "captured_at": now - 3}
    same, moved = 0b1010 << 20, (0b1010 << 20) ^ (2 ** 40 - 1)      # distance 0 / 40 bits
    check(book.consider(dict(DOOR, in_path=False), frame, same, same, now) is None,
          "ordinary object (not in the walking path) -> no warning", "")
    check(book.consider(DOOR, dict(frame, captured_at=now - hz.MAX_FRAME_AGE_S - 5), same, same, now) is None,
          f"hazard frame older than {hz.MAX_FRAME_AGE_S:.0f} s -> no warning", "")
    check(book.consider(DOOR, frame, same, moved, now) is None, "camera already moved away -> no warning", "")
    check(book.consider(DOOR, frame, same, None, now) is None, "phone not streaming -> no warning", "")
    alert = book.consider(DOOR, frame, same, same ^ 0b111, now)
    check(alert is not None, "obstacle still in view (small camera shake) -> warning", "")
    check(book.consider(DOOR, frame, same, same, now + 10) is None, "same obstacle within 45 s -> not repeated", "")
    check(book.consider(dict(DOOR, position="left"), frame, same, same, now + 11) is not None,
          "a different position is a new warning", "")
    check(book.consider(DOOR, dict(frame, captured_at=now + 47), same, same, now + 50) is not None,
          "after 45 s the obstacle (seen again, fresh frame) may be announced again", "")
    book2 = hz.AlertBook()
    book2.consider(DOOR, frame, same, same, now)
    check(len(book2.active("phone", 0, lambda d: same, now + 2)) == 1, "poll while still in view -> returned", "")
    check(book2.active("phone", 0, lambda d: moved, now + 3) == [] and book2.stats["withdrawn"] == 1,
          "poll after the camera moved -> withdrawn immediately", "")
    book3 = hz.AlertBook()
    book3.consider(DOOR, frame, same, same, now)
    check(book3.active("phone", 0, lambda d: same, now + hz.ALERT_TTL_S + 0.1) == [] and book3.stats["expired"] == 1,
          f"older than {hz.ALERT_TTL_S:.0f} s -> expired even if still in view", "")
    check(book3.active("other-phone", 0, lambda d: same, now + 1) == [], "another phone never gets this warning", "")

    print("\n[2] Messages", flush=True)
    unsure = dict(DOOR, position="unknown", certain=False)
    msgs = {lang: (hz.message(DOOR, lang), hz.message(unsure, lang)) for lang in ("en", "fr", "ar", "ar-LB")}
    for lang, (sure, unsure_msg) in msgs.items():
        print(f"     {lang:5s} | {sure} | {unsure_msg}")
    check("ahead of you" in msgs["en"][0] and "devant vous" in msgs["fr"][0] and "أمامك" in msgs["ar"][0]
          and "قدّامك" in msgs["ar-LB"][0], "position said in each language", "")
    check("can't tell exactly where" in msgs["en"][1] and "ne sais pas exactement" in msgs["fr"][1]
          and "لست متأكدًا" in msgs["ar"][1] and "مش متأكد" in msgs["ar-LB"][1],
          "unknown position -> says so (uncertainty)", "")
    check("مسكّر" in msgs["ar-LB"][0] and "مغلق" in msgs["ar"][0], "formal Arabic and Lebanese wording differ", "")
    check(not any(SAFE_WORDS.search(m) for pair in msgs.values() for m in pair), "no message ever says 'safe'", "")

    print("\n[3] Answer language check", flush=True)
    cases = [
        ("fr", "fr", "Il est écrit CAFE sur le panneau.", False),
        ("fr", "fr", "Il est écrit مخرج sur le panneau.", False),            # quoted sign text is fine
        ("fr", "fr", "Il y a un panneau, مكتوب عليه CAFE قدامك.", True),       # mixed French + Arabic
        ("en", "ar-LB", "مكتوب CAFE.", True),                                 # English question, Arabic answer
        ("ar", "ar-LB", "مكتوب عاللافتة CAFE.", True),                         # formal question, Lebanese answer
        ("ar-LB", "ar-LB", "مكتوب عاللافتة CAFE.", False),
        ("en", "en", "The sign says CAFE.", False),
    ]
    bad = [c for c in cases if language_mismatch(c[0], c[1], c[2]) != c[3]]
    check(not bad, "mismatches detected (and quoted sign text allowed)", f"{len(cases)} cases, wrong: {bad}")


async def live() -> None:
    print("\n[4] Live: closed door ahead -> sign on a wall -> door again", flush=True)
    import httpx
    from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
    from av import VideoFrame

    from test_assistant_flow import draw_scene
    from test_helpers import wipe
    from test_live_latency import free_port

    class Camera(VideoStreamTrack):
        kind_now = "door"

        async def recv(self):
            pts, time_base = await self.next_timestamp()
            frame = VideoFrame.from_image(draw_scene(self.kind_now, int(time.time() * 3) % 6))
            frame.pts, frame.time_base = pts, time_base
            return frame

    store = HERE / "storage" / "memory_warnings_test"
    wipe(store)
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    log = open(HERE / "test_warnings_server.log", "w", encoding="utf-8")
    server = subprocess.Popen(
        [str(HERE / ".venv" / "Scripts" / "python.exe"), "-m", "uvicorn", "main:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=HERE, stdout=log, stderr=subprocess.STDOUT,
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "WV_MEMORY_DIR": str(store), "WV_MEMORY_ANALYZE_EVERY": "4"})
    phone = None
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            for _ in range(80):
                try:
                    await c.get(base + "/health")
                    break
                except httpx.HTTPError:
                    await asyncio.sleep(0.25)
            cam = Camera()
            phone = RTCPeerConnection(RTCConfiguration(iceServers=[]))
            phone.addTrack(cam)
            await phone.setLocalDescription(await phone.createOffer())
            a = (await c.post(base + "/stream/publish", json={
                "sdp": phone.localDescription.sdp, "type": phone.localDescription.type, "device_id": "phone"})).json()
            await phone.setRemoteDescription(RTCSessionDescription(sdp=a["sdp"], type=a["type"]))
            await asyncio.sleep(3)
            r = await c.post(base + "/ask", data={"question": "Qu'est-ce qu'il y a devant moi ?", "device_id": "phone"})
            check(r.status_code == 200 and r.json()["answer_language"] == "fr",
                  "user asks in French (sets the warning language)", r.json().get("answer", r.text)[:70])

            async def alerts():
                return (await c.get(base + "/memory/alerts?device=phone")).json()["alerts"]

            got, waited = [], 0.0
            while waited < 30 and not got:
                await asyncio.sleep(1)
                waited += 1
                got = await alerts()
            msg = got[0]["message"] if got else ""
            check(got and got[0]["language"] == "fr" and "porte" in msg.lower() and not SAFE_WORDS.search(msg),
                  "closed door ahead -> warning in French", f"after {waited:.0f} s: {msg!r}")

            cam.kind_now = "cafe"                                   # the user turns to a sign on a wall
            t0, gone = time.time(), None
            while time.time() - t0 < 6:
                await asyncio.sleep(0.5)
                if not await alerts():
                    gone = time.time() - t0
                    break
            check(gone is not None and gone <= 3.5, "camera turned away -> warning withdrawn",
                  f"after {gone:.1f} s" if gone is not None else "still active after 6 s")
            # Current view = the newest frame at the moment of the question.
            r = await c.post(base + "/ask", data={"question": "What does the sign in front of me say?",
                                                  "device_id": "phone"})
            d = r.json()
            check(r.status_code == 200 and "CAFE" in d["answer"].upper() and "PORTE" not in d["answer"].upper()
                  and d["timings"].get("view_age", 9) <= 0.3,
                  "question right after turning: answered from the NEW view (fresh frame)",
                  f"view age {d['timings'].get('view_age')} s: {d['answer'][:60]!r}")
            ordinary = []
            for _ in range(14):                                     # time for the sign frame to be analysed
                await asyncio.sleep(1)
                ordinary += await alerts()
            stats = (await c.get(base + "/memory/stats")).json()["warnings"]
            check(not ordinary, "a sign on a wall (ordinary object) -> no warning", f"stats {stats}")

            cam.kind_now = "door"                                   # turns back within 45 s
            again = []
            for _ in range(14):
                await asyncio.sleep(1)
                again += await alerts()
            stats = (await c.get(base + "/memory/stats")).json()["warnings"]
            check(not again and stats["repeated"] >= 1, "same door again within 45 s -> not repeated",
                  f"stats {stats}")
    finally:
        if phone:
            await phone.close()
        server.terminate()
        server.wait(10)
        log.close()
        wipe(store)
    tracebacks = "Traceback" in (HERE / "test_warnings_server.log").read_text(encoding="utf-8")
    check(not tracebacks, "server log: no tracebacks", "")


if __name__ == "__main__":
    offline()
    if "--live" in sys.argv:
        asyncio.run(live())
    passed = sum(ok for ok, _, _ in results)
    print(f"\n{passed}/{len(results)} warning / language checks passed")
    sys.exit(0 if passed == len(results) else 1)
