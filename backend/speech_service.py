
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from google import genai
from google.genai import errors, types

load_dotenv(Path(__file__).with_name(".env"))

app = FastAPI(
    title="World Voice AI - Speech Recognition Test",
    version="1.0.1",
)

logger = logging.getLogger(__name__)

# This model already works with our Gemini key.
# It supports text, images, and audio.
MODEL_NAME = "gemini-3.5-flash-lite"

MAX_AUDIO_BYTES = 6 * 1024 * 1024

SUPPORTED_AUDIO = {
    "audio/mpeg": "audio/mp3",
    "audio/mp3": "audio/mp3",
    "audio/wav": "audio/wav",
    "audio/x-wav": "audio/wav",
    "audio/mp4": "audio/mp4",
    "audio/x-m4a": "audio/mp4",
    "audio/m4a": "audio/mp4",
    "audio/aac": "audio/aac",
    "audio/ogg": "audio/ogg",
    "audio/flac": "audio/flac",
}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/transcribe")
async def transcribe(
    audio: UploadFile = File(...)
):
    incoming_type = (
        audio.content_type or ""
    ).split(";")[0].strip().lower()

    mime_type = SUPPORTED_AUDIO.get(incoming_type)

    if not mime_type:
        raise HTTPException(
            status_code=400,
            detail="Unsupported audio format.",
        )

    audio_bytes = await audio.read(
        MAX_AUDIO_BYTES + 1
    )
    await audio.close()

    if not audio_bytes:
        raise HTTPException(
            status_code=400,
            detail="The audio recording is empty.",
        )

    if len(audio_bytes) > MAX_AUDIO_BYTES:
        raise HTTPException(
            status_code=413,
            detail="Audio recording must be 6 MB or smaller.",
        )

    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="Gemini API key is missing.",
        )

    # Ask Gemini to transcribe, not translate.
    prompt = (
        "Transcribe the spoken words in this audio accurately. "
        "The speaker may use Lebanese Arabic, English, French, "
        "or mix these languages in the same sentence. "
        "Preserve the original languages. "
        "Do not translate, summarize, or answer the question. "
        "Output only the recognized words. "
        "Do not invent words when speech is unclear. "
        "If there is no intelligible speech, return an empty response."
    )

    audio_part = types.Part.from_bytes(
        data=audio_bytes,
        mime_type=mime_type,
    )

    try:
        async with genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                api_version="v1beta"
            ),
        ).aio as client:

            response = await client.models.generate_content(
                model=MODEL_NAME,
                contents=[
                    prompt,
                    audio_part,
                ],
                config=types.GenerateContentConfig(
                    temperature=0.0,
                    max_output_tokens=512,
                ),
            )

        transcript = (response.text or "").strip()

    except errors.APIError as error:
        logger.warning(
            "Gemini transcription API error: %s",
            error,
        )

        if error.code == 429:
            raise HTTPException(
                status_code=429,
                detail="Gemini quota exceeded.",
            ) from error

        raise HTTPException(
            status_code=502,
            detail=(
                f"Gemini transcription failed "
                f"(API error {error.code})."
            ),
        ) from error

    except Exception as error:
        logger.exception(
            "Unexpected transcription failure"
        )
        raise HTTPException(
            status_code=502,
            detail="Unexpected transcription error. Check server logs.",
        ) from error

    if not transcript:
        raise HTTPException(
            status_code=422,
            detail=(
                "Could not understand the speech. "
                "Please record the question again."
            ),
        )

    return {
        "transcript": transcript,
        "source": "gemini",
        "model": MODEL_NAME,
    }
