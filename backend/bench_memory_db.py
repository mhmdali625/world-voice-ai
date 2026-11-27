"""
Visual-memory database benchmark (SQLite), on the real MemoryStore code
path: the same async lock + worker-thread calls the server uses, real
JPEG files of camera-like size, 768-dimension embeddings.

  [1] journal mode A/B (insert latency): DELETE (old) vs WAL (current)
  [2] continuous load, several phones at once, real time:
        realistic : 4 phones, one kept frame per 1.5 s window each
        stress    : 4 phones, EVERY sample kept (2 frames/s each)
      plus background analysis (4 frames / 8 s), 2 dashboards polling
      every 3 s, and one question per phone every 6 s.
      -> latency per operation (p50 / p95 / max, including lock waits),
         CPU, RAM, storage
  [3] integrity: PRAGMA integrity_check, FTS index == analysed frames,
      every row has its file and every file has its row
  [4] session isolation: every read limited to a device returns only it
  [5] retention: frames older than 30 min deleted WITH their files;
      per-device cap; recent frames kept
  [6] meaning search as memory grows (150 / 600 / 2,400 / 10,000 frames)

No API calls, no quota. Usage: .venv\\Scripts\\python bench_memory_db.py [--quick]
"""

import asyncio
import io
import json
import os
import random
import statistics
import struct
import sys
import tempfile
import time
import uuid
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="wv_dbbench_"))
os.environ["WV_MEMORY_DIR"] = str(TMP / "default")

from PIL import Image, ImageDraw, ImageFilter  # noqa: E402

import memory_service as m  # noqa: E402
from test_helpers import ProcessMeter, folder_size, wipe  # noqa: E402

QUICK = "--quick" in sys.argv
DURATION_S = 20 if QUICK else 60
DEVICES = [f"phone{i}" for i in range(1, 5)]
results: list[tuple[bool, str, str]] = []
report: dict = {}


def check(ok: bool, name: str, detail: str = "") -> None:
    results.append((ok, name, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}  -> {detail}", flush=True)


def camera_jpeg(seed: int) -> bytes:
    """A 1024x768 frame with photo-like detail (noise + shapes), saved like _keep does."""
    rng = random.Random(seed)
    img = Image.effect_noise((1024, 768), 40).convert("RGB")
    d = ImageDraw.Draw(img)
    for _ in range(40):
        x, y = rng.randint(0, 1000), rng.randint(0, 740)
        d.rectangle([x, y, x + rng.randint(30, 300), y + rng.randint(30, 200)],
                    fill=tuple(rng.randint(0, 255) for _ in range(3)))
    img = img.filter(ImageFilter.GaussianBlur(1.2))
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=80)
    return buffer.getvalue()


JPEGS = [camera_jpeg(i) for i in range(8)]


def random_vector(rng: random.Random) -> list[float]:
    return [rng.uniform(-1, 1) for _ in range(m.EMBED_DIM)]


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    return values[min(len(values) - 1, int(round(p / 100 * (len(values) - 1))))]


class Timer:
    def __init__(self) -> None:
        self.ms: dict[str, list[float]] = {}

    async def run(self, name: str, coro):
        t = time.perf_counter()
        result = await coro
        self.ms.setdefault(name, []).append((time.perf_counter() - t) * 1000)
        return result

    def table(self) -> dict:
        return {k: {"n": len(v), "p50_ms": round(statistics.median(v), 2), "p95_ms": round(pct(v, 95), 2),
                    "max_ms": round(max(v), 2)} for k, v in sorted(self.ms.items())}


def new_store(folder: Path, journal: str = "WAL") -> m.MemoryStore:
    folder.mkdir(parents=True, exist_ok=True)
    store = m.MemoryStore(folder / "memory.db")
    if journal != "WAL":
        store.db.execute(f"PRAGMA journal_mode={journal}")
        store.db.execute("PRAGMA synchronous=FULL")
    return store


def frame_row(device: str, at: float, folder: Path, i: int) -> dict:
    frame_id = uuid.uuid4().hex
    path = folder / f"{frame_id}.jpg"
    path.write_bytes(JPEGS[i % len(JPEGS)])
    return {"id": frame_id, "session": "s-" + device, "device": device, "captured_at": at, "path": str(path),
            "width": 1024, "height": 768, "sharpness": 100.0, "brightness": 120.0, "edges": 10.0,
            "novelty": 20, "enhanced": "", "keep_reason": "new view"}


def analysis(device: str, i: int) -> dict:
    return {"caption": f"A corridor seen by {device}, view {i}.", "text": f"ROOM {i} {device.upper()}",
            "objects": ["door", "sign", "wall"], "hazards": [], "text_languages": ["en"]}


# ======================================================================

async def journal_ab() -> None:
    print("\n[1] Journal mode A/B: 300 frame inserts (each committed)", flush=True)
    out = {}
    for journal in ("DELETE", "WAL"):
        folder = TMP / f"ab_{journal}"
        store = new_store(folder, journal)
        frames_dir = folder / "frames"
        frames_dir.mkdir(exist_ok=True)
        timer = Timer()
        for i in range(300):
            row = frame_row("phone1", time.time(), frames_dir, i)
            await timer.run("insert", store.run(store._insert_frame, row))
        out[journal] = timer.table()["insert"]
        store.db.close()
        print(f"  {journal:6s} insert p50 {out[journal]['p50_ms']} ms, p95 {out[journal]['p95_ms']} ms, "
              f"max {out[journal]['max_ms']} ms", flush=True)
    report["journal_ab"] = out


async def continuous(label: str, keep_every_s: float) -> m.MemoryStore:
    print(f"\n[2] Continuous load '{label}': {len(DEVICES)} phones, a kept frame every {keep_every_s} s "
          f"each, {DURATION_S} s real time", flush=True)
    folder = TMP / f"load_{label}"
    store = new_store(folder)
    frames_dir = folder / "frames"
    frames_dir.mkdir(exist_ok=True)
    timer = Timer()
    meter = ProcessMeter()
    rng = random.Random(1)
    stop = time.time() + DURATION_S
    counter = {"frames": 0, "analysed": 0, "questions": 0}

    async def phone(device: str) -> None:
        i = 0
        while time.time() < stop:
            row = await asyncio.to_thread(frame_row, device, time.time(), frames_dir, i)
            await timer.run("insert frame", store.run(store._insert_frame, row))
            counter["frames"] += 1
            i += 1
            await asyncio.sleep(keep_every_s)

    async def analyzer() -> None:
        while time.time() < stop:
            await asyncio.sleep(8 if not QUICK else 4)
            rows = await timer.run("pending batch", store.run(store._pending, 4))
            for r in rows:
                await timer.run("save analysis", store.run(
                    store._save_analysis, r["id"], analysis(r["device"], counter["analysed"]), random_vector(rng)))
                counter["analysed"] += 1

    async def dashboard() -> None:
        while time.time() < stop:
            now = time.time()
            await timer.run("dashboard timeline", store.run(store._frames, now - 1800, now + 1, 60, None))
            await timer.run("dashboard Q&A", store.run(store._qa, 30, None))
            await timer.run("dashboard stats", store.run(store._counts, None))
            meter.sample()
            await asyncio.sleep(3)

    async def asker(device: str) -> None:
        await asyncio.sleep(rng.uniform(1, 6))
        while time.time() < stop:
            now = time.time()
            await timer.run("ask: memory index (40)", store.run(store._frames, now - 1800, now + 1, 40, device))
            rows = await timer.run("ask: load embeddings", store.run(store._analyzed_with_embeddings, device))
            await timer.run("ask: rank by meaning", asyncio.to_thread(
                m.rank_by_meaning, random_vector(rng), rows, 4, None))
            await timer.run("ask: keyword (FTS5)", store.run(store._fts, "room sign", 4, device))
            await timer.run("ask: record Q&A", store.run(store._insert_qa, {
                "id": uuid.uuid4().hex, "device": device, "asked_at": now, "question": "q", "question_heard": "",
                "answer": "a", "answer_language": "en", "frames_used": "{}", "evidence": "strong",
                "latency_s": 3.0, "mode": "live"}))
            counter["questions"] += 1
            await asyncio.sleep(6)

    async def retention() -> None:
        while time.time() < stop:
            await asyncio.sleep(10)
            await timer.run("retention pass", store.run(store._delete_old, time.time() - m.RETENTION_S))

    await asyncio.gather(*(phone(d) for d in DEVICES), analyzer(), dashboard(), dashboard(),
                         *(asker(d) for d in DEVICES), retention())
    meter.sample()
    table = timer.table()
    db_bytes = sum((folder / f).stat().st_size for f in ("memory.db", "memory.db-wal", "memory.db-shm")
                   if (folder / f).exists())
    files_bytes = folder_size(frames_dir)
    per_frame = files_bytes / max(1, counter["frames"])
    frames_30min_per_phone = min(m.MAX_FRAMES, int(1800 / keep_every_s))
    report[f"load_{label}"] = {
        "devices": len(DEVICES), "duration_s": DURATION_S, "keep_every_s": keep_every_s, **counter,
        "latency": table, "cpu_percent_one_core": round(meter.cpu_percent(), 1),
        "peak_rss_mb": round(meter.peak_rss / 2 ** 20, 1), "db_mb": round(db_bytes / 2 ** 20, 2),
        "frames_mb": round(files_bytes / 2 ** 20, 1), "jpeg_kb_avg": round(per_frame / 1024, 1),
        "projected_30min_mb_4_phones": round(len(DEVICES) * frames_30min_per_phone * (per_frame + 4096) / 2 ** 20),
    }
    r = report[f"load_{label}"]
    for name, v in table.items():
        print(f"  {name:24s} n={v['n']:4d}  p50 {v['p50_ms']:7.2f} ms  p95 {v['p95_ms']:7.2f} ms  "
              f"max {v['max_ms']:7.2f} ms", flush=True)
    print(f"  frames {counter['frames']}, analysed {counter['analysed']}, questions {counter['questions']} | "
          f"CPU {r['cpu_percent_one_core']} % of one core | peak RAM {r['peak_rss_mb']} MB | "
          f"db {r['db_mb']} MB + images {r['frames_mb']} MB ({r['jpeg_kb_avg']} KB/frame) | "
          f"30 min, 4 phones, capped: ~{r['projected_30min_mb_4_phones']} MB", flush=True)
    worst_question = max(table[k]["p95_ms"] for k in table if k.startswith("ask:"))
    check(worst_question < 100, f"{label}: every question step p95 < 100 ms (DB never slows an answer)",
          f"worst p95 {worst_question} ms")
    check(table["insert frame"]["p95_ms"] < 50, f"{label}: frame insert p95 < 50 ms",
          f"{table['insert frame']['p95_ms']} ms")
    return store


async def integrity_and_isolation(store: m.MemoryStore, folder: Path) -> None:
    print("\n[3] Integrity", flush=True)
    ok = store.db.execute("PRAGMA integrity_check").fetchone()[0]
    check(ok == "ok", "PRAGMA integrity_check", ok)
    analysed = store.db.execute("SELECT COUNT(*) FROM frames WHERE status='analyzed'").fetchone()[0]
    fts = store.db.execute("SELECT COUNT(*) FROM frames_fts").fetchone()[0]
    check(analysed == fts, "keyword index matches analysed frames", f"analysed {analysed}, FTS rows {fts}")
    paths = {Path(r[0]).name for r in store.db.execute("SELECT path FROM frames")}
    files = {f.name for f in (folder / "frames").glob("*.jpg")}
    check(paths == files, "every row has its image and every image has its row",
          f"rows {len(paths)}, files {len(files)}, missing {len(paths - files)}, orphans {len(files - paths)}")

    print("\n[4] Session isolation", flush=True)
    now = time.time()
    leaks = []
    for d in DEVICES:
        for name, rows in (("timeline", store._frames(0, now + 1, 10000, d)), ("Q&A", store._qa(10000, d)),
                           ("embeddings", store._analyzed_with_embeddings(d)),
                           ("keyword", store._fts(d.upper(), 1000, d))):
            bad = [r["device"] for r in rows if r["device"] != d]
            if bad:
                leaks.append(f"{d} {name}: {len(bad)} rows of {set(bad)}")
        counts = store._counts(d)
        mine = store.db.execute("SELECT COUNT(*) FROM frames WHERE device=?", (d,)).fetchone()[0]
        if counts["frames"] != mine:
            leaks.append(f"{d} stats {counts['frames']} != {mine}")
    check(not leaks, "every device-limited read returns only that phone's data", "; ".join(leaks) or "4 phones x 5 reads")
    all_rows = len(store._frames(0, now + 1, 100000, None))
    per_device = sum(len(store._frames(0, now + 1, 100000, d)) for d in DEVICES)
    check(all_rows == per_device, "devices partition the memory exactly (dashboard 'all' = sum of phones)",
          f"all {all_rows}, sum {per_device}")
    before = {d: len(store._frames(0, now + 1, 100000, d)) for d in DEVICES}
    deleted = store._clear("phone2")
    after = {d: len(store._frames(0, now + 1, 100000, d)) for d in DEVICES}
    others_kept = all(after[d] == before[d] for d in DEVICES if d != "phone2")
    check(deleted == before["phone2"] and after["phone2"] == 0 and not store._qa(10, "phone2") and others_kept,
          "clearing one phone deletes only its frames, files and questions",
          f"deleted {deleted}, others unchanged: {others_kept}")


async def retention_check() -> None:
    print("\n[5] Retention (30 min) and per-phone cap", flush=True)
    folder = TMP / "retention"
    store = new_store(folder)
    frames_dir = folder / "frames"
    frames_dir.mkdir(exist_ok=True)
    now = time.time()
    old = [frame_row("phone1", now - 1800 - 60 - i, frames_dir, i) for i in range(30)]
    fresh = [frame_row(d, now - 60 - i, frames_dir, i) for d in ("phone1", "phone2") for i in range(20)]
    for row in old + fresh:
        store._insert_frame(row)
        store._save_analysis(row["id"], analysis(row["device"], 1), random_vector(random.Random(2)))
    deleted = store._delete_old(now - m.RETENTION_S)
    old_files_left = [r["path"] for r in old if Path(r["path"]).exists()]
    fresh_left = sum(Path(r["path"]).exists() for r in fresh)
    fts_left = store.db.execute("SELECT COUNT(*) FROM frames_fts").fetchone()[0]
    check(deleted == 30 and not old_files_left and fresh_left == 40 and fts_left == 40,
          "frames older than 30 min deleted with image files and index entries; recent kept",
          f"deleted {deleted}, old files left {len(old_files_left)}, recent kept {fresh_left}/40, FTS {fts_left}")
    saved = m.MAX_FRAMES
    m.MAX_FRAMES = 15
    try:
        capped = store._delete_old(now - m.RETENTION_S)
    finally:
        m.MAX_FRAMES = saved
    left = {d: store.db.execute("SELECT COUNT(*) FROM frames WHERE device=?", (d,)).fetchone()[0]
            for d in ("phone1", "phone2")}
    newest_kept = store.db.execute(
        "SELECT MAX(captured_at) FROM frames WHERE device='phone1'").fetchone()[0] == max(
        r["captured_at"] for r in fresh if r["device"] == "phone1")
    check(capped == 10 and left == {"phone1": 15, "phone2": 15} and newest_kept,
          "cap applies per phone and removes the OLDEST frames", f"removed {capped}, left {left}")
    store.db.close()


async def search_scaling() -> None:
    print("\n[6] Meaning search as memory grows (load embeddings + rank, one phone's or all)", flush=True)
    folder = TMP / "scaling"
    store = new_store(folder)
    frames_dir = folder / "frames"
    frames_dir.mkdir(exist_ok=True)
    rng = random.Random(3)
    out = {}
    inserted = 0
    for target in (150, 600, 2400, 10000):
        rows = []
        while inserted < target:
            row = frame_row("phone1", time.time() - rng.uniform(0, 1700), frames_dir, inserted)
            Path(row["path"]).unlink()          # images not needed for this measure
            rows.append(row)
            inserted += 1
        for row in rows:
            store.db.execute(
                "INSERT INTO frames (id, session, device, captured_at, path, width, height, sharpness, brightness, "
                "edges, novelty, enhanced, keep_reason) VALUES (:id,:session,:device,:captured_at,:path,:width,"
                ":height,:sharpness,:brightness,:edges,:novelty,:enhanced,:keep_reason)", row)
            blob = struct.pack(f"{m.EMBED_DIM}f", *random_vector(rng))
            a = analysis("phone1", inserted)
            store.db.execute("UPDATE frames SET status='analyzed', caption=?, ocr_text=?, objects=?, embedding=? "
                             "WHERE id=?", (a["caption"], a["text"], json.dumps(a["objects"]), blob, row["id"]))
            store.db.execute("INSERT INTO frames_fts (id, caption, ocr_text, objects) VALUES (?,?,?,?)",
                             (row["id"], a["caption"], a["text"], " ".join(a["objects"])))
        store.db.commit()
        load, rank, fts = [], [], []
        for _ in range(5):
            t = time.perf_counter()
            loaded = store._analyzed_with_embeddings("phone1")
            load.append((time.perf_counter() - t) * 1000)
            t = time.perf_counter()
            m.rank_by_meaning(random_vector(rng), loaded, 4)
            rank.append((time.perf_counter() - t) * 1000)
            t = time.perf_counter()
            store._fts("room sign", 4, "phone1")
            fts.append((time.perf_counter() - t) * 1000)
        out[target] = {"load_ms": round(statistics.median(load), 1), "rank_ms": round(statistics.median(rank), 1),
                       "fts_ms": round(statistics.median(fts), 2)}
        total = out[target]["load_ms"] + out[target]["rank_ms"]
        print(f"  {target:6d} frames: load {out[target]['load_ms']:7.1f} ms + rank {out[target]['rank_ms']:7.1f} ms "
              f"= {total:7.1f} ms | keyword {out[target]['fts_ms']} ms", flush=True)
    report["search_scaling"] = out
    cap_total = out[600]["load_ms"] + out[600]["rank_ms"]
    check(cap_total < 300, "meaning search at the per-phone cap (600 frames) < 300 ms",
          f"{cap_total:.1f} ms")
    store.db.close()


async def main() -> None:
    print(f"temporary folder: {TMP}")
    await journal_ab()
    await continuous("realistic", 1.5)
    stress = await continuous("stress", 0.5)
    await integrity_and_isolation(stress, TMP / "load_stress")
    stress.db.close()
    await retention_check()
    await search_scaling()
    (Path(__file__).resolve().parent / "bench_memory_db.json").write_text(json.dumps(report, indent=2))
    wipe(TMP)
    passed = sum(ok for ok, _, _ in results)
    print(f"\n{passed}/{len(results)} database checks passed (details: bench_memory_db.json)")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
