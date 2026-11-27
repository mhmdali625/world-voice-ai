"""
How much would streaming the vision answer save? (vision quota only)

Measures, for the production prompt and schema, the time to
the first streamed text vs the time to the complete answer.
The difference is the most that pipelining vision -> speech
could gain (speech could start on the first sentence).

Usage: .venv\\Scripts\\python bench_vision_stream.py image.jpg "question" [n]
"""

import asyncio
import re
import statistics
import sys
import time

from google import genai
from google.genai import types

import config
from main import AI_SYSTEM_INSTRUCTION, VisualAnswer


async def main():
    path, question = sys.argv[1], sys.argv[2]
    n = int(sys.argv[3]) if len(sys.argv) > 3 else 5
    image = open(path, "rb").read()
    mime = "image/png" if path.lower().endswith(".png") else "image/jpeg"
    client = genai.Client(api_key=config.GEMINI_API_KEY)
    await client.aio.models.get(model=config.VISION_MODEL)

    cfg = types.GenerateContentConfig(
        system_instruction=AI_SYSTEM_INSTRUCTION,
        temperature=0.2,
        max_output_tokens=config.VISION_MAX_OUTPUT_TOKENS,
        response_mime_type="application/json",
        response_schema=VisualAnswer,
        thinking_config=types.ThinkingConfig(thinking_level=config.VISION_THINKING_LEVEL),
    )

    first_texts, first_sentences, totals = [], [], []
    for i in range(n):
        start = time.perf_counter()
        first_text = first_sentence = None
        text = ""
        stream = await client.aio.models.generate_content_stream(
            model=config.VISION_MODEL,
            contents=[question, types.Part.from_bytes(data=image, mime_type=mime)],
            config=cfg,
        )
        async for chunk in stream:
            if chunk.text:
                now = time.perf_counter() - start
                first_text = first_text or now
                text += chunk.text
                if first_sentence is None and re.search(r'"answer"\s*:\s*"[^"]*?[.!?؟]', text):
                    first_sentence = now
        total = time.perf_counter() - start
        first_texts.append(first_text or total)
        first_sentences.append(first_sentence or total)
        totals.append(total)
        print(f"run {i + 1}: first text {first_text:.2f} s | first full sentence "
              f"{(first_sentence or total):.2f} s | complete {total:.2f} s | {len(text)} chars", flush=True)

    print(f"\nmedian: first text {statistics.median(first_texts):.2f} s, first sentence "
          f"{statistics.median(first_sentences):.2f} s, complete {statistics.median(totals):.2f} s")
    print(f"=> pipelining could start speech ~{statistics.median(totals) - statistics.median(first_sentences):.2f} s earlier")


if __name__ == "__main__":
    asyncio.run(main())
