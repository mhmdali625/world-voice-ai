"""
Multilingual recognition validation on the Azure test set
(test_audio/azure_asr, made by make_azure_testset.py).

For every recording, both stages the app uses are tested:
  1. Whisper pipeline (/transcribe logic): transcript shown first
  2. Gemini hearing the question audio (/analyze): what is actually
     understood and answered (question_heard + answer language)

A recording PASSES when the final understood question (stage 2) keeps
every language and every key word, Lebanese words included. Stage 1
results are reported separately (Whisper decodes one language per
recording, a documented limitation).

Usage: .venv\\Scripts\\python test_multilingual_azure.py
Uses ~30 Groq requests (paced < 20/min) and ~15 Gemini vision requests.
"""

import asyncio
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

import config
import whisper_service as w

HERE = Path(__file__).resolve().parent
FOLDER = HERE / "test_audio" / "azure_asr"
IMAGE = HERE.parent / "dashboard" / "src" / "assets" / "hero.png"

ARABIC = re.compile(r"[؀-ۿ]")
LATIN = re.compile(r"[A-Za-zÀ-ÿ]{2,}")

# Equivalent spellings that mean the same spoken word.
ALTERNATIVES = {
    "عاليمين": ["عاليمين", "على اليمين", "عاليمن", "عا اليمين"],
    "هلق": ["هلق", "هلأ", "هلا", "هالق"],
    "الطاولة": ["الطاولة", "طاولة", "الطاوله"],
    "الباب": ["الباب", "باب"],
    "الكباية": ["الكباية", "كباية", "كبايه", "الكبايه"],
    "قدامي": ["قدامي", "قدّامي", "قبالي"],
    "قدامك": ["قدامك", "قدّامك", "قبالك"],
    "تقرب": ["تقرب", "تقرّب", "تقربي"],
}


def norm(text: str) -> str:
    text = re.sub(r"[ً-ْـ]", "", text.lower())
    for a, b in (("أ", "ا"), ("إ", "ا"), ("آ", "ا"), ("ى", "ي")):
        text = text.replace(a, b)
    return text


def keys_kept(text: str, keys: str) -> list[str]:
    missing = []
    t = norm(text)
    for key in keys.split("|"):
        options = ALTERNATIVES.get(key, [key])
        if not any(norm(o) in t for o in options):
            missing.append(key)
    return missing


def languages_kept(languages: str, text: str) -> bool:
    has_ar = bool(ARABIC.search(text))
    has_lat = len(LATIN.findall(text)) >= 1
    need_ar = "ar" in languages
    need_lat = "en" in languages or "fr" in languages
    return (has_ar or not need_ar) and (has_lat or not need_lat) and \
        (need_ar or not has_ar) and (need_lat or not has_lat)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def whisper_all(rows):
    config.ASR_HEDGE_AFTER_SECONDS = 0
    out = {}
    for name, *_ in rows:
        r = await w.transcribe_bytes((FOLDER / f"{name}.wav").read_bytes(), "audio/wav")
        out[name] = (r["transcript"], r["language_check"]["reason"])
        await asyncio.sleep(3.5)          # 2 Groq requests per file, < 20/min
    await w.close_groq_client()
    return out


def main() -> None:
    rows = [l.split("\t") for l in (FOLDER / "expected.tsv").read_text(encoding="utf-8").splitlines() if l.strip()]

    print("Stage 1: Whisper pipeline ...", flush=True)
    whisper = asyncio.run(whisper_all(rows))

    port = free_port()
    server = subprocess.Popen(
        [str(HERE / ".venv" / "Scripts" / "python.exe"), "-m", "uvicorn", "main:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=HERE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    base = f"http://127.0.0.1:{port}"
    for _ in range(80):
        try:
            httpx.get(base + "/health", timeout=1)
            break
        except httpx.HTTPError:
            time.sleep(0.25)

    image = IMAGE.read_bytes()
    passed = 0
    whisper_ok = 0
    gemini_times = []
    report = []
    print("Stage 2: Gemini hearing the question audio ...\n", flush=True)
    try:
        with httpx.Client(timeout=90) as c:
            for name, languages, expected, keys in rows:
                transcript, reason = whisper[name]
                w_ok = languages_kept(languages, transcript) and not keys_kept(transcript, keys)
                whisper_ok += w_ok
                lang_hint = "mixed" if "+" in languages else {"fr": "french", "en": "english"}.get(languages, "arabic")
                r = c.post(base + "/analyze",
                           files={"image": ("hero.png", image, "image/png"),
                                  "question_audio": (f"{name}.wav", (FOLDER / f"{name}.wav").read_bytes(), "audio/wav")},
                           data={"question": transcript, "question_language": lang_hint, "speak": "false"})
                d = r.json() if r.status_code == 200 else {}
                heard = d.get("question_heard", "")
                missing = keys_kept(heard, keys)
                lang_ok = languages_kept(languages, heard)
                answer_lang = d.get("answer_language")
                answer_lang_ok = {"fr": answer_lang == "fr", "ar": answer_lang in ("ar", "ar-LB")}.get(languages, True)
                ok = r.status_code == 200 and lang_ok and not missing and answer_lang_ok
                passed += ok
                if d.get("timings", {}).get("gemini"):
                    gemini_times.append(d["timings"]["gemini"])
                report.append((name, languages, ok, w_ok))
                print(f"{'PASS' if ok else 'FAIL'}  {name:18s} [{languages:8s}] answer={answer_lang}"
                      f"{'' if not missing else '  MISSING ' + str(missing)}{'' if lang_ok else '  LANGUAGE LOST'}\n"
                      f"        expected : {expected}\n"
                      f"        heard    : {heard}\n"
                      f"        whisper  : {'ok ' if w_ok else 'LIMIT'} {transcript}  [{reason}]", flush=True)
    finally:
        server.terminate()
        server.wait(10)

    gemini_times.sort()
    print(f"\nUnderstood question (Gemini, used for the answer): {passed}/{len(rows)} kept every language and key word")
    print(f"Whisper transcript alone (shown first):           {whisper_ok}/{len(rows)}")
    if gemini_times:
        print(f"Gemini analysis time with audio: median {gemini_times[len(gemini_times) // 2]:.2f} s, "
              f"max {gemini_times[-1]:.2f} s")
    sys.exit(0 if passed == len(rows) else 1)


if __name__ == "__main__":
    main()
