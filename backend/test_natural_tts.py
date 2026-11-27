import base64
import json
import os
import tempfile
import time

from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from dotenv import load_dotenv


# ========================================
# LOAD EXISTING API KEY
# ========================================

load_dotenv(Path(__file__).parent / ".env")

API_KEY = (
    os.getenv("GEMINI_API_KEY")
    or os.getenv("GOOGLE_API_KEY")
)

if not API_KEY:
    raise RuntimeError("Gemini API key was not found.")


# ========================================
# APPROVED TTS CONFIGURATION
# ========================================

MODEL = "gemini-3.8-flash-tts"

VOICE = "Fola"

API_URL = (
    "https://generativelanguage.googleapis.com"
    "/v1beta/interactions"
)


# ========================================
# APPROVED LEBANESE SPEAKING STYLE
# ========================================

LEBANESE_STYLE = """
Speak like a native Lebanese man in his early thirties.
Use a medium-low pitched voice, relaxed confidence,
everyday Lebanese pronunciation and natural
conversational rhythm.

Speak at a normal pace with subtle emotion and
a slightly more straightforward tone.

Speak in authentic everyday Lebanese dialect,
exactly as Lebanese friends speak to each other
in casual conversation.

Use natural Lebanese pronunciation, connected
words and spontaneous rhythm.

Do not pronounce words like a formal Arabic
news presenter.

Avoid exaggerated friendliness, theatrical
delivery, robotic intonation and formal Arabic.
""".strip()


# ========================================
# TEST TEXT
# ========================================

TEST_TEXT = (
    "ليك، قدامك في لابتوب مفتوح عالطاولة. "
    "حدّو في شوية أغراض، "
    "وعلى يمينك في مزهرية صغيرة. "
    "هيدا تقريباً كل شي مبين بالصورة."
)


# ========================================
# GENERATE NATURAL SPEECH
# ========================================

def generate_speech():

    print("[TTS] Starting natural speech generation")
    print(f"[TTS] Model: {MODEL}")
    print(f"[TTS] Voice: {VOICE}")
    print("[TTS] Language: Lebanese Arabic")

    start_time = time.perf_counter()

    payload = {
        "model": MODEL,

        "input": [
            {
                "type": "user_input",
                "content": [
                    {
                        "type": "text",
                        "text": TEST_TEXT,

                        "annotations": [
                            {
                                "type": "speech_metadata",
                                "style": LEBANESE_STYLE,
                            }
                        ],
                    }
                ],
            }
        ],

        "response_format": {
            "type": "audio"
        },

        "generation_config": {
            "speech_config": [
                {
                    "voice": VOICE
                }
            ]
        },
    }

    request = Request(
        API_URL,
        data=json.dumps(
            payload,
            ensure_ascii=False,
        ).encode("utf-8"),

        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": API_KEY,
        },

        method="POST",
    )

    try:

        with urlopen(request, timeout=120) as response:
            result = json.loads(
                response.read().decode("utf-8")
            )

    except HTTPError as error:

        print(f"[TTS ERROR] HTTP {error.code}")

        print(
            error.read().decode(
                "utf-8",
                errors="replace",
            )
        )

        raise

    # Find the generated audio.

    audio_blocks = []

    for step in result.get("steps", []):

        if step.get("type") != "model_output":
            continue

        for content in step.get("content", []):

            if (
                content.get("type") == "audio"
                and content.get("data")
            ):
                audio_blocks.append(content)

    if not audio_blocks:
        raise RuntimeError(
            "The API did not return audio."
        )

    audio_data = base64.b64decode(
        audio_blocks[-1]["data"]
    )

    # Validate the WAV format.

    if (
        audio_data[:4] != b"RIFF"
        or audio_data[8:12] != b"WAVE"
    ):
        raise RuntimeError(
            "The returned audio is not a WAV file."
        )

    # Save outside the Git repository.

    output_path = (
        Path(tempfile.gettempdir())
        / "world_voice_tts_test.wav"
    )

    output_path.write_bytes(audio_data)

    elapsed_time = (
        time.perf_counter() - start_time
    )

    print("[TTS] Audio generated successfully")
    print(f"[TTS] Generation time: {elapsed_time:.2f} seconds")
    print(f"[TTS] Audio size: {len(audio_data)} bytes")
    print(f"[TTS] Saved to: {output_path}")

    return output_path


# ========================================
# RUN INDEPENDENT TEST
# ========================================

if __name__ == "__main__":

    audio_path = generate_speech()

    print("[TTS] Opening audio player...")

    os.startfile(str(audio_path))