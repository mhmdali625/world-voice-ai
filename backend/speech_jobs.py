"""
Continuous speech delivery: one answer = one audio stream.

WHY THIS EXISTS (root cause of the audio cuts)
----------------------------------------------
Real-device tests showed the voice cutting out even on
REPLAY of fully downloaded audio. The audio data was
verified sample-exact (benchmark in BENCHMARK_REPORT.md),
so the cuts came from playback: the app played 9 separate
WAV files through 9 separate native players. At every
boundary expo-audio released Android audio focus (no
player was playing for an instant), then re-requested it
for the next player, which also had to start its own
audio output. Those hand-offs are audible.

The fix is architectural: the phone plays ONE continuous
WAV stream through ONE player, so there are no boundaries.

  POST /tts/jobs              -> starts generation now, returns an id
  GET  /tts/jobs/{id}         -> status (progress, errors, metrics)
  GET  /tts/jobs/{id}/audio   -> one WAV stream (live, or complete for replay)

START POLICY ("smooth", default)
--------------------------------
If audio is generated slower than real time (Gemini free
tier measured 0.63x on the phone), any player WILL run out
of audio unless playback starts with enough reserve. Instead
of a fixed buffer, the server measures the real generation
rate r and starts as soon as

    audio_ready >= estimated_total * (1 - r)

which is the minimum mathematically sufficient for zero
interruptions. With a provider faster than real time
(r >= 1) this is ~1.0 s, i.e. no extra wait at all.
"fast" starts after 1.0 s regardless (may interrupt).

Replay: the same URL serves the cached audio as one
complete WAV, instantly, with no API request.
"""

import asyncio

import httpx
import struct
import time
import uuid

from collections import OrderedDict
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

import config
from neterrors import is_network_error, probably_offline
import tts_providers
import tts_service
from tts_service import (
    BYTES_PER_SECOND,
    CHANNELS,
    SAMPLE_RATE,
    SAMPLE_WIDTH,
    TTSError,
    TTSRequest,
)


router = APIRouter(tags=["Continuous Speech"])

MIN_START_SECONDS = 1.0
EXO_START_BUFFER = 1.0          # media3 DefaultLoadControl.DEFAULT_BUFFER_FOR_PLAYBACK_MS (1000)
RATE_MEASURE_SECONDS = 1.2      # audio needed before trusting a rate
RECENT_WINDOW_SECONDS = 3.0     # window for the burst-proof rate
SAFETY_FACTOR = 1.12            # margin for rate fluctuation
JOIN_SILENCE_KEEP = 0.25        # max silence kept on each side of a join
MAX_JOBS = 24
JOB_TTL_SECONDS = 60 * 60

# Seconds of speech per character, learned from finished
# jobs (starting values measured on Fola output).
_seconds_per_char: dict[str, float] = {
    "ar-LB": 0.080,
    "ar": 0.080,
    "en": 0.065,
    "fr": 0.065,
    "und": 0.075,
}

# Learned provider behaviour (exponential moving averages),
# used to predict parts that have not produced audio yet.
# Per provider: a fast provider must not inherit Gemini's slow history.
_learned: dict[str, dict[str, float]] = {
    "gemini": {"rate": 0.63, "first_audio": 2.5},
    "elevenlabs": {"rate": 2.0, "first_audio": 0.6},
    "azure": {"rate": 2.0, "first_audio": 0.6},
    "file": {"rate": 1.0, "first_audio": 1.0},
}


def _stats(provider: str) -> dict[str, float]:
    return _learned.setdefault(provider, {"rate": 1.0, "first_audio": 1.5})


def split_into_parts(text: str, parts: int) -> list[str]:
    """
    Sentence-aligned split for parallel generation. The first
    part is short (fast first audio); the rest is balanced.
    Short answers are never split (no extra quota).
    """
    if parts <= 1 or len(text) < 120:
        return [text]

    sentences = [s for s in tts_service._SENTENCE_END.split(text.strip()) if s.strip()]  # noqa: SLF001
    if len(sentences) < 2:
        return [text]

    first, rest = sentences[0].strip(), [s.strip() for s in sentences[1:]]
    while rest and len(first) < 40:
        first = f"{first} {rest.pop(0)}"
    if not rest:
        return [text]

    # Fill parts 2..N in order, each about the same length.
    groups = max(1, min(parts - 1, len(rest)))
    target = sum(len(s) for s in rest) / groups
    out, current = [first], ""
    for sentence in rest:
        if current and len(current) >= target and len(out) < groups:
            out.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    out.append(current)
    return out


def _leading_silence_bytes(pcm: bytes | bytearray, limit_bytes: int) -> int:
    """Bytes of near-silence at the start (scanned up to limit)."""
    window = int(BYTES_PER_SECOND * 0.02) // SAMPLE_WIDTH * SAMPLE_WIDTH
    offset = 0
    while offset + window <= min(len(pcm), limit_bytes):
        if tts_service._window_energy(pcm, offset) >= 300:  # noqa: SLF001
            break
        offset += window
    return offset


def _trailing_silence_bytes(pcm: bytes | bytearray) -> int:
    window = int(BYTES_PER_SECOND * 0.02) // SAMPLE_WIDTH * SAMPLE_WIDTH
    end = len(pcm) - len(pcm) % SAMPLE_WIDTH
    offset = end
    while offset - window >= 0:
        if tts_service._window_energy(pcm, offset - window) >= 300:  # noqa: SLF001
            break
        offset -= window
    return end - offset


class _TooSlow(Exception):
    """The provider could not start playback before the switch deadline."""


class SpeechPart:

    def __init__(self, index: int, text: str):
        self.index = index
        self.text = text
        self.pcm = bytearray()
        self.started_at: float | None = None
        self.first_audio_at: float | None = None
        self.finished_at: float | None = None
        self.done = False
        self.trimmed_lead = False
        self.arrivals: list[tuple[float, int]] = []

    def audio_seconds(self) -> float:
        return len(self.pcm) / BYTES_PER_SECOND

    def record_arrival(self) -> None:
        now = time.perf_counter()
        self.arrivals.append((now, len(self.pcm)))
        # Keep ~10 s of history.
        while len(self.arrivals) > 2 and now - self.arrivals[0][0] > 10:
            self.arrivals.pop(0)

    def rate(self) -> float | None:
        """
        Conservative generation rate: the SLOWER of the average
        since first audio and the rate over the last 3 s.
        Providers can deliver audio in bursts; an average that
        includes an early burst would start playback too soon
        (a likely cause of the stall at 0.27 s on the phone).
        """
        if self.first_audio_at is None or self.audio_seconds() < RATE_MEASURE_SECONDS:
            return None
        end = self.finished_at if self.done else time.perf_counter()
        elapsed = end - self.first_audio_at
        if elapsed <= 0.2:
            return None
        average = self.audio_seconds() / elapsed
        if self.done:
            return average

        now = time.perf_counter()
        window_start = next(
            ((t, b) for t, b in self.arrivals if now - t <= RECENT_WINDOW_SECONDS),
            None,
        )
        if window_start is None or now - window_start[0] < 1.0:
            return average
        recent = (len(self.pcm) - window_start[1]) / BYTES_PER_SECOND / (now - window_start[0])
        return min(average, recent)


# ========================================
# JOB
# ========================================

class SpeechJob:

    def __init__(self, text: str, language: str, switch_after: float | None = None):
        self.id = uuid.uuid4().hex
        self.text = text
        self.language = language
        # Seconds the preferred voice may take before the answer moves to
        # the next approved voice (live answers use a shorter deadline).
        self.switch_after = config.TTS_SWITCH_AFTER if switch_after is None else switch_after
        # One provider for the WHOLE answer (never mixed).
        self.provider = tts_providers.choose_provider() or tts_providers.provider_name()
        self.created = time.time()
        self.started = time.perf_counter()
        self.pcm = bytearray()                 # contiguous, playable audio
        self.done = False
        self.error: tuple[int, str] | None = None
        self.retry_after_s: float | None = None
        self.model: str | None = None
        self.first_audio_at: float | None = None
        self.finished_at: float | None = None
        self.stream_started_at: float | None = None
        self._changed = asyncio.Event()

        texts = split_into_parts(text, config.TTS_SPLIT)
        self.parts = [SpeechPart(i, t) for i, t in enumerate(texts)]
        self._assembled_part = 0              # part currently being appended
        self._assembled_offset = 0            # bytes of that part already appended

        self.task = asyncio.create_task(self._run())

    # ---------- generation ----------

    async def _run(self) -> None:
        try:
            # Approved providers after the chosen one, in order.
            # A switch is ONLY allowed before any audio of this
            # answer exists, so the user never hears two voices
            # in one answer.
            order = config.TTS_PROVIDERS
            start = order.index(self.provider) if self.provider in order else 0
            candidates = [self.provider] + [
                name for name in order[start + 1:]
                if name != self.provider
                and tts_providers._configured(name)  # noqa: SLF001
                and not tts_providers.provider_blocked_for(name)
            ]

            if probably_offline() and "piper" in candidates:
                # No internet right now: the cloud voices cannot answer; use the local one.
                candidates = ["piper"]
            for attempt, provider in enumerate(candidates):
                self.provider = provider
                # Same model for every part of this answer.
                model = tts_providers.choose_model(provider)
                self.model = model
                has_next = attempt < len(candidates) - 1
                try:
                    await self._generate_with_deadline(model, has_next)
                    break
                except Exception as raw_error:  # noqa: BLE001
                    if not isinstance(raw_error, (TTSError, _TooSlow)) and not is_network_error(raw_error):
                        raise
                    # No internet / connection reset: try the next approved voice
                    # (not blocked like a bad key: back as soon as the network is).
                    error = (raw_error if isinstance(raw_error, (TTSError, _TooSlow))
                             else TTSError(599, f"network: {type(raw_error).__name__}", True))
                    nothing_played = self.stream_started_at is None
                    if not nothing_played or not has_next:
                        if isinstance(error, _TooSlow):
                            continue          # cannot happen (no next), keep going
                        raise
                    if isinstance(error, TTSError) and error.status in {401, 403, 503}:
                        # Bad key / access problem: avoid retrying it on every answer.
                        tts_providers.block_provider(provider, 600, f"error {error.status}")
                    reason = (
                        f"not ready after {self.switch_after:.1f} s"
                        if isinstance(error, _TooSlow)
                        else f"failed ({error.status} {error.message})"
                    )
                    print(
                        f"[SPEECH JOB {self.id[:6]}] {provider} {reason} before anything "
                        f"was played; using next approved provider {candidates[attempt + 1]}",
                        flush=True,
                    )
                    self._reset_audio()

            if not self.pcm:
                raise TTSError(502, "No speech audio was generated.", True)

        except TTSError as error:
            self.error = (error.status, error.message)
            self.retry_after_s = getattr(error, "retry_after", None)
        except asyncio.CancelledError:
            self.error = (499, "Cancelled.")
            raise
        except Exception as error:  # never crash the server
            print(f"[SPEECH JOB ERROR] {type(error).__name__}: {error}", flush=True)
            self.error = (502, "Natural speech generation failed.")
        finally:
            self.done = True
            self.finished_at = time.perf_counter()
            self._assemble(final=True)
            self._learn()
            self._log_summary()
            self._notify()

    async def _generate_with_deadline(self, model: str | None, has_next: bool) -> None:
        """
        Generate all parts. If another approved provider exists and
        this one cannot START playback (ready_to_start) within
        WV_TTS_SWITCH_AFTER seconds, give up on it while nothing has
        been played, so the answer moves to the faster provider.
        """
        task = asyncio.ensure_future(
            asyncio.gather(*(self._run_part(part, model) for part in self.parts))
        )
        deadline = (
            time.perf_counter() + self.switch_after
            if has_next and self.switch_after > 0
            else None
        )
        try:
            while not task.done():
                if deadline is not None:
                    if self.stream_started_at is not None or (self.pcm and self.ready_to_start()):
                        deadline = None                 # this provider is fast enough
                    elif time.perf_counter() >= deadline:
                        raise _TooSlow()
                await self.wait_for_change(0.25)
            await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    def _reset_audio(self) -> None:
        """Discard all generated audio (only before anything was played)."""
        for part in self.parts:
            part.pcm.clear()
            part.done = False
            part.started_at = part.first_audio_at = part.finished_at = None
            part.trimmed_lead = False
            part.arrivals.clear()
        self.pcm.clear()
        self._assembled_part = 0
        self._assembled_offset = 0
        self.first_audio_at = None

    async def _run_part(self, part: SpeechPart, model: str | None) -> None:
        attempts = 0
        while True:
            attempts += 1
            part.started_at = time.perf_counter()
            try:
                async for piece in tts_providers.stream_pcm(
                    part.text,
                    self.language,
                    on_model=self._set_model,
                    models=[model] if model else None,
                    part=(part.index, len(self.parts)),
                    provider=self.provider,
                ):
                    now = time.perf_counter()
                    if part.first_audio_at is None:
                        part.first_audio_at = now
                    if self.first_audio_at is None:
                        self.first_audio_at = now
                        print(
                            f"[SPEECH JOB {self.id[:6]}] first audio "
                            f"{now - self.started:.2f} s ({self.provider})",
                            flush=True,
                        )
                    part.pcm.extend(piece)
                    part.record_arrival()
                    self._assemble()
                    self._notify()
                part.done = True
                part.finished_at = time.perf_counter()
                if len(part.pcm) % SAMPLE_WIDTH:
                    del part.pcm[-1]
                self._assemble()
                self._notify()
                return
            except TTSError as error:
                # Restart a part only before it produced audio;
                # quota errors are never retried.
                if (
                    part.pcm
                    or not error.retryable
                    or error.status == 429
                    or attempts > config.TTS_RETRIES
                ):
                    raise
                await asyncio.sleep(0.8 * attempts)

    def _assemble(self, final: bool = False) -> None:
        """
        Append newly available audio to the single playable
        stream, strictly in part order. At each join, silence
        is reduced to a natural sentence pause.
        """
        keep = int(BYTES_PER_SECOND * JOIN_SILENCE_KEEP) // SAMPLE_WIDTH * SAMPLE_WIDTH

        while self._assembled_part < len(self.parts):
            part = self.parts[self._assembled_part]

            # Trim excessive leading silence of a following
            # part once enough of it is known.
            if self._assembled_part > 0 and not part.trimmed_lead:
                if not part.done and len(part.pcm) < BYTES_PER_SECOND:
                    return
                lead = _leading_silence_bytes(part.pcm, BYTES_PER_SECOND)
                self._assembled_offset = max(0, lead - keep)
                part.trimmed_lead = True

            usable = len(part.pcm) - len(part.pcm) % SAMPLE_WIDTH
            is_last = self._assembled_part == len(self.parts) - 1

            if part.done and not is_last:
                # Trim excessive trailing silence before the join.
                trail = _trailing_silence_bytes(part.pcm)
                usable = max(self._assembled_offset, usable - max(0, trail - keep))

            if usable > self._assembled_offset:
                self.pcm.extend(part.pcm[self._assembled_offset:usable])
                self._assembled_offset = usable

            if not part.done:
                return

            self._assembled_part += 1
            self._assembled_offset = 0

    def _set_model(self, model: str) -> None:
        self.model = model

    def _notify(self) -> None:
        self._changed.set()
        self._changed = asyncio.Event()

    async def wait_for_change(self, timeout: float) -> None:
        event = self._changed
        try:
            await asyncio.wait_for(event.wait(), timeout)
        except asyncio.TimeoutError:
            pass

    # ---------- measurements ----------

    def audio_seconds(self) -> float:
        return len(self.pcm) / BYTES_PER_SECOND

    def generated_seconds(self) -> float:
        return sum(p.audio_seconds() for p in self.parts)

    def generation_rate(self) -> float | None:
        """Total audio produced per second (all parts together)."""
        if self.first_audio_at is None:
            return None
        end = self.finished_at if self.done else time.perf_counter()
        elapsed = end - self.first_audio_at
        if self.generated_seconds() < RATE_MEASURE_SECONDS or elapsed <= 0.2:
            return None
        return self.generated_seconds() / elapsed

    def _part_estimate(self, part: SpeechPart) -> float:
        if part.done:
            return part.audio_seconds()
        known = tts_providers.expected_duration(
            part.text, (part.index, len(self.parts)), self.provider
        )
        if known:
            return max(part.audio_seconds(), known)
        per_char = _seconds_per_char.get(self.language, 0.075)
        return max(part.audio_seconds(), len(part.text) * per_char)

    def estimated_total_seconds(self) -> float:
        return sum(self._part_estimate(p) for p in self.parts)

    def _measured_rate(self) -> float | None:
        rates = [r for r in (p.rate() for p in self.parts) if r]
        return min(rates) if rates else None

    def ready_to_start(self) -> bool:
        """
        Start now only if a forward simulation predicts that
        playback will never overtake the available audio.
        """
        if self.done:
            return bool(self.pcm)
        if config.TTS_START_POLICY == "fast":
            return self.audio_seconds() >= MIN_START_SECONDS
        if self.audio_seconds() < min(MIN_START_SECONDS, self.estimated_total_seconds()):
            return False

        measured = self._measured_rate()
        if measured is None:
            return False

        now = time.perf_counter()
        rate = measured / SAFETY_FACTOR
        first_latency = _stats(self.provider)["first_audio"]

        # Predicted audio of each part at future time t (seconds from now).
        plans = []
        for part in self.parts:
            estimate = self._part_estimate(part)
            have = part.audio_seconds()
            part_rate = (part.rate() or measured) / SAFETY_FACTOR
            if part.first_audio_at is None:
                begun = part.started_at or now
                wait = max(0.0, first_latency - (now - begun))
            else:
                wait = 0.0
            plans.append((estimate, have, part_rate if part.first_audio_at else rate, wait))

        def contiguous(t: float) -> float:
            total = 0.0
            for estimate, have, part_rate, wait in plans:
                available = min(estimate, have + part_rate * max(0.0, t - wait))
                total += available
                if available < estimate - 1e-6:
                    break
            return total

        total = sum(p[0] for p in plans)
        step = 0.05

        # ExoPlayer itself waits for EXO_START_BUFFER seconds.
        t = 0.0
        while contiguous(t) < min(EXO_START_BUFFER, total) and t < 120:
            t += step
        play_start = t

        while t < play_start + total + 1:
            played = t - play_start
            if played >= total:
                return True
            if contiguous(t) + 1e-6 < min(total, played + 0.2):
                return False
            t += step
        return True

    def _learn(self) -> None:
        if self.error or self.provider == "file":
            return
        for part in self.parts:
            if part.done and part.first_audio_at and part.started_at:
                stats = _stats(self.provider)
                stats["first_audio"] = round(
                    0.7 * stats["first_audio"] + 0.3 * (part.first_audio_at - part.started_at), 3)
            rate = part.rate()
            if rate:
                stats = _stats(self.provider)
                stats["rate"] = round(0.7 * stats["rate"] + 0.3 * rate, 3)
            if len(part.text) >= 40 and part.done:
                observed = part.audio_seconds() / len(part.text)
                old = _seconds_per_char.get(self.language, observed)
                _seconds_per_char[self.language] = round(0.7 * old + 0.3 * observed, 4)

    def status(self) -> dict:
        rate = self.generation_rate()
        return {
            "id": self.id,
            "provider": self.provider,
            "model": self.model,
            "language": self.language,
            "parts": len(self.parts),
            "done": self.done,
            # The phone opens its player only when this is
            # true, so a failed job never triggers player
            # retries against the audio URL.
            "ready_to_start": bool(self.pcm) and self.ready_to_start(),
            "error": None if not self.error else {
                "status": self.error[0],
                "message": self.error[1],
                "retry_after_s": None if self.retry_after_s is None
                else round(self.retry_after_s),
            },
            "audio_seconds": round(self.audio_seconds(), 2),
            "estimated_total_seconds": round(self.estimated_total_seconds(), 2),
            "generation_rate": None if rate is None else round(rate, 3),
            "part_rates": [None if p.rate() is None else round(p.rate(), 3) for p in self.parts],
            "first_audio_s": None if self.first_audio_at is None
            else round(self.first_audio_at - self.started, 2),
            "stream_started_s": None if self.stream_started_at is None
            else round(self.stream_started_at - self.started, 2),
            "total_generation_s": None if self.finished_at is None
            else round(self.finished_at - self.started, 2),
            "start_policy": config.TTS_START_POLICY,
        }

    def _log_summary(self) -> None:
        s = self.status()
        print(
            f"[SPEECH JOB {self.id[:6]}] done | parts {s['parts']} | {s['audio_seconds']} s audio | "
            f"first {s['first_audio_s']} s | stream start {s['stream_started_s']} s | "
            f"total {s['total_generation_s']} s | rate x{s['generation_rate']} "
            f"(per part {s['part_rates']}) | error={s['error']}",
            flush=True,
        )


# ========================================
# JOB STORE (bounded, in memory)
# ========================================

_jobs: "OrderedDict[str, SpeechJob]" = OrderedDict()


def _prune() -> None:
    now = time.time()
    for job_id in list(_jobs):
        job = _jobs[job_id]
        if now - job.created > JOB_TTL_SECONDS and job.done:
            _jobs.pop(job_id)
    while len(_jobs) > MAX_JOBS:
        _, oldest = _jobs.popitem(last=False)
        if not oldest.done:
            oldest.task.cancel()


def create_job(text: str, language: str, switch_after: float | None = None) -> SpeechJob:
    _prune()
    job = SpeechJob(text, language, switch_after)
    _jobs[job.id] = job
    print(
        f"[SPEECH JOB {job.id[:6]}] {job.provider} | {language} | {len(text)} chars | "
        f"policy={config.TTS_START_POLICY}",
        flush=True,
    )
    return job


def get_job(job_id: str) -> SpeechJob:
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "Speech job not found (expired or server restarted).")
    return job


def job_urls(job: SpeechJob) -> dict:
    return {
        "id": job.id,
        "audio_path": f"/tts/jobs/{job.id}/audio",
        "status_path": f"/tts/jobs/{job.id}",
    }


# ========================================
# WAV STREAM
# ========================================

# Placeholder data size for a live stream whose length is
# not known yet (~6 h). The stream simply ends when the
# speech ends. Replays of finished jobs use the exact size.
_LIVE_DATA_SIZE = 0x7FFFFF00


def wav_header(data_size: int) -> bytes:
    byte_rate = SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH
    return b"".join([
        b"RIFF", struct.pack("<I", min(data_size + 36, 0xFFFFFFFF)), b"WAVE",
        b"fmt ", struct.pack("<IHHIIHH", 16, 1, CHANNELS, SAMPLE_RATE,
                             byte_rate, CHANNELS * SAMPLE_WIDTH, SAMPLE_WIDTH * 8),
        b"data", struct.pack("<I", data_size),
    ])


async def _stream_audio(job: SpeechJob) -> AsyncIterator[bytes]:
    # Hold the first byte until playback can run to the end
    # without stalling (see START POLICY above).
    while not job.ready_to_start():
        if job.done and not job.pcm:
            return          # failed before any audio: nothing to stream
        await job.wait_for_change(0.5)

    if job.stream_started_at is None:
        job.stream_started_at = time.perf_counter()
        print(
            f"[SPEECH JOB {job.id[:6]}] stream starts at "
            f"{job.stream_started_at - job.started:.2f} s with "
            f"{job.audio_seconds():.2f} s ready (rate x{job.generation_rate()})",
            flush=True,
        )

    sent = 0
    yield wav_header(len(job.pcm) if job.done else _LIVE_DATA_SIZE)

    while True:
        available = len(job.pcm) - (len(job.pcm) % SAMPLE_WIDTH)
        if available > sent:
            chunk = bytes(job.pcm[sent:available])
            sent = available
            yield chunk
            continue
        if job.done:
            return
        await job.wait_for_change(1.0)


# ========================================
# ENDPOINTS
# ========================================

# ========================================
# DEVELOPMENT-ONLY TEST CONTROL
# ========================================
#
# Active only when WV_TTS_PROVIDER=file (saved Fola audio,
# zero API quota). Switches between "normal" and "slow"
# generation between answers, without restarting:
#
#   GET  /dev/tts-test                 current settings
#   POST /dev/tts-test?rate=0.63       slow (free-tier speed)
#   POST /dev/tts-test?rate=1.5        normal / fast provider
#   POST /dev/tts-test?policy=fast     compare start policies

def _require_test_mode() -> None:
    if tts_providers.provider_name() != "file":
        raise HTTPException(404, "Not available (test mode is off).")


def _test_settings() -> dict:
    return {
        "provider": "file",
        "test_wav": config.TTS_TEST_WAV,
        "audio_seconds": round(tts_providers.expected_duration("") or 0, 2),
        "rate": config.TTS_TEST_RATE,
        "first_audio_s": config.TTS_TEST_FIRST_AUDIO,
        "start_policy": config.TTS_START_POLICY,
        "split": config.TTS_SPLIT,
    }


@router.get("/dev/tts-test")
async def get_test_settings():
    _require_test_mode()
    return _test_settings()


@router.post("/dev/tts-test")
async def set_test_settings(
    rate: float | None = None,
    first_audio: float | None = None,
    policy: str | None = None,
    split: int | None = None,
):
    _require_test_mode()
    if split is not None:
        config.TTS_SPLIT = min(3, max(1, split))
    if rate is not None:
        config.TTS_TEST_RATE = min(20.0, max(0.05, rate))
    if first_audio is not None:
        config.TTS_TEST_FIRST_AUDIO = min(30.0, max(0.0, first_audio))
    if policy in {"smooth", "fast"}:
        config.TTS_START_POLICY = policy
    print(f"[TTS TEST] settings changed: {_test_settings()}", flush=True)
    return _test_settings()


@router.get("/tts/quota")
async def tts_quota():
    """Which Fola models can be used now, and when blocked ones reset."""
    from tts_service import quota_state
    return {
        "next_answer_provider": tts_providers.choose_provider(),
        "providers": tts_providers.providers_state(),
        "gemini_models": quota_state(),
    }


@router.post("/tts/jobs")
async def create_speech_job(request: TTSRequest):
    text = request.text.strip()
    if not text:
        raise HTTPException(400, "Speech text cannot be empty.")
    job = create_job(text, request.language)
    return {**job_urls(job), **job.status()}


@router.get("/tts/jobs/{job_id}")
async def speech_job_status(job_id: str):
    return get_job(job_id).status()


@router.get("/tts/jobs/{job_id}/audio")
async def speech_job_audio(job_id: str, request: Request):
    job = get_job(job_id)

    # Finished audio supports HTTP Range requests, so the phone
    # can RESUME at its last position after a network drop
    # instead of restarting the answer.
    range_header = request.headers.get("range", "")
    if job.done and job.pcm and range_header.startswith("bytes="):
        body = wav_header(len(job.pcm)) + bytes(job.pcm)
        try:
            first, _, last = range_header[6:].partition("-")
            start = int(first) if first else 0
            end = min(int(last), len(body) - 1) if last else len(body) - 1
        except ValueError:
            start, end = 0, len(body) - 1
        if start >= len(body) or start > end:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{len(body)}"})
        return Response(
            content=body[start:end + 1],
            status_code=206,
            media_type="audio/wav",
            headers={
                "Content-Range": f"bytes {start}-{end}/{len(body)}",
                "Accept-Ranges": "bytes",
                "Cache-Control": "no-store",
            },
        )

    # Fail fast with a real HTTP error if generation failed
    # before producing anything (e.g. quota reached).
    while not job.done and not job.pcm:
        await job.wait_for_change(0.5)
    if job.error and not job.pcm:
        raise HTTPException(job.error[0] if job.error[0] >= 400 else 502, job.error[1])

    headers = {"Cache-Control": "no-store", "X-TTS-Provider": job.provider}
    if job.done:
        headers["Content-Length"] = str(44 + len(job.pcm) - len(job.pcm) % SAMPLE_WIDTH)
        headers["Accept-Ranges"] = "bytes"

    return StreamingResponse(
        _stream_audio(job),
        media_type="audio/wav",
        headers=headers,
    )
