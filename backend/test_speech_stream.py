"""
Offline test of continuous speech delivery (no API calls, no quota).

Streams the saved Fola recording through the REAL speech_jobs
code using the test "file" provider at a simulated generation
rate PER REQUEST, with 1, 2 or 3 parallel parts, and plays the
result with a model of ExoPlayer (Android's player):
  * playback starts once 1.0 s is buffered (media3 default),
  * after running dry it waits for 2.0 s of audio.

Usage: .venv\\Scripts\\python test_speech_stream.py [rate] [first_audio]
"""

import asyncio
import sys
import time

import config
import speech_jobs
from tts_service import BYTES_PER_SECOND

EXO_START = 1.0
EXO_REBUFFER = 2.0
WAV = "test_audio/fola_sample_ar-LB.wav"

# A Lebanese answer with four sentences, so it can be split.
TEXT = (
    "في قدامك لابتوب مفتوح عالطاولة، والشاشة شغّالة وعليها صفحة كتابة. "
    "حدّ اللابتوب عاليمين في كباية قهوة وتلفون أسود. "
    "ورا الطاولة في شبّاك والضو جايي منّو، فالصورة واضحة منيحة. "
    "ما في شي مكتوب واضح بقدر إقراه من هالبعد."
)


async def run(rate: float, first_audio: float, split: int, policy: str = "smooth") -> dict:
    config.TTS_PROVIDER = "file"
    config.TTS_PROVIDERS = ["file"]
    config.TTS_TEST_WAV = WAV
    config.TTS_TEST_RATE = rate
    config.TTS_TEST_FIRST_AUDIO = first_audio
    config.TTS_SPLIT = split
    config.TTS_START_POLICY = policy

    job = speech_jobs.create_job(TEXT, "ar-LB")
    t0 = time.perf_counter()

    received = 0
    arrivals: list[tuple[float, int]] = []
    async for chunk in speech_jobs._stream_audio(job):  # noqa: SLF001
        received += len(chunk)
        arrivals.append((time.perf_counter() - t0, max(0, received - 44)))
    await job.task

    total = job.audio_seconds()

    def buffered_at(t: float) -> float:
        got = 0
        for at, size in arrivals:
            if at <= t:
                got = size
        return got / BYTES_PER_SECOND

    t, played, playing = 0.0, 0.0, False
    first_sound, stalls, stalled_time, step = None, 0, 0.0, 0.01
    threshold = EXO_START
    while played < total - 1e-6 and t < 300:
        ahead = buffered_at(t) - played
        if not playing:
            if ahead >= min(threshold, total - played) - 1e-9:
                playing = True
                if first_sound is None:
                    first_sound = t
            elif first_sound is not None:
                stalled_time += step
        else:
            if ahead <= 1e-9:
                playing = False
                stalls += 1
                threshold = EXO_REBUFFER
            else:
                played += min(step, ahead)
        t += step

    return {
        "parts": len(job.parts),
        "requests": len(job.parts),
        "policy": policy,
        "per_request_rate": rate,
        "first_sound_s": round(first_sound or 0, 2),
        "interruptions": stalls,
        "interrupted_s": round(stalled_time, 2),
        "finished_s": round(t, 2),
        "audio_s": round(total, 2),
        "original_audio_s": round(job.generated_seconds(), 2),
    }


async def main() -> None:
    rate = float(sys.argv[1]) if len(sys.argv) > 1 else 0.63
    first_audio = float(sys.argv[2]) if len(sys.argv) > 2 else 2.5
    for split in (1, 2, 3):
        print(await run(rate, first_audio, split), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
