"""
Background frame analysis: local model (llama-server on :8091) vs the truth,
frame by frame. Decides what the local model may index by itself.

  * OCR exactness per script (English / French / Arabic)
  * obstacle reported for a closed door ahead, NOT for a wall or a sign on a wall
  * captions/objects on real photos (printed for human judgement)
  * time per frame

Usage: .venv\\Scripts\\python bench_local_analysis.py [label]     (local only, no cloud)
"""

import asyncio
import io
import re
import statistics
import sys
import time
from pathlib import Path

from PIL import Image

import local_ai
from test_assistant_flow import draw_scene
from test_memory import scene as sign_scene

HERE = Path(__file__).resolve().parent


SIZE = int(sys.argv[2]) if len(sys.argv) > 2 else 1024


def jpeg(image: Image.Image) -> bytes:
    image = image.convert("RGB")
    image.thumbnail((SIZE, SIZE))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=80)
    return buffer.getvalue()


def norm(text: str) -> str:
    return re.sub(r"[\sً-ْـ\"'.,:;!?]+", "", text.upper())


CASES = [
    # name, image, expected text (None = no check), expected obstacle (True/False/None)
    ("EN sign EXIT", draw_scene("exit", 2), "EXIT", False),
    ("EN door ROOM 12 (ahead)", draw_scene("door", 2), "ROOM 12", True),
    ("EN sign CAFE on wall", draw_scene("cafe", 2), "CAFE", False),
    ("plain wall", draw_scene("wall", 2), "", False),
    ("FR sign SORTIE", sign_scene("SORTIE", 3), "SORTIE", None),
    ("AR sign مخرج الطوارئ", sign_scene("مخرج الطوارئ", 21), "مخرج الطوارئ", None),
    ("EN sign ROOM 12 (corridor)", sign_scene("ROOM 12", 4), "ROOM 12", None),
]


async def main() -> None:
    label = sys.argv[1] if len(sys.argv) > 1 else "local"
    faces = HERE / "storage" / "test_faces"
    cases = list(CASES)
    if (faces / "basketball1.png").exists():
        cases.append(("photo: two people, ball", Image.open(faces / "basketball1.png"), None, None))
    ocr = {"latin": [], "arabic": []}
    hazard_ok, times = [], []
    for name, image, text, obstacle in cases:
        t = time.perf_counter()
        try:
            a = await local_ai.analyze_frame(jpeg(image))
        except Exception as error:  # noqa: BLE001
            print(f"ERROR {name}: {type(error).__name__}: {error}")
            continue
        secs = time.perf_counter() - t
        times.append(secs)
        verdict = []
        if text is not None:
            ok = norm(a["text"]) == norm(text)
            (ocr["arabic"] if re.search(r"[؀-ۿ]", text) else ocr["latin"]).append(ok)
            verdict.append(f"text {'OK' if ok else 'WRONG'}")
        if obstacle is not None:
            found = any(h["in_path"] for h in a["hazards"])
            hazard_ok.append(found == obstacle)
            verdict.append(f"obstacle {'OK' if found == obstacle else 'WRONG'} ({'reported' if found else 'none'})")
        print(f"{name:28s} {secs:5.1f} s | {' | '.join(verdict) or 'judge by eye'}\n"
              f"{'':28s} text={a['text']!r} objects={a['objects'][:6]} hazards={[h['label_en'] for h in a['hazards']]}\n"
              f"{'':28s} caption={a['caption']!r}", flush=True)
    print(f"\n[{label} @ {SIZE}px] OCR latin {sum(ocr['latin'])}/{len(ocr['latin'])} | OCR arabic "
          f"{sum(ocr['arabic'])}/{len(ocr['arabic'])} | obstacle decisions {sum(hazard_ok)}/{len(hazard_ok)} | "
          f"time per frame median {statistics.median(times):.1f} s, max {max(times):.1f} s")


if __name__ == "__main__":
    asyncio.run(main())
