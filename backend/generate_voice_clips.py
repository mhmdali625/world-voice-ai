"""
Pre-generate natural Fola announcement clips for the app.

WHY
---
Predictable announcements ("listening", "analyzing",
"connection failed") do not need live speech generation.
Generating them once and bundling them in the app gives:
  * instant playback (no network, works offline),
  * zero quota use at runtime,
  * the same approved Fola voice as the answers,
    instead of the robotic Android voice.

QUOTA-FRIENDLY
--------------
The free tier allows only 10 TTS requests per day, so
all phrases of one language are generated in ONE request
with pauses between them. The audio is then split at the
longest silences. If the number of pieces does not match
the number of phrases, nothing is written for that
language (no wrong clip can be produced).

Usage:
  .venv\\Scripts\\python generate_voice_clips.py            # all languages
  .venv\\Scripts\\python generate_voice_clips.py ar-LB      # one language
  .venv\\Scripts\\python generate_voice_clips.py --registry # only rebuild the TS registry

Output:
  mobile/assets/voice/<lang>/<key>.wav
  mobile/Services/voiceClips.generated.ts
"""

import asyncio
import json
import sys
import wave
from pathlib import Path

import tts_service as tts


PROJECT = Path(__file__).resolve().parent.parent
VOICE_DIR = PROJECT / "mobile" / "assets" / "voice"
PHRASES_FILE = VOICE_DIR / "phrases.json"
REGISTRY_FILE = PROJECT / "mobile" / "Services" / "voiceClips.generated.ts"

FRAME = int(tts.SAMPLE_RATE * 0.02)        # 20 ms in samples
QUIET = 300                                # mean |amplitude| for silence
PADDING = int(tts.SAMPLE_RATE * 0.06)      # keep 60 ms around speech


def _samples(pcm: bytes) -> memoryview:
    return memoryview(pcm).cast("h")


def _frame_energies(samples: memoryview) -> list[int]:
    energies = []
    for start in range(0, len(samples) - FRAME, FRAME):
        window = samples[start:start + FRAME]
        energies.append(sum(abs(s) for s in window) // FRAME)
    return energies


def split_on_pauses(pcm: bytes, count: int) -> list[bytes] | None:
    """Split speech into `count` clips at the longest pauses."""

    samples = _samples(pcm)
    energies = _frame_energies(samples)
    quiet = [e < QUIET for e in energies]

    # Collect silent runs: (length_in_frames, start_frame, end_frame).
    runs, start = [], None
    for index, is_quiet in enumerate(quiet + [False]):
        if is_quiet and start is None:
            start = index
        elif not is_quiet and start is not None:
            runs.append((index - start, start, index))
            start = None

    # Ignore leading and trailing silence.
    first_voice = next((i for i, q in enumerate(quiet) if not q), None)
    last_voice = len(quiet) - 1 - next((i for i, q in enumerate(reversed(quiet)) if not q), 0)
    if first_voice is None:
        return None
    inner = [r for r in runs if r[1] > first_voice and r[2] <= last_voice]

    if len(inner) < count - 1:
        return None

    gaps = sorted(sorted(inner, reverse=True)[:count - 1], key=lambda r: r[1])

    # The chosen gaps must be clearly longer than normal
    # pauses inside a phrase, otherwise the split is unsafe.
    shortest_gap = min((g[0] for g in gaps), default=99)
    next_best = sorted(inner, reverse=True)[count - 1][0] if len(inner) >= count else 0
    if count > 1 and shortest_gap <= next_best:
        return None

    bounds = [first_voice] + [x for g in gaps for x in (g[1], g[2])] + [last_voice + 1]
    clips = []
    for a, b in zip(bounds[0::2], bounds[1::2]):
        lo = max(0, a * FRAME - PADDING)
        hi = min(len(samples), b * FRAME + PADDING)
        clips.append(samples[lo:hi].tobytes())
    return clips


def write_wav(path: Path, pcm: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(tts.SAMPLE_RATE)
        file.writeframes(pcm)


async def generate_language(language: str, phrases: dict[str, str]) -> bool:
    keys = list(phrases)
    script = "\n\n".join(phrases[k] for k in keys)
    style_language = language if language in tts.STYLES else "und"

    print(f"[{language}] requesting {len(keys)} phrases in one request...")
    try:
        pcm = await tts._synthesize_once(script, style_language, False)  # noqa: SLF001
    except tts.TTSError as error:
        print(f"[{language}] FAILED: {error.status} {error.message}")
        return False

    clips = split_on_pauses(pcm, len(keys))
    if clips is None:
        debug = VOICE_DIR / f"_unsplit_{language}.wav"
        write_wav(debug, pcm)
        print(f"[{language}] could not split reliably; saved {debug.name} for inspection")
        return False

    for key, clip in zip(keys, clips):
        write_wav(VOICE_DIR / language / f"{key}.wav", clip)
        print(f"[{language}] {key}: {len(clip) / tts.BYTES_PER_SECOND:.2f} s")
    return True


def write_registry() -> None:
    """Write a TS map of the clips that actually exist."""

    lines = [
        "// AUTO-GENERATED by backend/generate_voice_clips.py. Do not edit.",
        "// Only clips that exist on disk are listed, so Metro",
        "// never tries to bundle a missing file.",
        "",
        "export const VOICE_CLIPS: Record<string, Record<string, number>> = {",
    ]
    for lang_dir in sorted(p for p in VOICE_DIR.iterdir() if p.is_dir()):
        files = sorted(lang_dir.glob("*.wav"))
        if not files:
            continue
        lines.append(f"  '{lang_dir.name}': {{")
        for file in files:
            lines.append(
                f"    {file.stem}: require('../assets/voice/{lang_dir.name}/{file.name}'),"
            )
        lines.append("  },")
    lines.append("};")
    REGISTRY_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("wrote", REGISTRY_FILE)


async def main() -> None:
    all_phrases = json.loads(PHRASES_FILE.read_text(encoding="utf-8"))

    if "--registry" not in sys.argv:
        languages = [a for a in sys.argv[1:] if not a.startswith("--")] or list(all_phrases)
        for language in languages:
            await generate_language(language, all_phrases[language])
        await tts.close_http_client()

    write_registry()


if __name__ == "__main__":
    asyncio.run(main())
