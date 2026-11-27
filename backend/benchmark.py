"""
Repeatable latency benchmark for the World Voice AI backend.

Runs the SAME inputs against the running backend so that
model or configuration changes can be compared fairly.

Usage (backend must be running on port 8000):

  .venv\\Scripts\\python benchmark.py tts  "النص هون" --lang ar-LB
  .venv\\Scripts\\python benchmark.py analyze path\\to\\photo.jpg "شو في قدامي؟"
  .venv\\Scripts\\python benchmark.py asr path\\to\\question.m4a
  .venv\\Scripts\\python benchmark.py suite benchmark_set\\

A suite folder may contain:
  *.jpg / *.png        test photos
  questions.txt        one question per line
  *.m4a / *.wav        recorded questions
  expected.txt         optional "file.m4a<TAB>expected transcript"

Results are printed and appended to benchmark_results.jsonl.
"""

import argparse
import base64
import json
import mimetypes
import statistics
import sys
import time

from datetime import datetime, timezone
from pathlib import Path

import httpx

import config


BASE_URL = "http://127.0.0.1:8000"
RESULTS_FILE = Path(__file__).with_name("benchmark_results.jsonl")


def _headers() -> dict[str, str]:
    return {"X-WV-Token": config.ACCESS_TOKEN} if config.ACCESS_TOKEN else {}


def _record(kind: str, data: dict) -> None:
    data = {
        "kind": kind,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "vision_model": config.VISION_MODEL,
        "tts_model": config.TTS_MODEL,
        "asr_prompt": config.WHISPER_PROMPT_MODE,
        **data,
    }
    with RESULTS_FILE.open("a", encoding="utf-8") as file:
        file.write(json.dumps(data, ensure_ascii=False) + "\n")


# ========================================
# TTS STREAM
# ========================================

def bench_tts(text: str, language: str, save: Path | None = None) -> dict:
    """
    Simulates the phone: plays chunks in order as soon as
    they arrive and counts every moment where playback
    would have had to wait (an "underrun").
    """

    started = time.perf_counter()
    first_audio = None
    play_clock = None      # when the current audio ends
    underruns = 0
    waited = 0.0
    chunks = []
    wav_parts = []

    with httpx.Client(timeout=120) as client:
        with client.stream(
            "POST",
            f"{BASE_URL}/tts/stream",
            json={"text": text, "language": language},
            headers=_headers(),
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line.strip():
                    continue
                message = json.loads(line)
                now = time.perf_counter() - started

                if message["type"] == "error":
                    raise RuntimeError(f"TTS error: {message}")

                if message["type"] != "audio":
                    continue

                duration = message["duration"]
                chunks.append({"index": message["index"], "at": round(now, 2),
                               "duration": round(duration, 2)})
                if save:
                    wav_parts.append(base64.b64decode(message["data"]))

                if first_audio is None:
                    first_audio = now
                    play_clock = now + duration
                else:
                    if now > play_clock:
                        underruns += 1
                        waited += now - play_clock
                        play_clock = now + duration
                    else:
                        play_clock += duration

    total = time.perf_counter() - started
    audio = sum(c["duration"] for c in chunks)

    result = {
        "chars": len(text),
        "language": language,
        "chunks": len(chunks),
        "first_audio_s": round(first_audio or 0, 2),
        "total_generation_s": round(total, 2),
        "audio_s": round(audio, 2),
        "underruns": underruns,
        "silence_waiting_s": round(waited, 2),
        "chunk_arrivals": chunks,
    }

    if save and wav_parts:
        save.mkdir(parents=True, exist_ok=True)
        for index, part in enumerate(wav_parts):
            (save / f"chunk_{index}.wav").write_bytes(part)

    return result


# ========================================
# ANALYZE
# ========================================

def bench_analyze(image: Path, question: str) -> dict:
    mime = mimetypes.guess_type(image.name)[0] or "image/jpeg"
    started = time.perf_counter()
    with httpx.Client(timeout=90) as client:
        response = client.post(
            f"{BASE_URL}/analyze",
            files={"image": (image.name, image.read_bytes(), mime)},
            data={"question": question},
            headers=_headers(),
        )
    round_trip = time.perf_counter() - started
    response.raise_for_status()
    data = response.json()
    return {
        "image": image.name,
        "image_kb": image.stat().st_size // 1024,
        "question": question,
        "round_trip_s": round(round_trip, 2),
        "server_timings": data.get("timings"),
        "answer_language": data.get("answer_language"),
        "answer_chars": len(data.get("answer", "")),
        "answer": data.get("answer"),
    }


# ========================================
# SPEECH RECOGNITION
# ========================================

def bench_asr(audio: Path, expected: str | None = None) -> dict:
    mime = mimetypes.guess_type(audio.name)[0] or "audio/mp4"
    if audio.suffix.lower() == ".m4a":
        mime = "audio/mp4"
    started = time.perf_counter()
    with httpx.Client(timeout=60) as client:
        response = client.post(
            f"{BASE_URL}/transcribe",
            files={"audio": (audio.name, audio.read_bytes(), mime)},
            headers=_headers(),
        )
    round_trip = time.perf_counter() - started
    response.raise_for_status()
    data = response.json()
    result = {
        "audio": audio.name,
        "audio_kb": audio.stat().st_size // 1024,
        "round_trip_s": round(round_trip, 2),
        "transcript": data.get("transcript"),
        "detected_language": data.get("detected_language"),
        "server_timings": data.get("timings"),
    }
    if expected:
        result["expected"] = expected
        result["exact_match"] = _normalise(expected) == _normalise(data.get("transcript", ""))
    return result


def _normalise(text: str) -> str:
    import re
    return re.sub(r"[\s\.,،؟?!]+", " ", text).strip().lower()


# ========================================
# CLI
# ========================================

def _print(result: dict) -> None:
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    tts = sub.add_parser("tts")
    tts.add_argument("text")
    tts.add_argument("--lang", default="ar-LB")
    tts.add_argument("--save", type=Path)

    analyze = sub.add_parser("analyze")
    analyze.add_argument("image", type=Path)
    analyze.add_argument("question")

    asr = sub.add_parser("asr")
    asr.add_argument("audio", type=Path)
    asr.add_argument("--expected")

    suite = sub.add_parser("suite")
    suite.add_argument("folder", type=Path)

    args = parser.parse_args()

    if args.command == "tts":
        result = bench_tts(args.text, args.lang, args.save)
        _record("tts", result)
        _print(result)

    elif args.command == "analyze":
        result = bench_analyze(args.image, args.question)
        _record("analyze", result)
        _print(result)

    elif args.command == "asr":
        result = bench_asr(args.audio, args.expected)
        _record("asr", result)
        _print(result)

    elif args.command == "suite":
        folder: Path = args.folder
        questions = []
        if (folder / "questions.txt").exists():
            questions = [q.strip() for q in
                         (folder / "questions.txt").read_text(encoding="utf-8").splitlines()
                         if q.strip()]
        expected = {}
        if (folder / "expected.txt").exists():
            for row in (folder / "expected.txt").read_text(encoding="utf-8").splitlines():
                if "\t" in row:
                    name, text = row.split("\t", 1)
                    expected[name.strip()] = text.strip()

        images = sorted(p for p in folder.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"})
        audios = sorted(p for p in folder.iterdir() if p.suffix.lower() in {".m4a", ".wav", ".mp3", ".ogg"})

        gemini_times = []
        for image in images:
            for question in questions or ["Describe what is in front of me."]:
                result = bench_analyze(image, question)
                _record("analyze", result)
                gemini_times.append(result["server_timings"]["gemini"])
                print(f"{image.name} | {result['round_trip_s']} s | {question[:40]}")

        asr_correct = 0
        for audio in audios:
            result = bench_asr(audio, expected.get(audio.name))
            _record("asr", result)
            asr_correct += int(bool(result.get("exact_match")))
            print(f"{audio.name} | {result['round_trip_s']} s | {result['transcript']}")

        if gemini_times:
            print(f"\nGemini median {statistics.median(gemini_times):.2f} s "
                  f"over {len(gemini_times)} requests")
        if expected:
            print(f"ASR exact matches: {asr_correct}/{len(expected)}")

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
