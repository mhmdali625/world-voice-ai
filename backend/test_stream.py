"""
Streaming integration test (no phone needed).

A simulated phone (aiortc) streams a moving test pattern to a real
backend server; a simulated dashboard watches it through the backend.
Checks: negotiation over HTTP, frames reach the server, status, viewer
receives video, latest frame, publisher reconnect, viewer before stream.

Usage: .venv\\Scripts\\python test_stream.py
"""

import asyncio
import fractions
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from av import VideoFrame
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
RESULTS = []


def record(name, ok, detail=""):
    RESULTS.append(ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  -> ' + detail) if detail else ''}", flush=True)


class TestPattern(VideoStreamTrack):
    """640x480 moving pattern with text, 15 fps (like the phone)."""

    def __init__(self, label="SIGN: EXIT"):
        super().__init__()
        self.label = label
        self.n = 0

    async def recv(self):
        pts, time_base = await self.next_timestamp()
        image = Image.new("RGB", (640, 480), (30, 60, 90))
        draw = ImageDraw.Draw(image)
        x = (self.n * 7) % 560
        draw.rectangle([x, 200, x + 80, 280], fill=(240, 200, 40))
        draw.text((40, 40), f"{self.label} {self.n}", fill=(255, 255, 255))
        self.n += 1
        frame = VideoFrame.from_image(image)
        frame.pts, frame.time_base = pts, time_base
        return frame


async def negotiate(client, url, pc):
    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)
    r = await client.post(url, json={"sdp": pc.localDescription.sdp, "type": pc.localDescription.type})
    if r.status_code != 200:
        return r
    data = r.json()
    await pc.setRemoteDescription(RTCSessionDescription(sdp=data["sdp"], type=data["type"]))
    return r


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def main():
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    log = open(HERE / "test_stream_server.log", "w", encoding="utf-8")
    server = subprocess.Popen(
        [str(HERE / ".venv" / "Scripts" / "python.exe"), "-m", "uvicorn", "main:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=HERE, stdout=log, stderr=subprocess.STDOUT,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            for _ in range(80):
                try:
                    await c.get(base + "/health")
                    break
                except httpx.HTTPError:
                    await asyncio.sleep(0.25)

            print("\n[stream] viewer before any stream")
            viewer0 = RTCPeerConnection(RTCConfiguration(iceServers=[]))
            viewer0.addTransceiver("video", direction="recvonly")
            r = await negotiate(c, base + "/stream/view", viewer0)
            record("viewer before stream -> clean 409 (no crash)", r.status_code == 409, r.text[:80])
            await viewer0.close()

            print("\n[stream] phone publishes")
            phone = RTCPeerConnection(RTCConfiguration(iceServers=[]))
            phone.addTrack(TestPattern())
            t0 = time.perf_counter()
            r = await negotiate(c, base + "/stream/publish", phone)
            record("publish negotiation over HTTP", r.status_code == 200, f"{time.perf_counter() - t0:.2f} s")
            for _ in range(100):
                s = (await c.get(base + "/stream/status")).json()
                if s["live"]:
                    break
                await asyncio.sleep(0.1)
            record("stream live on the server", s["live"], f"after {time.perf_counter() - t0:.2f} s")
            await asyncio.sleep(4)
            s = (await c.get(base + "/stream/status")).json()
            record("frames received at ~15 fps", (s["fps"] or 0) >= 10, f"fps {s['fps']}, {s['frames_received']} frames, {s['resolution']}")
            record("sampled ~2 frames/s for memory", 6 <= s["frames_sampled"] <= 14, f"{s['frames_sampled']} samples")

            img = await c.get(base + "/stream/latest.jpg")
            record("latest frame available as JPEG", img.status_code == 200 and img.content[:2] == b"\xff\xd8",
                   f"{len(img.content)} bytes")

            print("\n[stream] dashboard viewer")
            got = asyncio.Event()
            viewer = RTCPeerConnection(RTCConfiguration(iceServers=[]))
            viewer.addTransceiver("video", direction="recvonly")
            frames_seen = []

            @viewer.on("track")
            def on_track(track):
                async def read():
                    while True:
                        frame = await track.recv()
                        frames_seen.append(frame)
                        got.set()
                asyncio.ensure_future(read())

            r = await negotiate(c, base + "/stream/view", viewer)
            try:
                await asyncio.wait_for(got.wait(), 10)
            except asyncio.TimeoutError:
                pass
            await asyncio.sleep(2)
            record("viewer receives forwarded video", len(frames_seen) > 10, f"{len(frames_seen)} frames in ~2 s")
            s = (await c.get(base + "/stream/status")).json()
            record("status counts the viewer", s["viewers"] == 1)

            print("\n[stream] phone reconnects (new session)")
            first_session = s["session_id"]
            await phone.close()
            phone2 = RTCPeerConnection(RTCConfiguration(iceServers=[]))
            phone2.addTrack(TestPattern("SIGN: ROOM 12"))
            r = await negotiate(c, base + "/stream/publish", phone2)
            for _ in range(100):
                s = (await c.get(base + "/stream/status")).json()
                if s["live"] and s["session_id"] != first_session:
                    break
                await asyncio.sleep(0.1)
            record("republish -> new live session; old viewers closed", s["live"] and s["session_id"] != first_session and s["viewers"] == 0,
                   f"session {first_session} -> {s['session_id']}")

            await phone2.close()
            await asyncio.sleep(6)
            s = (await c.get(base + "/stream/status")).json()
            record("phone stops -> not live (stalled or disconnected)", not s["live"], str({k: s[k] for k in ("live", "stalled", "connected")}))
            await viewer.close()
    finally:
        server.terminate()
        server.wait(10)
        log.close()
    text = (HERE / "test_stream_server.log").read_text(encoding="utf-8", errors="replace")
    bad = [l for l in text.splitlines() if "Traceback" in l or "ERROR" in l]
    record("server log: no tracebacks", not bad, "; ".join(bad[:2])[:200])
    print(f"\n{sum(RESULTS)}/{len(RESULTS)} stream checks passed")
    sys.exit(0 if all(RESULTS) else 1)


if __name__ == "__main__":
    asyncio.run(main())
