"""
Access code (WV_ACCESS_TOKEN) does not break the app, WebRTC or the dashboard.

The server is reached through the laptop's NETWORK address (like the phone;
loopback is the dashboard's path and is always allowed).

  phone side (network address):  without code -> 401 everywhere except /health;
                                  with code    -> WebRTC publish, /ask, warnings,
                                                  speech job + its audio all work
  dashboard side (127.0.0.1):    no code needed: status, memory, frame image,
                                  WebRTC viewer, CORS preflight, Clear Memory

Usage: .venv\\Scripts\\python test_access_code.py      (1 answer request)
"""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import httpx
from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription

import network
from test_assistant_flow import Walk
from test_helpers import wipe
from test_live_latency import free_port

HERE = Path(__file__).resolve().parent
CODE = "demo-code-4821"
results: list[tuple[bool, str, str]] = []


def check(ok, name: str, detail: str = "") -> None:
    results.append((bool(ok), name, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}  -> {detail}", flush=True)


async def main() -> None:
    lan = next((a["address"] for a in network.local_addresses() if a["recommended"]), None)
    if not lan:
        print("no network address found; connect to a network first")
        sys.exit(2)
    port = free_port()
    phone_base, dash_base = f"http://{lan}:{port}", f"http://127.0.0.1:{port}"
    store = HERE / "storage" / "memory_access_test"
    wipe(store)
    log = open(HERE / "test_access_server.log", "w", encoding="utf-8")
    server = subprocess.Popen(
        [str(HERE / ".venv" / "Scripts" / "python.exe"), "-m", "uvicorn", "main:app",
         "--host", "0.0.0.0", "--port", str(port)],
        cwd=HERE, stdout=log, stderr=subprocess.STDOUT,
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "WV_MEMORY_DIR": str(store), "WV_ACCESS_TOKEN": CODE,
             "WV_QUOTA_STATE": str(store / "quota_state.json")})
    phone = viewer = None
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            for _ in range(80):
                try:
                    await c.get(dash_base + "/health")
                    break
                except httpx.HTTPError:
                    await asyncio.sleep(0.25)
            print(f"[1] phone side via {lan} (not loopback)", flush=True)
            r = await c.get(phone_base + "/health")
            check(r.status_code == 200, "/health open without code", f"HTTP {r.status_code}")
            blocked = {}
            for method, path in (("GET", "/stream/status"), ("GET", "/memory/frames"), ("GET", "/memory/alerts"),
                                 ("POST", "/ask"), ("POST", "/tts/jobs"), ("POST", "/stream/publish"), ("GET", "/network")):
                r = await c.request(method, phone_base + path)
                blocked[path] = r.status_code
            check(all(code == 401 for code in blocked.values()), "without the code: every other endpoint refused (401)",
                  str(blocked))
            r = await c.get(phone_base + "/stream/status", headers={"X-WV-Token": "wrong"})
            check(r.status_code == 401, "wrong code refused", f"HTTP {r.status_code}")

            h = {"X-WV-Token": CODE}
            phone = RTCPeerConnection(RTCConfiguration(iceServers=[]))
            phone.addTrack(Walk())
            await phone.setLocalDescription(await phone.createOffer())
            r = await c.post(phone_base + "/stream/publish", headers=h, json={
                "sdp": phone.localDescription.sdp, "type": phone.localDescription.type, "device_id": "phone-code"})
            ok = r.status_code == 200
            if ok:
                await phone.setRemoteDescription(RTCSessionDescription(sdp=r.json()["sdp"], type=r.json()["type"]))
            await asyncio.sleep(8)
            st = (await c.get(phone_base + "/stream/status?device=phone-code", headers=h)).json()
            check(ok and st.get("live"), "with the code: WebRTC publish works, stream live",
                  f"HTTP {r.status_code}, live={st.get('live')}, fps={st.get('fps')}")
            r = await c.post(phone_base + "/ask", headers=h, data={
                "question": "What does the sign in front of me say?", "device_id": "phone-code", "speak": "true"})
            d = r.json() if r.status_code == 200 else {}
            check(r.status_code == 200 and d.get("answer"), "with the code: /ask answers",
                  f"HTTP {r.status_code} {d.get('answer', r.text)[:60]!r} via {d.get('timings', {}).get('model')}")
            speech = d.get("speech")
            if speech:
                s = await c.get(phone_base + speech["status_path"], headers=h)
                a = await c.get(phone_base + speech["audio_path"], headers=h)
                no = await c.get(phone_base + speech["audio_path"])
                check(s.status_code == 200 and a.status_code == 200 and len(a.content) > 1000 and no.status_code == 401,
                      "with the code: speech job + audio stream (player sends the header); without: 401",
                      f"status {s.status_code}, audio {a.status_code} {len(a.content)} bytes, no code {no.status_code}")
            r = await c.get(phone_base + "/memory/alerts?device=phone-code", headers=h)
            check(r.status_code == 200, "with the code: warning polling works", f"HTTP {r.status_code}")

            print("[2] dashboard side (127.0.0.1, no code)", flush=True)
            got = {p: (await c.get(dash_base + p)).status_code for p in
                   ("/stream/status", "/memory/frames", "/memory/stats", "/memory/qa", "/memory/alerts/history",
                    "/tts/quota", "/network", "/api/images")}
            check(all(v == 200 for v in got.values()), "dashboard reads everything without the code", str(got))
            frames = (await c.get(dash_base + "/memory/frames")).json()["frames"]
            if frames:
                img = await c.get(dash_base + frames[0]["image_url"])
                check(img.status_code == 200, "dashboard frame thumbnail", f"HTTP {img.status_code}")
            pre = await c.options(dash_base + "/memory", headers={"Origin": "http://localhost:5173",
                                                                   "Access-Control-Request-Method": "DELETE"})
            check(pre.status_code == 200, "CORS preflight (Clear Memory) not blocked", f"HTTP {pre.status_code}")
            viewer = RTCPeerConnection(RTCConfiguration(iceServers=[]))
            viewer.addTransceiver("video", direction="recvonly")
            await viewer.setLocalDescription(await viewer.createOffer())
            r = await c.post(dash_base + "/stream/view", json={
                "sdp": viewer.localDescription.sdp, "type": viewer.localDescription.type, "device_id": "phone-code"})
            check(r.status_code == 200, "dashboard WebRTC viewer connects without the code", f"HTTP {r.status_code}")
            r = await c.delete(dash_base + "/memory?device=phone-code")
            check(r.status_code == 200, "Clear Memory from the dashboard", f"HTTP {r.status_code}")
    finally:
        for pc in (phone, viewer):
            if pc:
                await pc.close()
        server.terminate()
        server.wait(10)
        log.close()
        wipe(store)
    tracebacks = "Traceback" in (HERE / "test_access_server.log").read_text(encoding="utf-8")
    check(not tracebacks, "server log: no tracebacks", "")
    passed = sum(ok for ok, _, _ in results)
    print(f"\n{passed}/{len(results)} access-code checks passed")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
