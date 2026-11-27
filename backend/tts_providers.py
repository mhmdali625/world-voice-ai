"""
Neural TTS providers. Each yields raw PCM: 24 kHz, 16-bit, mono.

  gemini     : Gemini TTS, voice Fola (approved quality benchmark).
               Free tier: 10 requests/day per model. Measured
               0.63x-1.1x real time (flash-lite); the flash model
               measured ~0.34x and caused a 42 s wait on the phone,
               so it is no longer a default fallback.
  elevenlabs : ElevenLabs streaming TTS (free plan: ~10,000
               characters/month, non-commercial). Needs
               ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID.
  azure      : Azure AI Speech, Lebanese neural voice
               ar-LB-RamiNeural (free F0: 0.5 M characters/month).
               Needs AZURE_SPEECH_KEY and AZURE_SPEECH_REGION.
  file       : development-only test audio (no API).

WV_TTS_PROVIDERS is an ordered list, e.g. "gemini,azure".
Each ANSWER uses the first provider that is configured and
not quota-blocked, from start to finish: voices are never
mixed inside an answer. All providers use plain HTTPS
(no extra packages).
"""

import json
import re
import time
from typing import AsyncIterator
from xml.sax.saxutils import escape

import httpx

import config
import tts_service
from tts_service import TTSError


# ========================================
# PROVIDER SELECTION
# ========================================

_provider_blocked_until: dict[str, float] = {}


def _configured(name: str) -> bool:
    if name == "gemini":
        return bool(config.GEMINI_API_KEY)
    if name == "azure":
        return bool(config.AZURE_SPEECH_KEY and config.AZURE_SPEECH_REGION)
    if name == "elevenlabs":
        return bool(config.ELEVENLABS_API_KEY and config.ELEVENLABS_VOICE_ID)
    if name == "file":
        return bool(config.TTS_TEST_WAV)
    if name == "piper":                       # local offline voice (local_sidecar.py)
        import local_ai
        return bool(local_ai.SIDECAR_URL)
    return False


def provider_blocked_for(name: str) -> float:
    if name == "gemini":
        waits = [tts_service.blocked_for(m) for m in config.TTS_MODELS]
        return min(waits) if waits else 0.0
    return max(0.0, _provider_blocked_until.get(name, 0.0) - time.time())


def block_provider(name: str, seconds: float, reason: str) -> None:
    _provider_blocked_until[name] = time.time() + seconds
    print(f"[TTS QUOTA] provider {name} blocked for {seconds / 3600:.1f} h ({reason})", flush=True)


def choose_provider() -> str | None:
    """First configured, non-blocked provider for a new answer."""
    for name in config.TTS_PROVIDERS:
        if _configured(name) and not provider_blocked_for(name):
            return name
    return None


def provider_name() -> str:
    """Provider a new answer would use now (or the first listed)."""
    return choose_provider() or (config.TTS_PROVIDERS[0] if config.TTS_PROVIDERS else "gemini")


def providers_state() -> dict:
    return {
        name: {
            "configured": _configured(name),
            "available": _configured(name) and not provider_blocked_for(name),
            "resets_in_s": round(provider_blocked_for(name)),
        }
        for name in config.TTS_PROVIDERS
    }


def choose_model(provider: str | None = None) -> str | None:
    """First Gemini model (same Fola voice) that is not quota-blocked."""
    if (provider or provider_name()) != "gemini":
        return None
    for model in config.TTS_MODELS:
        if not tts_service.blocked_for(model):
            return model
    return None


def all_blocked_error() -> TTSError:
    waits = [provider_blocked_for(n) for n in config.TTS_PROVIDERS if _configured(n)]
    wait = min(waits) if waits else 0
    error = TTSError(
        429,
        "Natural voice quota reached. Available again in about "
        f"{max(1, round(wait / 3600))} h.",
        False,
    )
    error.retry_after = wait  # type: ignore[attr-defined]
    return error


async def stream_pcm(
    text: str,
    language: str,
    on_model: "callable | None" = None,
    models: list[str] | None = None,
    part: tuple[int, int] | None = None,
    provider: str | None = None,
) -> AsyncIterator[bytes]:
    provider = provider or choose_provider()

    if provider is None:
        raise all_blocked_error()

    if provider == "azure":
        async for piece in _azure_stream(text, language):
            yield piece
        return

    if provider == "elevenlabs":
        async for piece in _elevenlabs_stream(text, language):
            yield piece
        return

    if provider == "file":
        async for piece in _file_stream(part):
            yield piece
        return

    if provider == "piper":
        async for piece in _piper_stream(text, language):
            yield piece
        return

    # Gemini (Fola). Only models in WV_TTS_MODELS are used.
    last_error: TTSError | None = None
    chain = list(models or []) + [m for m in config.TTS_MODELS if m not in (models or [])]

    for model in chain:
        if tts_service.blocked_for(model):
            last_error = tts_service.quota_error(model)
            continue

        if on_model:
            on_model(model)

        produced = False
        try:
            async with tts_service._limiter():  # noqa: SLF001
                async for piece in tts_service._stream_pcm(  # noqa: SLF001
                    text, language, False, model
                ):
                    produced = True
                    yield piece
            return
        except TTSError as error:
            if produced or error.status != 429:
                raise
            last_error = error
            print(f"[TTS] {model} quota exhausted; trying next model", flush=True)

    blocked = [m for m in config.TTS_MODELS if tts_service.blocked_for(m)]
    if blocked and len(blocked) == len(config.TTS_MODELS):
        raise tts_service.quota_error(min(blocked, key=tts_service.blocked_for))

    raise last_error or TTSError(429, "Natural TTS quota reached.", False)


def expected_duration(
    text: str,
    part: tuple[int, int] | None = None,
    provider: str | None = None,
) -> float | None:
    """Known audio length, when the provider can tell in advance."""
    if (provider or provider_name()) == "file":
        try:
            return len(_test_slice(part)) / tts_service.BYTES_PER_SECOND
        except TTSError:
            return None
    return None


def quota_blocked_seconds() -> float:
    """0 if at least one provider can be used now."""
    return 0.0 if choose_provider() else min(
        (provider_blocked_for(n) for n in config.TTS_PROVIDERS), default=0.0
    )


# ========================================
# TEST PROVIDER (no API, no quota)
# ========================================
#
# Streams a previously generated Fola WAV at a chosen
# rate, e.g. 0.63x (measured on the phone). Lets the whole
# phone playback path be tested without using the
# 10-requests-per-day TTS quota. Never use for the demo.

_test_pcm_cache: dict[str, bytes] = {}


def _load_test_pcm() -> bytes:
    import wave

    path = config.TTS_TEST_WAV
    if path not in _test_pcm_cache:
        try:
            with wave.open(path, "rb") as file:
                if (
                    file.getframerate() != 24000
                    or file.getsampwidth() != 2
                    or file.getnchannels() != 1
                ):
                    raise TTSError(500, "Test WAV must be 24 kHz 16-bit mono.", False)
                _test_pcm_cache[path] = file.readframes(file.getnframes())
        except (OSError, EOFError) as error:
            raise TTSError(500, f"Cannot read test WAV: {path}", False) from error
    return _test_pcm_cache[path]


def _test_slice(part: tuple[int, int] | None) -> bytes:
    """
    For split-mode tests: part i of n of the saved recording,
    cut at natural pauses (like a sentence-aligned split).
    """
    pcm = _load_test_pcm()
    if not part or part[1] <= 1:
        return pcm

    index, count = part
    bounds = [0]
    for k in range(1, count):
        target = len(pcm) * k // count
        lo = max(bounds[-1], target - tts_service.BYTES_PER_SECOND)
        cut = tts_service.find_cut_point(
            pcm[lo:target + tts_service.BYTES_PER_SECOND],
            0.0, 2.0, force=True,
        )
        bounds.append(lo + (cut or (target - lo)))
    bounds.append(len(pcm))
    start, end = bounds[index], bounds[index + 1]
    return pcm[start - start % 2:end - end % 2]


async def _file_stream(part: tuple[int, int] | None = None) -> AsyncIterator[bytes]:
    import asyncio

    pcm = _test_slice(part)

    # Read the settings at the start of each answer, so
    # /dev/tts-test can switch normal/slow between answers.
    rate = config.TTS_TEST_RATE
    piece = 4800  # 0.1 s of audio

    print(
        f"[TTS TEST] streaming saved Fola audio at x{rate} real time "
        f"(first audio after {config.TTS_TEST_FIRST_AUDIO} s, no API call)",
        flush=True,
    )

    await asyncio.sleep(config.TTS_TEST_FIRST_AUDIO)
    for start in range(0, len(pcm), piece):
        await asyncio.sleep(0.1 / rate)
        yield pcm[start:start + piece]


# ========================================
# AZURE AI SPEECH (REST, streamed)
# ========================================

AZURE_VOICES = {
    "ar-LB": ("ar-LB", "ar-LB-RamiNeural"),
    "ar": ("ar-LB", "ar-LB-RamiNeural"),
    "und": ("ar-LB", "ar-LB-RamiNeural"),
    "en": ("en-US", "en-US-AndrewNeural"),
    "fr": ("fr-FR", "fr-FR-HenriNeural"),
}


# ----------------------------------------
# Lebanese pronunciation of English / French loanwords
# ----------------------------------------
#
# ar-LB-RamiNeural is a single-language voice: Latin-script
# words inside an Arabic answer were mispronounced (Whisper
# heard "كلاب توض" for laptop, "ثون" for phone). SSML <lang>
# is ignored by this voice (measured). Standard SSML
# <sub alias> gives each word its everyday Lebanese
# pronunciation in Arabic script, so the SAME voice says
# it naturally. Measured similarity of the mixed test
# sentence: 0.87 -> 0.94 (BENCHMARK_REPORT.md section 13).
# Unlisted Latin words are left for the voice to read.

LEBANESE_LOANWORDS = {
    "laptop": "لابْتوب", "computer": "كومْبيوتَر", "screen": "سْكْرين",
    "keyboard": "كيبورْد", "mouse": "ماوْس", "phone": "تِلِفون",
    "mobile": "موبايْل", "iphone": "آيْفون", "charger": "تْشارْجَر",
    "cable": "كابْل", "battery": "باتْري", "wifi": "وايْفاي",
    "internet": "إنْتَرْنِت", "email": "إيمَيْل", "message": "مِسِّج",
    "document": "دوكيومِنْت", "file": "فايْل", "folder": "فولْدَر",
    "printer": "بْرينْتَر", "camera": "كاميرا", "video": "فيديو",
    "photo": "فوتو", "app": "أبْ", "application": "أبْليكايْشِن",
    "password": "باسْوورْد", "website": "وِبْسايْت", "link": "لينْك",
    "online": "أونْلايْن", "table": "تَيْبِل", "menu": "مِنْيو",
    "ok": "أوكَيْ", "okay": "أوكَيْ", "sorry": "سوري", "taxi": "تاكْسي",
    "bus": "باص", "parking": "باركينْغ", "ticket": "تيكِت", "cash": "كاش",
    "card": "كارْت", "label": "لَيْبِل", "text": "تِكْسْت",
    "merci": "مِرْسي", "bonjour": "بونْجور", "français": "فْرانْسي",
    "en": "أَنْ",
}

# Optional Arabic prefix glued to the next Latin word
# ("عالـ screen", "بالـ laptop", "الـ charger").
# Only real article forms count as a prefix (not any word that
# happens to end in "ال", such as "مثال").
_PREFIX_LATIN = re.compile(
    r"(?<![؀-ۿ])(?P<prefix>[وف]?(?:[بعكل]|عَ)?ال|[وف]?لل)ـ?\s*(?P<word>[A-Za-zÀ-ÿ]+)"
    r"|(?P<solo>[A-Za-zÀ-ÿ]+)"
)


def build_azure_ssml(text: str, language: str, locale: str, voice: str) -> str:
    """SSML for Azure; Arabic voices get Lebanese loanword pronunciations."""

    body = escape(text)

    if locale.startswith("ar"):
        def replace(match: re.Match) -> str:
            word = match.group("word") or match.group("solo")
            alias = LEBANESE_LOANWORDS.get(word.lower())
            if alias is None:
                return match.group(0)
            prefix = match.group("prefix")
            if prefix:
                # Measured: glued "عالسْكْرين" was misheard ("عسك");
                # the expanded "على السْكْرين" was clear (0.84 -> 0.95).
                if prefix.endswith("عال"):
                    prefix = prefix[:-3] + "على ال"
                return f"<sub alias='{prefix}{alias}'>{match.group(0)}</sub>"
            return f"<sub alias='{alias}'>{word}</sub>"

        body = _PREFIX_LATIN.sub(replace, body)
        if config.AZURE_ARABIC_RATE.strip("+-0%"):
            body = f"<prosody rate='{config.AZURE_ARABIC_RATE}'>{body}</prosody>"

    return (
        f"<speak version='1.0' xml:lang='{locale}'>"
        f"<voice name='{voice}'>{body}</voice></speak>"
    )


async def _azure_stream(text: str, language: str) -> AsyncIterator[bytes]:
    locale, voice = AZURE_VOICES.get(language, AZURE_VOICES["und"])
    voice = config.AZURE_VOICE_OVERRIDES.get(language, voice)

    ssml = build_azure_ssml(text, language, locale, voice)
    url = (
        f"https://{config.AZURE_SPEECH_REGION}.tts.speech.microsoft.com"
        "/cognitiveservices/v1"
    )
    headers = {
        "Ocp-Apim-Subscription-Key": config.AZURE_SPEECH_KEY,
        "Content-Type": "application/ssml+xml",
        "X-Microsoft-OutputFormat": "raw-24khz-16bit-mono-pcm",
        "User-Agent": "WorldVoiceAI",
    }

    received = 0
    try:
        async with tts_service._client().stream(  # noqa: SLF001
            "POST", url, headers=headers, content=ssml.encode("utf-8")
        ) as response:
            if response.status_code != 200:
                await response.aread()
                raise tts_service._http_error(response.status_code)  # noqa: SLF001
            async for piece in response.aiter_bytes():
                if piece:
                    received += len(piece)
                    yield piece
    except httpx.TimeoutException as error:
        raise TTSError(504, "Azure speech timed out.", True) from error
    except httpx.TransportError as error:
        raise TTSError(503, "Could not connect to Azure speech.", True) from error

    if not received:
        raise TTSError(502, "Azure returned no audio.", True)


# ========================================
# ELEVENLABS (REST, streamed raw PCM)
# ========================================

async def _piper_stream(text: str, language: str) -> AsyncIterator[bytes]:
    """
    Piper (local, offline) through local_sidecar.py. Piper speaks 22.05 kHz;
    the player's stream is 24 kHz 16-bit mono, so the audio is resampled.
    Only used when listed in WV_TTS_PROVIDERS (offline last resort).
    """
    import numpy as np

    import local_ai
    try:
        pcm, rate = await local_ai.speak_pcm(text, language if language in ("en", "fr", "ar", "ar-LB") else "ar-LB")
    except Exception as error:  # noqa: BLE001
        raise TTSError(503, f"Local voice unavailable ({type(error).__name__}).", True) from error
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    if rate != 24000 and samples.size:
        target = np.linspace(0, samples.size - 1, int(samples.size * 24000 / rate))
        samples = np.interp(target, np.arange(samples.size), samples)
    out = np.clip(samples, -32768, 32767).astype(np.int16).tobytes()
    for i in range(0, len(out), 9600):                      # 0.2 s pieces
        yield out[i:i + 9600]


async def _elevenlabs_stream(text: str, language: str) -> AsyncIterator[bytes]:
    url = (
        "https://api.elevenlabs.io/v1/text-to-speech/"
        f"{config.ELEVENLABS_VOICE_ID}/stream"
    )
    params = {"output_format": "pcm_24000"}
    body = {"text": text, "model_id": config.ELEVENLABS_MODEL}
    headers = {
        "xi-api-key": config.ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
    }

    received = 0
    try:
        async with tts_service._client().stream(  # noqa: SLF001
            "POST", url, params=params, headers=headers,
            content=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        ) as response:
            if response.status_code != 200:
                detail = (await response.aread()).decode("utf-8", "replace")
                if "quota_exceeded" in detail or response.status_code == 402:
                    block_provider("elevenlabs", 24 * 3600, "monthly character quota")
                    error = TTSError(429, "ElevenLabs character quota reached.", False)
                    error.retry_after = 24 * 3600  # type: ignore[attr-defined]
                    raise error
                print(f"[TTS ELEVENLABS] HTTP {response.status_code}: {detail[:200]}", flush=True)
                raise tts_service._http_error(response.status_code)  # noqa: SLF001
            async for piece in response.aiter_bytes():
                if piece:
                    received += len(piece)
                    yield piece
    except httpx.TimeoutException as error:
        raise TTSError(504, "ElevenLabs timed out.", True) from error
    except httpx.TransportError as error:
        raise TTSError(503, "Could not connect to ElevenLabs.", True) from error

    if not received:
        raise TTSError(502, "ElevenLabs returned no audio.", True)
