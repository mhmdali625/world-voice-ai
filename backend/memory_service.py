"""
Visual memory: the spec's "Remember" step and multimedia database.

  live frames (2/s from stream_service)
     -> select useful frames   : sharp, new, informative     (Pillow)
     -> improve poor frames     : dark / soft frames enhanced (bonus)
     -> store                   : JPEG file + SQLite row (time, metrics)
     -> analyse in batches      : Gemini reads text (OCR), objects,
                                  caption, hazards - up to 4 frames
                                  per request, at most 1 request / 8 s
     -> index                   : multilingual embeddings (meaning)
                                  + SQLite FTS5 (keywords)
     -> retention               : frames older than 30 min deleted

Search: by time (timeline, "what did I just pass") and by meaning.
Devices: every phone (device_id) has its own frame selector, and all
reads can be limited to one device, so phones never see each other's
memory or questions.
Privacy: no face recognition; people are never identified; only a
downscaled copy of useful frames is kept, and only for 30 minutes.
"""

import asyncio
import io
import json
import math
import os
import sqlite3
import struct
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from google import genai
from google.genai import errors, types
from PIL import Image, ImageFilter, ImageOps, ImageStat
from pydantic import BaseModel, Field

import config
import hazards as hazard_rules
import groq_vision
import privacy
import vision
from quota import budget

router = APIRouter(tags=["Visual Memory"])

STORE_DIR = Path(os.getenv("WV_MEMORY_DIR") or Path(__file__).resolve().parent / "storage" / "memory")
FRAMES_DIR = STORE_DIR / "frames"
DB_PATH = STORE_DIR / "memory.db"

RETENTION_S = float(os.getenv("WV_MEMORY_RETENTION_MIN", "30")) * 60
ANALYZE_EVERY_S = float(os.getenv("WV_MEMORY_ANALYZE_EVERY", "8"))
if os.getenv("WV_PRESENTATION_MODE", "0").strip() in {"1", "true", "yes"}:
    ANALYZE_EVERY_S = max(ANALYZE_EVERY_S, 15.0)           # presentation mode: conserve quota
BATCH_SIZE = int(os.getenv("WV_MEMORY_BATCH", "4"))
MAX_FRAMES = int(os.getenv("WV_MEMORY_MAX_FRAMES", "600"))
EMBED_MODEL = os.getenv("WV_EMBED_MODEL", "gemini-embedding-001")
EMBED_DIM = 768
MAX_ANALYSIS_ATTEMPTS = 3
# A "periodic refresh" frame within this many bits of an analysed frame of
# the same scene (last REUSE_WINDOW_S) reuses its analysis: no API request.
REUSE_BITS = int(os.getenv("WV_REUSE_BITS", "6"))
REUSE_WINDOW_S = 180.0
# Local-first: frames the local model has not reached after this many seconds
# go to the cloud (memory never falls behind while walking).
LOCAL_OVERFLOW_S = float(os.getenv("WV_LOCAL_OVERFLOW_S", "45"))

# Selection thresholds (calibrated on 320-px grayscale analysis images).
WINDOW_S = 1.5            # pick the sharpest sample in each 1.5 s window
NOVELTY_BITS = 10         # dHash distance (of 64) that counts as a new view
REFRESH_S = 20.0          # keep a frame at least every 20 s if still useful
MIN_SHARPNESS = 12.0      # absolute floor (variance of Laplacian)
SHARP_RATIO = 0.55        # and >= 55 % of the recent median sharpness
MIN_EDGES = 3.0           # mean edge strength: blank wall/ceiling below this
DARK_BELOW = 70.0         # mean brightness (0-255) that triggers enhancement
TOO_DARK = 18.0           # unusable even after enhancement


# ======================================================================
# 1. FRAME QUALITY (pure functions, unit-tested)
# ======================================================================

_LAPLACIAN = ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1, offset=128)


def analysis_image(image: Image.Image) -> Image.Image:
    small = image.convert("L")
    small.thumbnail((320, 320))
    # Trim 2 px so edge filters do not see the image border as detail.
    w, h = small.size
    return small.crop((2, 2, w - 2, h - 2)) if w > 8 and h > 8 else small


def sharpness(gray: Image.Image) -> float:
    return float(ImageStat.Stat(gray.filter(_LAPLACIAN)).var[0])


def edge_density(gray: Image.Image) -> float:
    return float(ImageStat.Stat(gray.filter(ImageFilter.FIND_EDGES)).mean[0])


def brightness(gray: Image.Image) -> float:
    return float(ImageStat.Stat(gray).mean[0])


def dhash(gray: Image.Image) -> int:
    small = gray.resize((9, 8), Image.Resampling.BILINEAR)
    pixels = list(small.getdata())
    value = 0
    for row in range(8):
        for col in range(8):
            value = (value << 1) | (pixels[row * 9 + col] > pixels[row * 9 + col + 1])
    return value


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def enhance(image: Image.Image, light: float, sharp: float, baseline: float) -> tuple[Image.Image, list[str]]:
    """Bonus: improve dark or slightly soft frames before analysis."""
    steps = []
    out = image
    if light < DARK_BELOW:
        out = ImageOps.autocontrast(out, cutoff=1)
        gamma = 0.6 if light < 40 else 0.8
        out = out.point(lambda v: int(255 * ((v / 255) ** gamma)))
        steps.append("brightened")
    if baseline and sharp < 0.8 * baseline:
        out = out.filter(ImageFilter.UnsharpMask(radius=2, percent=90, threshold=3))
        steps.append("sharpened")
    return out, steps


class FrameSelector:
    """Keeps only useful frames: sharp, new and informative."""

    def __init__(self) -> None:
        self.window: list[dict] = []
        self.window_start = 0.0
        self.recent_sharpness: list[float] = []
        self.last_kept_hash: int | None = None
        self.last_kept_at = 0.0
        self.last_rejection: str | None = None
        self.stats = {"seen": 0, "kept": 0, "blurry": 0, "duplicate": 0, "uninformative": 0, "too_dark": 0}

    def baseline(self) -> float:
        if not self.recent_sharpness:
            return 0.0
        values = sorted(self.recent_sharpness)
        return values[len(values) // 2]

    def offer(self, image: Image.Image, at: float) -> dict | None:
        """Feed one sample; returns a chosen candidate (dict) or None."""
        gray = analysis_image(image)
        # Sharpness and edges are measured on a contrast-normalised
        # copy: otherwise a dim but perfectly focused frame scores
        # like a blurry one (Laplacian variance scales with contrast^2).
        normalised = ImageOps.autocontrast(gray, cutoff=1)
        sample = {
            "image": image, "at": at, "sharpness": sharpness(normalised),
            "edges": edge_density(normalised), "brightness": brightness(gray), "hash": dhash(gray),
            "rejected": None,
        }
        self.stats["seen"] += 1
        self.recent_sharpness = (self.recent_sharpness + [sample["sharpness"]])[-40:]

        # Close the previous window BEFORE adding this sample, so a
        # window never mixes samples from different moments (e.g. a
        # leftover sample from an earlier scene after a pause).
        result = None
        if self.window and at - self.window_start >= WINDOW_S:
            best = max(self.window, key=lambda s: s["sharpness"])
            self.window = []
            result = self._judge(best)
        if not self.window:
            self.window_start = at
        self.window.append(sample)
        return result

    def flush(self, judge: bool = True) -> dict | None:
        """Close the pending window (e.g. when the stream stops)."""
        if not self.window:
            return None
        best = max(self.window, key=lambda s: s["sharpness"])
        self.window = []
        return self._judge(best) if judge else None

    def _judge(self, s: dict) -> dict | None:
        floor = max(MIN_SHARPNESS, SHARP_RATIO * self.baseline())
        self.last_rejection = None
        if s["brightness"] < TOO_DARK:
            return self._reject("too_dark")
        if s["edges"] < MIN_EDGES:
            return self._reject("uninformative")
        if s["sharpness"] < floor:
            return self._reject("blurry")
        novelty = 64 if self.last_kept_hash is None else hamming(s["hash"], self.last_kept_hash)
        due_refresh = s["at"] - self.last_kept_at >= REFRESH_S
        if novelty < NOVELTY_BITS and not due_refresh:
            return self._reject("duplicate")
        self.last_kept_hash = s["hash"]
        self.last_kept_at = s["at"]
        self.stats["kept"] += 1
        s["novelty"] = novelty
        s["reason"] = "new view" if novelty >= NOVELTY_BITS else "periodic refresh"
        return s

    def _reject(self, reason: str) -> None:
        self.stats[reason] += 1
        self.last_rejection = reason
        return None


# ======================================================================
# 2. DATABASE
# ======================================================================

SCHEMA = """
CREATE TABLE IF NOT EXISTS frames (
    id TEXT PRIMARY KEY,
    session TEXT,
    device TEXT NOT NULL DEFAULT 'phone',
    captured_at REAL NOT NULL,
    path TEXT NOT NULL,
    width INTEGER, height INTEGER,
    sharpness REAL, brightness REAL, edges REAL, novelty INTEGER,
    enhanced TEXT,
    keep_reason TEXT,
    faces INTEGER,                              -- faces anonymised before storage (NULL = never checked)
    dhash TEXT,                                 -- 64-bit image fingerprint (hex): unchanged-scene detection
    analysis_source TEXT,                       -- gemini model name | 'reused' | local model
    status TEXT NOT NULL DEFAULT 'pending',     -- pending | analyzed | failed
    caption TEXT, ocr_text TEXT, objects TEXT, hazards TEXT, text_languages TEXT,
    embedding BLOB,
    analyzed_at REAL
);
CREATE INDEX IF NOT EXISTS frames_time ON frames(captured_at);
CREATE VIRTUAL TABLE IF NOT EXISTS frames_fts USING fts5(id UNINDEXED, caption, ocr_text, objects);
CREATE TABLE IF NOT EXISTS qa (
    id TEXT PRIMARY KEY,
    device TEXT NOT NULL DEFAULT 'phone',
    asked_at REAL NOT NULL,
    question TEXT, question_heard TEXT, answer TEXT, answer_language TEXT,
    frames_used TEXT, evidence TEXT, latency_s REAL, mode TEXT,
    first_word_s REAL                           -- user stopped speaking -> first audio played (from the phone)
);
CREATE INDEX IF NOT EXISTS qa_time ON qa(asked_at);
"""


class MemoryStore:
    def __init__(self, path: Path = DB_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        FRAMES_DIR.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        # WAL: readers never wait for the writer; NORMAL sync is crash-safe
        # in WAL mode (a power cut can lose only the last commits).
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(SCHEMA)
        self._migrate()
        self.lock = asyncio.Lock()

    def _migrate(self) -> None:
        """Older databases get the device and faces columns."""
        for table in ("frames", "qa"):
            columns = {r["name"] for r in self.db.execute(f"PRAGMA table_info({table})")}
            if "device" not in columns:
                self.db.execute(f"ALTER TABLE {table} ADD COLUMN device TEXT NOT NULL DEFAULT 'phone'")
            if table == "qa" and "first_word_s" not in columns:
                self.db.execute("ALTER TABLE qa ADD COLUMN first_word_s REAL")
            if table == "frames" and "dhash" not in columns:
                self.db.execute("ALTER TABLE frames ADD COLUMN dhash TEXT")
                self.db.execute("ALTER TABLE frames ADD COLUMN analysis_source TEXT")
            if table == "frames" and "faces" not in columns:
                self.db.execute("ALTER TABLE frames ADD COLUMN faces INTEGER")   # NULL: frame never checked
        self.db.execute("CREATE INDEX IF NOT EXISTS frames_device_time ON frames(device, captured_at)")
        self.db.execute("CREATE INDEX IF NOT EXISTS qa_device_time ON qa(device, asked_at)")
        self.db.commit()

    async def run(self, fn, *args):
        async with self.lock:
            return await asyncio.to_thread(fn, *args)

    # --- writes ---
    def _insert_frame(self, row: dict) -> None:
        self.db.execute(
            "INSERT INTO frames (id, session, device, captured_at, path, width, height, sharpness, brightness, "
            "edges, novelty, enhanced, keep_reason, faces, dhash) VALUES (:id,:session,:device,:captured_at,:path,"
            ":width,:height,:sharpness,:brightness,:edges,:novelty,:enhanced,:keep_reason,:faces,:dhash)",
            {"device": "phone", "faces": None, "dhash": None, **row})
        self.db.commit()

    def _save_analysis(self, frame_id: str, a: dict, embedding: list[float] | None) -> None:
        blob = struct.pack(f"{len(embedding)}f", *embedding) if embedding else None
        self.db.execute(
            "UPDATE frames SET status='analyzed', caption=?, ocr_text=?, objects=?, hazards=?, "
            "text_languages=?, embedding=?, analyzed_at=? WHERE id=?",
            (a["caption"], a["text"], json.dumps(a["objects"], ensure_ascii=False),
             json.dumps(a["hazards"], ensure_ascii=False), json.dumps(a["text_languages"]),
             blob, time.time(), frame_id))
        self.db.execute("DELETE FROM frames_fts WHERE id=?", (frame_id,))
        self.db.execute("INSERT INTO frames_fts (id, caption, ocr_text, objects) VALUES (?,?,?,?)",
                        (frame_id, a["caption"], a["text"], " ".join(a["objects"])))
        self.db.commit()

    def _set_first_word(self, qa_id: str, seconds: float) -> int:
        cur = self.db.execute("UPDATE qa SET first_word_s=? WHERE id=?", (seconds, qa_id))
        self.db.commit()
        return cur.rowcount

    def _unchecked(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT id, path FROM frames WHERE faces IS NULL").fetchall()

    def _set_faces(self, frame_id: str, faces: int) -> None:
        self.db.execute("UPDATE frames SET faces=? WHERE id=?", (faces, frame_id))
        self.db.commit()

    def _delete_ids(self, ids: list[str]) -> None:
        rows = [self.db.execute("SELECT id, path FROM frames WHERE id=?", (i,)).fetchone() for i in ids]
        for row in rows:
            if row:
                Path(row["path"]).unlink(missing_ok=True)
        self.db.executemany("DELETE FROM frames WHERE id=?", [(i,) for i in ids])
        self.db.executemany("DELETE FROM frames_fts WHERE id=?", [(i,) for i in ids])
        self.db.commit()

    def _all_paths(self) -> set[str]:
        return {Path(r[0]).name for r in self.db.execute("SELECT path FROM frames")}

    def _set_hazards(self, frame_id: str, hazards: list[dict]) -> None:
        self.db.execute("UPDATE frames SET hazards=? WHERE id=?", (json.dumps(hazards, ensure_ascii=False), frame_id))
        self.db.commit()

    def _set_source(self, frame_id: str, source: str) -> None:
        self.db.execute("UPDATE frames SET analysis_source=? WHERE id=?", (source, frame_id))
        self.db.commit()

    def _reusable(self, device: str, dhash_hex: str, since: float) -> sqlite3.Row | None:
        """The newest analysed frame of this phone showing the same scene (fingerprint)."""
        target = int(dhash_hex, 16)
        for row in self.db.execute(
                "SELECT * FROM frames WHERE device=? AND status='analyzed' AND dhash IS NOT NULL "
                "AND captured_at >= ? ORDER BY captured_at DESC LIMIT 20", (device, since)):
            if bin(int(row["dhash"], 16) ^ target).count("1") <= REUSE_BITS:
                return row
        return None

    def _copy_analysis(self, frame_id: str, src: sqlite3.Row) -> None:
        self.db.execute(
            "UPDATE frames SET status='analyzed', caption=?, ocr_text=?, objects=?, hazards=?, text_languages=?, "
            "embedding=?, analyzed_at=?, analysis_source='reused' WHERE id=?",
            (src["caption"], src["ocr_text"], src["objects"], src["hazards"], src["text_languages"],
             src["embedding"], time.time(), frame_id))
        self.db.execute("DELETE FROM frames_fts WHERE id=?", (frame_id,))
        self.db.execute("INSERT INTO frames_fts (id, caption, ocr_text, objects) VALUES (?,?,?,?)",
                        (frame_id, src["caption"], src["ocr_text"],
                         " ".join(json.loads(src["objects"]) if src["objects"] else [])))
        self.db.commit()

    def _mark_failed(self, ids: list[str]) -> None:
        self.db.executemany("UPDATE frames SET status='failed' WHERE id=?", [(i,) for i in ids])
        self.db.commit()

    def _delete_old(self, cutoff: float) -> int:
        rows = self.db.execute("SELECT id, path FROM frames WHERE captured_at < ?", (cutoff,)).fetchall()
        # Storage cap per device: the oldest frames beyond MAX_FRAMES go first.
        for device, count in self.db.execute(
                "SELECT device, COUNT(*) FROM frames WHERE captured_at >= ? GROUP BY device", (cutoff,)).fetchall():
            if count > MAX_FRAMES:
                rows += self.db.execute(
                    "SELECT id, path FROM frames WHERE device=? AND captured_at >= ? ORDER BY captured_at LIMIT ?",
                    (device, cutoff, count - MAX_FRAMES)).fetchall()
        for row in rows:
            try:
                Path(row["path"]).unlink(missing_ok=True)
            except OSError:
                pass
        self.db.executemany("DELETE FROM frames WHERE id=?", [(r["id"],) for r in rows])
        self.db.executemany("DELETE FROM frames_fts WHERE id=?", [(r["id"],) for r in rows])
        self.db.commit()
        return len(rows)

    def _clear(self, device: str | None = None) -> int:
        """Frames, their image files and the questions/answers: all, or one device's."""
        if device is None:
            deleted = self._delete_old(time.time() + 10 ** 9)
            self.db.execute("DELETE FROM qa")
            self.db.commit()
            return deleted
        rows = self.db.execute("SELECT id, path FROM frames WHERE device=?", (device,)).fetchall()
        for row in rows:
            try:
                Path(row["path"]).unlink(missing_ok=True)
            except OSError:
                pass
        self.db.executemany("DELETE FROM frames WHERE id=?", [(r["id"],) for r in rows])
        self.db.executemany("DELETE FROM frames_fts WHERE id=?", [(r["id"],) for r in rows])
        self.db.execute("DELETE FROM qa WHERE device=?", (device,))
        self.db.commit()
        return len(rows)

    def _insert_qa(self, row: dict) -> None:
        self.db.execute(
            "INSERT INTO qa (id, device, asked_at, question, question_heard, answer, answer_language, "
            "frames_used, evidence, latency_s, mode) VALUES (:id,:device,:asked_at,:question,:question_heard,"
            ":answer,:answer_language,:frames_used,:evidence,:latency_s,:mode)", {"device": "phone", **row})
        self.db.commit()

    # --- reads ---
    def _pending(self, limit: int, overflow_before: float | None = None) -> list[sqlite3.Row]:
        """Pending frames, newest first. With overflow_before (local-first mode): only frames the
        local model has not reached in time, or that it escalated."""
        if overflow_before is None:
            return self.db.execute(
                "SELECT * FROM frames WHERE status='pending' ORDER BY captured_at DESC LIMIT ?", (limit,)).fetchall()
        return self.db.execute(
            "SELECT * FROM frames WHERE status='pending' AND (captured_at < ? OR analysis_source='escalate') "
            "ORDER BY captured_at DESC LIMIT ?", (overflow_before, limit)).fetchall()

    def _pending_local(self, since: float) -> list[sqlite3.Row]:
        """The newest fresh frame the local model has not tried yet."""
        return self.db.execute(
            "SELECT * FROM frames WHERE status='pending' AND analysis_source IS NULL AND captured_at >= ? "
            "ORDER BY captured_at DESC LIMIT 1", (since,)).fetchall()

    def _frames(self, since: float, until: float, limit: int, device: str | None = None) -> list[sqlite3.Row]:
        if device is None:
            return self.db.execute(
                "SELECT * FROM frames WHERE captured_at BETWEEN ? AND ? ORDER BY captured_at DESC LIMIT ?",
                (since, until, limit)).fetchall()
        return self.db.execute(
            "SELECT * FROM frames WHERE device=? AND captured_at BETWEEN ? AND ? ORDER BY captured_at DESC LIMIT ?",
            (device, since, until, limit)).fetchall()

    def _frame(self, frame_id: str) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM frames WHERE id=?", (frame_id,)).fetchone()

    def _analyzed_with_embeddings(self, device: str | None = None) -> list[sqlite3.Row]:
        if device is None:
            return self.db.execute(
                "SELECT * FROM frames WHERE status='analyzed' AND embedding IS NOT NULL").fetchall()
        return self.db.execute(
            "SELECT * FROM frames WHERE device=? AND status='analyzed' AND embedding IS NOT NULL",
            (device,)).fetchall()

    def _fts(self, query: str, limit: int, device: str | None = None) -> list[sqlite3.Row]:
        terms = [t for t in "".join(ch if ch.isalnum() else " " for ch in query).split() if len(t) > 1]
        if not terms:
            return []
        match = " OR ".join(f'"{t}"' for t in terms)
        return self.db.execute(
            "SELECT f.* FROM frames_fts JOIN frames f ON f.id = frames_fts.id "
            "WHERE frames_fts MATCH ? AND (? IS NULL OR f.device = ?) ORDER BY rank LIMIT ?",
            (match, device, device, limit)).fetchall()

    def _qa(self, limit: int, device: str | None = None) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM qa WHERE (? IS NULL OR device = ?) ORDER BY asked_at DESC LIMIT ?",
            (device, device, limit)).fetchall()

    def _counts(self, device: str | None = None) -> dict:
        c = dict(self.db.execute(
            "SELECT COUNT(*) AS frames, SUM(status='pending') AS pending, SUM(status='analyzed') AS analyzed, "
            "SUM(status='failed') AS failed, MIN(captured_at) AS oldest FROM frames "
            "WHERE (? IS NULL OR device = ?)", (device, device)).fetchone())
        c["questions"] = self.db.execute(
            "SELECT COUNT(*) FROM qa WHERE (? IS NULL OR device = ?)", (device, device)).fetchone()[0]
        c["devices"] = [r[0] for r in self.db.execute("SELECT DISTINCT device FROM frames ORDER BY device")]
        return c


def frame_dict(row: sqlite3.Row, include_text: bool = True) -> dict:
    d = {
        "id": row["id"], "device": row["device"], "captured_at": row["captured_at"],
        "age_s": round(time.time() - row["captured_at"], 1),
        "status": row["status"], "keep_reason": row["keep_reason"],
        "enhanced": row["enhanced"] or "",
        "faces_blurred": row["faces"],
        "dhash": row["dhash"],
        "sharpness": round(row["sharpness"] or 0, 1), "brightness": round(row["brightness"] or 0, 1),
        "image_url": f"/memory/frames/{row['id']}.jpg",
    }
    if include_text:
        d.update({
            "caption": row["caption"] or "", "text": row["ocr_text"] or "",
            "objects": json.loads(row["objects"]) if row["objects"] else [],
            "hazards": _hazard_labels(row["hazards"]),
            "hazard_details": [h for h in (json.loads(row["hazards"]) if row["hazards"] else [])
                               if isinstance(h, dict)],
        })
    return d


def _hazard_labels(raw: str | None) -> list[str]:
    """Hazard labels for the dashboard (old rows stored plain strings)."""
    items = json.loads(raw) if raw else []
    labels = []
    for h in items:
        if isinstance(h, str):
            labels.append(h)
        elif hazard_rules.is_obstacle(h):
            where = "" if h.get("position") in (None, "unknown") else f" ({h['position']})"
            labels.append(f"{h['label_en']}{where}")
    return labels


# ======================================================================
# 3. GEMINI BATCH ANALYSIS + EMBEDDINGS
# ======================================================================

class FrameAnalysis(BaseModel):
    index: int = Field(description="0-based index of the image in this request.")
    caption: str = Field(description="One short English sentence describing the scene. Never identify people.")
    text: str = Field(description="All readable text exactly as written (any language/script); empty if none.")
    text_languages: list[str] = Field(description="ISO codes of languages in the text, e.g. ['ar','en'].")
    objects: list[str] = Field(description="Main visible objects as short English nouns (max 10).")
    hazards: list[hazard_rules.Hazard] = Field(default_factory=list, description=hazard_rules.HAZARD_FIELD_DESCRIPTION)


ANALYSIS_PROMPT = (
    "You index frames from a blind user's phone camera into a visual memory. "
    "For EACH image (in order, index starting at 0) return: a short factual caption, "
    "ALL readable text copied exactly as written (signs, labels, doors, screens; keep Arabic in "
    "Arabic script, French and English as written; write nothing if unreadable), the main objects, "
    "and obstacles in the walking path (hazards: only things that block the way, with their position; "
    "ordinary objects that do not block the way are not hazards). Never identify or describe the identity of people; at most "
    "say 'a person'. Do not guess text you cannot read."
)


class MemoryService:
    def __init__(self) -> None:
        self.store: MemoryStore | None = None
        self.selector = FrameSelector()                  # default device ("phone")
        self.selectors: dict[str, FrameSelector] = {"phone": self.selector}
        self.client: genai.Client | None = None
        self.tasks: list[asyncio.Task] = []
        self.alert_book = hazard_rules.AlertBook()
        self.analysis_stats = {"requests": 0, "frames": 0, "errors": 0, "last_error": None,
                               "last_batch_s": None}
        self.wake = asyncio.Event()
        self.attempts: dict[str, int] = {}               # frame id -> failed analysis attempts
        self.device_language: dict[str, str] = {}        # phone -> language of its last question
        self.local_analyze = None                         # async (jpeg) -> analysis dict, when a local model runs
        self.local_down_until = 0.0                       # local model failed: cloud analyses everything meanwhile

    def start(self, client: genai.Client | None) -> None:
        self.store = MemoryStore()
        self.client = client
        self.privacy_stats = {"skipped_frames": 0, "legacy_protected": 0, "legacy_deleted": 0, "orphans_deleted": 0}
        self.tasks = [asyncio.create_task(self._protect_existing()), asyncio.create_task(self._analyzer()),
                      asyncio.create_task(self._local_analyzer()), asyncio.create_task(self._retention())]

    async def _protect_existing(self) -> None:
        """
        Frames stored before face privacy existed (faces IS NULL) are
        anonymised in place, or deleted if that is impossible. Image files
        without a database row are deleted. Runs once at start.
        """
        try:
            for row in await self.store.run(self.store._unchecked):
                try:
                    def fix(path: str = row["path"]) -> int:
                        with Image.open(path) as im:
                            protected, faces = privacy.protect_faces(im.convert("RGB"))
                        protected.save(path, format="JPEG", quality=80)
                        return faces
                    faces = await asyncio.to_thread(fix)
                    await self.store.run(self.store._set_faces, row["id"], faces)
                    self.privacy_stats["legacy_protected"] += 1
                except Exception:
                    await self.store.run(self.store._delete_ids, [row["id"]])
                    self.privacy_stats["legacy_deleted"] += 1
            known = await self.store.run(self.store._all_paths)
            for file in FRAMES_DIR.glob("*.jpg"):
                if file.name not in known:
                    file.unlink(missing_ok=True)
                    self.privacy_stats["orphans_deleted"] += 1
            if any(self.privacy_stats.values()):
                print(f"[MEMORY] privacy sweep of existing frames: {self.privacy_stats}", flush=True)
        except Exception as error:
            print(f"[MEMORY] privacy sweep error: {type(error).__name__}: {error}", flush=True)

    async def stop(self) -> None:
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    # --- frames from the stream ---
    def selector_for(self, device: str) -> FrameSelector:
        if device not in self.selectors:
            self.selectors[device] = FrameSelector()
        return self.selectors[device]

    def selection_stats(self, device: str | None = None) -> dict:
        if device is not None:
            return dict(self.selectors[device].stats) if device in self.selectors else {}
        total: dict[str, int] = {}
        for sel in self.selectors.values():
            for key, value in sel.stats.items():
                total[key] = total.get(key, 0) + value
        return total

    async def on_frame(self, image: Image.Image, at: float, session: str, device: str = "phone") -> None:
        if self.store is None:
            return
        chosen = await asyncio.to_thread(self.selector_for(device).offer, image, at)
        if chosen is None:
            return
        await self._keep(chosen, session, device)

    async def _keep(self, s: dict, session: str, device: str = "phone") -> None:
        baseline = self.selector_for(device).baseline()

        def prepare() -> tuple[bytes, str, tuple[int, int], int]:
            image, steps = enhance(s["image"].convert("RGB"), s["brightness"], s["sharpness"], baseline)
            image.thumbnail((1024, 1024))
            # Privacy: faces anonymised BEFORE anything is written to disk.
            image, faces = privacy.protect_faces(image)
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=80)
            return buffer.getvalue(), ",".join(steps), image.size, faces

        try:
            data, steps, size, faces = await asyncio.to_thread(prepare)
        except privacy.FacePrivacyError as error:
            # Never store a frame whose faces could not be checked.
            if hasattr(self, "privacy_stats"):
                self.privacy_stats["skipped_frames"] += 1
            print(f"[MEMORY] frame NOT stored (privacy check failed): {error}", flush=True)
            return
        frame_id = uuid.uuid4().hex
        path = FRAMES_DIR / f"{frame_id}.jpg"
        await asyncio.to_thread(path.write_bytes, data)
        await self.store.run(self.store._insert_frame, {
            "id": frame_id, "session": session, "device": device, "captured_at": s["at"], "path": str(path),
            "width": size[0], "height": size[1], "sharpness": s["sharpness"],
            "brightness": s["brightness"], "edges": s["edges"], "novelty": s["novelty"],
            "enhanced": steps, "keep_reason": s["reason"], "faces": faces,
            "dhash": f"{s['hash']:016x}" if "hash" in s else None})
        self.wake.set()

    # --- background analysis ---
    async def _analyzer(self) -> None:
        last_request = 0.0
        while True:
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=ANALYZE_EVERY_S)
            except asyncio.TimeoutError:
                pass
            self.wake.clear()
            wait = ANALYZE_EVERY_S - (time.monotonic() - last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            if self.client is None or self.store is None:
                continue
            # Local-first: the cloud only takes frames the local model could not reach
            # in LOCAL_OVERFLOW_S (or escalated); everything when no local model runs.
            overflow = time.time() - LOCAL_OVERFLOW_S if self.local_active() else None
            rows = await self.store.run(self.store._pending, BATCH_SIZE, overflow)
            if not rows:
                continue
            # User questions have priority over background analysis.
            while not budget.background_allowed():
                await asyncio.sleep(1.0)
            overflow = time.time() - LOCAL_OVERFLOW_S if self.local_active() else None
            rows = await self.store.run(self.store._pending, BATCH_SIZE, overflow)   # refresh after waiting
            rows = await self._reuse_unchanged(rows)
            if not rows:
                continue
            last_request = time.monotonic()
            await self.analyze_batch(rows)

    def local_active(self) -> bool:
        return self.local_analyze is not None and time.time() >= self.local_down_until

    async def _local_analyzer(self) -> None:
        """
        Local-first background analysis: the local model indexes each new frame
        (text, objects, caption), newest first, one at a time, never starting while
        a question is in flight. Obstacles it reports are confirmed by ONE cloud
        request on that frame before any warning; offline they are spoken as
        uncertain. The cloud analyser takes only what the local model cannot reach.
        """
        while True:
            await asyncio.sleep(1.0)
            if self.local_analyze is None or self.store is None or time.time() < self.local_down_until:
                continue
            if budget.user_inflight or time.monotonic() - budget.last_user_at < 3.0:
                continue                                   # questions first
            rows = await self.store.run(self.store._pending_local, time.time() - LOCAL_OVERFLOW_S)
            rows = await self._reuse_unchanged(rows)
            if not rows:
                continue
            row = rows[0]
            t = time.perf_counter()
            try:
                data = await self.local_analyze(await asyncio.to_thread(Path(row["path"]).read_bytes))
                a = FrameAnalysis.model_validate({"index": 0, **data}).model_dump()
            except Exception as error:  # noqa: BLE001 - local model down: cloud takes over for a while
                print(f"[MEMORY] local analysis unavailable ({type(error).__name__}); cloud takes over 60 s", flush=True)
                self.local_down_until = time.time() + 60
                continue
            local_hazards = a["hazards"]
            a["hazards"] = []                              # warnings only after confirmation
            vector = (await self.embed([self._index_text(a)]))[0]
            await self.store.run(self.store._save_analysis, row["id"], a, vector)
            await self.store.run(self.store._set_source, row["id"], "local")
            self.analysis_stats["by_local"] = self.analysis_stats.get("by_local", 0) + 1
            self.analysis_stats["local_last_s"] = round(time.perf_counter() - t, 1)
            if local_hazards:
                await self._confirm_hazards(row, local_hazards)

    async def _confirm_hazards(self, row, local_hazards: list[dict]) -> None:
        """A local obstacle finding is confirmed by one cloud request on that frame."""
        self.analysis_stats["hazard_checks"] = self.analysis_stats.get("hazard_checks", 0) + 1
        jpeg = await asyncio.to_thread(Path(row["path"]).read_bytes)
        confirmed = None
        try:
            response, _ = await vision.generate(
                self.client, [ANALYSIS_PROMPT, "Image 0:", types.Part.from_bytes(data=jpeg, mime_type="image/jpeg")],
                types.GenerateContentConfig(temperature=0.1, response_mime_type="application/json",
                                            response_schema=list[FrameAnalysis],
                                            thinking_config=types.ThinkingConfig(thinking_level="minimal")),
                kind="background", attempt_timeout=20.0)
            confirmed = FrameAnalysis.model_validate(json.loads(response.text)[0]).model_dump()["hazards"]
        except Exception:  # noqa: BLE001
            try:
                if groq_vision.background_allowed():
                    confirmed = (await groq_vision.analyze_frame(jpeg))["hazards"]
            except Exception:  # noqa: BLE001
                confirmed = None
        if confirmed is None:
            confirmed = [dict(h, certain=False) for h in local_hazards]      # offline: spoken as uncertain
            self.analysis_stats["hazards_unconfirmed"] = self.analysis_stats.get("hazards_unconfirmed", 0) + 1
        if confirmed:
            await self.store.run(self.store._set_hazards, row["id"], confirmed)
            await self._consider_hazards(confirmed, row)

    async def _reuse_unchanged(self, rows: list) -> list:
        """
        The camera resting on one scene produces a 'periodic refresh' frame
        every 20 s. If it shows the same scene as a frame analysed in the
        last 3 minutes, copy that analysis instead of asking Gemini again.
        Returns the frames that still need a real analysis.
        """
        remaining = []
        for r in rows:
            src = None
            if r["keep_reason"] == "periodic refresh" and r["dhash"]:
                src = await self.store.run(self.store._reusable, r["device"], r["dhash"],
                                           time.time() - REUSE_WINDOW_S)
            if src is None:
                remaining.append(r)
                continue
            await self.store.run(self.store._copy_analysis, r["id"], src)
            budget.stats["reused_analysis"] += 1
            hazards = json.loads(src["hazards"]) if src["hazards"] else []
            if any(isinstance(h, dict) for h in hazards):
                await self._consider_hazards([h for h in hazards if isinstance(h, dict)], r)
        return remaining

    async def analyze_batch(self, rows: list) -> None:
        started = time.perf_counter()
        ids = [r["id"] for r in rows]
        try:
            parts: list = [ANALYSIS_PROMPT]
            for i, r in enumerate(rows):
                parts.append(f"Image {i}:")
                parts.append(types.Part.from_bytes(data=Path(r["path"]).read_bytes(), mime_type="image/jpeg"))
            response, model = await vision.generate(
                self.client, parts, types.GenerateContentConfig(
                    temperature=0.1, response_mime_type="application/json",
                    response_schema=list[FrameAnalysis],
                    thinking_config=types.ThinkingConfig(thinking_level="minimal")),
                kind="background", attempt_timeout=30.0)
            results = [FrameAnalysis.model_validate(x).model_dump() for x in json.loads(response.text)]
            by_index = {a["index"]: a for a in results if 0 <= a["index"] < len(rows)}
            texts = [self._index_text(by_index.get(i)) for i in range(len(rows))]
            vectors = await self.embed(texts) if any(texts) else [None] * len(rows)
            for i, r in enumerate(rows):
                a = by_index.get(i)
                if a is None:                    # missing from the reply: try again later
                    await self._retry_later([r["id"]])
                    continue
                self.attempts.pop(r["id"], None)
                await self.store.run(self.store._save_analysis, r["id"], a, vectors[i])
                await self.store.run(self.store._set_source, r["id"], model)
                if a["hazards"]:
                    await self._consider_hazards(a["hazards"], r)
            self.analysis_stats["requests"] += 1
            self.analysis_stats["frames"] += len(rows)
            self.analysis_stats["last_batch_s"] = round(time.perf_counter() - started, 2)
        except vision.NoCloudVision:
            # No Gemini model may be used for background work now (quota kept for
            # questions, exhausted or overloaded): local model, then Groq
            # (throttled), otherwise the frames simply wait for later.
            if not await self._analyze_without_gemini(rows):
                self.analysis_stats["waiting_for_quota"] = self.analysis_stats.get("waiting_for_quota", 0) + 1
        except errors.APIError as error:
            self.analysis_stats["errors"] += 1
            self.analysis_stats["last_error"] = f"{error.code}"
            print(f"[MEMORY] analysis error: HTTP {error.code}", flush=True)
            if error.code == 429:            # rate limit: frames stay pending, retried later
                budget.rate_limited(30)
            else:                            # 5xx "high demand", bad reply: retry, then give up
                await self._retry_later(ids)
        except Exception as error:
            self.analysis_stats["errors"] += 1
            self.analysis_stats["last_error"] = type(error).__name__
            await self._retry_later(ids)
            print(f"[MEMORY] analysis error: {type(error).__name__}: {error}", flush=True)

    async def _analyze_without_gemini(self, rows: list) -> bool:
        """Analyse the newest pending frame with the local model or Groq. True if one was analysed."""
        row = rows[0]                                    # _pending is newest first
        analyzer, source = None, None
        if self.local_analyze is not None:
            analyzer, source = self.local_analyze, "local"
        elif groq_vision.background_allowed():
            analyzer, source = groq_vision.analyze_frame, groq_vision.BUDGET_NAME
        if analyzer is None:
            return False
        try:
            data = await analyzer(await asyncio.to_thread(Path(row["path"]).read_bytes))
            a = FrameAnalysis.model_validate({"index": 0, **data}).model_dump()
        except Exception as error:  # noqa: BLE001
            print(f"[MEMORY] {source} analysis failed: {type(error).__name__}: {error}", flush=True)
            return False
        vector = (await self.embed([self._index_text(a)]))[0]
        await self.store.run(self.store._save_analysis, row["id"], a, vector)
        await self.store.run(self.store._set_source, row["id"], source)
        self.analysis_stats["requests"] += 1
        self.analysis_stats["frames"] += 1
        self.analysis_stats[f"by_{source.split(':')[0]}"] = self.analysis_stats.get(f"by_{source.split(':')[0]}", 0) + 1
        if a["hazards"]:
            await self._consider_hazards(a["hazards"], row)
        return True

    async def _retry_later(self, ids: list[str]) -> None:
        """
        A transient failure must not erase a scene from memory: the frame
        stays pending (analysed again in the next batch) up to 3 attempts.
        """
        give_up = []
        for frame_id in ids:
            self.attempts[frame_id] = self.attempts.get(frame_id, 0) + 1
            if self.attempts[frame_id] >= MAX_ANALYSIS_ATTEMPTS:
                give_up.append(frame_id)
                self.attempts.pop(frame_id, None)
        if give_up:
            await self.store.run(self.store._mark_failed, give_up)

    @staticmethod
    def _index_text(a: dict | None) -> str:
        if not a:
            return ""
        return " | ".join(x for x in (a["caption"], a["text"], ", ".join(a["objects"])) if x)

    async def embed(self, texts: list[str]) -> list[list[float] | None]:
        """Multilingual embeddings; None where text is empty or on failure."""
        if self.client is None:
            return [None] * len(texts)
        idx = [i for i, t in enumerate(texts) if t.strip()]
        out: list[list[float] | None] = [None] * len(texts)
        if not idx:
            return out
        try:
            result = await self.client.aio.models.embed_content(
                model=EMBED_MODEL, contents=[texts[i] for i in idx],
                config=types.EmbedContentConfig(output_dimensionality=EMBED_DIM))
            for i, e in zip(idx, result.embeddings):
                out[i] = list(e.values)
        except Exception as error:
            print(f"[MEMORY] embedding error: {type(error).__name__}", flush=True)
        return out

    async def _consider_hazards(self, found: list[dict], row) -> None:
        """Turn analysed hazards into warnings only if still relevant (hazards.py rules)."""
        def frame_hash() -> int:
            with Image.open(row["path"]) as im:
                return dhash(analysis_image(im))
        try:
            fh = await asyncio.to_thread(frame_hash)
            live = await asyncio.to_thread(live_hash, row["device"])
        except Exception:
            return
        for hazard in found:
            alert = self.alert_book.consider(hazard, {"id": row["id"], "device": row["device"],
                                                      "captured_at": row["captured_at"]}, fh, live)
            if alert:
                print(f"[MEMORY] warning for {row['device']}: {hazard['label_en']} ({hazard['position']})",
                      flush=True)

    # --- retention ---
    async def _retention(self) -> None:
        while True:
            try:
                if self.store:
                    await self.store.run(self.store._delete_old, time.time() - RETENTION_S)
            except Exception as error:
                print(f"[MEMORY] retention error: {type(error).__name__}", flush=True)
            await asyncio.sleep(60)

    # --- search ---
    async def search(self, query: str, k: int = 5, since: float | None = None,
                     device: str | None = None) -> list[dict]:
        """Meaning (embeddings) first, keywords (FTS5) as fallback/complement."""
        if self.store is None or not query.strip():
            return []
        results: dict[str, dict] = {}
        vector = (await self.embed([query]))[0]
        if vector:
            rows = await self.store.run(self.store._analyzed_with_embeddings, device)
            for score, r in await asyncio.to_thread(rank_by_meaning, vector, rows, k, since):
                d = frame_dict(r)
                d["score"] = round(score, 3)
                d["match"] = "meaning"
                results[r["id"]] = d
        for r in await self.store.run(self.store._fts, query, k, device):
            if r["id"] not in results and (not since or r["captured_at"] >= since):
                d = frame_dict(r)
                d["score"] = None
                d["match"] = "keyword"
                results[r["id"]] = d
        return list(results.values())[:k]

    async def recent(self, seconds: float, limit: int = 20, device: str | None = None) -> list[dict]:
        if self.store is None:
            return []
        now = time.time()
        rows = await self.store.run(self.store._frames, now - seconds, now + 1, limit, device)
        return [frame_dict(r) for r in rows]

    async def record_qa(self, row: dict) -> None:
        if self.store:
            await self.store.run(self.store._insert_qa, row)


def rank_by_meaning(vector: list[float], rows: list, k: int, since: float | None = None) -> list[tuple[float, object]]:
    """Cosine similarity of the query vector with every stored embedding; best k."""
    # math.sumprod is implemented in C (Python 3.12+): ~15x faster than a
    # Python loop, so no numpy dependency is needed (bench_memory_db.py).
    qn = math.sqrt(math.sumprod(vector, vector)) or 1.0
    scored = []
    for r in rows:
        if since and r["captured_at"] < since:
            continue
        emb = struct.unpack(f"{len(r['embedding']) // 4}f", r["embedding"])
        en = math.sqrt(math.sumprod(emb, emb)) or 1.0
        scored.append((math.sumprod(vector, emb) / (qn * en), r))
    return sorted(scored, key=lambda x: -x[0])[:k]


def live_hash(device: str) -> int | None:
    """Fingerprint of what the phone's camera shows now (None if not streaming)."""
    from stream_service import hub        # lazy: stream_service does not import this module
    latest = hub.latest(device)
    if latest is None or time.time() - latest[1] > 3.0:
        return None
    return dhash(analysis_image(latest[0]))


memory = MemoryService()


# ======================================================================
# 4. API (dashboard timeline, search, Q&A history, alerts)
# ======================================================================

def _local_only(request: Request) -> None:
    if request.client and request.client.host not in {"127.0.0.1", "::1"}:
        raise HTTPException(403, "Local only.")


def _device(value: str | None) -> str | None:
    """Optional device filter (None = all devices)."""
    if not value:
        return None
    from stream_service import device_name
    return device_name(value)


@router.get("/memory/stats")
async def memory_stats(device: str | None = None):
    device = _device(device)
    counts = await memory.store.run(memory.store._counts, device) if memory.store else {}
    return {"device": device, "selection": memory.selection_stats(device), "analysis": memory.analysis_stats,
            "privacy": {**privacy.stats, **getattr(memory, "privacy_stats", {})},
            "warnings": memory.alert_book.stats,
            "vision": vision.status(),
            "store": counts, "retention_min": RETENTION_S / 60, "gemini_budget": budget.status()}


@router.get("/memory/frames")
async def memory_frames(minutes: float = 30, limit: int = 60, device: str | None = None):
    return {"frames": await memory.recent(minutes * 60, min(limit, 200), _device(device))}


@router.get("/memory/frames/{frame_id}.jpg")
async def memory_frame_image(frame_id: str, request: Request):
    _local_only(request)
    if not frame_id.isalnum() or memory.store is None:
        raise HTTPException(404, "Not found.")
    row = await memory.store.run(memory.store._frame, frame_id)
    if row is None or not Path(row["path"]).is_file():
        raise HTTPException(404, "Not found.")
    return FileResponse(row["path"], media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.get("/memory/search")
async def memory_search(q: str, k: int = 5, device: str | None = None):
    return {"query": q, "results": await memory.search(q, min(k, 20), device=_device(device))}


@router.get("/memory/qa")
async def memory_qa(limit: int = 50, device: str | None = None):
    if memory.store is None:
        return {"qa": []}
    rows = await memory.store.run(memory.store._qa, min(limit, 200), _device(device))
    return {"qa": [dict(r) for r in rows]}


@router.get("/memory/alerts")
async def memory_alerts(after: float = 0, device: str | None = None, lang: str | None = None):
    """
    Warnings still valid NOW: not expired, and the camera still shows the
    hazard scene (re-checked at every poll). Message in the user's language.
    """
    device = _device(device)
    hashes: dict[str, int | None] = {}

    def live_for(d: str) -> int | None:
        if d not in hashes:
            hashes[d] = live_hash(d)
        return hashes[d]

    active = await asyncio.to_thread(memory.alert_book.active, device, after, live_for)
    out = []
    for a in active:
        language = lang or memory.device_language.get(a["device"]) or "en"
        language = language if language in ("en", "fr", "ar", "ar-LB") else "en"
        out.append({"id": a["id"], "at": a["at"], "device": a["device"], "hazard": a["hazard"],
                    "position": a["details"].get("position"), "certain": a["details"].get("certain"),
                    "message": hazard_rules.message(a["details"], language), "language": language,
                    "expires_in": round(a["expires_at"] - time.time(), 1), "frame_id": a["frame_id"]})
    return {"alerts": out}


@router.get("/memory/alerts/history")
async def memory_alert_history(device: str | None = None, limit: int = 30):
    """Warnings spoken earlier (dashboard), newest first; 'active' = still valid now."""
    device = _device(device)
    now = time.time()
    active_ids = {a["id"] for a in memory.alert_book.alerts}
    rows = [a for a in reversed(memory.alert_book.history) if device is None or a["device"] == device]
    return {"warnings": [{"id": a["id"], "at": a["at"], "device": a["device"], "hazard": a["hazard"],
                          "position": a["details"].get("position"), "certain": a["details"].get("certain"),
                          "message_en": hazard_rules.message(a["details"], "en"), "frame_id": a["frame_id"],
                          "active": a["id"] in active_ids and now <= a["expires_at"]}
                         for a in rows[:min(limit, 100)]]}


@router.delete("/memory")
async def memory_clear(request: Request, device: str | None = None):
    _local_only(request)
    device = _device(device)
    deleted = await memory.store.run(memory.store._clear, device) if memory.store else 0
    memory.alert_book.clear(device)
    return {"deleted_frames": deleted}
