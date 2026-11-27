"""
Central configuration for the World Voice AI backend.

Every service reads its settings from here, so the API key,
model names and limits are defined in exactly one place.

Values come from backend/.env (never committed) and can be
overridden with environment variables for benchmarking.
"""

import os
import re
from pathlib import Path

from dotenv import load_dotenv


BACKEND_DIR = Path(__file__).resolve().parent

# override=False: an explicitly exported variable wins,
# which lets benchmark runs switch models without
# editing the .env file.
load_dotenv(BACKEND_DIR / ".env", override=False)


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


# ========================================
# API KEYS
# ========================================
#
# The project key lives in backend/.env as GEMINI_API_KEY.
#
# A GOOGLE_API_KEY variable also exists in the Windows user
# environment. The google-genai SDK prefers GOOGLE_API_KEY
# when no key is passed explicitly, which is why the
# "Both GOOGLE_API_KEY and GEMINI_API_KEY are set" warning
# appeared. We always pass GEMINI_API_KEY explicitly so the
# backend never silently uses a different project's key.

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or ""

if GEMINI_API_KEY:
    # Remove the competing variable from THIS process only
    # (the Windows user setting is not touched), so no SDK
    # can pick the wrong key.
    os.environ.pop("GOOGLE_API_KEY", None)

GROQ_API_KEY = os.getenv("GROQ_API_KEY") or ""


# ========================================
# OPTIONAL LAN ACCESS TOKEN
# ========================================
#
# When WV_ACCESS_TOKEN is set, every endpoint except /health requires
# the header X-WV-Token (requests from the laptop itself are allowed). This stops
# other devices on the same Wi-Fi from using our quota.
# Empty = disabled (development default).

ACCESS_TOKEN = os.getenv("WV_ACCESS_TOKEN", "").strip()


# ========================================
# VISION MODEL
# ========================================

VISION_MODEL = os.getenv(
    "WV_VISION_MODEL",
    "gemini-3.5-flash-lite",
)

# Gemini "thinking" improves hard reasoning but adds
# latency. Valid values: minimal, low, medium, high,
# or empty to use the model's default.
VISION_THINKING_LEVEL = os.getenv(
    "WV_VISION_THINKING",
    "low",
).strip().lower()

# Upper safety bound only. Answer length is controlled
# by the system prompt, which adapts to the question.
# The previous 512-token cap could cut long Arabic
# answers in half and break the JSON response.
VISION_MAX_OUTPUT_TOKENS = _int(
    "WV_VISION_MAX_TOKENS",
    2048,
)

VISION_TIMEOUT_SECONDS = _float(
    "WV_VISION_TIMEOUT",
    45.0,
)

# Model chain (vision.py): each Gemini model has its OWN free daily quota
# (measured: 500 requests/day for gemini-3.5-flash-lite). When the first is
# exhausted, overloaded (503) or too slow, the next one answers.
VISION_MODELS = list(dict.fromkeys(
    m.strip() for m in os.getenv(
        "WV_VISION_MODELS",
        f"{VISION_MODEL},gemini-3.1-flash-lite,gemini-3.5-flash",
    ).split(",") if m.strip()
))

# A model that has not answered a waiting user within this many seconds is
# abandoned for the next one (measured: gemini-3.1-flash-lite once took 167 s).
VISION_ATTEMPT_TIMEOUT = _float("WV_VISION_ATTEMPT_TIMEOUT", 12.0)


# ========================================
# SPEECH RECOGNITION
# ========================================

WHISPER_MODEL = os.getenv(
    "WV_WHISPER_MODEL",
    "whisper-large-v3",
)

# natural = short natural Lebanese sentence (default)
# none    = no prompt at all
# legacy  = previous comma-separated vocabulary list
WHISPER_PROMPT_MODE = os.getenv(
    "WV_WHISPER_PROMPT",
    "natural",
).strip().lower()

# Optional ISO-639-1 hint such as "ar". Empty keeps
# Whisper's automatic language detection, which we
# need for English and French questions.
WHISPER_LANGUAGE = os.getenv(
    "WV_WHISPER_LANGUAGE",
    "",
).strip().lower()

# Parallel language check (see whisper_service.py,
# "LANGUAGE CHECK"): the audio is also decoded with this
# language and the more confident decode wins when Whisper
# detects Arabic. Fixes English questions being returned as
# an Arabic translation. Empty = off. Costs 1 extra Groq
# request per question, no extra latency in the normal case.
ASR_LANGUAGE_CHECK = os.getenv(
    "WV_ASR_LANGUAGE_CHECK",
    "en",
).strip().lower()


# ========================================
# NATURAL TEXT-TO-SPEECH
# ========================================

TTS_MODEL = os.getenv(
    "WV_TTS_MODEL",
    "gemini-3.8-flash-lite-tts",
)

TTS_VOICE = os.getenv("WV_TTS_VOICE", "Fola")

# Gemini TTS models tried in order, all with the SAME voice
# (Fola). Daily free-tier quotas are counted per model, so
# when the first model's daily quota is used up, the NEXT
# answer uses the next model: more answers per day, same
# voice identity. A switch never happens inside an answer.
#
# gemini-3.8-flash-tts was removed from the default chain on
# 2026-11-27: it generates ~0.34x real time, which made the
# phone wait 42 s for a long answer. It can still be added
# explicitly with WV_TTS_MODELS.
TTS_MODELS = [
    name.strip()
    for name in os.getenv("WV_TTS_MODELS", TTS_MODEL).split(",")
    if name.strip()
]

# "single"  = ONE Gemini request per answer, streamed and
#             cut into segments at natural pauses (silence).
#             Default, because the free tier for
#             gemini-3.8-flash-lite-tts allows only
#             10 requests per day (confirmed by the API's
#             own 429 response on 2026-11-26).
# "chunked" = split the text into sentence chunks and
#             generate several in parallel. Faster and more
#             robust, but uses one request per chunk: only
#             sensible with a paid / higher-quota tier.
TTS_MODE = os.getenv("WV_TTS_MODE", "single").strip().lower()

# Pipelined speech: split each answer (at sentence
# boundaries) into up to this many parts that Gemini
# generates IN PARALLEL; the server merges them, in order,
# into the same single audio stream (one player on the
# phone, no audible joins). 1 = off (default).
#
# Costs one TTS request per part. Answers under 120
# characters are never split. Only worth enabling if a
# live test (bench_tts_live.py) shows the free tier does
# not slow down parallel requests.
TTS_SPLIT = min(3, max(1, _int("WV_TTS_SPLIT", 1)))

# How many Gemini TTS requests may run at the same time.
TTS_PARALLEL = max(TTS_SPLIT, _int("WV_TTS_PARALLEL", 2))

# "single" mode segmentation: a segment is closed at the
# quietest point after MIN seconds, and at the latest at
# MAX seconds. The first segment is short so playback
# starts quickly.
TTS_FIRST_SEGMENT_MIN = _float("WV_TTS_FIRST_MIN", 1.2)
TTS_FIRST_SEGMENT_MAX = _float("WV_TTS_FIRST_MAX", 3.0)
TTS_SEGMENT_MIN = _float("WV_TTS_SEGMENT_MIN", 2.5)
TTS_SEGMENT_MAX = _float("WV_TTS_SEGMENT_MAX", 6.0)

# Target size of each speech chunk. Chunks always end at
# sentence boundaries, so any wait between chunks sounds
# like a natural pause instead of a cut inside a word.
TTS_FIRST_CHUNK_CHARS = _int("WV_TTS_FIRST_CHUNK", 90)

TTS_CHUNK_CHARS = _int("WV_TTS_CHUNK", 220)

TTS_RETRIES = max(0, _int("WV_TTS_RETRIES", 2))

# APPROVED natural-voice providers, in order of preference.
# Each answer uses the first one that is configured and not
# quota-blocked (one provider per answer, never mixed).
# Names: gemini (Fola), elevenlabs, azure, file (test only).
# WV_TTS_PROVIDER (single name, older setting) still works.
TTS_PROVIDER = os.getenv("WV_TTS_PROVIDER", "").strip().lower()
TTS_PROVIDERS = [
    name.strip().lower()
    for name in (
        os.getenv("WV_TTS_PROVIDERS")
        or TTS_PROVIDER
        or "gemini"
    ).split(",")
    if name.strip()
]

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "").strip()
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "").strip()
# eleven_multilingual_v2 = most natural; eleven_flash_v2_5 = lowest latency.
ELEVENLABS_MODEL = os.getenv("ELEVENLABS_MODEL", "eleven_multilingual_v2").strip()

# "smooth" = start playback at the earliest moment that
#            guarantees no interruption, computed from the
#            measured generation rate (default).
# "fast"   = start after 1.5 s of audio; may interrupt if
#            the provider is slower than real time.
TTS_START_POLICY = os.getenv("WV_TTS_START", "smooth").strip().lower()

# Test-only provider "file": streams this WAV instead of
# calling an API (zero quota). See tts_providers.py.
TTS_TEST_WAV = os.getenv("WV_TTS_TEST_WAV", "").strip()
TTS_TEST_RATE = max(0.05, _float("WV_TTS_TEST_RATE", 0.63))
TTS_TEST_FIRST_AUDIO = _float("WV_TTS_TEST_FIRST_AUDIO", 2.5)

# Consistent latency: if the preferred provider (e.g. Fola) is
# not ready to START playback within this many seconds after
# the answer exists, and another approved provider is
# configured, the answer moves to that provider BEFORE anything
# is played (no mixed voices). 0 = never switch for speed.
TTS_SWITCH_AFTER = _float("WV_TTS_SWITCH_AFTER", 4.0)
# Live Assistant answers (3-5 s target): shorter wait for the preferred
# voice. Fola is normally ready in 1.5-2.2 s, so it still wins; a slow
# Fola now costs 2.5 s instead of 4 s before Rami takes the answer.
LIVE_TTS_SWITCH_AFTER = _float("WV_LIVE_TTS_SWITCH_AFTER", 2.5)

AZURE_SPEECH_KEY = os.getenv("AZURE_SPEECH_KEY", "").strip()

# Connection ping to Azure every WV_KEEPALIVE seconds (no speech).
# Disabled by default because "no charge / no quota use" could not
# be verified. 1 = enable (faster first Rami answer after idle).
AZURE_KEEPALIVE = os.getenv("WV_AZURE_KEEPALIVE", "0").strip() in {"1", "true", "yes"}
AZURE_SPEECH_REGION = os.getenv("AZURE_SPEECH_REGION", "").strip()

# Rami (Arabic Azure voices) speaking rate. Measured: Rami speaks about
# 23 % slower than Fola on the same text (BENCHMARK_REPORT.md section 13);
# +15 % brings the backup voice close to Fola's tempo while keeping every
# syllable clear. Empty or "0%" = Azure's default tempo.
AZURE_ARABIC_RATE = os.getenv("WV_AZURE_AR_RATE", "+15%").strip()
if AZURE_ARABIC_RATE and not re.fullmatch(r"[+-]?\d{1,2}%", AZURE_ARABIC_RATE):
    raise ValueError("WV_AZURE_AR_RATE must look like +15% (or be empty)")

# Optional per-language voice override, e.g.
# WV_AZURE_VOICE_AR_LB=ar-LB-LaylaNeural
AZURE_VOICE_OVERRIDES = {
    language: os.environ[f"WV_AZURE_VOICE_{language.upper().replace('-', '_')}"]
    for language in ("ar-LB", "ar", "en", "fr", "und")
    if os.getenv(f"WV_AZURE_VOICE_{language.upper().replace('-', '_')}")
}

# Vision tail-latency hedging: if Gemini has not answered
# after this many seconds, send one identical backup
# request and use whichever answers first (same model,
# same quality). 0 disables. Costs an extra request only
# for slow answers.
# Presentation mode: conserve cloud quota during a demo without removing
# any feature (background analysis every 15 s at most, a larger daily
# reserve for questions, no backup requests).
PRESENTATION_MODE = os.getenv("WV_PRESENTATION_MODE", "0").strip() in {"1", "true", "yes"}

VISION_HEDGE_AFTER_SECONDS = 0.0 if PRESENTATION_MODE else _float("WV_VISION_HEDGE_AFTER", 3.5)

# Same idea for Groq Whisper (free tier: 2,000 requests/day):
# measured 0.78-6.0 s for similar recordings on the phone.
ASR_HEDGE_AFTER_SECONDS = _float("WV_ASR_HEDGE_AFTER", 2.5)

# Keep HTTPS connections to Google and Groq warm with free
# metadata calls (no generation quota), so the first real
# request after idle does not pay DNS + TLS setup.
KEEPALIVE_SECONDS = _float("WV_KEEPALIVE", 45.0)

TTS_TIMEOUT_SECONDS = _float("WV_TTS_TIMEOUT", 40.0)
