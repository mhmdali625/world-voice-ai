"""
Groq probe: does a model accept images, is it correct, how fast, and what
are its REAL limits (Groq returns them in the response headers).

Usage: .venv\\Scripts\\python probe_groq.py [model]     (1-3 requests)
"""

import base64
import io
import sys
import time

import httpx

import config
from test_assistant_flow import draw_scene
from test_memory import scene as sign_scene

URL = "https://api.groq.com/openai/v1/chat/completions"


def uri(image) -> str:
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()


def ask(client: httpx.Client, model: str, image, question: str) -> None:
    body = {"model": model, "temperature": 0.2, "max_completion_tokens": 200,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": question},
                {"type": "image_url", "image_url": {"url": uri(image)}}]}]}
    t = time.perf_counter()
    r = client.post(URL, json=body, headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"})
    secs = time.perf_counter() - t
    limits = {k: v for k, v in r.headers.items() if k.startswith("x-ratelimit")}
    if r.status_code != 200:
        print(f"HTTP {r.status_code} in {secs:.2f} s: {r.text[:300]}")
    else:
        data = r.json()
        print(f"{secs:.2f} s | {data['choices'][0]['message']['content'].strip()[:200]!r}")
        print(f"   usage {data.get('usage', {}).get('prompt_tokens')} prompt tokens")
    print(f"   limits {limits}")


def main() -> None:
    model = sys.argv[1] if len(sys.argv) > 1 else "qwen/qwen3.8-27b"
    with httpx.Client(timeout=60) as c:
        ask(c, model, draw_scene("door", 2), "What is written on the door? Answer in one short sentence.")
        time.sleep(2)
        ask(c, model, sign_scene("مخرج الطوارئ", 21), "شو مكتوب عاللافتة؟ جاوب بجملة قصيرة باللهجة اللبنانية.")


if __name__ == "__main__":
    main()
