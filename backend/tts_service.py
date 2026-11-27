"""
Natural text-to-speech using Gemini TTS (voice: Fola).

WHY THIS DESIGN
---------------
Measurements showed Gemini TTS generates audio slower
than real time (about 14 s of speech took 31 s). With one
request per answer, playback inevitably catches up with
generation and stalls in the middle of a word.

This service therefore:

1. Splits the answer into sentence-aligned chunks. The
   first chunk is short so speech can start quickly.
2. Generates several chunks at the same time (bounded by
   WV_TTS_PARALLEL), so total throughput can exceed real
   time while the user listens to the first sentence.
3. Sends chunks to the phone strictly in order. If the
   phone ever has to wait, the wait happens between
   sentences, where a pause sounds natural.
4. Retries a failed chunk on its own, in the same voice,
   instead of abandoning the whole answer.
5. Uses asyncio + httpx. When the phone cancels, the
   request is cancelled immediately, so no lock is left
   held (the old non-blocking lock caused "busy" errors
   and a switch to the robotic Android voice).

The NDJSON protocol is unchanged:
  {"type":"start", ...}
  {"type":"audio","index":n,"duration":s,"data":<base64 WAV>,"text":...}
  {"type":"complete","segments":n,...}
  {"type":"error","status":code,"message":...}
"""

import asyncio
import base64
import io
import json
import re
import time
import wave

from typing import AsyncIterator, Literal

import httpx

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

import config


# ========================================
# CONSTANTS
# ========================================

API_URL = (
    "https://generativelanguage.googleapis.com"
    "/v1beta/interactions"
)

MAX_TEXT_LENGTH = 4000

SAMPLE_RATE = 24000
CHANNELS = 1
SAMPLE_WIDTH = 2
BYTES_PER_SECOND = SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH


router = APIRouter(tags=["Natural Text-to-Speech"])


AnswerLanguage = Literal["en", "ar", "ar-LB", "fr", "und"]


# ========================================
# SPEAKING STYLES (approved Fola style kept verbatim)
# ========================================

LEBANESE_STYLE = """
Speak like a native Lebanese man in his early thirties.
Use a medium-low pitched voice, relaxed confidence,
everyday Lebanese pronunciation and natural
conversational rhythm.

Speak at a normal pace with subtle emotion and
a slightly more straightforward tone.

Speak in authentic everyday Lebanese dialect,
exactly as Lebanese friends speak to each other
in casual conversation.

Use natural Lebanese pronunciation, connected
words and spontaneous rhythm.

Do not pronounce words like a formal Arabic
news presenter.

Avoid exaggerated friendliness, theatrical
delivery, robotic intonation and formal Arabic.
""".strip()

ENGLISH_STYLE = """
Speak like a natural adult man in everyday
English conversation.

Use a medium-low pitched voice, relaxed
confidence and natural conversational rhythm.

Speak clearly at a normal pace with subtle
emotion and realistic pauses.

Avoid exaggerated friendliness, theatrical
delivery and robotic intonation.
""".strip()

FRENCH_STYLE = """
Speak like a native French-speaking adult man.

Use natural French pronunciation, a relaxed
conversational tone and clear articulation.

Speak at a normal pace with realistic pauses
and subtle emotion.

Avoid exaggerated friendliness, theatrical
delivery and robotic intonation.
""".strip()

ARABIC_STYLE = """
Speak naturally in clear Arabic using the
dialect and expressions present in the text.

Use a confident, relaxed adult male voice
with natural conversational rhythm.

Avoid robotic intonation, exaggerated
friendliness and theatrical delivery.
""".strip()

DEFAULT_STYLE = """
Read the supplied text naturally in its
original language.

Use a relaxed, confident adult male voice
with natural pronunciation and rhythm.

Do not translate the text.

Avoid theatrical or robotic delivery.
""".strip()

# Every chunk of one answer is generated as a separate
# request. This sentence keeps chunks consistent: each
# one is read as a continuation, not a new announcement.
CONTINUATION_NOTE = (
    "This text is one part of a longer spoken answer. "
    "Read it as a natural continuation with the same "
    "voice, pace and tone. Do not add an introduction."
)

STYLES: dict[str, str] = {
    "ar-LB": LEBANESE_STYLE,
    "ar": ARABIC_STYLE,
    "en": ENGLISH_STYLE,
    "fr": FRENCH_STYLE,
    "und": DEFAULT_STYLE,
}


def get_speaking_style(
    language: AnswerLanguage,
    continuation: bool = False,
) -> str:
    style = STYLES.get(language, DEFAULT_STYLE)
    if continuation:
        style = f"{style}\n\n{CONTINUATION_NOTE}"
    return style


# ========================================
# REQUEST SCHEMA
# ========================================

class TTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TEXT_LENGTH)
    language: AnswerLanguage = "und"


# ========================================
# ERRORS
# ========================================

class TTSError(Exception):

    def __init__(self, status: int, message: str, retryable: bool):
        super().__init__(message)
        self.status = status
        self.message = message
        self.retryable = retryable


def _http_error(code: int, retry_after: float | None = None) -> TTSError:
    if code == 429:
        error = TTSError(429, "Natural TTS quota reached.", False)
        error.retry_after = retry_after  # type: ignore[attr-defined]
        return error
    if code in {401, 403}:
        return TTSError(503, "Natural TTS access is unavailable.", False)
    if code >= 500:
        return TTSError(502, "Natural TTS service error.", True)
    return TTSError(502, f"Natural TTS request failed ({code}).", False)


# ========================================
# TEXT CHUNKING
# ========================================

# Sentence endings in English, French and Arabic.
_SENTENCE_END = re.compile(r"(?<=[.!?؟…;؛])\s+|\n+")

# Secondary split points for very long sentences.
_CLAUSE_END = re.compile(r"(?<=[,،:])\s+")


def _split_long(sentence: str, limit: int) -> list[str]:
    if len(sentence) <= limit:
        return [sentence]

    parts: list[str] = []
    current = ""
    for clause in _CLAUSE_END.split(sentence):
        candidate = f"{current} {clause}".strip()
        if current and len(candidate) > limit:
            parts.append(current)
            current = clause
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


def split_into_chunks(
    text: str,
    first_limit: int | None = None,
    limit: int | None = None,
) -> list[str]:
    """
    Split text into sentence-aligned speech chunks.

    The first chunk is kept short so the user hears the
    answer quickly. Later chunks are larger, because
    fewer requests means fewer quota hits and fewer
    joins between separately generated audio.
    """

    first_limit = first_limit or config.TTS_FIRST_CHUNK_CHARS
    limit = limit or config.TTS_CHUNK_CHARS

    sentences: list[str] = []
    for raw in _SENTENCE_END.split(text.strip()):
        raw = raw.strip()
        if raw:
            sentences.extend(_split_long(raw, limit))

    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        target = first_limit if not chunks else limit
        candidate = f"{current} {sentence}".strip()
        if current and len(candidate) > target:
            chunks.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        chunks.append(current)

    # Avoid a tiny trailing chunk (an extra request for
    # one or two words).
    if len(chunks) >= 2 and len(chunks[-1]) < 25:
        tail = chunks.pop()
        chunks[-1] = f"{chunks[-1]} {tail}"

    return chunks


# ========================================
# AUDIO HELPERS
# ========================================

def pcm_to_wav(pcm_data: bytes) -> bytes:
    if len(pcm_data) % SAMPLE_WIDTH:
        pcm_data = pcm_data[:-1]
    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(CHANNELS)
        wav_file.setsampwidth(SAMPLE_WIDTH)
        wav_file.setframerate(SAMPLE_RATE)
        wav_file.writeframes(pcm_data)
    return output.getvalue()


def stream_message(payload: dict) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


# ========================================
# SHARED HTTP CLIENT AND CONCURRENCY LIMIT
# ========================================
#
# One pooled client keeps the TLS connection to Google
# warm between requests. The semaphore caps parallel TTS
# requests for the whole server, so two overlapping
# answers cannot exceed the free-tier request rate.

_http_client: httpx.AsyncClient | None = None
_semaphore: asyncio.Semaphore | None = None


def _client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                config.TTS_TIMEOUT_SECONDS,
                connect=10.0,
            ),
            # Keep idle connections 2 min (no extra requests are sent): the
            # next answer reuses the TLS connection instead of a new handshake.
            limits=httpx.Limits(max_connections=8, keepalive_expiry=120),
        )
    return _http_client


def _limiter() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(config.TTS_PARALLEL)
    return _semaphore


async def close_http_client() -> None:
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None


# ========================================
# DAILY-QUOTA CIRCUIT BREAKER
# ========================================
#
# After a 429 the old code kept sending requests (every
# retry or tap = one more Gemini call). A daily limit does
# not recover for hours, so once Google reports it, this
# breaker blocks that model until the reset time stated in
# Google's own error message and answers instantly.

_blocked_until: dict[str, float] = {}
_blocked_reason: dict[str, str] = {}

_RETRY_IN = re.compile(r"retry in\s+(?:(\d+)h)?\s*(?:(\d+)m)?\s*(?:([\d.]+)s)?", re.I)


def _parse_quota_block(body: bytes) -> tuple[float, str]:
    """Return (seconds to block, short reason) from a 429 body."""
    text = body.decode("utf-8", "replace")
    try:
        message = json.loads(text).get("error", {}).get("message", text)
    except (ValueError, AttributeError):
        message = text

    seconds = 0.0
    match = _RETRY_IN.search(message)
    if match and any(match.groups()):
        hours, minutes, secs = match.groups()
        seconds = int(hours or 0) * 3600 + int(minutes or 0) * 60 + float(secs or 0)

    daily = "per day" in message.lower() or "requests per day" in message.lower()
    if not seconds:
        seconds = 3600.0 if daily else 60.0

    reason = "daily quota" if daily else "rate limit"
    return seconds, reason


def block_model(model: str, seconds: float, reason: str) -> None:
    _blocked_until[model] = time.time() + seconds
    _blocked_reason[model] = reason
    print(
        f"[TTS QUOTA] {model} blocked for {seconds / 3600:.1f} h ({reason}); "
        "no further requests will be sent to it until then",
        flush=True,
    )


def blocked_for(model: str) -> float:
    """Seconds until the model may be used again (0 = available)."""
    remaining = _blocked_until.get(model, 0.0) - time.time()
    return max(0.0, remaining)


def quota_state() -> dict:
    return {
        model: {
            "available": blocked_for(model) == 0,
            "resets_in_s": round(blocked_for(model)),
            "reason": _blocked_reason.get(model) if blocked_for(model) else None,
        }
        for model in config.TTS_MODELS
    }


def quota_error(model: str) -> TTSError:
    remaining = blocked_for(model)
    error = TTSError(
        429,
        f"Natural TTS quota reached. Available again in about "
        f"{max(1, round(remaining / 3600))} h.",
        False,
    )
    error.retry_after = remaining  # type: ignore[attr-defined]
    return error


# ========================================
# ONE GEMINI TTS REQUEST
# ========================================

def _payload(
    text: str,
    language: AnswerLanguage,
    continuation: bool,
    model: str | None = None,
) -> dict:
    return {
        "model": model or config.TTS_MODEL,
        "input": [
            {
                "type": "user_input",
                "content": [
                    {
                        "type": "text",
                        "text": text,
                        "annotations": [
                            {
                                "type": "speech_metadata",
                                "style": get_speaking_style(
                                    language, continuation
                                ),
                            }
                        ],
                    }
                ],
            }
        ],
        "response_format": {
            "type": "audio",
            "mime_type": "audio/l16",
            "sample_rate": SAMPLE_RATE,
        },
        "generation_config": {
            "speech_config": [{"voice": config.TTS_VOICE}],
        },
        "stream": True,
    }


async def _synthesize_once(
    text: str,
    language: AnswerLanguage,
    continuation: bool,
) -> bytes:
    """Stream one chunk from Gemini and return raw PCM."""

    pcm = bytearray()
    async for piece in _stream_pcm(text, language, continuation):
        pcm.extend(piece)
    if not pcm:
        raise TTSError(502, "Gemini returned no speech audio.", True)
    return bytes(pcm)


async def _stream_pcm(
    text: str,
    language: AnswerLanguage,
    continuation: bool,
    model: str | None = None,
) -> AsyncIterator[bytes]:
    """Yield raw PCM pieces as Gemini streams them."""

    model = model or config.TTS_MODEL

    if not config.GEMINI_API_KEY:
        raise TTSError(503, "TTS API key is not configured.", False)

    # Do not spend a request we already know will fail.
    if blocked_for(model):
        raise quota_error(model)

    headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "x-goog-api-key": config.GEMINI_API_KEY,
    }

    received = 0
    completed = False

    try:
        async with _client().stream(
            "POST",
            API_URL,
            headers=headers,
            content=json.dumps(
                _payload(text, language, continuation, model),
                ensure_ascii=False,
            ).encode("utf-8"),
        ) as response:

            if response.status_code != 200:
                body = await response.aread()
                if response.status_code == 429:
                    seconds, reason = _parse_quota_block(body)
                    block_model(model, seconds, reason)
                    raise quota_error(model)
                raise _http_error(response.status_code)

            data_lines: list[str] = []

            async for line in response.aiter_lines():
                if line.startswith("data:"):
                    data_lines.append(line[5:].lstrip(" "))
                    continue
                if line != "" or not data_lines:
                    continue

                data = "\n".join(data_lines)
                data_lines.clear()
                if data == "[DONE]":
                    break

                event = json.loads(data)
                event_type = event.get("event_type")

                if event_type == "error":
                    raise TTSError(502, "Gemini streaming error.", True)

                if event_type == "interaction.completed":
                    status = event.get("interaction", {}).get("status")
                    if status not in {None, "completed"}:
                        raise TTSError(
                            502, "Speech generation did not complete.", True
                        )
                    completed = True
                    break

                if event_type != "step.delta":
                    continue

                delta = event.get("delta") or {}
                if delta.get("type") == "audio" and delta.get("data"):
                    piece = base64.b64decode(delta["data"], validate=True)
                    if piece:
                        received += len(piece)
                        yield piece

    except httpx.TimeoutException as error:
        raise TTSError(504, "Speech generation timed out.", True) from error
    except httpx.TransportError as error:
        raise TTSError(503, "Could not connect to natural TTS.", True) from error
    except (ValueError, KeyError) as error:
        raise TTSError(502, "Invalid natural TTS response.", True) from error

    if not received:
        raise TTSError(
            502,
            "Speech stream ended unexpectedly."
            if not completed
            else "Gemini returned no speech audio.",
            True,
        )


# ========================================
# PAUSE-AWARE SEGMENTATION ("single" mode)
# ========================================
#
# The old code cut the stream every 1.5 s / 3 s exactly,
# often in the middle of a syllable. Any tiny gap between
# two players was then clearly audible. Here a segment is
# closed at the quietest 20 ms window, i.e. inside a
# natural pause, so a join (or a wait for more audio)
# lands where the speaker is silent anyway.

_WINDOW_BYTES = int(BYTES_PER_SECOND * 0.02) // SAMPLE_WIDTH * SAMPLE_WIDTH


def _window_energy(pcm: bytes | bytearray, start: int) -> int:
    """Mean absolute amplitude of one 20 ms window."""
    window = memoryview(pcm)[start:start + _WINDOW_BYTES].cast("h")
    if not len(window):
        return 0
    return sum(abs(sample) for sample in window[::4]) // max(1, len(window[::4]))


def find_cut_point(
    pcm: bytes | bytearray,
    min_seconds: float,
    max_seconds: float,
    force: bool,
) -> int | None:
    """
    Return a byte offset where the buffer should be cut,
    or None to keep waiting for more audio.
    """

    min_bytes = int(BYTES_PER_SECOND * min_seconds) // SAMPLE_WIDTH * SAMPLE_WIDTH
    max_bytes = int(BYTES_PER_SECOND * max_seconds) // SAMPLE_WIDTH * SAMPLE_WIDTH

    if len(pcm) < min_bytes + _WINDOW_BYTES:
        return None

    search_end = min(len(pcm), max_bytes) - _WINDOW_BYTES
    best_offset, best_energy = None, None

    for offset in range(min_bytes, search_end, _WINDOW_BYTES):
        energy = _window_energy(pcm, offset)
        if best_energy is None or energy < best_energy:
            best_offset, best_energy = offset, energy
        # Clear silence: cut immediately (at the middle of
        # the quiet window).
        if energy < 250:
            return offset + _WINDOW_BYTES // 2 // SAMPLE_WIDTH * SAMPLE_WIDTH

    if force or len(pcm) >= max_bytes:
        if best_offset is not None:
            return best_offset + _WINDOW_BYTES // 2 // SAMPLE_WIDTH * SAMPLE_WIDTH
        return len(pcm) - len(pcm) % SAMPLE_WIDTH

    return None


async def synthesize_chunk(
    text: str,
    language: AnswerLanguage,
    continuation: bool = False,
) -> tuple[bytes, float, int]:
    """
    Generate one chunk with bounded concurrency and
    retries. Returns (pcm, generation_seconds, attempts).
    """

    attempts = 0
    last_error: TTSError | None = None

    while attempts <= config.TTS_RETRIES:
        attempts += 1
        async with _limiter():
            started = time.perf_counter()
            try:
                pcm = await _synthesize_once(text, language, continuation)
                return pcm, time.perf_counter() - started, attempts
            except TTSError as error:
                last_error = error
                print(
                    f"[TTS] Chunk attempt {attempts} failed: "
                    f"{error.status} {error.message}",
                    flush=True,
                )
                if not error.retryable:
                    break

        # Back off outside the semaphore so other chunks
        # can keep using the slot.
        delay = getattr(last_error, "retry_after", None) or (0.8 * attempts)
        await asyncio.sleep(min(delay, 8.0))

    assert last_error is not None
    raise last_error


# ========================================
# STREAMING ENDPOINT LOGIC
# ========================================

def stream_natural_speech(
    text: str,
    language: AnswerLanguage,
) -> AsyncIterator[bytes]:
    if config.TTS_MODE == "chunked":
        return _stream_chunked(text, language)
    return _stream_single(text, language)


async def _stream_single(
    text: str,
    language: AnswerLanguage,
) -> AsyncIterator[bytes]:
    """
    One Gemini request per answer (quota friendly).
    Audio is forwarded as soon as a natural pause is
    found, so playback starts after ~1-3 s of audio.
    """

    started = time.perf_counter()

    print(
        f"[TTS STREAM] single | {config.TTS_MODEL} / {config.TTS_VOICE} / "
        f"{language} | {len(text)} chars",
        flush=True,
    )

    yield stream_message({
        "type": "start",
        "model": config.TTS_MODEL,
        "voice": config.TTS_VOICE,
        "language": language,
        "mode": "single",
    })

    buffer = bytearray()
    index = 0
    total_audio = 0.0
    first_audio_at = None
    attempts = 0

    def segment_message(pcm: bytes) -> bytes:
        return stream_message({
            "type": "audio",
            "index": index,
            "duration": len(pcm) / BYTES_PER_SECOND,
            "data": base64.b64encode(pcm_to_wav(pcm)).decode("ascii"),
        })

    try:
        while True:
            attempts += 1
            try:
                async with _limiter():
                    async for piece in _stream_pcm(text, language, False):
                        if first_audio_at is None:
                            first_audio_at = time.perf_counter() - started
                            print(
                                f"[TTS STREAM] First raw audio: {first_audio_at:.2f} s",
                                flush=True,
                            )

                        buffer.extend(piece)

                        while True:
                            first = index == 0
                            cut = find_cut_point(
                                buffer,
                                config.TTS_FIRST_SEGMENT_MIN if first else config.TTS_SEGMENT_MIN,
                                config.TTS_FIRST_SEGMENT_MAX if first else config.TTS_SEGMENT_MAX,
                                force=False,
                            )
                            if cut is None:
                                break

                            pcm = bytes(buffer[:cut])
                            del buffer[:cut]
                            total_audio += len(pcm) / BYTES_PER_SECOND

                            if first:
                                print(
                                    "[TTS STREAM] First playable segment: "
                                    f"{time.perf_counter() - started:.2f} s "
                                    f"({len(pcm) / BYTES_PER_SECOND:.2f} s audio)",
                                    flush=True,
                                )

                            yield segment_message(pcm)
                            index += 1
                break

            except TTSError as error:
                # A restart is only safe before the user has
                # heard anything; quota errors are never
                # retried (each retry costs a daily request).
                if (
                    index > 0
                    or not error.retryable
                    or error.status == 429
                    or attempts > config.TTS_RETRIES
                ):
                    raise
                print(
                    f"[TTS STREAM] Retrying before first audio: {error.message}",
                    flush=True,
                )
                buffer.clear()
                await asyncio.sleep(0.8 * attempts)

        # Flush the remaining audio.
        while buffer:
            cut = find_cut_point(
                buffer,
                config.TTS_SEGMENT_MIN,
                config.TTS_SEGMENT_MAX,
                force=True,
            ) if len(buffer) > BYTES_PER_SECOND * config.TTS_SEGMENT_MAX else len(buffer)
            pcm = bytes(buffer[:cut])
            del buffer[:cut]
            if len(pcm) < SAMPLE_WIDTH:
                break
            total_audio += len(pcm) / BYTES_PER_SECOND
            yield segment_message(pcm)
            index += 1

        elapsed = time.perf_counter() - started
        print(
            f"[TTS STREAM] Complete: {index} segments, {total_audio:.1f} s audio "
            f"in {elapsed:.2f} s (x{total_audio / max(elapsed, 0.01):.2f} real time)",
            flush=True,
        )

        yield stream_message({
            "type": "complete",
            "segments": index,
            "duration": total_audio,
            "generation_time": round(elapsed, 2),
        })

    except TTSError as error:
        print(f"[TTS STREAM ERROR] {error.status} {error.message}", flush=True)
        yield stream_message({
            "type": "error",
            "status": error.status,
            "message": error.message,
            "failed_index": index,
        })

    except Exception as error:
        print(f"[TTS STREAM ERROR] {type(error).__name__}: {error}", flush=True)
        yield stream_message({
            "type": "error",
            "status": 502,
            "message": "Natural speech streaming failed.",
            "failed_index": index,
        })


async def _stream_chunked(
    text: str,
    language: AnswerLanguage,
) -> AsyncIterator[bytes]:

    started = time.perf_counter()
    chunks = split_into_chunks(text)

    print(
        f"[TTS STREAM] {config.TTS_MODEL} / {config.TTS_VOICE} / {language} | "
        f"{len(text)} chars in {len(chunks)} chunks "
        f"(parallel {config.TTS_PARALLEL})",
        flush=True,
    )

    yield stream_message({
        "type": "start",
        "model": config.TTS_MODEL,
        "voice": config.TTS_VOICE,
        "language": language,
        "chunks": len(chunks),
    })

    # Tasks are created in order; the semaphore admits
    # them first-come-first-served, so chunk 0 always
    # starts first.
    tasks = [
        asyncio.create_task(
            synthesize_chunk(chunk, language, continuation=index > 0)
        )
        for index, chunk in enumerate(chunks)
    ]

    total_audio = 0.0
    sent = 0

    try:
        for index, task in enumerate(tasks):
            pcm, generation_time, attempts = await task
            duration = len(pcm) / BYTES_PER_SECOND
            total_audio += duration

            if index == 0:
                print(
                    "[TTS STREAM] First chunk ready: "
                    f"{time.perf_counter() - started:.2f} s "
                    f"({duration:.1f} s of audio)",
                    flush=True,
                )

            print(
                f"[TTS STREAM] Chunk {index}: {duration:.1f} s audio, "
                f"generated in {generation_time:.2f} s "
                f"(x{duration / max(generation_time, 0.01):.2f} real time, "
                f"attempts {attempts})",
                flush=True,
            )

            yield stream_message({
                "type": "audio",
                "index": index,
                "duration": duration,
                "text": chunks[index],
                "data": base64.b64encode(pcm_to_wav(pcm)).decode("ascii"),
            })
            sent += 1

        elapsed = time.perf_counter() - started
        print(
            f"[TTS STREAM] Complete: {sent} chunks, "
            f"{total_audio:.1f} s audio in {elapsed:.2f} s",
            flush=True,
        )

        yield stream_message({
            "type": "complete",
            "segments": sent,
            "duration": total_audio,
            "generation_time": round(elapsed, 2),
        })

    except TTSError as error:
        print(f"[TTS STREAM ERROR] {error.status} {error.message}", flush=True)
        yield stream_message({
            "type": "error",
            "status": error.status,
            "message": error.message,
            "failed_index": sent,
        })

    except Exception as error:  # never leak details to the client
        print(f"[TTS STREAM ERROR] {type(error).__name__}: {error}", flush=True)
        yield stream_message({
            "type": "error",
            "status": 502,
            "message": "Natural speech streaming failed.",
            "failed_index": sent,
        })

    finally:
        # Runs on completion, error AND client disconnect:
        # cancel every outstanding Gemini request so no
        # quota or concurrency slot is wasted.
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def _validate_text(text: str) -> str:
    if not config.GEMINI_API_KEY:
        raise HTTPException(503, "TTS API key is not configured.")
    text = text.strip()
    if not text:
        raise HTTPException(400, "Speech text cannot be empty.")
    return text


def _headers(language: str) -> dict[str, str]:
    return {
        "Cache-Control": "no-store",
        "X-TTS-Model": config.TTS_MODEL,
        "X-TTS-Voice": config.TTS_VOICE,
        "X-TTS-Language": language,
    }


# ========================================
# ENDPOINTS
# ========================================

@router.post("/tts")
async def text_to_speech(request: TTSRequest):
    """Complete WAV in one response (used for short clips)."""

    text = _validate_text(request.text)
    try:
        pcm, generation_time, _ = await synthesize_chunk(text, request.language)
    except TTSError as error:
        raise HTTPException(error.status, error.message) from error

    print(
        f"[TTS] {len(text)} chars -> {len(pcm) / BYTES_PER_SECOND:.1f} s "
        f"audio in {generation_time:.2f} s",
        flush=True,
    )

    return Response(
        content=pcm_to_wav(pcm),
        media_type="audio/wav",
        headers=_headers(request.language),
    )


@router.post("/tts/stream")
async def text_to_speech_stream(request: TTSRequest):
    text = _validate_text(request.text)
    return StreamingResponse(
        stream_natural_speech(text, request.language),
        media_type="application/x-ndjson",
        headers={**_headers(request.language), "X-Accel-Buffering": "no"},
    )
