"""
Generate short, soft audio cues ("earcons") for the app.

Earcons give a blind user instant, language-independent
feedback (listening started, stopped, working, done,
error) with zero network requests and zero quota. They
are generated mathematically here, so they are royalty
free and reproducible.

Run once:  .venv\\Scripts\\python generate_earcons.py
Output:    mobile/assets/sounds/*.wav
"""

import math
import struct
import wave
from pathlib import Path

RATE = 24000
OUT = Path(__file__).resolve().parent.parent / "mobile" / "assets" / "sounds"


def tone(freq: float, seconds: float, volume: float = 0.35) -> list[float]:
    samples = int(RATE * seconds)
    attack = int(RATE * 0.008)
    release = int(RATE * min(0.08, seconds / 2))
    out = []
    for i in range(samples):
        envelope = 1.0
        if i < attack:
            envelope = i / attack
        elif i > samples - release:
            envelope = (samples - i) / release
        # Fundamental + soft octave for a warm, non-harsh sound.
        value = math.sin(2 * math.pi * freq * i / RATE)
        value += 0.25 * math.sin(4 * math.pi * freq * i / RATE)
        out.append(volume * envelope * value / 1.25)
    return out


def silence(seconds: float) -> list[float]:
    return [0.0] * int(RATE * seconds)


def save(name: str, samples: list[float]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with wave.open(str(OUT / f"{name}.wav"), "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(RATE)
        file.writeframes(b"".join(
            struct.pack("<h", int(max(-1, min(1, s)) * 32767)) for s in samples
        ))
    print("wrote", OUT / f"{name}.wav")


if __name__ == "__main__":
    # Rising pair: "I'm listening now".
    save("listen_start", tone(660, 0.09) + silence(0.03) + tone(880, 0.12))
    # Falling pair: "recording stopped".
    save("listen_stop", tone(880, 0.09) + silence(0.03) + tone(660, 0.12))
    # Single soft tick: "working on it".
    save("processing", tone(520, 0.07, 0.25))
    # Bright three-note chime: "done / answer ready".
    save("success", tone(660, 0.08) + tone(880, 0.08) + tone(1175, 0.14))
    # Low double: "something went wrong".
    save("error", tone(330, 0.14) + silence(0.06) + tone(262, 0.2))
    # Camera shutter-like click.
    save("capture", tone(1400, 0.03, 0.3) + silence(0.02) + tone(900, 0.04, 0.25))
