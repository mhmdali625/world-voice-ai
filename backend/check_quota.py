"""
Quota probe: ONE small request per kind of Gemini use in the app, and,
for a 429, which limit it refers to (quota id, limit value, retry delay).

Usage: .venv\\Scripts\\python check_quota.py      (at most 1 request per line)
"""

import io
import re

from google import genai
from google.genai import types
from PIL import Image

import config


def tiny_jpeg() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), (200, 30, 30)).save(buffer, format="JPEG")
    return buffer.getvalue()


def explain(error: Exception) -> str:
    text = str(error)
    quota_ids = sorted(set(re.findall(r"quotaId['\"]?:\s*['\"]([^'\"]+)", text)))
    values = re.findall(r"quotaValue['\"]?:\s*['\"]?(\d+)", text)
    delay = re.findall(r"retryDelay['\"]?:\s*['\"]([^'\"]+)", text)
    message = re.findall(r"'message':\s*'([^']{0,240})", text)
    return (f"HTTP {getattr(error, 'code', '?')} quota={quota_ids} limit={values} retry={delay} "
            f"message={message[:1]}")


def main() -> None:
    client = genai.Client(api_key=config.GEMINI_API_KEY)
    checks = [
        ("text", lambda: client.models.generate_content(model=config.VISION_MODEL, contents="Say OK").text),
        ("text + image", lambda: client.models.generate_content(model=config.VISION_MODEL, contents=[
            types.Part.from_bytes(data=tiny_jpeg(), mime_type="image/jpeg"), "What colour is this? One word."]).text),
        ("embeddings", lambda: str(len(client.models.embed_content(
            model="gemini-embedding-001", contents=["test"]).embeddings))),
    ]
    for name, call in checks:
        try:
            print(f"{name:14s} OK  {str(call()).strip()[:30]}")
        except Exception as error:  # noqa: BLE001 - report every failure
            print(f"{name:14s} {explain(error)}")
    print(f"model: {config.VISION_MODEL}")


if __name__ == "__main__":
    main()
