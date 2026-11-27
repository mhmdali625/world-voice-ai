"""
Voice comparison samples for the user's approval (no app setting changes).

Identical sentences spoken by: Fola (Gemini TTS, preferred today), Rami
(Azure ar-LB, the current backup, exactly as the app sends it: +15 %
rate, Lebanese loanwords), the matching Azure voices for English/French,
and Piper 1.8 (local, offline: Arabic ar_JO-kareem, French siwis, English
lessac). Measures time to first audio, total time, real-time factor, and
Piper's CPU/RAM. Writes voice_compare/final/index.html to listen.

Usage: .venv\\Scripts\\python make_voice_samples.py [--no-fola]
Quota: Fola 1 Gemini TTS request per sentence (skipped if exhausted); Azure ~600 characters.
"""

import asyncio
import json
import subprocess
import sys
import time
import wave
from html import escape
from pathlib import Path

import tts_providers
import tts_service

HERE = Path(__file__).resolve().parent
OUT = HERE / "voice_compare" / "final"
PIPER_PY = Path(r"C:\Multimedia Labs\local-ai\piper-venv\Scripts\python.exe")
PIPER_VOICES = Path(r"C:\Multimedia Labs\local-ai\piper-voices")

SENTENCES = [
    ("lb_door", "ar-LB", "في باب مسكّر قدّامك، لازم تفتحو قبل ما تمرق."),
    ("lb_laptop", "ar-LB", "اللابتوب عالطاولة عيمينك، حدّ الكباية، والشاحن تحتو."),
    ("lb_mixed", "ar-LB", "شوف إذا الـ charger موصول بالـ laptop، والـ écran بعدو مضوّي."),
    ("lb_warning", "ar-LB", "انتبه: في درج نازل قدّامك."),
    ("msa", "ar", "يوجد باب مغلق أمامك، ويجب فتحه قبل المرور."),
    ("en", "en", "There is a closed door ahead of you. You need to open it before you can pass."),
    ("fr", "fr", "Il y a une porte fermée devant vous. Il faut l'ouvrir avant de passer."),
    ("en_warning", "en", "Caution: stairs going down ahead of you."),
]
PIPER_FOR = {"ar-LB": "ar_JO-kareem-medium", "ar": "ar_JO-kareem-medium",
             "fr": "fr_FR-siwis-medium", "en": "en_US-lessac-medium"}
RATE = 24000                          # Fola and Azure stream 24 kHz 16-bit mono PCM


def write_wav(path: Path, pcm: bytes, rate: int = RATE) -> float:
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(pcm)
    return len(pcm) / 2 / rate


async def cloud(provider: str, name: str, language: str, text: str) -> dict:
    t0 = time.perf_counter()
    first, pcm = None, b""
    try:
        async for piece in tts_providers.stream_pcm(text, language, provider=provider):
            if first is None and piece:
                first = time.perf_counter() - t0
            pcm += piece
    except Exception as error:  # noqa: BLE001
        return {"error": f"{type(error).__name__}: {error}"[:120]}
    total = time.perf_counter() - t0
    path = OUT / f"{name}_{provider}.wav"
    audio = write_wav(path, pcm)
    return {"file": path.name, "first_s": round(first or total, 2), "total_s": round(total, 2),
            "audio_s": round(audio, 2)}


def piper(jobs: list[dict]) -> dict[str, dict]:
    if not PIPER_PY.exists():
        return {}
    run = subprocess.run([str(PIPER_PY), str(HERE / "piper_synth.py")], input=json.dumps(jobs),
                         capture_output=True, text=True, encoding="utf-8", timeout=600)
    results = {}
    for line in run.stdout.splitlines():
        data = json.loads(line)
        results[Path(data["out"]).name] = data
    if run.returncode:
        print(run.stderr[-500:])
    return results


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    fola = "--no-fola" not in sys.argv
    rows = []
    for name, language, text in SENTENCES:
        row = {"name": name, "language": language, "text": text}
        if fola and not all(tts_service.blocked_for(m) for m in tts_providers.config.TTS_MODELS):
            row["fola"] = await cloud("gemini", name, language, text)
        else:
            row["fola"] = {"error": "Fola daily quota used up (not generated)"}
        row["azure"] = await cloud("azure", name, language, text)
        rows.append(row)
        print(f"{name}: fola {row['fola'].get('first_s', row['fola'].get('error'))} | "
              f"azure {row['azure'].get('first_s', row['azure'].get('error'))}", flush=True)

    jobs = [{"voice": str(PIPER_VOICES / f"{PIPER_FOR[r['language']]}.onnx"), "text": r["text"],
             "out": str(OUT / f"{r['name']}_piper.wav")} for r in rows]
    for name, data in piper(jobs).items():
        row = next(r for r in rows if f"{r['name']}_piper.wav" == name)
        row["piper"] = {"file": name, **{k: data[k] for k in ("first_s", "total_s", "audio_s", "load_s",
                                                              "cpu_s", "peak_mb")}}
        print(f"{row['name']}: piper first {data['first_s']} s, total {data['total_s']} s, "
              f"audio {data['audio_s']} s, peak RAM {data['peak_mb']} MB", flush=True)

    voices = {"fola": "Fola (Gemini TTS) - preferred today",
              "azure": "Rami / Andrew / Henri (Azure) - backup today",
              "piper": "Piper 1.8 (local, offline) - kareem / lessac / siwis"}
    html = ["<!doctype html><meta charset='utf-8'><title>World Voice AI - voice comparison</title>",
            "<style>body{font-family:Segoe UI,Arial;margin:24px;background:#0b1220;color:#f8fafc}"
            "td,th{border:1px solid #334155;padding:8px;vertical-align:top}table{border-collapse:collapse}"
            "small{color:#94a3b8}</style>",
            "<h1>Voice comparison (same sentences)</h1><p>Listen and choose. Times: first audio / total "
            "generation / audio length.</p><table><tr><th>Sentence</th>"]
    html += [f"<th>{escape(v)}</th>" for v in voices.values()] + ["</tr>"]
    for r in rows:
        html.append(f"<tr><td dir='auto'><b>{escape(r['name'])}</b><br>{escape(r['text'])}</td>")
        for key in voices:
            d = r.get(key, {"error": "not generated"})
            if "file" in d:
                html.append(f"<td><audio controls src='{d['file']}'></audio><br><small>first {d['first_s']} s, "
                            f"total {d['total_s']} s, audio {d['audio_s']} s</small></td>")
            else:
                html.append(f"<td><small>{escape(d['error'])}</small></td>")
        html.append("</tr>")
    html.append("</table>")
    (OUT / "index.html").write_text("\n".join(html), encoding="utf-8")
    (OUT / "results.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nlisten: {OUT / 'index.html'}")


if __name__ == "__main__":
    asyncio.run(main())
