"""
Live video streaming hub (spec step 1: "The phone sends live video to your server").

    phone --WebRTC--> backend --WebRTC--> dashboard(s)
                         |
                         +--> frame sampler --> memory pipeline (stage 2)

Why the backend is the WebRTC peer (not the dashboard):
  * frames reach the server even when no dashboard is open;
  * the phone sends ONE stream; the backend forwards the same
    encoded video to any number of viewers (aiortc MediaRelay),
    so viewers add no load on the phone.

Signalling is plain HTTP (one offer -> one answer, ICE gathered
before answering), which is reliable on a LAN and needs no
WebSocket relay.

    POST /stream/publish   phone offer  -> answer
    POST /stream/view      viewer offer -> answer (recvonly)
    GET  /stream/status    live status for the dashboard and phone
    GET  /stream/latest.jpg  most recent frame (local only)

Several phones can stream at the same time. Each phone sends a stable
device_id; every device has its own stream, frame selection, memory
and questions (session isolation). A phone that reconnects replaces only
its own previous connection. Requests without device_id use "phone".
"""

import asyncio
import io
import re
import time
import uuid
from typing import Awaitable, Callable

from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaRelay
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from PIL import Image
from pydantic import BaseModel

router = APIRouter(tags=["Live Stream"])

SAMPLE_INTERVAL_S = 0.5          # frames handed to the memory pipeline: 2 per second

# Phone and laptop are on the same LAN: host candidates are enough.
# aiortc's default config queries a public STUN server while
# gathering, which made negotiation take ~10 s (measured).
LAN_CONFIG = RTCConfiguration(iceServers=[])
STALE_AFTER_S = 5.0              # no frame for 5 s -> stream reported as stalled
MAX_DEVICES = 4                  # each stream is decoded on the server (CPU)
DEFAULT_DEVICE = "phone"
_DEVICE_RE = re.compile(r"[A-Za-z0-9_-]{1,40}")


def device_name(value: str | None) -> str:
    value = (value or "").strip() or DEFAULT_DEVICE
    if not _DEVICE_RE.fullmatch(value):
        raise HTTPException(400, "Invalid device_id.")
    return value


class SessionDescription(BaseModel):
    sdp: str
    type: str
    device_id: str | None = None


FrameCallback = Callable[[Image.Image, float, str, str], Awaitable[None]]


class DeviceStream:
    """One phone's live stream: publisher, viewers and frame sampler."""

    def __init__(self, device: str, callbacks: list[FrameCallback]) -> None:
        self.device = device
        self.relay = MediaRelay()
        self.publisher: RTCPeerConnection | None = None
        self.track = None
        self.session_id: str | None = None
        self.started_at: float | None = None
        self.viewers: set[RTCPeerConnection] = set()
        self.frames_received = 0
        self.frames_sampled = 0
        self.last_frame_at: float | None = None
        self.size: tuple[int, int] | None = None
        self.latest: tuple[Image.Image, float] | None = None
        self.last_decoded = None                  # (av.VideoFrame, time): the newest frame, for /ask
        self.callbacks = callbacks               # shared list owned by the hub
        self._tap_task: asyncio.Task | None = None
        self._busy = False
        self.dropped_samples = 0
        self.lock = asyncio.Lock()

    # ---------------- publisher (phone) ----------------

    async def publish(self, offer: SessionDescription) -> RTCSessionDescription:
        async with self.lock:
            await self._close_publisher()
            pc = RTCPeerConnection(LAN_CONFIG)
            self.publisher = pc
            self.session_id = uuid.uuid4().hex[:12]
            self.started_at = time.time()
            self.frames_received = self.frames_sampled = 0
            session = self.session_id

            @pc.on("track")
            def on_track(track) -> None:
                if track.kind != "video" or self.publisher is not pc:
                    return
                self.track = track
                self._tap_task = asyncio.create_task(self._tap(self.relay.subscribe(track), session))
                print(f"[STREAM] {self.device}: video track received (session {session})", flush=True)

            @pc.on("connectionstatechange")
            async def on_state() -> None:
                print(f"[STREAM] {self.device} publisher {session}: {pc.connectionState}", flush=True)
                if pc.connectionState in {"failed", "closed"} and self.publisher is pc:
                    async with self.lock:
                        if self.publisher is pc:
                            await self._close_publisher()

            await pc.setRemoteDescription(RTCSessionDescription(sdp=offer.sdp, type=offer.type))
            answer = await pc.createAnswer()
            await pc.setLocalDescription(answer)     # waits until ICE gathering is complete
            return pc.localDescription

    async def _close_publisher(self) -> None:
        if self._tap_task:
            self._tap_task.cancel()
            self._tap_task = None
        if self.publisher:
            await self.publisher.close()
        self.publisher = None
        self.track = None
        self.session_id = None
        # Viewers were bound to the old track: close them; the
        # dashboard reconnects when it sees a new session id.
        for viewer in list(self.viewers):
            await viewer.close()
        self.viewers.clear()

    async def _tap(self, track, session: str) -> None:
        last_sample = 0.0
        try:
            while True:
                frame = await track.recv()
                now = time.time()
                self.frames_received += 1
                self.last_frame_at = now
                self.size = (frame.width, frame.height)
                self.last_decoded = (frame, now)  # questions use the newest frame
                if now - last_sample < SAMPLE_INTERVAL_S:
                    continue
                last_sample = now
                image = frame.to_image()
                self.latest = (image, now)       # always fresh, even while memory is busy
                self.frames_sampled += 1
                if self._busy:
                    # Never block video reception: if the memory
                    # pipeline is still working, skip this sample.
                    self.dropped_samples += 1
                    continue
                if self.callbacks:
                    self._busy = True
                    asyncio.create_task(self._dispatch(image, now, session))
        except asyncio.CancelledError:
            raise
        except Exception as error:  # MediaStreamError when the phone stops
            print(f"[STREAM] track ended ({type(error).__name__})", flush=True)

    async def _dispatch(self, image: Image.Image, at: float, session: str) -> None:
        try:
            for callback in self.callbacks:
                await callback(image, at, session, self.device)
        except Exception as error:
            print(f"[STREAM] frame callback error: {type(error).__name__}: {error}", flush=True)
        finally:
            self._busy = False

    # ---------------- viewers (dashboard) ----------------

    async def view(self, offer: SessionDescription) -> RTCSessionDescription:
        if self.track is None:
            raise HTTPException(409, "No live stream yet. Start streaming on the phone.")
        pc = RTCPeerConnection(LAN_CONFIG)
        self.viewers.add(pc)

        @pc.on("connectionstatechange")
        async def on_state() -> None:
            if pc.connectionState in {"failed", "closed"}:
                self.viewers.discard(pc)

        pc.addTrack(self.relay.subscribe(self.track))
        await pc.setRemoteDescription(RTCSessionDescription(sdp=offer.sdp, type=offer.type))
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        return pc.localDescription

    # ---------------- status ----------------

    def status(self) -> dict:
        now = time.time()
        live = self.track is not None and self.last_frame_at is not None \
            and now - self.last_frame_at < STALE_AFTER_S
        elapsed = now - self.started_at if self.started_at else 0
        return {
            "device": self.device,
            "session_id": self.session_id,
            "connected": self.publisher is not None and self.publisher.connectionState == "connected",
            "live": live,
            "stalled": self.track is not None and not live,
            "since": self.started_at,
            "resolution": f"{self.size[0]}x{self.size[1]}" if self.size else None,
            "fps": round(self.frames_received / elapsed, 1) if elapsed > 1 else None,
            "frames_received": self.frames_received,
            "frames_sampled": self.frames_sampled,
            "samples_skipped_busy": self.dropped_samples,
            "last_frame_age_s": round(now - self.last_frame_at, 1) if self.last_frame_at else None,
            "viewers": len(self.viewers),
        }

    def latest_jpeg(self, max_side: int = 1280) -> bytes | None:
        if not self.latest:
            return None
        image = self.latest[0].copy()
        image.thumbnail((max_side, max_side))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=85)
        return buffer.getvalue()

    def fresh_image(self) -> tuple[Image.Image, float] | None:
        """The newest decoded frame as an image (at most one frame old)."""
        if self.last_decoded is None:
            return self.latest
        frame, at = self.last_decoded
        return frame.to_image(), at

    def is_live(self) -> bool:
        return self.track is not None and self.last_frame_at is not None \
            and time.time() - self.last_frame_at < STALE_AFTER_S

    async def close(self) -> None:
        async with self.lock:
            await self._close_publisher()


class StreamHub:
    """All devices. Frame callbacks receive (image, time, session, device)."""

    def __init__(self) -> None:
        self.devices: dict[str, DeviceStream] = {}
        self.callbacks: list[FrameCallback] = []
        self.lock = asyncio.Lock()

    def get(self, device: str | None) -> DeviceStream | None:
        return self.devices.get(device or DEFAULT_DEVICE)

    def primary(self) -> DeviceStream | None:
        """The device shown by default: live first, then most recently started."""
        if not self.devices:
            return None
        return max(self.devices.values(), key=lambda d: (d.is_live(), d.started_at or 0))

    async def publish(self, device: str, offer: SessionDescription) -> tuple[DeviceStream, RTCSessionDescription]:
        async with self.lock:
            stream = self.devices.get(device)
            if stream is None:
                active = [d for d in self.devices.values() if d.publisher is not None]
                if len(active) >= MAX_DEVICES:
                    raise HTTPException(503, f"Too many phones streaming (max {MAX_DEVICES}).")
                for name, d in list(self.devices.items()):     # forget stopped devices
                    if d.publisher is None and not d.viewers:
                        del self.devices[name]
                stream = self.devices[device] = DeviceStream(device, self.callbacks)
        return stream, await stream.publish(offer)

    def status(self, device: str | None = None) -> dict:
        chosen = self.get(device) if device else self.primary()
        base = chosen.status() if chosen else DeviceStream(device or DEFAULT_DEVICE, []).status()
        base["devices"] = [d.status() for d in sorted(self.devices.values(), key=lambda d: d.device)]
        return base

    def fresh(self, device: str | None = None) -> tuple[Image.Image, float] | None:
        stream = self.get(device) if device else self.primary()
        return stream.fresh_image() if stream else None

    def latest(self, device: str | None = None) -> tuple[Image.Image, float] | None:
        stream = self.get(device) if device else self.primary()
        return stream.latest if stream else None

    async def close(self) -> None:
        for stream in list(self.devices.values()):
            await stream.close()


hub = StreamHub()


@router.post("/stream/publish")
async def publish(offer: SessionDescription):
    stream, answer = await hub.publish(device_name(offer.device_id), offer)
    return {"sdp": answer.sdp, "type": answer.type, "session_id": stream.session_id,
            "device": stream.device}


@router.post("/stream/view")
async def view(offer: SessionDescription):
    stream = hub.get(device_name(offer.device_id)) if offer.device_id else hub.primary()
    if stream is None:
        raise HTTPException(409, "No live stream yet. Start streaming on the phone.")
    answer = await stream.view(offer)
    return {"sdp": answer.sdp, "type": answer.type, "session_id": stream.session_id,
            "device": stream.device}


@router.get("/stream/status")
async def status(device: str | None = None):
    return hub.status(device_name(device) if device else None)


@router.get("/stream/latest.jpg")
async def latest(request: Request, device: str | None = None):
    if request.client and request.client.host not in {"127.0.0.1", "::1"}:
        raise HTTPException(403, "Local only.")
    stream = hub.get(device_name(device)) if device else hub.primary()
    data = stream.latest_jpeg() if stream else None
    if data is None:
        raise HTTPException(404, "No frame yet.")
    return Response(content=data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})
