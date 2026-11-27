"""
Tests for memory_service.py (frame selection, enhancement, database,
retention, Gemini batch OCR/objects, search by meaning and time).

  .venv\\Scripts\\python test_memory.py          offline (no API)
  .venv\\Scripts\\python test_memory.py --live   + real Gemini analysis (2-3 requests)
"""

import asyncio
import random
import sys
import tempfile
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

import memory_service as m

RESULTS = []


def record(name, ok, detail=""):
    RESULTS.append(ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  -> ' + detail) if detail else ''}", flush=True)


def font(size):
    for name in ("arial.ttf", "segoeui.ttf", "tahoma.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


# Pillow here has no text shaping (raqm), so Arabic would be drawn as
# isolated letters in the wrong order - not what a real sign looks like.
# Signs are drawn from their correctly joined presentation forms in
# visual (right-to-left) order; the EXPECTED text stays the normal one.
ARABIC_VISUAL = {
    # م خ ر ج | ا ل ط و ا ر ئ  -> initial/medial/final/isolated forms
    "مخرج الطوارئ": "ﻣﺨﺮﺝ ﺍﻟﻄﻮﺍﺭﺉ"[::-1],
}


def scene(text, seed, size=(960, 720)):
    """A synthetic 'corridor' frame with a sign carrying text."""
    text = ARABIC_VISUAL.get(text, text)
    rng = random.Random(seed)
    img = Image.new("RGB", size, (rng.randint(90, 140),) * 3)
    d = ImageDraw.Draw(img)
    for _ in range(25):
        x, y = rng.randint(0, size[0]), rng.randint(0, size[1])
        d.rectangle([x, y, x + rng.randint(20, 160), y + rng.randint(20, 160)],
                    outline=(rng.randint(0, 255),) * 3, width=3)
    d.rectangle([250, 200, 710, 420], fill=(20, 110, 40), outline=(255, 255, 255), width=6)
    d.text((290, 250), text, fill=(255, 255, 255), font=font(80))
    return img


def feed(sel, img, t0, n=5):
    """Offer n samples of one view (0.5 s apart), isolated from earlier views,
    and close the last window. Returns the kept candidates."""
    sel.flush(judge=False)
    results = [sel.offer(img, t0 + i * 0.5) for i in range(n)]
    results.append(sel.flush())
    return [r for r in results if r]


def offline() -> None:
    print("\n[1] Frame selection (offline)")
    sel = m.FrameSelector()
    t = 1000.0
    base = scene("EXIT", 1)

    # One window with blurred, sharp, blurred copies -> the sharp one is chosen.
    for img in (base.filter(ImageFilter.GaussianBlur(6)), base, base.filter(ImageFilter.GaussianBlur(4))):
        sel.offer(img, t)
        t += 0.5
    kept = [r for r in (sel.flush(),) if r]
    record("sharpest frame of the window is kept", len(kept) == 1 and kept[0]["image"] is base,
           f"sharpness {kept[0]['sharpness']:.0f}" if kept else f"nothing kept ({sel.last_rejection})")

    # Same view for 10 s -> duplicates rejected.
    before = sel.stats["kept"]
    got = feed(sel, base, t, 20)
    t += 10
    record("unchanged view is not stored again (duplicates)", not got and sel.stats["kept"] == before,
           f"duplicates rejected: {sel.stats['duplicate']}")

    # After 20 s the same useful view is refreshed once.
    got = feed(sel, base, t, 25)
    t += 12.5
    record("periodic refresh after 20 s", len(got) == 1 and got[0]["reason"] == "periodic refresh")

    # New scene -> kept.
    t += 1
    new_scene = scene("ROOM 12", 7)
    got = feed(sel, new_scene, t)
    t += 3
    record("new view is kept immediately", len(got) == 1 and got[0]["reason"] == "new view")

    wall = Image.new("RGB", (960, 720), (180, 178, 175))
    got = feed(sel, wall, t)
    t += 3
    record("blank wall rejected AS uninformative", not got and sel.last_rejection == "uninformative",
           f"reason: {sel.last_rejection}")

    black = scene("DARK", 3).point(lambda v: int(v * 0.05))
    got = feed(sel, black, t)
    t += 3
    record("almost black frame rejected AS too dark", not got and sel.last_rejection == "too_dark",
           f"reason: {sel.last_rejection}")

    soft = scene("CAFE", 11).filter(ImageFilter.GaussianBlur(8))
    got = feed(sel, soft, t)
    t += 3
    record("out-of-focus frame rejected AS blurry", not got and sel.last_rejection == "blurry",
           f"reason: {sel.last_rejection}")

    dim = scene("SORTIE", 5).point(lambda v: int(v * 0.35))
    got = feed(sel, dim, t)
    t += 3
    if got:
        enhanced, steps = m.enhance(got[0]["image"], got[0]["brightness"], got[0]["sharpness"], sel.baseline())
        gray_before = m.brightness(m.analysis_image(got[0]["image"]))
        gray_after = m.brightness(m.analysis_image(enhanced))
        record("dim but sharp frame kept and brightened (pre-processing)",
               "brightened" in steps and gray_after > gray_before + 25,
               f"brightness {gray_before:.0f} -> {gray_after:.0f}, steps {steps}")
    else:
        record("dim but sharp frame kept and brightened (pre-processing)", False,
               f"dim frame rejected: {sel.last_rejection}")

    print("\n[2] Database, keyword search and retention (offline)")
    tmp = Path(tempfile.mkdtemp())
    m.FRAMES_DIR = tmp / "frames"

    async def db_tests():
        svc = m.MemoryService()
        svc.store = m.MemoryStore(tmp / "test.db")
        sample = {"image": base, "at": time.time() - 3600, "sharpness": 50.0, "brightness": 120.0,
                  "edges": 10.0, "novelty": 64, "reason": "new view"}
        await svc._keep(sample, "s1")
        sample2 = dict(sample, at=time.time(), image=new_scene)
        await svc._keep(sample2, "s1")
        rows = await svc.store.run(svc.store._pending, 10)
        record("useful frames stored with time and metrics", len(rows) == 2 and all(Path(r["path"]).is_file() for r in rows))
        newest, oldest = rows[0], rows[1]
        await svc.store.run(svc.store._save_analysis, newest["id"], {
            "caption": "A green room sign in a corridor", "text": "ROOM 12", "text_languages": ["en"],
            "objects": ["sign", "door"], "hazards": []}, None)
        hits = await svc.search("room 12 sign")
        record("keyword search finds the frame by its text", hits and hits[0]["id"] == newest["id"],
               f"{len(hits)} hit(s) via {hits[0]['match'] if hits else '-'}")
        recent = await svc.recent(120)
        record("time search returns only recent frames", [f["id"] for f in recent] == [newest["id"]])
        deleted = await svc.store.run(svc.store._delete_old, time.time() - m.RETENTION_S)
        record("retention deletes frames older than 30 min (row + file)",
               deleted == 1 and not Path(oldest["path"]).exists() and Path(newest["path"]).exists())
        await svc.store.run(svc.store._insert_qa, {
            "id": "q1", "asked_at": time.time(), "question": "What does the sign say?", "question_heard": "",
            "answer": "It says Room 12.", "answer_language": "en", "frames_used": "[]", "evidence": "strong",
            "latency_s": 2.1, "mode": "live"})
        qa = await svc.store.run(svc.store._qa, 5)
        record("questions and answers stored for the dashboard", len(qa) == 1 and qa[0]["answer"] == "It says Room 12.")

        # A transient analysis failure (e.g. 503 "high demand") must not erase a scene.
        class Overloaded:
            class aio:
                class models:
                    @staticmethod
                    async def generate_content(**_):
                        raise RuntimeError("503 high demand (simulated)")
        svc.client = Overloaded()
        await svc._keep(dict(sample, at=time.time(), image=scene("EXIT", 77)), "s1")
        door = (await svc.store.run(svc.store._pending, 10))[0]
        statuses = []
        for _ in range(m.MAX_ANALYSIS_ATTEMPTS):
            await svc.analyze_batch([door])
            statuses.append((await svc.store.run(svc.store._frame, door["id"]))["status"])
        record("transient analysis failure: frame retried, failed only after 3 attempts",
               statuses == ["pending"] * (m.MAX_ANALYSIS_ATTEMPTS - 1) + ["failed"], " -> ".join(statuses))
        svc.store.db.close()

    asyncio.run(db_tests())


def live() -> None:
    print("\n[3] Live: Gemini batch OCR/objects + search by meaning")
    import config
    from google import genai

    tmp = Path(tempfile.mkdtemp())
    m.FRAMES_DIR = tmp / "frames"

    async def run():
        svc = m.MemoryService()
        svc.store = m.MemoryStore(tmp / "live.db")
        svc.client = genai.Client(api_key=config.GEMINI_API_KEY)
        signs = [("EXIT", "en"), ("مخرج الطوارئ", "ar"), ("SORTIE", "fr"), ("ROOM 12", "en")]
        now = time.time()
        for i, (text, _) in enumerate(signs):
            await svc._keep({"image": scene(text, 20 + i), "at": now - 60 * (len(signs) - i),
                             "sharpness": 60.0, "brightness": 120.0, "edges": 10.0, "novelty": 64,
                             "reason": "new view"}, "live")
        rows = await svc.store.run(svc.store._pending, 10)
        t0 = time.perf_counter()
        await svc.analyze_batch(rows)
        batch_s = time.perf_counter() - t0
        frames = await svc.recent(3600, 10)
        analyzed = [f for f in frames if f["status"] == "analyzed"]
        record("one Gemini request analysed 4 frames", len(analyzed) == 4,
               f"{batch_s:.2f} s, stats {svc.analysis_stats}")
        texts = " | ".join(f["text"] for f in analyzed)
        for sign, lang in signs:
            ok = sign.replace(" ", "").lower() in texts.replace(" ", "").lower()
            record(f"OCR read '{sign}' ({lang})", ok, texts[:120])
        hits = await svc.search("where is the way out of the building", 3)
        record("search by MEANING: 'way out of the building' -> exit sign",
               bool(hits) and any(w in (hits[0]["text"] or "").upper() for w in ("EXIT", "SORTIE", "مخرج")),
               f"top: {hits[0]['text']!r} score {hits[0]['score']}" if hits else "no hits")
        hits_fr = await svc.search("chambre numéro douze", 2)
        record("multilingual meaning search (French query -> 'ROOM 12')",
               bool(hits_fr) and "12" in (hits_fr[0]["text"] or ""),
               f"top: {hits_fr[0]['text']!r}" if hits_fr else "no hits")
        recent = await svc.recent(90)
        record("time search: last 90 s -> only the newest sign", len(recent) == 1 and "12" in recent[0]["text"],
               f"{[f['text'] for f in recent]}")
        svc.store.db.close()

    asyncio.run(run())


if __name__ == "__main__":
    offline()
    if "--live" in sys.argv:
        live()
    print(f"\n{sum(RESULTS)}/{len(RESULTS)} memory checks passed")
    sys.exit(0 if all(RESULTS) else 1)
