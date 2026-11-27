"""
Face privacy of everything the server stores.

Offline (no API):
  detection on real photos (incl. a person turned away), no false alarms
  on face-free frames, faces unrecognisable after protection, signs and
  face-free frames pixel-identical (OCR unaffected), speed, fail-safe
  (no detector -> frame NOT stored), memory frames stored protected,
  sweep of frames stored before this change, Photo Mode blur + 30-minute
  retention.
Live (--live, real server + Gemini):
  phone streams a wall with a framed portrait next to a sign -> every
  stored frame has no detectable face, OCR still reads the sign, the
  answer about the sign is right; legacy clear frames and old photos
  handled at server start.

Test images: OpenCV's public sample photos (messi5.jpg, basketball1.png),
downloaded to backend/storage/test_faces (git-ignored). To test with your
own photo of a person who agreed: --face path\\to\\photo.jpg

Usage: .venv\\Scripts\\python test_privacy.py [--live] [--face PATH]
Quota (--live): ~3 Gemini analysis requests + 1 answer.
"""

import asyncio
import io
import os
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import httpx
from PIL import Image, ImageChops, ImageDraw, ImageStat

import image_storage
import memory_service as m
import privacy
from test_assistant_flow import draw_scene
from test_helpers import wipe
from test_memory import font, scene

HERE = Path(__file__).resolve().parent
FACES = HERE / "storage" / "test_faces"
SAMPLE_URL = "https://raw.githubusercontent.com/opencv/opencv/4.x/samples/data/"
results: list[tuple[bool, str, str]] = []


def check(ok, name: str, detail: str = "") -> None:
    results.append((bool(ok), name, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}  -> {detail}", flush=True)


def sample(name: str) -> Image.Image:
    path = FACES / name
    if not path.exists():
        FACES.mkdir(parents=True, exist_ok=True)
        path.write_bytes(httpx.get(SAMPLE_URL + name, timeout=30, follow_redirects=True).content)
    return Image.open(path).convert("RGB")


def own_face() -> Image.Image | None:
    if "--face" in sys.argv:
        return Image.open(sys.argv[sys.argv.index("--face") + 1]).convert("RGB")
    return None


SIGN_BOX = (60, 170, 420, 330)


def portrait_scene(face: Image.Image, text: str = "PHARMACY", jitter: int = 0) -> Image.Image:
    """A wall with a sign (left) and a framed portrait photo (right)."""
    img = Image.new("RGB", (960, 720), (128, 124, 118))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 560, 960, 720], fill=(80, 70, 60))
    x0, y0, x1, y1 = SIGN_BOX
    d.rectangle([x0 + jitter, y0, x1 + jitter, y1], fill=(25, 90, 160), outline="white", width=6)
    d.text((x0 + 25 + jitter, y0 + 40), text, fill="white", font=font(64))
    d.rectangle([560 + jitter, 90, 860 + jitter, 470], fill=(60, 40, 20))      # frame
    img.paste(face.resize((260, 340)), (580 + jitter, 110))
    return img


def face_crop(photo: Image.Image, box) -> Image.Image:
    x, y, w, h = box
    return photo.crop((x - w, y - h // 2, x + 2 * w, y + int(1.8 * h)))


def detected(image: Image.Image) -> list:
    return privacy.detect_faces(image)


# ======================================================================

def offline() -> None:
    print("\n[1] Detection", flush=True)
    check(privacy.available(), "face detector loads (YuNet, local)", str(privacy.MODEL_PATH.name))
    messi, basket = sample("messi5.jpg"), sample("basketball1.png")
    b_boxes = detected(basket)
    near = lambda boxes, x, y: any(abs(bx - x) < 30 and abs(by - y) < 30 for bx, by, *_ in boxes)
    check(near(b_boxes, 72, 92) and near(b_boxes, 510, 55),
          "both people found, including the man turned away (near profile)",
          f"{[(x, y, w, h, round(s, 2)) for x, y, w, h, s in b_boxes]}")
    m_boxes = detected(messi)
    check(near(m_boxes, 225, 93), "player's face found in a busy sports photo", f"{len(m_boxes)} face(s)")

    face = face_crop(basket, (72, 92, 32, 41))
    portrait = portrait_scene(own_face() or face)
    p_boxes = detected(portrait)
    check(any(560 <= x <= 860 for x, *_ in p_boxes), "face in a framed PORTRAIT on the wall found",
          f"{[(x, y, w, h, round(s, 2)) for x, y, w, h, s in p_boxes]}")

    face_free = [draw_scene(k, j) for k in ("exit", "door", "wall", "cafe") for j in (0, 3)] + \
                [scene(t, i) for i, t in enumerate(["EXIT", "ROOM 12", "SORTIE", "CAFE", "مخرج الطوارئ"])]
    false_alarms = sum(len(detected(im)) for im in face_free)
    check(false_alarms == 0, "no false alarms on 13 face-free sign / door / wall frames", f"{false_alarms}")

    print("\n[2] Protection", flush=True)
    for name, im in (("basketball1", basket), ("messi5", messi), ("portrait on wall", portrait)):
        protected, n = privacy.protect_faces(im)
        left = [b for b in detected(protected) if b[4] >= privacy.SCORE_THRESHOLD]
        check(n >= 1 and not left, f"{name}: {n} face(s) anonymised, none detectable afterwards",
              f"after: {len(left)}")
    protected, _ = privacy.protect_faces(portrait)
    sign_same = ImageChops.difference(portrait.crop(SIGN_BOX), protected.crop(SIGN_BOX)).getbbox() is None
    check(sign_same, "the sign next to the portrait keeps its exact pixels (OCR unaffected)", "")
    identical = all(ImageChops.difference(im, privacy.protect_faces(im)[0]).getbbox() is None for im in face_free)
    check(identical, "face-free frames are stored pixel-identical", "13 frames")
    x, y, w, h, _ = max(detected(basket), key=lambda b: b[4])
    before = basket.crop((x, y, x + w, y + h)).convert("L")
    after = privacy.protect_faces(basket)[0].crop((x, y, x + w, y + h)).convert("L")
    detail_before = ImageStat.Stat(before).stddev[0]
    detail_after = ImageStat.Stat(after).stddev[0]
    check(detail_after < detail_before * 0.5, "face detail removed (pixelate + blur, not reversible)",
          f"pixel std {detail_before:.1f} -> {detail_after:.1f}")

    # Requirement: protection must keep up with storage (at most one frame per
    # 1.5 s window per phone) using < 10 % of that time; it never runs on the
    # answer path (questions use the in-memory current view).
    def timing(image):
        values = []
        for _ in range(20):
            t = time.perf_counter()
            privacy.protect_faces(image)
            values.append((time.perf_counter() - t) * 1000)
        values.sort()
        return statistics.median(values), values[int(0.95 * (len(values) - 1))]
    rows, worst = [], 0.0
    # Stored sizes the phone produces: 640x480 (Android default) and 1280x720 -> 1024x576.
    for size in ((640, 480), (1024, 576)):
        face_t, plain_t = timing(portrait.resize(size)), timing(face_free[-1].resize(size))
        worst = max(worst, face_t[1], plain_t[1])
        rows.append(f"{size[0]}x{size[1]}: no face {plain_t[0]:.0f}/{plain_t[1]:.0f} ms, "
                    f"face {face_t[0]:.0f}/{face_t[1]:.0f} ms")
    test_size = timing(portrait)
    check(worst < 150, "time per stored frame p95 < 150 ms (10 % of the 1.5 s window), median/p95",
          "; ".join(rows) + f" | (test scene 960x720 with face: {test_size[0]:.0f}/{test_size[1]:.0f} ms)")

    print("\n[3] Visual memory storage", flush=True)
    tmp = Path(tempfile.mkdtemp())
    m.FRAMES_DIR = tmp / "frames"

    async def memory_checks():
        svc = m.MemoryService()
        svc.store = m.MemoryStore(tmp / "mem.db")
        svc.privacy_stats = {"skipped_frames": 0, "legacy_protected": 0, "legacy_deleted": 0, "orphans_deleted": 0}
        sample_ = {"image": portrait, "at": time.time(), "sharpness": 100.0, "brightness": 120.0, "edges": 10.0,
                   "novelty": 30, "reason": "new view"}
        await svc._keep(sample_, "s1", "phone")
        row = (await svc.store.run(svc.store._pending, 5))[0]
        stored = Image.open(row["path"]).convert("RGB")
        check(row["faces"] >= 1 and not [b for b in detected(stored) if b[4] >= privacy.SCORE_THRESHOLD],
              "memory frame written to disk with the portrait face anonymised", f"faces column {row['faces']}")

        # Fail-safe: the detector cannot run -> the frame is NOT stored at all.
        saved, privacy._detector, privacy.MODEL_PATH = privacy.MODEL_PATH, None, tmp / "missing.onnx"
        try:
            before_files = len(list(m.FRAMES_DIR.glob("*.jpg")))
            await svc._keep(dict(sample_, at=time.time()), "s1", "phone")
            after_files = len(list(m.FRAMES_DIR.glob("*.jpg")))
            rows = await svc.store.run(svc.store._pending, 10)
        finally:
            privacy.MODEL_PATH, privacy._detector = saved, None
        check(after_files == before_files and len(rows) == 1 and svc.privacy_stats["skipped_frames"] == 1,
              "fail-safe: face check impossible -> frame NOT written (no file, no row)",
              f"files {before_files}->{after_files}, rows {len(rows)}, skipped {svc.privacy_stats['skipped_frames']}")

        # Frames stored BEFORE this change (faces NULL) + an orphan file + an unreadable one.
        legacy_id, broken_id = uuid.uuid4().hex, uuid.uuid4().hex
        legacy_path, broken_path = m.FRAMES_DIR / f"{legacy_id}.jpg", m.FRAMES_DIR / f"{broken_id}.jpg"
        portrait.save(legacy_path, format="JPEG", quality=80)
        broken_path.write_bytes(b"not a jpeg")
        orphan = m.FRAMES_DIR / f"{uuid.uuid4().hex}.jpg"
        portrait.save(orphan, format="JPEG")
        for fid, path in ((legacy_id, legacy_path), (broken_id, broken_path)):
            await svc.store.run(svc.store._insert_frame, {
                "id": fid, "session": "old", "captured_at": time.time() - 60, "path": str(path), "width": 960,
                "height": 720, "sharpness": 1.0, "brightness": 1.0, "edges": 1.0, "novelty": 1,
                "enhanced": "", "keep_reason": "new view", "faces": None})
        await svc._protect_existing()
        legacy_row = await svc.store.run(svc.store._frame, legacy_id)
        legacy_faces = [b for b in detected(Image.open(legacy_path).convert("RGB")) if b[4] >= privacy.SCORE_THRESHOLD]
        check(legacy_row["faces"] >= 1 and not legacy_faces,
              "frames stored before this change: anonymised in place at start", f"faces {legacy_row['faces']}")
        check(await svc.store.run(svc.store._frame, broken_id) is None and not broken_path.exists(),
              "an old frame that cannot be checked is deleted (row + file)", "")
        check(not orphan.exists(), "image files without a database row are deleted", "")
        check(not await svc.store.run(svc.store._unchecked), "no unchecked frame remains", "")
        svc.store.db.close()

    asyncio.run(memory_checks())
    wipe(tmp)

    print("\n[4] Photo Mode storage", flush=True)
    tmp = Path(tempfile.mkdtemp())
    saved_dir, image_storage.STORAGE_DIR = image_storage.STORAGE_DIR, tmp
    try:
        buffer = io.BytesIO()
        basket.save(buffer, format="JPEG")
        name = image_storage.save_blurred_photo(buffer.getvalue())
        stored = Image.open(tmp / name).convert("RGB")
        check(not detected(stored), "Photo Mode: stored photo fully blurred, no detectable face", name)
        old = tmp / f"{uuid.uuid4().hex}.jpg"
        old.write_bytes((tmp / name).read_bytes())
        past = time.time() - 31 * 60
        os.utime(old, (past, past))
        deleted = image_storage.delete_old_photos()
        check(deleted == 1 and not old.exists() and (tmp / name).exists(),
              "Photo Mode: photos older than 30 min deleted, recent kept", f"deleted {deleted}")
    finally:
        image_storage.STORAGE_DIR = saved_dir
        wipe(tmp)


# ======================================================================

async def live() -> None:
    print("\n[5] Live: real server, phone streams a portrait next to a sign", flush=True)
    from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
    from av import VideoFrame

    from test_live_latency import free_port

    basket = sample("basketball1.png")
    face = own_face() or face_crop(basket, (72, 92, 32, 41))

    class PortraitWalk(VideoStreamTrack):
        def __init__(self):
            super().__init__()
            self.start = time.time()

        async def recv(self):
            pts, time_base = await self.next_timestamp()
            first = time.time() - self.start < 12
            img = portrait_scene(face, "PHARMACY" if first else "ROOM 12", int(time.time() * 3) % 6)
            frame = VideoFrame.from_image(img)
            frame.pts, frame.time_base = pts, time_base
            return frame

    store, photos = HERE / "storage" / "memory_privacy_test", HERE / "storage" / "photos_privacy_test"
    wipe(store)
    wipe(photos)
    # Before the server starts: an old-style clear frame with a face, and photos old and new.
    (store / "frames").mkdir(parents=True)
    legacy = m.MemoryStore.__new__(m.MemoryStore)
    db = sqlite3.connect(store / "memory.db")
    db.executescript("CREATE TABLE frames (id TEXT PRIMARY KEY, session TEXT, captured_at REAL NOT NULL, "
                     "path TEXT NOT NULL, width INTEGER, height INTEGER, sharpness REAL, brightness REAL, "
                     "edges REAL, novelty INTEGER, enhanced TEXT, keep_reason TEXT, status TEXT NOT NULL DEFAULT "
                     "'pending', caption TEXT, ocr_text TEXT, objects TEXT, hazards TEXT, text_languages TEXT, "
                     "embedding BLOB, analyzed_at REAL)")
    legacy_id = uuid.uuid4().hex
    legacy_path = store / "frames" / f"{legacy_id}.jpg"
    portrait_scene(face, "EXIT").save(legacy_path, format="JPEG")
    db.execute("INSERT INTO frames (id, session, captured_at, path, status) VALUES (?, 'old', ?, ?, 'failed')",
               (legacy_id, time.time() - 120, str(legacy_path)))
    db.commit()
    db.close()
    del legacy
    photos.mkdir(parents=True)
    old_photo, new_photo = photos / f"{uuid.uuid4().hex}.jpg", photos / f"{uuid.uuid4().hex}.jpg"
    for p in (old_photo, new_photo):
        p.write_bytes(b"\xff\xd8 blurred")
    past = time.time() - 40 * 60
    os.utime(old_photo, (past, past))

    port = free_port()
    base = f"http://127.0.0.1:{port}"
    log = open(HERE / "test_privacy_server.log", "w", encoding="utf-8")
    server = subprocess.Popen(
        [str(HERE / ".venv" / "Scripts" / "python.exe"), "-m", "uvicorn", "main:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=HERE, stdout=log, stderr=subprocess.STDOUT,
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "WV_MEMORY_DIR": str(store), "WV_PHOTO_DIR": str(photos),
             "WV_MEMORY_ANALYZE_EVERY": "6"})
    phone = None
    try:
        async with httpx.AsyncClient(timeout=90) as c:
            for _ in range(80):
                try:
                    await c.get(base + "/health")
                    break
                except httpx.HTTPError:
                    await asyncio.sleep(0.25)
            await asyncio.sleep(2)
            legacy_img = Image.open(legacy_path).convert("RGB")
            check(not [b for b in detected(legacy_img) if b[4] >= privacy.SCORE_THRESHOLD],
                  "server start: clear frame from before the change anonymised", "")
            check(not old_photo.exists() and new_photo.exists(),
                  "server start: Photo Mode photo older than 30 min deleted, recent kept", "")

            phone = RTCPeerConnection(RTCConfiguration(iceServers=[]))
            phone.addTrack(PortraitWalk())
            await phone.setLocalDescription(await phone.createOffer())
            a = (await c.post(base + "/stream/publish", json={
                "sdp": phone.localDescription.sdp, "type": phone.localDescription.type})).json()
            await phone.setRemoteDescription(RTCSessionDescription(sdp=a["sdp"], type=a["type"]))
            await asyncio.sleep(30)

            frames = [f for f in (await c.get(base + "/memory/frames")).json()["frames"] if f["id"] != legacy_id]
            leaks, blurred = 0, 0
            for f in frames:
                img = Image.open(io.BytesIO((await c.get(base + f["image_url"])).content)).convert("RGB")
                leaks += bool([b for b in detected(img) if b[4] >= privacy.SCORE_THRESHOLD])
                blurred += (f["faces_blurred"] or 0) > 0
            check(frames and leaks == 0 and blurred == len(frames),
                  "every stored frame: portrait face anonymised before storage",
                  f"{len(frames)} frames, {blurred} with faces blurred, {leaks} with a detectable face")
            texts = " | ".join(f["text"] for f in frames if f["status"] == "analyzed")
            check("PHARMACY" in texts.upper() and "ROOM 12" in texts.upper(),
                  "OCR still reads the signs next to the portrait", texts[:100])
            r = await c.post(base + "/ask", data={"question": "What is written on the sign in front of me?"})
            ans = r.json()
            check(r.status_code == 200 and "ROOM 12" in ans["answer"].upper(), "answer about the sign is correct",
                  f"{ans.get('answer', r.text)[:80]} (gemini {ans.get('timings', {}).get('gemini')} s)")
            stats = (await c.get(base + "/memory/stats")).json()["privacy"]
            check(stats["failures"] == 0 and stats["skipped_frames"] == 0,
                  "no frame skipped, no detector failure while streaming",
                  f"checked {stats['frames']} frames, {stats['faces']} faces, last {stats['last_ms']} ms")
    finally:
        if phone:
            await phone.close()
        server.terminate()
        server.wait(10)
        log.close()
        wipe(store)
        wipe(photos)
    tracebacks = "Traceback" in (HERE / "test_privacy_server.log").read_text(encoding="utf-8")
    check(not tracebacks, "server log: no tracebacks", "")


if __name__ == "__main__":
    offline()
    if "--live" in sys.argv:
        asyncio.run(live())
    passed = sum(ok for ok, _, _ in results)
    print(f"\n{passed}/{len(results)} privacy checks passed")
    sys.exit(0 if passed == len(results) else 1)
