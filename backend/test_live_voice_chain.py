"""
Live integration test of the natural-voice chain (real Azure, real Gemini).

Starts real backend servers on free ports and checks, over HTTP:
  A  Rami: 8 consecutive answers (Lebanese / English / French / mixed,
     short and long): continuity (ExoPlayer model), replay, Range resume
  B  real chain gemini,azure: which voice answers and how fast
  C  forced deadline switch (Fola cannot be ready in 0.5 s) -> Rami
  D  Azure failing (bad key) -> clean fallback / clean error
  E  client disconnect mid-stream, reconnect -> full audio
  +  server logs scanned for tracebacks / errors

Usage: .venv\\Scripts\\python test_live_voice_chain.py
Quota: ~20 Azure requests (free F0); at most a few Gemini TTS requests
in B (only if today's Fola quota is available).
"""

import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
PY = str(HERE / ".venv" / "Scripts" / "python.exe")
RESULTS = []

TEXTS = [
    ("ar-LB", "short", "في قدامك كباية قهوة عالطاولة."),
    ("ar-LB", "long",
     "في قدامك laptop مفتوح عالطاولة، والشاشة شغّالة وعليها صفحة كتابة. حدّ الـ laptop عاليمين في كباية قهوة "
     "وتلفون أسود، وعالشمال في charger أبيض موصول. ورا الطاولة في شبّاك والضو جايي منّو، فالصورة واضحة منيحة. "
     "مش واضح إذا في شي مكتوب عالورقة، فيك تقرّب الكاميرا شوي؟"),
    ("en", "short", "This is a black phone."),
    ("en", "long",
     "In front of you there is an open laptop on a wooden table. To its right are a coffee mug and a black phone, "
     "and on the left a white charger is plugged in. Behind the table there is a window, so the picture is bright. "
     "I can't read the paper clearly; could you move the camera a little closer?"),
    ("fr", "short", "C'est un téléphone noir."),
    ("fr", "long",
     "Devant vous, il y a un ordinateur portable ouvert sur une table en bois. À droite, une tasse de café et un "
     "téléphone noir. Je ne peux pas lire la feuille ; pouvez-vous rapprocher la caméra ?"),
    ("ar-LB", "mixed", "شوف إذا الـ charger موصول بالـ laptop، والـ battery بعدا مليانة."),
    ("ar-LB", "mixed", "فيك تقرالي شو مكتوب عالـ document en français?"),
]


def record(section, name, ok, detail=""):
    RESULTS.append((section, name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  -> ' + detail) if detail else ''}", flush=True)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    def __init__(self, **env):
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.log = tempfile.NamedTemporaryFile("w+", suffix=".log", delete=False, encoding="utf-8")
        self.proc = subprocess.Popen(
            [PY, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(self.port)],
            cwd=HERE, stdout=self.log, stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONIOENCODING": "utf-8", **env},
        )
        for _ in range(80):
            try:
                httpx.get(self.base + "/health", timeout=1)
                return
            except httpx.HTTPError:
                time.sleep(0.25)
        raise RuntimeError("server did not start")

    def stop(self):
        self.proc.terminate()
        self.proc.wait(10)
        self.log.flush()
        return Path(self.log.name).read_text(encoding="utf-8", errors="replace")


def speak(client, base, text, language):
    """Exactly what the phone does; returns metrics."""
    t0 = time.perf_counter()
    job = client.post(base + "/tts/jobs", json={"text": text, "language": language}).json()
    while True:
        s = client.get(base + job["status_path"]).json()
        if s["error"] and not s["audio_seconds"]:
            return {"error": s["error"], "status": s}
        if s["ready_to_start"] or s["done"]:
            break
        time.sleep(0.35)
    opened = time.perf_counter() - t0
    arrivals, total = [], 0
    with client.stream("GET", base + job["audio_path"]) as r:
        r.raise_for_status()
        for chunk in r.iter_bytes():
            total += len(chunk)
            arrivals.append((time.perf_counter() - t0, max(0, total - 44)))
    audio = arrivals[-1][1] / 48000 if arrivals else 0

    def buffered(t):
        got = 0
        for at, size in arrivals:
            if at <= t:
                got = size
        return got / 48000

    t, played, playing, first, stalls, threshold = opened, 0.0, False, None, 0, 1.0
    while played < audio - 1e-6 and t < 400:
        ahead = buffered(t) - played
        if not playing:
            if ahead >= min(threshold, audio - played) - 1e-9:
                playing, first = True, (first if first is not None else t)
        elif ahead <= 1e-9:
            playing, stalls, threshold = False, stalls + 1, 2.0
        else:
            played += min(0.01, ahead)
        t += 0.01
    final = client.get(base + job["status_path"]).json()
    return {"job": job, "first_sound": first, "stalls": stalls, "audio": audio,
            "provider": final["provider"], "status": final}


def scan_log(section, text):
    bad = [l for l in text.splitlines()
           if "Traceback" in l or "ERROR" in l or "RuntimeError" in l or "never retrieved" in l]
    record(section, "server log: no tracebacks / errors", not bad, "; ".join(bad[:3])[:200])


def main():
    # ---------------- A: Rami, consecutive, all languages ----------------
    print("\n[A] Rami (Azure): 8 consecutive answers, all languages and lengths")
    srv = Server(WV_TTS_PROVIDERS="azure")
    try:
        with httpx.Client(timeout=120) as c:
            firsts = []
            for language, kind, text in TEXTS:
                m = speak(c, srv.base, text, language)
                if "error" in m:
                    record("A", f"{language} {kind}", False, str(m["error"]))
                    continue
                firsts.append(m["first_sound"])
                record("A", f"{language} {kind}: continuous", m["stalls"] == 0 and m["audio"] > 0.5,
                       f"first sound {m['first_sound']:.2f} s, {m['audio']:.1f} s audio, "
                       f"rate x{m['status']['generation_rate']}, interruptions {m['stalls']}")
            if firsts:
                # The user's target: first sound within 2 s of the answer.
                # Kept strict on purpose; a miss is reported, not hidden.
                ordered = sorted(firsts)
                record("A", "first sound within 2 s for every answer (user target)", max(firsts) <= 2.0,
                       f"min {ordered[0]:.2f} / median {ordered[len(ordered) // 2]:.2f} / max {ordered[-1]:.2f} s")
            last = m["job"]
            full = c.get(srv.base + last["audio_path"])
            t0 = time.perf_counter()
            again = c.get(srv.base + last["audio_path"])
            record("A", "cached replay identical and instant",
                   again.content == full.content, f"{time.perf_counter() - t0:.3f} s")
            r = c.get(srv.base + last["audio_path"], headers={"Range": "bytes=48044-"})
            record("A", "Range resume (206) after network drop",
                   r.status_code == 206 and r.content == full.content[48044:])

            # E: disconnect mid-stream, reconnect
            job = c.post(srv.base + "/tts/jobs", json={"text": TEXTS[1][2], "language": "ar-LB"}).json()
            while not c.get(srv.base + job["status_path"]).json()["ready_to_start"]:
                time.sleep(0.2)
            with c.stream("GET", srv.base + job["audio_path"]) as stream:
                for _ in stream.iter_bytes(2048):
                    break
            while not c.get(srv.base + job["status_path"]).json()["done"]:
                time.sleep(0.2)
            body = c.get(srv.base + job["audio_path"]).content
            st = c.get(srv.base + job["status_path"]).json()
            record("E", "disconnect mid-stream + reconnect: complete audio",
                   abs(len(body) - 44 - st["audio_seconds"] * 48000) < 4800 and not st["error"],
                   f"{st['audio_seconds']} s")
    finally:
        scan_log("A", srv.stop())

    # ---------------- B: real chain gemini,azure ----------------
    print("\n[B] Real chain gemini,azure (Fola first, Rami when needed)")
    srv = Server(WV_TTS_PROVIDERS="gemini,azure", WV_TTS_SWITCH_AFTER="4")
    try:
        with httpx.Client(timeout=120) as c:
            print("  quota:", c.get(srv.base + "/tts/quota").json()["providers"])
            for language, kind, text in (TEXTS[0], TEXTS[2], TEXTS[6]):
                m = speak(c, srv.base, text, language)
                ok = "error" not in m and m["stalls"] == 0 and m["first_sound"] <= 6.0
                record("B", f"{language} {kind}: answered by an approved voice, continuous, <= 6 s", ok,
                       str(m.get("error")) if "error" in m else
                       f"voice {m['provider']}, first sound {m['first_sound']:.2f} s, interruptions {m['stalls']}")
    finally:
        log = srv.stop()
        scan_log("B", log)
        switches = [l for l in log.splitlines() if "using next approved provider" in l or "TTS QUOTA" in l]
        for l in switches[:4]:
            print("   log:", l.strip()[:150])

    # ---------------- C: forced deadline switch ----------------
    print("\n[C] Forced switch: Fola cannot be ready in 0.5 s -> Rami before playback")
    srv = Server(WV_TTS_PROVIDERS="gemini,azure", WV_TTS_SWITCH_AFTER="0.5")
    try:
        with httpx.Client(timeout=120) as c:
            m = speak(c, srv.base, TEXTS[1][2], "ar-LB")
            record("C", "answer moved to Rami, single voice, continuous",
                   "error" not in m and m["provider"] == "azure" and m["stalls"] == 0,
                   str(m.get("error")) if "error" in m else
                   f"voice {m['provider']}, first sound {m['first_sound']:.2f} s, {m['audio']:.1f} s audio")
    finally:
        scan_log("C", srv.stop())

    # ---------------- D: Azure failure ----------------
    print("\n[D] Azure failing (bad key)")
    srv = Server(WV_TTS_PROVIDERS="azure,file", AZURE_SPEECH_KEY="invalid-test-key",
                 WV_TTS_TEST_WAV=str(HERE / "test_audio" / "fola_sample_ar-LB.wav"), WV_TTS_TEST_RATE="3")
    try:
        with httpx.Client(timeout=120) as c:
            m = speak(c, srv.base, TEXTS[0][2], "ar-LB")
            record("D", "bad Azure key -> same answer from next approved provider",
                   "error" not in m and m["provider"] == "file" and m["stalls"] == 0,
                   str(m.get("error")) if "error" in m else f"voice {m['provider']}")
    finally:
        scan_log("D", srv.stop())

    srv = Server(WV_TTS_PROVIDERS="azure", AZURE_SPEECH_KEY="invalid-test-key")
    try:
        with httpx.Client(timeout=120) as c:
            t0 = time.perf_counter()
            m = speak(c, srv.base, TEXTS[0][2], "ar-LB")
            record("D", "bad Azure key, no other voice -> clean error (no hang)",
                   "error" in m, f"{m.get('error')} in {time.perf_counter() - t0:.2f} s")
    finally:
        scan_log("D", srv.stop())

    passed = sum(r[2] for r in RESULTS)
    print(f"\n{passed}/{len(RESULTS)} live voice checks passed")
    sys.exit(0 if passed == len(RESULTS) else 1)


if __name__ == "__main__":
    main()
