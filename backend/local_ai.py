"""
Local (offline) AI: the last answer provider, used when no cloud model can
answer (no internet, every quota used up). Two local processes, started by
start_local_ai.ps1 and kept LOADED:

  * llama-server (llama.cpp, CPU) with Qwen3.5 + its vision projector
      -> vision-language answers and, if validated, background frame analysis;
  * local_sidecar.py -> faster-whisper (speech to text) and Piper (speech).

    WV_LOCAL_VLM_URL     = http://127.0.0.1:8091   (empty = off)
    WV_LOCAL_SIDECAR_URL = http://127.0.0.1:8092   (empty = off)
    WV_LOCAL_BACKGROUND  = 0 | 1   use the local model for background analysis
                                    when Gemini cannot (only if validated)

Nothing here runs unless those processes answer /health.
"""

import base64
import json
import os
import re
import time

import httpx

VLM_URL = os.getenv("WV_LOCAL_VLM_URL", "http://127.0.0.1:8091").rstrip("/")
SIDECAR_URL = os.getenv("WV_LOCAL_SIDECAR_URL", "http://127.0.0.1:8092").rstrip("/")
# Local-first background analysis (default ON): used whenever the local model runs;
# when it does not, the cloud analyses frames exactly as before.
BACKGROUND = os.getenv("WV_LOCAL_BACKGROUND", "1") == "1"
# Languages the local model answers correctly (bench_local_vlm.py): Qwen3.5-0.8B read English and
# French signs but misread Arabic and answered a Lebanese question in English -> en,fr only.
# An Arabic question offline gets the honest "unavailable" message instead of a wrong answer.
LANGS = {x.strip() for x in os.getenv("WV_LOCAL_LANGS", "en,fr").split(",") if x.strip()}
ANSWER_TIMEOUT_S = float(os.getenv("WV_LOCAL_TIMEOUT", "40"))

_health: dict[str, tuple[float, bool]] = {}


async def available(url: str) -> bool:
    """/health of a local service, cached 10 s."""
    if not url:
        return False
    cached = _health.get(url)
    if cached and time.time() - cached[0] < 10:
        return cached[1]
    try:
        async with httpx.AsyncClient(timeout=1.5) as c:
            ok = (await c.get(f"{url}/health")).status_code == 200
    except httpx.HTTPError:
        ok = False
    _health[url] = (time.time(), ok)
    return ok


async def transcribe(audio: bytes, language: str | None = None) -> tuple[str, str]:
    """Local speech recognition -> (text, language code)."""
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{SIDECAR_URL}/asr" + (f"?language={language}" if language else ""), content=audio)
    r.raise_for_status()
    data = r.json()
    return data["text"], data["language"]


async def speak_pcm(text: str, language: str) -> tuple[bytes, int]:
    """Local speech (Piper) -> (16-bit mono PCM, sample rate)."""
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{SIDECAR_URL}/tts", json={"text": text, "language": language})
    r.raise_for_status()
    return r.content, int(r.headers.get("X-Sample-Rate", "22050"))


LOCAL_RULES = """You are the eyes of a blind person. Answer in one or two short sentences, ONLY from the image.
Answer in the SAME language as the question (English, French, formal Arabic or Lebanese Arabic).
If something is not visible or unreadable, say you are not sure. Never say a path is safe.
Never identify people. Read text exactly as written.
Reply ONLY with JSON: {"answer": "...", "evidence": "strong|weak|none"}"""


def _uri(jpeg: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()


async def ask_vlm(system: str, question: str, image: bytes | None, extra_text: str = "",
                  max_tokens: int = 160) -> dict:
    content: list[dict] = []
    if image is not None:
        content.append({"type": "image_url", "image_url": {"url": _uri(image)}})
    content.append({"type": "text", "text": (extra_text + "\n" if extra_text else "") + question})
    body = {"messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
            "max_tokens": max_tokens, "temperature": 0.2,
            "chat_template_kwargs": {"enable_thinking": False}}     # Qwen3.5 thinking off: short answers
    async with httpx.AsyncClient(timeout=ANSWER_TIMEOUT_S) as c:
        r = await c.post(f"{VLM_URL}/v1/chat/completions", json=body)
    r.raise_for_status()
    raw = r.json()["choices"][0]["message"]["content"]
    m = re.search(r"\{.*\}", raw, re.S)
    try:
        return json.loads(m.group(0)) if m else {"answer": raw.strip()}
    except ValueError:
        return {"answer": raw.strip()}


def make_answer_provider(answer_model):
    """Builds the `local_answer` hook for assistant_service (answer_model = AssistantAnswer)."""

    async def local_answer(*, device, now, audio_bytes, audio_mime, question, lang, current, memory_frames,
                           index_lines, timings):
        if not await available(VLM_URL):
            raise RuntimeError("local vision model not running")
        if not question and audio_bytes:
            if not await available(SIDECAR_URL):
                raise RuntimeError("local speech recognition not running")
            t = time.perf_counter()
            question, heard_lang = await transcribe(audio_bytes)
            timings["local_asr"] = round(time.perf_counter() - t, 3)
            lang = {"ar": "ar-LB", "fr": "fr", "en": "en"}.get(heard_lang, lang)
        if not question.strip():
            raise RuntimeError("nothing was understood")
        if lang not in LANGS:
            raise RuntimeError(f"local model not validated for {lang} (WV_LOCAL_LANGS={sorted(LANGS)})")
        memory_text = ("Earlier (memory, newest first):\n" + "\n".join(index_lines[:8])) if index_lines else ""
        t = time.perf_counter()
        data = await ask_vlm(LOCAL_RULES, question, current, memory_text)
        timings["local_vlm"] = round(time.perf_counter() - t, 3)
        timings["model"] = "local"
        evidence = data.get("evidence") if data.get("evidence") in ("strong", "weak", "none") else "weak"
        return answer_model(question_heard=question, spoken_language=lang, focus="", used=["CURRENT"],
                            answer=str(data.get("answer", "")).strip() or "…", answer_language=lang,
                            evidence=evidence)

    return local_answer


ANALYSIS_RULES = """You index ONE frame from a blind user's phone camera into a visual memory.
Reply ONLY with this JSON (replace the <...> parts):
{"caption": "<one short factual English sentence about the scene>",
 "text": "<every word written in the image, exactly as written, or empty>",
 "objects": ["<object>", "<object>"],
 "hazards": [{"label_en": "<obstacle>", "position": "ahead", "in_path": true}]}
hazards: ONLY obstacles in the walking path (closed door ahead, stairs, object on the floor ahead);
use [] when there is none. Ordinary objects at the side or on a wall are not hazards.
Never identify people. Do not invent text."""

_PLACEHOLDER = re.compile(r"<|>|short english|exactly as written|readable text|nouns|its own script|"
                          r"one short factual|every word written", re.I)


def _clean(value: str) -> str:
    """Drop answers that only repeat the prompt's own instructions."""
    return "" if _PLACEHOLDER.search(value) else value.strip()


async def analyze_frame(jpeg: bytes) -> dict:
    """One frame -> {caption, text, text_languages, objects, hazards} with the local model."""
    if not await available(VLM_URL):
        raise RuntimeError("local vision model not running")
    data = await ask_vlm(ANALYSIS_RULES, "Index this frame.", jpeg, max_tokens=260)
    text = _clean(str(data.get("text", "")))[:500]
    hazards = []
    for h in data.get("hazards", []) if isinstance(data.get("hazards"), list) else []:
        if isinstance(h, dict) and h.get("label_en"):
            label = str(h["label_en"])
            hazards.append({"label_en": label, "label_fr": label, "label_ar": label, "label_lb": label,
                            "position": h.get("position") if h.get("position") in ("ahead", "left", "right") else "unknown",
                            "in_path": bool(h.get("in_path")), "certain": False})
    return {"caption": _clean(str(data.get("caption", "")))[:300], "text": text,
            "text_languages": ["ar"] if re.search(r"[\u0600-\u06FF]", text) else ([] if not text else ["en"]),
            "objects": [o for o in (_clean(str(x)) for x in data.get("objects", []))
                        if o and len(o.split()) <= 3][:10] if isinstance(data.get("objects"), list) else [],
            "hazards": hazards}
