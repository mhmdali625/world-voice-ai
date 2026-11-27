"""
One request per candidate Gemini-API vision model: can it read a sign
in a real test scene, with the app's structured JSON output, and how fast?

Usage: .venv\\Scripts\\python probe_models.py [model ...]
Quota: 1 request per model.
"""

import io
import json
import re
import sys
import time

from google import genai
from google.genai import types
from pydantic import BaseModel

import config
from check_quota import explain
from test_assistant_flow import draw_scene

DEFAULT = ["gemini-3.1-flash-lite", "gemini-2.5-flash-lite", "gemini-2.5-flash", "gemini-3.5-flash",
           "gemma-4-26b-a4b-it", "gemma-4-31b-it"]


class Reading(BaseModel):
    text: str
    objects: list[str]
    answer: str


def jpeg(kind: str) -> bytes:
    buffer = io.BytesIO()
    draw_scene(kind, 2).save(buffer, format="JPEG", quality=82)
    return buffer.getvalue()


def main() -> None:
    client = genai.Client(api_key=config.GEMINI_API_KEY)
    prompt = ("Read all text in the image exactly, list the main objects, and answer in English: "
              "what is written on the sign in front of me?")
    for model in sys.argv[1:] or DEFAULT:
        gemma = model.startswith("gemma")
        cfg = types.GenerateContentConfig(temperature=0.1) if gemma else types.GenerateContentConfig(
            temperature=0.1, response_mime_type="application/json", response_schema=Reading)
        contents = [types.Part.from_bytes(data=jpeg("door"), mime_type="image/jpeg"),
                    prompt + (" Reply ONLY with JSON: {\"text\": str, \"objects\": [str], \"answer\": str}"
                              if gemma else "")]
        t = time.perf_counter()
        try:
            r = client.models.generate_content(model=model, contents=contents, config=cfg)
            secs = time.perf_counter() - t
            raw = r.text or ""
            m = re.search(r"\{.*\}", raw, re.S)
            data = json.loads(m.group(0)) if m else {}
            ok = "12" in (data.get("text", "") + data.get("answer", ""))
            print(f"{model:24s} {secs:5.2f} s  {'READ OK' if ok else 'WRONG '} json={'yes' if data else 'NO'} "
                  f"| {data.get('answer', raw)[:60]!r}", flush=True)
        except Exception as error:  # noqa: BLE001
            print(f"{model:24s} {explain(error)[:160]}", flush=True)
        time.sleep(2)


if __name__ == "__main__":
    main()
