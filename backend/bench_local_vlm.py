"""
Local vision-language model benchmark (llama.cpp server, CPU only).

Starts llama-server with a GGUF model + its vision projector, asks the
questions that matter for a blind user, and measures correctness,
latency and resources. Each answer is printed in full so it can be
judged by a person, not only by keyword checks.

Usage:
  .venv\\Scripts\\python bench_local_vlm.py MODEL.gguf MMPROJ.gguf [--threads 4] [--size 512]
                                            [--load]   (also stream 2 phones during the test)
No cloud request. Images: synthetic test scenes, OpenCV sample photos,
and up to 3 anonymised real frames from storage/memory (never uploaded).
"""

import argparse
import base64
import io
import json
import os
import sqlite3
import statistics
import subprocess
import sys
import time
from pathlib import Path

import httpx
from PIL import Image

from test_assistant_flow import draw_scene
from test_helpers import ProcessMeter
from test_memory import scene as sign_scene

HERE = Path(__file__).resolve().parent
LLAMA = Path(os.getenv("WV_LLAMA_DIR", r"C:\Multimedia Labs\local-ai\llama.cpp"))
PORT = 8091

SYSTEM = ("You help a blind person understand what their phone camera sees. Answer in ONE or TWO short "
          "spoken sentences, only from what is visible. If something is not visible or unreadable, say you "
          "are not sure. Never say a path is safe. Answer in the language of the question.")


def jpeg_uri(image: Image.Image, size: int) -> str:
    image = image.convert("RGB")
    image.thumbnail((size, size))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()


def cases() -> list[dict]:
    """(name, image, question, check(answer) -> bool | None for manual judgement)."""
    faces = HERE / "storage" / "test_faces"
    out = [
        dict(name="ocr door EN", image=draw_scene("door", 2), q="What is written on the door?",
             check=lambda a: "12" in a and "ROOM" in a.upper()),
        dict(name="ocr sign EN", image=draw_scene("cafe", 2), q="What does the sign in front of me say?",
             check=lambda a: "CAFE" in a.upper()),
        dict(name="ocr French", image=sign_scene("SORTIE", 3), q="Qu'est-ce qui est écrit sur le panneau ?",
             check=lambda a: "SORTIE" in a.upper()),
        dict(name="ocr Arabic", image=sign_scene("مخرج الطوارئ", 21), q="شو مكتوب عاللافتة؟",
             check=lambda a: "مخرج" in a),
        dict(name="hazard: can I walk", image=draw_scene("door", 1), q="Can I walk straight ahead?",
             check=lambda a: "door" in a.lower() and "safe" not in a.lower()),
        dict(name="lebanese answer", image=draw_scene("exit", 2), q="شو في قدامي؟",
             check=lambda a: "EXIT" in a.upper() or "مخرج" in a),
        dict(name="not visible -> unsure", image=draw_scene("wall", 2), q="What is written on the paper on the table?",
             check=lambda a: any(w in a.lower() for w in ("not sure", "don't see", "do not see", "no paper",
                                                          "no table", "cannot see", "can't see", "not visible"))),
        dict(name="french answer", image=draw_scene("cafe", 1), q="Qu'est-ce qu'il y a devant moi ?",
             check=lambda a: any(w in a.lower() for w in ("panneau", "devant", "il y a", "rouge"))),
    ]
    for name, file, q in (("photo: describe", "basketball1.png", "What is in front of me?"),
                          ("photo: where is the ball", "basketball1.png", "Where is the ball?")):
        if (faces / file).exists():
            out.append(dict(name=name, image=Image.open(faces / file), q=q, check=None))
    db = HERE / "storage" / "memory" / "memory.db"
    if db.exists():
        rows = sqlite3.connect(f"file:{db}?mode=ro", uri=True).execute(
            "SELECT path FROM frames WHERE faces IS NOT NULL ORDER BY captured_at DESC LIMIT 40").fetchall()
        for i, (path,) in enumerate(rows[::14][:3]):
            if Path(path).exists():
                out.append(dict(name=f"real frame {i + 1}", image=Image.open(path), q="What is in front of me?",
                                check=None, path=path))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("model")
    ap.add_argument("mmproj")
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--size", type=int, default=512)
    ap.add_argument("--load", action="store_true")
    args = ap.parse_args()

    log = open(HERE / "bench_local_vlm_server.log", "w", encoding="utf-8")
    server = subprocess.Popen(
        [str(LLAMA / "llama-server.exe"), "-m", args.model, "--mmproj", args.mmproj, "--port", str(PORT),
         "-t", str(args.threads), "-c", "4096", "--no-webui", "-np", "1", "--reasoning-budget", "0"],
        stdout=log, stderr=subprocess.STDOUT)
    meter = None
    results = []
    try:
        t0 = time.time()
        with httpx.Client(timeout=600) as c:
            while time.time() - t0 < 180:
                try:
                    if c.get(f"http://127.0.0.1:{PORT}/health").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.5)
            load_s = time.time() - t0
            meter = ProcessMeter(server.pid)
            print(f"model loaded in {load_s:.1f} s, RAM {meter.rss() / 2**20:.0f} MB "
                  f"(threads {args.threads}, image max side {args.size} px)", flush=True)
            for case in cases():
                body = {"messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": jpeg_uri(case["image"], args.size)}},
                    {"type": "text", "text": case["q"]}]}],
                    "max_tokens": 120, "temperature": 0.2, "stream": True,
                    "chat_template_kwargs": {"enable_thinking": False}}       # short spoken answers, no hidden reasoning
                t = time.perf_counter()
                first, text = None, ""
                with c.stream("POST", f"http://127.0.0.1:{PORT}/v1/chat/completions", json=body) as r:
                    for line in r.iter_lines():
                        if not line.startswith("data: ") or line.endswith("[DONE]"):
                            continue
                        delta = json.loads(line[6:])["choices"][0]["delta"].get("content") or ""
                        if delta and first is None:
                            first = time.perf_counter() - t
                        text += delta
                total = time.perf_counter() - t
                meter.sample()
                ok = case["check"](text) if case["check"] else None
                results.append((case["name"], ok, first or total, total))
                verdict = "OK  " if ok else ("??  " if ok is None else "FAIL")
                print(f"{verdict} {case['name']:24s} first token {first or 0:5.1f} s, total {total:5.1f} s | "
                      f"{case['q']} -> {text.strip()}", flush=True)
    finally:
        server.terminate()
        server.wait(10)
        log.close()
    auto = [r for r in results if r[1] is not None]
    firsts = [r[2] for r in results]
    print(f"\nautomatic checks: {sum(r[1] for r in auto)}/{len(auto)} | first token median "
          f"{statistics.median(firsts):.1f} s, max {max(firsts):.1f} s | total median "
          f"{statistics.median([r[3] for r in results]):.1f} s | peak RAM {meter.peak_rss / 2**20:.0f} MB | "
          f"CPU {meter.cpu_percent():.0f} % of one core (avg)")


if __name__ == "__main__":
    main()
