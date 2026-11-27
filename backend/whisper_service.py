"""
Speech recognition with Whisper Large V3 on Groq.

The /transcribe route is now part of the main backend
(port 8000), so the demo needs one Python process instead
of two. This file can still run on its own for
compatibility:  uvicorn whisper_service:app --port 8002

WHY THE PROMPT CHANGED
----------------------
Whisper does not treat its "prompt" as instructions. It
treats it as the transcript that came *before* this audio
and continues in the same style. The old prompt was a
comma-separated vocabulary list, so Whisper imitated it:
"كبرني، شو، في، قدامي." (commas between every word).

A short, natural, correctly punctuated Lebanese sentence
gives Whisper the right dialect and spelling cues without
pushing it toward a list format. WV_WHISPER_PROMPT selects
natural / none / legacy so the options can be compared on
the same recordings (see benchmark.py).
"""

import asyncio
import re
import time

from fastapi import APIRouter, FastAPI, File, HTTPException, UploadFile
from groq import APIConnectionError, APIStatusError, AsyncGroq

import config
from hedge import hedged


MAX_AUDIO_BYTES = 6 * 1024 * 1024

REQUEST_TIMEOUT = 30.0


# ========================================
# PROMPTS
# ========================================

# Natural everyday Lebanese with typical code-switching.
# Written as one continuous utterance, normally punctuated.
NATURAL_PROMPT = (
    "خبرني شو في قدامي. شو هيدا اللي عاليمين؟ "
    "وصفلي الصورة، وقلّي إذا في شي مكتوب عالـ screen."
)

# Previous vocabulary-list prompt, kept only for A/B tests.
LEGACY_PROMPT = (
    "خبرني, خبرنا, ليك, شو, في, قدامي, حدي, وراي, يمين, شمال, "
    "هيدا, هيدي, هول, هلق, فيك, بدي, وين, كيف, قديش, مش, "
    "اشرحلي, فرجيني, وصفلي. Camera, photo, image, object, color, "
    "distance, direction, document, screen, text, describe, explain, "
    "compare, identify. Image, couleur, objet, gauche, droite, "
    "distance, document, texte."
)


# Candidate: the same everyday question in English, French and
# Lebanese, so no single language dominates the context.
MULTI_PROMPT = (
    "Describe what's in front of me. "
    "Qu'est-ce qu'il y a devant moi ? "
    "خبرني شو في قدامي."
)


def get_prompt() -> str | None:
    mode = config.WHISPER_PROMPT_MODE
    if mode == "none":
        return None
    if mode == "legacy":
        return LEGACY_PROMPT
    if mode == "multi":
        return MULTI_PROMPT
    return NATURAL_PROMPT


# ========================================
# SUPPORTED AUDIO FORMATS
# ========================================

AUDIO_FORMATS = {
    "audio/x-m4a": ("recording.m4a", "audio/mp4"),
    "audio/m4a": ("recording.m4a", "audio/mp4"),
    "audio/mp4": ("recording.m4a", "audio/mp4"),
    "audio/aac": ("recording.m4a", "audio/mp4"),
    "audio/mpeg": ("recording.mp3", "audio/mpeg"),
    "audio/mp3": ("recording.mp3", "audio/mpeg"),
    "audio/wav": ("recording.wav", "audio/wav"),
    "audio/x-wav": ("recording.wav", "audio/wav"),
    "audio/ogg": ("recording.ogg", "audio/ogg"),
    "audio/flac": ("recording.flac", "audio/flac"),
    "audio/webm": ("recording.webm", "audio/webm"),
}


# ========================================
# SHARED GROQ CLIENT (connection reuse)
# ========================================

_groq: AsyncGroq | None = None


def _client() -> AsyncGroq:
    global _groq
    if _groq is None:
        _groq = AsyncGroq(
            api_key=config.GROQ_API_KEY,
            timeout=REQUEST_TIMEOUT,
            max_retries=1,
        )
    return _groq


async def close_groq_client() -> None:
    global _groq
    if _groq is not None:
        await _groq.close()
        _groq = None


# ========================================
# CORE TRANSCRIPTION
# ========================================

async def transcribe_bytes(
    audio_bytes: bytes,
    mime_type: str,
) -> dict:
    """Transcribe audio and return transcript + timings."""

    audio_format = AUDIO_FORMATS.get(mime_type)
    if not audio_format:
        raise HTTPException(400, "Unsupported audio format.")
    if not audio_bytes:
        raise HTTPException(400, "Audio recording is empty.")
    if len(audio_bytes) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "Audio exceeds 6 MB.")
    if not config.GROQ_API_KEY:
        raise HTTPException(503, "Groq API key is missing.")

    filename, upload_mime = audio_format

    options: dict = {
        "model": config.WHISPER_MODEL,
        "file": (filename, audio_bytes, upload_mime),
        "response_format": "verbose_json",
        "temperature": 0.0,
    }

    prompt = get_prompt()
    if prompt:
        options["prompt"] = prompt
    if config.WHISPER_LANGUAGE:
        options["language"] = config.WHISPER_LANGUAGE

    started = time.perf_counter()
    hedge_info: dict = {}

    def note_hedge() -> None:
        hedge_info["hedged_at"] = config.ASR_HEDGE_AFTER_SECONDS
        print(
            f"[WHISPER] No result after {config.ASR_HEDGE_AFTER_SECONDS:.1f} s; "
            "sending backup request",
            flush=True,
        )

    # Language check request, started IN PARALLEL (no added
    # latency). See LANGUAGE CHECK below.
    check_task: asyncio.Future | None = None
    check_language = config.ASR_LANGUAGE_CHECK
    if check_language and not config.WHISPER_LANGUAGE:
        check_options = {
            "model": config.WHISPER_MODEL,
            "file": (filename, audio_bytes, upload_mime),
            "response_format": "verbose_json",
            "temperature": 0.0,
            "language": check_language,
        }
        check_task = asyncio.ensure_future(
            _client().audio.transcriptions.create(**check_options)
        )

    try:
        result, winner = await hedged(
            lambda: _client().audio.transcriptions.create(**options),
            config.ASR_HEDGE_AFTER_SECONDS,
            note_hedge,
        )
        if hedge_info:
            hedge_info["winner"] = winner

    except APIStatusError as error:
        _cancel(check_task)
        print(f"[WHISPER ERROR] HTTP {error.status_code}", flush=True)
        if error.status_code == 429:
            raise HTTPException(429, "Groq rate limit reached.") from error
        raise HTTPException(502, f"Groq API error {error.status_code}.") from error

    except APIConnectionError as error:
        _cancel(check_task)
        print("[WHISPER ERROR] Connection failed", flush=True)
        raise HTTPException(502, "Could not connect to Groq.") from error

    api_seconds = time.perf_counter() - started

    transcript = (getattr(result, "text", "") or "").strip()
    language = getattr(result, "language", None)
    duration = getattr(result, "duration", None)

    decision = await _verify_language(result, check_task, check_language)
    if decision["use_check"]:
        transcript = decision["transcript"]
        language = decision["language"]

    total_seconds = time.perf_counter() - started

    print(
        f"[WHISPER] {config.WHISPER_MODEL} | prompt={config.WHISPER_PROMPT_MODE} | "
        f"{len(audio_bytes)} bytes | detected={getattr(result, 'language', None)} | "
        f"final={language} ({decision['reason']}) | audio={duration}s | "
        f"api={api_seconds:.2f}s total={total_seconds:.2f}s",
        flush=True,
    )

    if not transcript:
        raise HTTPException(422, "No speech could be recognized.")

    return {
        "transcript": transcript,
        "detected_language": language,
        "source": "groq",
        "model": config.WHISPER_MODEL,
        "prompt_mode": config.WHISPER_PROMPT_MODE,
        "language_check": {
            k: decision[k] for k in ("reason", "primary_logprob", "check_logprob")
        },
        "timings": {
            "asr_api": round(api_seconds, 3),
            "asr_with_check": round(total_seconds, 3),
            **hedge_info,
        },
    }


# ========================================
# LANGUAGE CHECK
# ========================================
#
# Real-device bug: an English question came back as an
# ARABIC TRANSCRIPT, and Gemini answered in Lebanese.
# Measured cause: when Whisper's automatic detection
# decides "Arabic" for English speech, it does not
# transcribe the English, it TRANSLATES it into formal
# Arabic ("Describe what is in front of me" ->
# "تصفح ما في أمامي"). Accented English is a known weak
# spot of Whisper language detection.
#
# Fix: the same audio is ALSO decoded with language=en,
# in parallel. Whisper's average log-probability (its
# confidence) picked the spoken language in 9/9 test
# recordings (BENCHMARK_REPORT.md). The English decode is
# used only when the main result says "Arabic" AND the
# English decode is clearly more confident AND it really
# contains Latin words. Lebanese questions keep the main
# result with the natural Lebanese prompt. The English
# decode also keeps BOTH halves of mixed Lebanese/English
# sentences, which the automatic decode dropped.

CHECK_MARGIN = 0.02        # logprob advantage required
CHECK_MAX_WAIT = 2.5       # seconds to wait for the check after the main result

_LATIN_WORD = re.compile(r"[A-Za-zÀ-ÿ]{2,}")
_ARABIC_LETTER = re.compile(r"[؀-ۿ]")


def _cancel(task: "asyncio.Future | None") -> None:
    if task is None:
        return
    if not task.done():
        task.cancel()
    elif not task.cancelled():
        task.exception()      # mark a failed check as handled (no warning)


def _mean_logprob(result) -> float | None:
    segments = getattr(result, "segments", None) or []
    values = []
    for segment in segments:
        value = segment.get("avg_logprob") if isinstance(segment, dict) else getattr(segment, "avg_logprob", None)
        if value is not None:
            values.append(float(value))
    return sum(values) / len(values) if values else None


async def _verify_language(primary, check_task, check_language: str) -> dict:
    decision = {
        "use_check": False,
        "reason": "auto",
        "primary_logprob": None,
        "check_logprob": None,
        "transcript": None,
        "language": None,
    }
    if check_task is None:
        return decision

    detected = (getattr(primary, "language", "") or "").lower()
    if detected not in {"arabic", "ar"}:
        # Whisper already chose a non-Arabic language: trust it
        # and do not wait for the check.
        _cancel(check_task)
        decision["reason"] = f"auto ({detected or 'unknown'})"
        return decision

    try:
        check = await asyncio.wait_for(check_task, CHECK_MAX_WAIT)
    except asyncio.TimeoutError:
        decision["reason"] = "check too slow; kept automatic result"
        return decision
    except Exception as error:  # noqa: BLE001 - the check is optional
        decision["reason"] = f"check failed ({type(error).__name__}); kept automatic result"
        return decision

    primary_lp = _mean_logprob(primary)
    check_lp = _mean_logprob(check)
    decision["primary_logprob"] = None if primary_lp is None else round(primary_lp, 3)
    decision["check_logprob"] = None if check_lp is None else round(check_lp, 3)

    check_text = (getattr(check, "text", "") or "").strip()
    latin_words = len(_LATIN_WORD.findall(check_text))

    if (
        primary_lp is not None
        and check_lp is not None
        and check_lp > primary_lp + CHECK_MARGIN
        and latin_words >= 2
    ):
        decision["use_check"] = True
        decision["transcript"] = check_text
        mixed = bool(_ARABIC_LETTER.search(check_text))
        decision["language"] = "mixed" if mixed else "english"
        decision["reason"] = (
            f"{'mixed Arabic/English' if mixed else 'English'} speech: "
            f"{check_language} decode more confident"
        )
    else:
        decision["reason"] = "Arabic confirmed"

    return decision


# ========================================
# ROUTER (mounted by main.py)
# ========================================

router = APIRouter(tags=["Speech Recognition"])


@router.post("/transcribe")
async def transcribe(audio: UploadFile = File(...)):
    started = time.perf_counter()

    mime_type = (audio.content_type or "").split(";")[0].lower().strip()
    try:
        audio_bytes = await audio.read(MAX_AUDIO_BYTES + 1)
    finally:
        await audio.close()

    result = await transcribe_bytes(audio_bytes, mime_type)
    result["timings"]["asr_total"] = round(time.perf_counter() - started, 3)
    return result


# ========================================
# STANDALONE APP (backward compatible, port 8002)
# ========================================

app = FastAPI(title="World Voice AI - Whisper Service", version="2.0.0")
app.include_router(router)


@app.get("/health")
def health():
    return {"status": "ok"}
