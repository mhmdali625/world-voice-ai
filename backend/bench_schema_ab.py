"""
A/B: does the answer schema order (question_heard -> focus -> used ->
answer) cost latency compared with the old order (answer first)?

Same request contents (spoken Lebanese past question + current view +
2 memory images + index), the two schemas interleaved so both see the
same Gemini load. Prints Gemini time and correctness per call.

Usage: .venv\\Scripts\\python bench_schema_ab.py [rounds]
Quota: 2 x rounds Gemini requests.
"""

import asyncio
import io
import statistics
import sys
import time
from typing import Literal

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

import assistant_service as a
import config
import main
from test_assistant_flow import draw_scene
from test_live_latency import QDIR

ROUNDS = int(sys.argv[1]) if len(sys.argv) > 1 else 5


class OldAnswer(BaseModel):
    answer: str = Field(description="The natural spoken answer.")
    answer_language: Literal["en", "ar", "ar-LB", "fr", "und"]
    question_heard: str = ""
    evidence: Literal["strong", "weak", "none"]
    used: list[str] = Field(default_factory=list)


def jpeg(kind: str) -> bytes:
    buffer = io.BytesIO()
    draw_scene(kind, 2).save(buffer, format="JPEG", quality=82)
    return buffer.getvalue()


async def main_() -> None:
    client = genai.Client(api_key=config.GEMINI_API_KEY)
    contents = [
        types.Part.from_bytes(data=(QDIR / "q_lb_past.wav").read_bytes(), mime_type="audio/wav"),
        "Above is the user's spoken question. It is AUTHORITATIVE: transcribe ALL of it into question_heard, "
        "and answer in the language the user SPOKE.",
        "CURRENT VIEW (now):", types.Part.from_bytes(data=jpeg("cafe"), mime_type="image/jpeg"),
        "MEMORY 1 (seen 30 seconds ago; scene: a gray wall):",
        types.Part.from_bytes(data=jpeg("wall"), mime_type="image/jpeg"),
        "MEMORY 2 (seen 36 seconds ago; text recorded: 'ROOM 12'):",
        types.Part.from_bytes(data=jpeg("door"), mime_type="image/jpeg"),
        "MEMORY INDEX (labelled S1, S2, ...):\n- S1, 14 seconds ago (still in view): red sign | text: 'CAFE'\n"
        "- S2, 30 seconds ago: gray wall\n- S3, 36 seconds ago: brown door | text: 'ROOM 12'\n"
        "- S4, 44 seconds ago: exit sign | text: 'EXIT'",
    ]
    times = {"old": [], "new": []}
    correct = {"old": 0, "new": 0}
    for i in range(ROUNDS):
        for name, schema in (("old", OldAnswer), ("new", a.AssistantAnswer)) if i % 2 == 0 else \
                (("new", a.AssistantAnswer), ("old", OldAnswer)):
            cfg = types.GenerateContentConfig(
                system_instruction=main.AI_SYSTEM_INSTRUCTION + a.ASSISTANT_RULES, temperature=0.2,
                max_output_tokens=config.VISION_MAX_OUTPUT_TOKENS, response_mime_type="application/json",
                response_schema=schema,
                thinking_config=types.ThinkingConfig(thinking_level=config.VISION_THINKING_LEVEL))
            t = time.perf_counter()
            r = await client.aio.models.generate_content(model=config.VISION_MODEL, contents=contents, config=cfg)
            secs = time.perf_counter() - t
            answer = schema.model_validate_json(r.text).answer
            ok = "12" in answer and "CAFE" not in answer.upper() and "كافيه" not in answer
            times[name].append(secs)
            correct[name] += ok
            print(f"{i + 1} {name}  {secs:5.2f} s  {'OK ' if ok else 'BAD'}  {answer[:70]}", flush=True)
            await asyncio.sleep(4)
    for name in times:
        print(f"{name}: median {statistics.median(times[name]):.2f} s, max {max(times[name]):.2f} s, "
              f"correct {correct[name]}/{ROUNDS}")


if __name__ == "__main__":
    asyncio.run(main_())
