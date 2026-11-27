"""
Interleaved vision latency A/B test (uses Gemini VISION quota only).

Runs the production prompt, schema and image against several
configurations in round-robin order, so time-of-day queueing
affects every configuration equally.

Usage: .venv\\Scripts\\python bench_vision.py image.jpg "question" [rounds]
"""

import asyncio
import statistics
import sys
import time

from google import genai
from google.genai import types

import config
from main import AI_SYSTEM_INSTRUCTION, VisualAnswer

CONFIGS = [
    ("gemini-3.5-flash-lite", "low"),
    ("gemini-3.5-flash-lite", "minimal"),
    ("gemini-3.8-flash", "low"),
]


async def one(client, model, thinking, image, mime, question):
    cfg = types.GenerateContentConfig(
        system_instruction=AI_SYSTEM_INSTRUCTION,
        temperature=0.2,
        max_output_tokens=config.VISION_MAX_OUTPUT_TOKENS,
        response_mime_type="application/json",
        response_schema=VisualAnswer,
        thinking_config=types.ThinkingConfig(thinking_level=thinking),
    )
    start = time.perf_counter()
    response = await client.aio.models.generate_content(
        model=model,
        contents=[question, types.Part.from_bytes(data=image, mime_type=mime)],
        config=cfg,
    )
    elapsed = time.perf_counter() - start
    usage = response.usage_metadata
    answer = VisualAnswer.model_validate_json(response.text)
    return elapsed, usage, answer


async def main():
    path, question = sys.argv[1], sys.argv[2]
    rounds = int(sys.argv[3]) if len(sys.argv) > 3 else 5
    image = open(path, "rb").read()
    mime = "image/png" if path.lower().endswith(".png") else "image/jpeg"

    client = genai.Client(api_key=config.GEMINI_API_KEY)
    await client.aio.models.get(model=CONFIGS[0][0])   # warm the connection

    results = {c: [] for c in CONFIGS}
    answers = {}
    for r in range(rounds):
        for model, thinking in CONFIGS:
            try:
                elapsed, usage, answer = await one(client, model, thinking, image, mime, question)
                results[(model, thinking)].append(elapsed)
                answers[(model, thinking)] = answer
                print(f"round {r + 1} {model:22s} {thinking:8s} {elapsed:6.2f} s  "
                      f"in={usage.prompt_token_count} out={usage.candidates_token_count} "
                      f"think={usage.thoughts_token_count}", flush=True)
            except Exception as error:
                print(f"round {r + 1} {model} {thinking} ERROR {str(error)[:160]}", flush=True)

    print("\nSUMMARY (seconds)")
    for c, times in results.items():
        if times:
            print(f"{c[0]:22s} {c[1]:8s} median {statistics.median(times):5.2f}  "
                  f"min {min(times):5.2f}  max {max(times):5.2f}  n={len(times)}")
    print("\nLAST ANSWERS")
    for c, a in answers.items():
        print(f"{c[0]} {c[1]} [{a.answer_language}]: {a.answer}")


if __name__ == "__main__":
    asyncio.run(main())
