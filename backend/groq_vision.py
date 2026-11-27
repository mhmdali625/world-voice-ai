"""
Second answer provider: Groq (independent free quota), used when Gemini
cannot answer (daily quota used up, overloaded, too slow).

Measured (2026-11-27, response headers and tests):
  * qwen/qwen3.8-27b accepts images; read "ROOM 12" (1.3 s) and the Arabic
    "مخرج الطوارئ" with a Lebanese answer (1.9 s);
  * limits: 1,000 requests/day; 7,000 INPUT tokens/minute; one image costs
    ~1,800 tokens whatever its size -> at most the current view + ONE
    memory image per question, the rest of the memory goes as text;
  * Groq Whisper (whisper-large-v3): 2,000 requests/day, ~1 s per question.

The system prompt is a compact version of the assistant rules (the full
prompt would use most of the per-minute token budget on its own).
"""

import asyncio
import base64
import json
import re
import time

import httpx

import config
from quota import budget

URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "qwen/qwen3.8-27b"
BUDGET_NAME = f"groq:{MODEL}"
TIMEOUT_S = 12.0

COMPACT_RULES = """You are the eyes of a BLIND person, speaking through their phone camera.
Answer in ONE to THREE short spoken sentences, most important first, ONLY from what the images show.

Language: answer in the language the user SPOKE (decided from the words, not the accent):
English -> English only; French -> French only; formal Arabic -> formal Arabic; Lebanese Arabic ->
natural Lebanese Arabic. Never mix in a language the user did not use. Quote sign text exactly as written.
Arabic only in Arabic script.

CURRENT VIEW = what the camera sees now: use it for questions about the present.
MEMORY = earlier frames: use it for past questions ("was", "كان", "était") and say roughly when.
"Where is X?": position from the image only (left/right/ahead, on/next to...), never invent distances;
if X is not visible, say so and, if it is in MEMORY, when it was seen.
"Can I pass?": name what blocks the way (a closed door must be opened first); if nothing is visible,
say you see no obstacle but the camera cannot guarantee the path is clear. NEVER say "safe".
If unsure or the images do not show it, say you are not sure. Never guess.
Never identify people (no face recognition): only say a person is present and where.
If the question is unintelligible, ask the user to repeat it.

Reply ONLY with JSON:
{"spoken_language": "en|fr|ar|ar-LB", "used": ["CURRENT" or "MEMORY 1" ...],
 "answer": "...", "answer_language": "en|fr|ar|ar-LB", "evidence": "strong|weak|none"}"""


class GroqUnavailable(RuntimeError):
    pass


def configured() -> bool:
    return bool(config.GROQ_API_KEY)


def usable() -> bool:
    return configured() and not budget.exhausted(BUDGET_NAME)


def _data_uri(jpeg: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()


def _retry_seconds(text: str) -> float:
    m = re.search(r"try again in (?:(\d+)m)?([\d.]+)s", text)
    return (int(m.group(1) or 0) * 60 + float(m.group(2))) if m else 60.0


async def answer(question: str, language_hint: str | None, current: bytes | None,
                 memory_image: tuple[str, bytes] | None, memory_text: str, context: str) -> dict:
    """Returns the parsed JSON answer. Raises GroqUnavailable (quota, outage, bad reply)."""
    if not usable():
        raise GroqUnavailable("Groq not configured or its daily quota is used up")
    content: list[dict] = [{"type": "text", "text": (
        (f"[Speech recognition heard the language: {language_hint}]\n" if language_hint else "")
        + f"Question: {question}")}]
    if current is not None:
        content += [{"type": "text", "text": "CURRENT VIEW (now):"},
                    {"type": "image_url", "image_url": {"url": _data_uri(current)}}]
    elif memory_image is not None:
        content.append({"type": "text", "text": "(Question about the past: the current view is not attached.)"})
    else:
        content.append({"type": "text", "text": "There is NO current camera view."})
    if memory_image is not None:
        content += [{"type": "text", "text": f"MEMORY 1 ({memory_image[0]}):"},
                    {"type": "image_url", "image_url": {"url": _data_uri(memory_image[1])}}]
    if memory_text:
        content.append({"type": "text", "text": memory_text})
    if context:
        content.append({"type": "text", "text": context})
    body = {"model": MODEL, "temperature": 0.2, "max_completion_tokens": 400,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": COMPACT_RULES}, {"role": "user", "content": content}]}
    return await _send(body, kind="user")


MAX_TOKEN_WAIT_S = 15.0         # the per-minute token limit frees up within a minute: wait once


# Groq counts INPUT tokens over a sliding minute (7,000). Track our own use
# (from each reply's usage) and plan: questions wait briefly BEFORE sending
# rather than being refused; background work only uses Groq when the past
# minute leaves room for a question.
TOKENS_PER_MINUTE = 7000
_token_log: list[tuple[float, int]] = []


def tokens_last_minute() -> int:
    now = time.time()
    _token_log[:] = [(t, n) for t, n in _token_log if now - t < 60]
    return sum(n for _, n in _token_log)


def _wait_for_tokens(needed: int) -> float:
    """Seconds until `needed` more tokens fit in the sliding minute."""
    now = time.time()
    used = tokens_last_minute()
    if used + needed <= TOKENS_PER_MINUTE:
        return 0.0
    freed = 0
    for t, n in sorted(_token_log):
        freed += n
        if used - freed + needed <= TOKENS_PER_MINUTE:
            return max(0.0, 60 - (now - t)) + 0.3
    return 60.0


def _estimate(body: dict) -> int:
    images = sum(1 for m in body["messages"] for c in (m["content"] if isinstance(m["content"], list) else [])
                 if c.get("type") == "image_url")
    text = sum(len(c.get("text", "")) for m in body["messages"]
               for c in (m["content"] if isinstance(m["content"], list) else [{"text": m["content"]}]))
    return images * 1850 + text // 3 + 200


async def _send(body: dict, kind: str) -> dict:
    needed = _estimate(body)
    wait = _wait_for_tokens(needed)
    if wait > 0:
        if kind != "user" or wait > MAX_TOKEN_WAIT_S:
            raise GroqUnavailable(f"token budget: {wait:.0f} s until {needed} tokens fit")
        print(f"[GROQ] waiting {wait:.1f} s for the per-minute token budget", flush=True)
        await asyncio.sleep(wait)
    budget.record(kind, BUDGET_NAME)
    r = None
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
                r = await client.post(URL, json=body, headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"})
        except httpx.HTTPError as error:
            raise GroqUnavailable(f"network: {type(error).__name__}") from error
        if r.status_code == 429 and "tokens per minute" in r.text.lower() and attempt == 0 and kind == "user":
            wait = _retry_seconds(r.text)
            if wait <= MAX_TOKEN_WAIT_S:
                print(f"[GROQ] token limit: waiting {wait:.1f} s and retrying once", flush=True)
                await asyncio.sleep(wait + 0.2)
                continue
        break
    if r.status_code == 429:
        text = r.text
        if "per day" in text.lower() or "(RPD)" in text or "requests per day" in text.lower():
            budget.mark_exhausted(BUDGET_NAME, _retry_seconds(text))
        raise GroqUnavailable(f"rate limited: {text[:120]}")
    if r.status_code != 200:
        raise GroqUnavailable(f"HTTP {r.status_code}: {r.text[:120]}")
    try:
        _token_log.append((time.time(), int(r.json().get("usage", {}).get("prompt_tokens", needed))))
        raw = r.json()["choices"][0]["message"]["content"]
        data = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
    except Exception as error:  # noqa: BLE001
        raise GroqUnavailable("invalid reply") from error
    if kind == "user" and not str(data.get("answer", "")).strip():
        raise GroqUnavailable("empty answer")
    return data


def wait_hint() -> float:
    """Seconds until Groq may answer again (0 if usable)."""
    until = budget.exhausted_until.get(BUDGET_NAME, 0)
    return max(0.0, until - time.time())


# ---------------- background frame analysis (throttled) ----------------
#
# Used only when Gemini cannot analyse frames (quota kept for questions,
# exhausted or overloaded) and no local model is installed. Groq's per-
# minute token budget is shared with the user's questions, so: one frame
# at a time, at most every 30 s, never within 20 s of a question, and at
# most BACKGROUND_DAILY_SHARE of the 1,000 daily requests.

BACKGROUND_MIN_INTERVAL_S = 30.0
QUIET_AFTER_QUESTION_S = 20.0
BACKGROUND_DAILY_SHARE = 300
_last_background = 0.0

ANALYSIS_RULES = """You index ONE frame from a blind user's phone camera into a visual memory.
Reply ONLY with JSON:
{"caption": "one short factual English sentence (never identify people; at most 'a person')",
 "text": "ALL readable text exactly as written, in its own script (Arabic in Arabic); empty if none",
 "text_languages": ["en", "fr", "ar" ...],
 "objects": ["up to 10 short English nouns"],
 "hazards": [{"label_en": "...", "label_fr": "...", "label_ar": "formal Arabic", "label_lb": "Lebanese Arabic",
              "position": "ahead|left|right|unknown", "in_path": true, "certain": true}]}
hazards: ONLY obstacles in the walking path (closed door ahead, stairs, object on the floor ahead);
ordinary objects at the side, on a wall or far away are not hazards. Do not guess unreadable text."""


def background_allowed() -> bool:
    if not usable():
        return False
    if time.time() - _last_background < BACKGROUND_MIN_INTERVAL_S:
        return False
    if time.monotonic() - budget.last_user_at < QUIET_AFTER_QUESTION_S or budget.user_inflight:
        return False
    if tokens_last_minute() + 2000 > TOKENS_PER_MINUTE - 3000:      # keep room for a question
        return False
    return budget.daily.get(BUDGET_NAME, {}).get("background", 0) < BACKGROUND_DAILY_SHARE


async def analyze_frame(jpeg: bytes) -> dict:
    """One frame -> {caption, text, text_languages, objects, hazards}. Raises GroqUnavailable."""
    global _last_background
    _last_background = time.time()
    body = {"model": MODEL, "temperature": 0.1, "max_completion_tokens": 500,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": ANALYSIS_RULES}, {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": _data_uri(jpeg)}}]}]}
    data = await _send(body, kind="background")
    return {"caption": str(data.get("caption", ""))[:300], "text": str(data.get("text", ""))[:500],
            "text_languages": [str(x) for x in data.get("text_languages", [])][:5],
            "objects": [str(x) for x in data.get("objects", [])][:10],
            "hazards": [_hazard(h) for h in data.get("hazards", []) if isinstance(h, dict) and h.get("label_en")]}


def _hazard(h: dict) -> dict:
    """Missing fields get safe defaults (unknown position, not certain)."""
    label = str(h["label_en"])
    position = h.get("position") if h.get("position") in ("ahead", "left", "right", "unknown") else "unknown"
    return {"label_en": label, "label_fr": str(h.get("label_fr") or label), "label_ar": str(h.get("label_ar") or label),
            "label_lb": str(h.get("label_lb") or h.get("label_ar") or label), "position": position,
            "in_path": bool(h.get("in_path", False)), "certain": bool(h.get("certain", False))}

