"""
Piper TTS helper, run with the SEPARATE Piper environment
(C:\\Multimedia Labs\\local-ai\\piper-venv), not the backend's.

Reads a JSON job list on stdin: [{"voice": path.onnx, "text": str, "out": path.wav}, ...]
Loads each voice once (as a server would), synthesises every text as a
stream of chunks, and prints one JSON line per job with timings and the
process's CPU time and peak memory.
"""

import json
import sys
import time
import wave

from piper import PiperVoice


def peak_rss_mb() -> float:
    try:
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("a", ctypes.c_size_t), ("b", ctypes.c_size_t), ("c", ctypes.c_size_t),
                        ("d", ctypes.c_size_t), ("e", ctypes.c_size_t), ("f", ctypes.c_size_t)]
        k32 = ctypes.WinDLL("kernel32")
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi = ctypes.WinDLL("psapi")
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        c = Counters()
        c.cb = ctypes.sizeof(c)
        psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(c), c.cb)
        return c.PeakWorkingSetSize / 2 ** 20
    except Exception:  # noqa: BLE001
        return -1.0


def main() -> None:
    jobs = json.load(sys.stdin)
    voices: dict[str, PiperVoice] = {}
    for job in jobs:
        load_s = 0.0
        if job["voice"] not in voices:
            t = time.perf_counter()
            voices[job["voice"]] = PiperVoice.load(job["voice"])
            load_s = time.perf_counter() - t
        voice = voices[job["voice"]]
        cpu0, t0 = time.process_time(), time.perf_counter()
        first, frames, rate = None, b"", 22050
        for chunk in voice.synthesize(job["text"]):
            if first is None:
                first = time.perf_counter() - t0
            frames += chunk.audio_int16_bytes
            rate = chunk.sample_rate
        total = time.perf_counter() - t0
        with wave.open(job["out"], "wb") as f:
            f.setnchannels(1)
            f.setsampwidth(2)
            f.setframerate(rate)
            f.writeframes(frames)
        audio_s = len(frames) / 2 / rate
        print(json.dumps({"out": job["out"], "first_s": round(first or total, 3), "total_s": round(total, 3),
                          "audio_s": round(audio_s, 2), "load_s": round(load_s, 2),
                          "cpu_s": round(time.process_time() - cpu0, 3), "peak_mb": round(peak_rss_mb())}),
              flush=True)


if __name__ == "__main__":
    main()
