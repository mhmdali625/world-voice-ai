"""
Local AI sidecar: speech recognition (faster-whisper, CPU int8) and speech
synthesis (Piper) kept LOADED, for when the cloud is unavailable.

Runs with the separate local environment (the backend's packages stay
unchanged):
    "C:\\Multimedia Labs\\local-ai\\piper-venv\\Scripts\\python.exe" local_sidecar.py

    GET  /health
    POST /asr?language=xx      body = audio file (wav / m4a / webm)  -> JSON {text, language, probability, seconds}
    POST /tts                  JSON {text, language}                  -> raw 16-bit mono PCM, header X-Sample-Rate

Only listens on 127.0.0.1 (the backend calls it; nothing else can).
"""

import json
import os
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(os.getenv("WV_LOCAL_AI_DIR", r"C:\Multimedia Labs\local-ai"))
PORT = int(os.getenv("WV_SIDECAR_PORT", "8092"))
ASR_MODEL = os.getenv("WV_LOCAL_ASR_MODEL", "base")            # faster-whisper size or a local path
ASR_THREADS = int(os.getenv("WV_LOCAL_ASR_THREADS", "4"))
PIPER_VOICES = {
    "ar-LB": "ar_JO-kareem-medium", "ar": "ar_JO-kareem-medium",
    "fr": "fr_FR-siwis-medium", "en": "en_US-lessac-medium",
}

_lock = threading.Lock()
_asr = None
_voices: dict = {}


def asr_model():
    global _asr
    with _lock:
        if _asr is None:
            from faster_whisper import WhisperModel
            _asr = WhisperModel(ASR_MODEL, device="cpu", compute_type="int8", cpu_threads=ASR_THREADS,
                                download_root=str(ROOT / "whisper-models"))
    return _asr


def voice(language: str):
    name = PIPER_VOICES.get(language, PIPER_VOICES["en"])
    with _lock:
        if name not in _voices:
            from piper import PiperVoice
            _voices[name] = PiperVoice.load(str(ROOT / "piper-voices" / f"{name}.onnx"))
    return _voices[name]


def decode_16k(data: bytes):
    """
    Any audio file (wav, m4a from the phone, webm) -> float32 mono 16 kHz.
    Decoded here with PyAV's current API: faster-whisper 1.2.1's own decoder
    passes an option the installed PyAV no longer accepts.
    """
    import io

    import av
    import numpy as np
    chunks = []
    with av.open(io.BytesIO(data)) as container:
        resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
        for frame in container.decode(audio=0):
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray().reshape(-1))
        for out in resampler.resample(None):
            chunks.append(out.to_ndarray().reshape(-1))
    pcm = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.int16)
    return pcm.astype(np.float32) / 32768.0


# Natural Lebanese prompt: helps Whisper with dialect words (same idea as the cloud path).
ASR_PROMPT = "شو في قدامي؟ What is in front of me? Qu'est-ce qu'il y a devant moi ?"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:   # quiet
        pass

    def _json(self, code: int, data: dict) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if urlparse(self.path).path == "/health":
            self._json(200, {"status": "ok", "asr_loaded": _asr is not None, "voices_loaded": sorted(_voices)})
        else:
            self._json(404, {"detail": "not found"})

    def do_POST(self) -> None:
        url = urlparse(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        try:
            if url.path == "/asr":
                language = (parse_qs(url.query).get("language") or [None])[0] or None
                t = time.perf_counter()
                audio = decode_16k(body)
                segments, info = asr_model().transcribe(audio, language=language, beam_size=1,
                                                        vad_filter=True, initial_prompt=ASR_PROMPT)
                text = " ".join(seg.text.strip() for seg in segments).strip()
                self._json(200, {"text": text, "language": info.language,
                                 "probability": round(info.language_probability, 3),
                                 "seconds": round(time.perf_counter() - t, 3)})
            elif url.path == "/tts":
                data = json.loads(body)
                pcm, rate = b"", 22050
                for chunk in voice(data.get("language", "en")).synthesize(data["text"]):
                    pcm += chunk.audio_int16_bytes
                    rate = chunk.sample_rate
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("X-Sample-Rate", str(rate))
                self.send_header("Content-Length", str(len(pcm)))
                self.end_headers()
                self.wfile.write(pcm)
            else:
                self._json(404, {"detail": "not found"})
        except Exception as error:  # noqa: BLE001
            self._json(500, {"detail": f"{type(error).__name__}: {error}"})


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"[SIDECAR] local ASR + TTS on http://127.0.0.1:{PORT} (ASR model: {ASR_MODEL})", flush=True)
    if os.getenv("WV_SIDECAR_PRELOAD", "1") == "1":      # load now: the first question is not slower
        threading.Thread(target=lambda: (asr_model(), voice("ar-LB"), voice("en"), voice("fr")), daemon=True).start()
    server.serve_forever()


if __name__ == "__main__":
    main()
