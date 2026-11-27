import asyncio
import secrets
import time
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)

from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from google import genai
from google.genai import errors, types

from pydantic import BaseModel, Field, ValidationError

import config

import image_storage
from image_storage import save_blurred_photo

from photo_history import router as photo_history_router

from tts_service import (
    close_http_client as close_tts_client,
    router as tts_router,
)

from whisper_service import (
    close_groq_client,
    router as whisper_router,
)

from speech_jobs import (
    create_job as create_speech_job,
    job_urls,
    router as speech_jobs_router,
)

from stream_service import hub as stream_hub, router as stream_router

from memory_service import memory, router as memory_router

import assistant_service
import local_ai
import network
import vision

from quota import budget


# ========================================
# CONFIGURATION
# ========================================
#
# Keys and model names live in config.py (loaded from
# backend/.env). The model can be switched for
# benchmarking with WV_VISION_MODEL.

MODEL_NAME = config.VISION_MODEL

MAX_IMAGE_BYTES = 8 * 1024 * 1024

# Question audio accepted by /analyze, mapped to the MIME
# types Gemini understands (phone recordings are .m4a).
QUESTION_AUDIO_TYPES = {
    "audio/mp4": "audio/mp4",
    "audio/m4a": "audio/mp4",
    "audio/x-m4a": "audio/mp4",
    "audio/aac": "audio/aac",
    "audio/wav": "audio/wav",
    "audio/x-wav": "audio/wav",
    "audio/mpeg": "audio/mp3",
    "audio/mp3": "audio/mp3",
    "audio/ogg": "audio/ogg",
    "audio/flac": "audio/flac",
    "audio/webm": "audio/webm",
}

MAX_QUESTION_AUDIO_BYTES = 6 * 1024 * 1024


async def read_question_audio(upload: UploadFile):
    """Return a Gemini audio Part, or None (question then uses text only)."""
    try:
        mime = (upload.content_type or "").split(";")[0].strip().lower()
        data = await upload.read(MAX_QUESTION_AUDIO_BYTES + 1)
    finally:
        await upload.close()

    gemini_mime = QUESTION_AUDIO_TYPES.get(mime)
    if not data or len(data) > MAX_QUESTION_AUDIO_BYTES or gemini_mime is None:
        print(f"[AI] Question audio ignored (type={mime}, {len(data)} bytes)", flush=True)
        return None
    return types.Part.from_bytes(data=data, mime_type=gemini_mime)


# Whisper language names -> wording for Gemini.
LANGUAGE_HINTS = {
    "english": "English",
    "en": "English",
    "french": "French",
    "fr": "French",
    "arabic": "Arabic (often Lebanese dialect)",
    "ar": "Arabic (often Lebanese dialect)",
    "mixed": "a mix of Lebanese Arabic and English; answer in the dominant language",
}


# ========================================
# STRUCTURED AI RESPONSE
# ========================================

class VisualAnswer(BaseModel):

    answer: str = Field(
        description=(
            "The natural spoken answer to the user's "
            "question about the camera image."
        )
    )

    answer_language: Literal[
        "en",
        "ar",
        "ar-LB",
        "fr",
        "und",
    ] = Field(
        description=(
            "The dominant language of the generated answer. "
            "Use en for English, ar for general Arabic, "
            "ar-LB for Lebanese Arabic, fr for French, "
            "and und for unknown or unsupported languages."
        )
    )

    question_heard: str = Field(
        default="",
        description=(
            "Only when the user's spoken question audio is attached: "
            "a verbatim transcript of that audio, every word in the "
            "language and script it was spoken in (Arabic words in "
            "Arabic script, English and French words in Latin script). "
            "Never translate and never omit parts. Empty if no audio."
        ),
    )


# ========================================
# AI BEHAVIOR AND LANGUAGE UNDERSTANDING
# ========================================

AI_SYSTEM_INSTRUCTION = """
You are World Voice AI, a helpful visual
assistant designed for blind and visually
impaired people.

Your task is to understand the user's
spoken question and answer it using the
provided camera image.

IMPORTANT CONTEXT:

The user's question is produced by an
automatic speech recognition system.

This transcription may contain mistakes,
missing words, unusual spelling, or
incorrectly recognized sounds.

The user may speak English, Lebanese
Arabic, Modern Standard Arabic, French,
or a mixture of these languages.


1. UNDERSTANDING SPEECH

Interpret the user's likely meaning
rather than relying only on the literal
words in the transcription.

Recognize common Lebanese Arabic
expressions such as:

"شو في قدامي؟"
"What is in front of me?"

"خبرني شو شايف"
"Tell me what you see."

"وصفلي الصورة"
"Describe the image."

"شو هيدا؟"
"What is this?"

"في شي قدامي؟"
"Is there something in front of me?"

Also understand Arabic written in Latin
letters, sometimes called Arabizi.

Examples:

"sho fi edeme"
"What is in front of me?"

"shou hayda"
"What is this?"

"wasifli l soura"
"Describe the image."

"2elle sho fi edeme"
"Tell me what is in front of me."


2. MIXED LANGUAGE SUPPORT

The user may mix Arabic and English
words in the same sentence.

For example:

"3melle describe la hayda"

can mean:

"Describe this for me."

"sho fi edeme describe"

can mean:

"Describe what is in front of me."

The words describe, description, camera,
photo, image, object, and street may
appear within Arabic sentences.

Use the meaning of the whole sentence,
not just individual words.

Recognize these variations when there
is enough evidence from the question.

Do not assume that every unclear
sentence is a request to describe
the entire image.


3. SPEECH RECOGNITION ERRORS

Ignore harmless fillers and repeated
words when the user's intention is clear.

For example, if a noisy transcription
contains:

"ولا شو في قدامي ولا ما الذي تسكرايب له"

the likely intention may be:

"Describe what is in front of me."

But only make that interpretation
when supported by the actual words.

Never invent a specific user intention
when the transcription is too unclear.

If you cannot confidently understand
the question, ask one short, polite
clarifying question.


4. IMAGE UNDERSTANDING

Use the actual image as the evidence
for your answer.

Do not invent objects, people, text,
colors, or events that are not visible.

If an object is unclear, describe
the uncertainty honestly.

If the image is blurry, dark, or
insufficient for the requested task,
explain that briefly.

Do not invent details based solely
on what the user asked.


5. ACCESSIBILITY

The person will usually hear your
answer through text-to-speech.

Use natural, simple sentences.

Match the length of the answer to the
question:

- A simple question ("what is this?",
  "what colour is it?") gets a short,
  direct answer of one or two sentences.
- A request for a description, a
  comparison, reading visible text, or
  explaining relationships between objects
  gets as much detail as is genuinely
  useful, organised from most to least
  important.
- Never pad an answer, and never cut
  important information, uncertainty or
  safety limitations to make it shorter.

Put the most important information in the
first sentence, because the user starts
hearing the answer before it is finished.

When asked to read text, read the relevant
visible text exactly, and say clearly if
part of it is unreadable.

Avoid unnecessary introductions.

Do not use markdown formatting,
bullet points, tables, or emojis.

For a general description request,
mention the most important visible
objects first.

Where visually supported, describe
their approximate positions using
terms such as left, right, center,
foreground, and background.

Do not state precise real-world
distances unless reliably established.


6. LANGUAGE OF THE ANSWER

Respond in the language the user
actually SPOKE. Decide it from the
WORDS and GRAMMAR of the question,
never from the accent: English spoken
with an Arabic or French accent is
English; French with an Arabic accent
is French. Pronunciation may be
imperfect; that does not change the
language.

English question: answer ONLY in
English. French question: answer ONLY
in French. Never add Arabic words to an
English or French answer, and never
answer an English or French question
in Arabic.

Formal Arabic / Modern Standard Arabic
(for example ماذا، هل، الذي، أمامي،
يوجد، ما هو): answer in clear Modern
Standard Arabic, NOT in Lebanese
dialect.

Lebanese Arabic (for example شو، في،
هيدا، قدّامي، عالـ، مرقنا، فيك):
answer in natural Lebanese Arabic.

Genuinely mixed questions (two
languages in one sentence): answer in
the language of the sentence's grammar
and keep the foreign words the user
used themselves. Never add a language
the user did not use.

Text read from a sign or screen is
quoted exactly as written (CAFE stays
CAFE, مخرج stays مخرج). Quoting it is
not mixing languages.

Do not automatically switch to English
because the transcript contains an
English word such as "describe".

If the user's language is genuinely
uncertain, use simple, clear language
appropriate to the available context.

When answering in Arabic or Lebanese
Arabic, write the answer in Arabic
script rather than Arabizi.

This improves compatibility with
Arabic text-to-speech engines.


7. SAFETY

You are helping visually impaired users,
so accuracy and uncertainty matter.

Never guarantee that an environment,
road, crossing, path, or obstacle
is safe.

Do not tell someone to cross a road
or walk into an area based only on
one camera image.

A single image cannot establish
whether moving vehicles, people,
or obstacles are approaching.

If the question involves navigation
or immediate physical safety,
describe only what is visibly
supported and explain relevant
limitations briefly.

Never present guesses as facts.


8. RESPONSE RULES

Answer the user's intended question.

Do not repeat the transcription unless
the user specifically requests it.

The question may start with a line such as
"[Speech recognition: the user spoke English.]".
This states the language the user actually
spoke. Answer in that language (for mixed
speech, the dominant language). Never mention
this line in the answer.

Do not mention Whisper, Groq,
speech recognition, or Gemini unless
the user asks about them.

If you understood the question,
provide the answer directly.

If the intended question remains
unclear, ask for clarification
rather than inventing an answer.

Keep your response suitable
for reading aloud.

10. SPOKEN QUESTION AUDIO

The user's original spoken question may be
attached as audio. If it is, the AUDIO is the
authoritative question: the text transcript
comes from automatic speech recognition and can
contain errors, translated words, or missing
parts of mixed-language speech (for example
"Can you tell me شو مكتوب عالباب؟" or
"شوف إذا الـ charger موصول بالـ laptop").

Understand the complete meaning of the audio,
including every language in it, and answer all
parts of the question.

Answer in the language the user spoke (rule 6:
English -> English, French -> French, formal
Arabic -> formal Arabic, Lebanese -> Lebanese).
In a mixed answer, keep the familiar foreign
words the user used (laptop, charger, screen,
document) as they are, so they are pronounced
naturally.

Follow the required response schema.

The answer language must describe
the generated answer itself, not
necessarily the original question.

For mixed-language answers, identify
the dominant language used in the
generated answer.

Do not include the language code
inside the spoken answer.


9. AUTHENTIC EVERYDAY LEBANESE ARABIC

When answering in Lebanese Arabic,
sound like a native Lebanese person
speaking naturally in everyday life.

Use conversational Lebanese vocabulary,
sentence structure and familiar
expressions.

Do not sound like a news presenter,
a translated textbook, or a robotic
assistant.

Use natural Lebanese alternatives
when appropriate.

For example:

Instead of:
"يوجد أمامك حاسوب محمول"

Say:
"في قدامك لابتوب"

Instead of:
"بجانب الحاسوب توجد بعض الأشياء"

Say:
"حدّ اللابتوب في كم غرض"

Instead of:
"على الجهة اليمنى"

Say:
"عاليمين"

Instead of:
"يبدو أن"

Say:
"مبيّن إنّو"

Instead of:
"لا أستطيع تحديد هذا الشيء بوضوح"

Say:
"مش واضح كتير شو هيدا"

Instead of:
"الآن"

Say:
"هلّق"

Instead of:
"هذا"

Say:
"هيدا"

Use familiar English loanwords naturally
when appropriate, such as laptop,
screen, keyboard, camera, and phone.

Common loanwords may be written in
Arabic script when this improves
natural spoken pronunciation.

Do not force slang into every sentence.

Do not repeatedly start every answer
with "ليك" or "إيه".

Avoid excessive fillers, exaggerated
friendliness, or unnecessary emotion.

Speak directly, naturally, and clearly.

Preserve all factual information,
uncertainty, and safety limitations.

These examples illustrate speaking style,
not facts about the current image.
Never invent objects from these examples.

For Lebanese Arabic answers, use the
language code ar-LB.

Write Arabic only in Arabic script,
never in Hebrew or any other script.


PRIVACY: NO FACE RECOGNITION

Never identify people. Do not say who
a person is, guess a name or identity,
or compare a face with anyone. You may
say that a person is present and where
(for example "a person is standing in
front of you, slightly to the left").
If asked who someone is, say that you
do not identify people.
"""


# ========================================
# FASTAPI APPLICATION
# ========================================

# ========================================
# SHARED GEMINI CLIENT
# ========================================
#
# Created once at startup and reused, so each request
# reuses an open HTTPS connection instead of paying the
# connection + TLS handshake cost every time.

gemini_client: genai.Client | None = None


async def keep_connections_warm() -> None:
    """
    Free metadata requests (no generation, no quota) keep the
    HTTPS connections to Google and Groq open, so a question
    asked after an idle minute does not pay DNS + TLS setup.
    """

    from tts_service import _client as tts_http_client
    from whisper_service import _client as groq_client

    while True:
        started = time.perf_counter()
        try:
            if gemini_client is not None:
                await gemini_client.aio.models.get(model=config.VISION_MODEL)
            if config.GEMINI_API_KEY:
                await tts_http_client().get(
                    "https://generativelanguage.googleapis.com/v1beta/models",
                    params={"pageSize": 1},
                    headers={"x-goog-api-key": config.GEMINI_API_KEY},
                )
            if config.GROQ_API_KEY:
                await groq_client().models.list()
            if (
                config.AZURE_KEEPALIVE
                and config.AZURE_SPEECH_KEY
                and config.AZURE_SPEECH_REGION
            ):
                # OFF by default: it generates no speech, but it could
                # not be verified that Azure never counts or bills these
                # requests. When enabled it keeps the TLS connection open
                # (measured first audio: 2.57 s cold -> 0.23 s warm).
                await tts_http_client().head(
                    f"https://{config.AZURE_SPEECH_REGION}.tts.speech.microsoft.com"
                    "/cognitiveservices/v1",
                    headers={"Ocp-Apim-Subscription-Key": config.AZURE_SPEECH_KEY},
                )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            print(f"[KEEPALIVE] {type(error).__name__}", flush=True)
        else:
            elapsed = time.perf_counter() - started
            if elapsed > 2:
                print(f"[KEEPALIVE] slow network: {elapsed:.1f} s", flush=True)

        await asyncio.sleep(max(10.0, config.KEEPALIVE_SECONDS))


@asynccontextmanager
async def lifespan(_: FastAPI):

    global gemini_client

    if config.GEMINI_API_KEY:
        gemini_client = genai.Client(
            api_key=config.GEMINI_API_KEY,
        )

    print(
        f"[CONFIG] Vision: {config.VISION_MODEL} "
        f"(thinking={config.VISION_THINKING_LEVEL or 'default'}) | "
        f"TTS: {config.TTS_MODEL}/{config.TTS_VOICE} "
        f"parallel={config.TTS_PARALLEL} | "
        f"ASR: {config.WHISPER_MODEL} prompt={config.WHISPER_PROMPT_MODE} | "
        f"token={'on' if config.ACCESS_TOKEN else 'off'} | "
        f"presentation mode={'ON' if config.PRESENTATION_MODE else 'off'} | "
        f"answer chain: {' -> '.join(r['name'] for r in vision.answer_chain())}",
        flush=True,
    )

    print(network.startup_banner(), flush=True)

    keepalive = asyncio.create_task(keep_connections_warm())

    # Photo Mode: blurred photos deleted after WV_PHOTO_RETENTION_MIN (30).
    async def photo_retention() -> None:
        while True:
            try:
                deleted = await asyncio.to_thread(image_storage.delete_old_photos)
                if deleted:
                    print(f"[PHOTOS] retention deleted {deleted} old blurred photo(s)", flush=True)
            except Exception as error:
                print(f"[PHOTOS] retention error: {type(error).__name__}", flush=True)
            await asyncio.sleep(60)

    photo_cleanup = asyncio.create_task(photo_retention())

    # Visual memory: live frames -> selection -> database.
    memory.start(gemini_client)
    stream_hub.callbacks.append(memory.on_frame)

    # Live assistant (/ask) shares the client and the tested helpers.
    assistant_service.configure(
        client=gemini_client,
        system_instruction=AI_SYSTEM_INSTRUCTION,
        language_hints=LANGUAGE_HINTS,
        read_question_audio=read_question_audio,
        audio_types=QUESTION_AUDIO_TYPES,
        create_speech_job=create_speech_job,
        job_urls=job_urls,
        # Offline last resort (start_local_ai.ps1): used only if no cloud model can answer.
        local_answer=local_ai.make_answer_provider(assistant_service.AssistantAnswer),
    )
    if local_ai.BACKGROUND:
        memory.local_analyze = local_ai.analyze_frame

    yield

    keepalive.cancel()
    photo_cleanup.cancel()

    await stream_hub.close()
    await memory.stop()

    await close_tts_client()
    await close_groq_client()

    if gemini_client is not None:
        try:
            await gemini_client.aio.aclose()
        except Exception:
            pass


app = FastAPI(
    title="World Voice AI Backend",

    description=(
        "AI visual analysis with multilingual "
        "question understanding, natural AI speech "
        "generation, answer language identification, "
        "and blurred image history"
    ),

    version="2.0.0",

    lifespan=lifespan,
)


# ========================================
# CORS FOR LOCAL REACT DASHBOARD
# ========================================

app.add_middleware(
    CORSMiddleware,

    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
        "http://localhost:5175",
        "http://127.0.0.1:5175",
    ],

    allow_methods=["GET", "POST", "DELETE"],

    allow_headers=["*"],
)


# ========================================
# PROTECT PHOTO HISTORY
# ========================================

# Only the laptop can access the saved
# photo history through these endpoints.
#
# Android can access /analyze and /tts.
#
# This is local-development protection,
# not production authentication.

@app.middleware("http")
async def protect_photo_history(
    request: Request,
    call_next,
):

    if request.url.path.startswith("/api/images"):

        client_host = (
            request.client.host
            if request.client
            else ""
        )

        if client_host not in {
            "127.0.0.1",
            "::1",
        }:

            return JSONResponse(
                status_code=403,

                content={
                    "detail": "Photo history is local-only."
                },
            )

    return await call_next(request)


# ========================================
# OPTIONAL LAN ACCESS TOKEN
# ========================================
#
# Enabled only when WV_ACCESS_TOKEN is set in .env.
# Prevents other devices on the same Wi-Fi from using
# the AI endpoints (and our free-tier quota).

# Every endpoint except /health requires the token when it is set:
# the phone sends it (X-WV-Token); the dashboard runs on the laptop
# itself (loopback), which is always allowed. This matters on shared
# networks (university Wi-Fi) and over Tailscale.
OPEN_PATHS = ("/health",)
LOOPBACK = {"127.0.0.1", "::1", "localhost"}


@app.middleware("http")
async def require_access_token(
    request: Request,
    call_next,
):

    if (
        config.ACCESS_TOKEN
        and request.method != "OPTIONS"
        and not request.url.path.startswith(OPEN_PATHS)
        and not (request.client and request.client.host in LOOPBACK)
    ):
        supplied = request.headers.get("x-wv-token", "")

        if not secrets.compare_digest(
            supplied,
            config.ACCESS_TOKEN,
        ):
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing or invalid access token."},
            )

    return await call_next(request)


# ========================================
# PHOTO HISTORY ROUTES
# ========================================

app.include_router(photo_history_router)
app.include_router(network.router)


# ========================================
# SPEECH RECOGNITION ROUTE (/transcribe)
# ========================================

app.include_router(whisper_router)


# ========================================
# NATURAL TEXT-TO-SPEECH ROUTES
# ========================================

app.include_router(tts_router)

# Continuous single-stream speech (/tts/jobs...).
app.include_router(speech_jobs_router)

# Live video: phone -> backend -> dashboard (/stream/...).
app.include_router(stream_router)

# Visual memory: timeline, search, Q&A history, alerts (/memory/...).
app.include_router(memory_router)

# Live assistant: answers from the current view + memory (/ask).
app.include_router(assistant_service.router)


# ========================================
# BASIC ROUTES
# ========================================

@app.get("/")
def home():

    return {
        "message": "World Voice AI Backend is running!"
    }


@app.get("/health")
def health():

    return {
        "status": "ok",
        "vision_model": config.VISION_MODEL,
        "tts_model": config.TTS_MODEL,
        "tts_voice": config.TTS_VOICE,
        "asr_model": config.WHISPER_MODEL,
        "gemini_configured": bool(config.GEMINI_API_KEY),
        "groq_configured": bool(config.GROQ_API_KEY),
    }


# ========================================
# GEMINI IMAGE ANALYSIS
# ========================================

@app.post("/analyze")
async def analyze(
    image: UploadFile = File(...),
    question: str = Form(...),
    speak: bool = Form(False),
    question_language: str = Form(""),
    question_audio: UploadFile | None = File(None),
):

    # Stage timings (seconds). Returned to the phone and
    # in a Server-Timing header, so every request shows
    # exactly where the time went.
    request_started = time.perf_counter()
    timings: dict[str, float] = {}

    def mark(stage: str, started: float) -> None:
        timings[stage] = round(time.perf_counter() - started, 3)


    # ====================================
    # VALIDATE QUESTION
    # ====================================

    question = question.strip()

    if not question or len(question) > 500:

        raise HTTPException(
            status_code=400,
            detail="Question must contain 1 to 500 characters.",
        )


    # ====================================
    # VALIDATE IMAGE TYPE
    # ====================================

    allowed_types = {
        "image/jpeg",
        "image/png",
        "image/webp",
    }

    content_type = (image.content_type or "").split(";")[0].strip().lower()

    if content_type not in allowed_types:

        raise HTTPException(
            status_code=400,
            detail="Only JPG, PNG and WebP are supported.",
        )


    # ====================================
    # READ IMAGE WITH SIZE LIMIT
    # ====================================

    stage = time.perf_counter()

    image_bytes = await image.read(MAX_IMAGE_BYTES + 1)

    await image.close()

    mark("read_upload", stage)

    if not image_bytes:

        raise HTTPException(
            status_code=400,
            detail="The uploaded image is empty.",
        )

    if len(image_bytes) > MAX_IMAGE_BYTES:

        raise HTTPException(
            status_code=413,
            detail="Image must be 8 MB or smaller.",
        )

    if gemini_client is None:

        raise HTTPException(
            status_code=503,
            detail="Gemini API key is not configured.",
        )


    # ====================================
    # BLUR + SAVE  (runs in parallel with Gemini)
    # ====================================
    #
    # Pillow is CPU-bound. Running it in a worker thread
    # keeps the server responsive and lets it overlap
    # with the Gemini request, instead of adding its
    # time before the request even starts. Only the
    # blurred copy is ever written to disk.

    async def blur_and_save() -> str:
        started = time.perf_counter()
        try:
            return await asyncio.to_thread(save_blurred_photo, image_bytes)
        finally:
            mark("blur_save", started)


    # ====================================
    # ASK GEMINI VISION
    # ====================================

    generation_config = types.GenerateContentConfig(
        system_instruction=AI_SYSTEM_INSTRUCTION,
        temperature=0.2,
        max_output_tokens=config.VISION_MAX_OUTPUT_TOKENS,
        response_mime_type="application/json",
        response_schema=VisualAnswer,
    )

    if config.VISION_THINKING_LEVEL in {"minimal", "low", "medium", "high"}:
        generation_config.thinking_config = types.ThinkingConfig(
            thinking_level=config.VISION_THINKING_LEVEL,
        )

    # Language hint from speech recognition (english, arabic,
    # french, mixed). It tells Gemini which language the user
    # SPOKE, so a short or ambiguous transcript cannot push
    # the answer into the wrong language.
    hint = LANGUAGE_HINTS.get(question_language.strip().lower())

    # Optional original question audio (same request, no extra
    # API call). Whisper decodes ONE language per recording, so
    # code-switched questions can come back translated or with
    # a part missing; Gemini hears the audio itself.
    audio_part = None
    if question_audio is not None:
        audio_part = await read_question_audio(question_audio)

    if audio_part is not None:
        # Measured (BENCHMARK_REPORT.md section 12): with the
        # transcript BEFORE the audio, Gemini dropped the English
        # opening of "Can you tell me + Lebanese" in 2/2 runs,
        # because Whisper's draft already contained its Arabic
        # translation. Audio FIRST + "do not trust the draft"
        # kept every part in 8/8 runs.
        prompt_text = (
            "Above is the user's spoken question. It is AUTHORITATIVE: "
            "transcribe ALL of it into question_heard, from the first word "
            "to the last, keeping each language, and answer all of it. "
            "Automatic speech-recognition draft (often wrong or translated "
            f"for mixed languages, do not trust it): {question}"
        )
        if hint:
            prompt_text += f"\n[Speech recognition guessed: {hint}.]"
    else:
        prompt_text = (
            f"[Speech recognition: the user spoke {hint}.]\n{question}"
            if hint else question
        )

    contents = []
    if audio_part is not None:
        contents.append(audio_part)
    contents.append(prompt_text)
    contents.append(
        types.Part.from_bytes(
            data=image_bytes,
            mime_type=content_type,
        )
    )

    async def one_request():
        # vision.generate: model chain + daily quota books. Overloaded,
        # exhausted or too slow models hand over to the next one.
        attempt = 0
        while True:
            try:
                response, model = await vision.generate(
                    gemini_client, contents, generation_config, "user",
                    config.VISION_ATTEMPT_TIMEOUT)
                timings["model"] = model
                return response
            except vision.NoCloudVision:
                # Every model busy at once (e.g. a 503 burst): one short retry.
                if attempt < 1:
                    attempt += 1
                    timings["retried_chain"] = attempt
                    await asyncio.sleep(1.5)
                    continue
                raise
            except errors.APIError as error:
                # If Gemini ever rejects the audio, answer from the
                # transcript instead of failing the whole question.
                if error.code != 400 or audio_part is None:
                    raise
                break
        print("[AI] Gemini rejected the question audio; retrying with text only", flush=True)
        timings["audio_rejected"] = 1
        response, model = await vision.generate(
            gemini_client, [question, contents[-1]], generation_config, "user",
            config.VISION_ATTEMPT_TIMEOUT)
        timings["model"] = model
        return response

    async def hedged_request():
        # Free-tier latency varies hugely for the SAME
        # request (measured 2.7 s to 20.8 s). If the first
        # request is slow, a second identical request is
        # sent and the first good answer wins. Same model,
        # same prompt: no loss of intelligence.
        first = asyncio.create_task(one_request())
        pending = {first}
        hedge_after = config.VISION_HEDGE_AFTER_SECONDS

        try:
            if hedge_after > 0:
                done, _ = await asyncio.wait(pending, timeout=hedge_after)
                if not done:
                    timings["hedged_at"] = round(hedge_after, 1)
                    print(
                        f"[AI] No answer after {hedge_after:.0f} s; "
                        "sending backup request",
                        flush=True,
                    )
                    pending.add(asyncio.create_task(one_request()))

            last_error: BaseException | None = None

            while pending:
                done, pending = await asyncio.wait(
                    pending,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in done:
                    if task.exception() is None:
                        if "hedged_at" in timings:
                            timings["winner"] = 1 if task is first else 2
                        return task.result()
                    last_error = task.exception()

            assert last_error is not None
            raise last_error

        finally:
            for task in pending:
                task.cancel()

    async def ask_gemini():
        started = time.perf_counter()
        try:
            # User question: pauses background frame analysis.
            async with budget.user_request():
                return await asyncio.wait_for(
                    hedged_request(),
                    timeout=config.VISION_TIMEOUT_SECONDS,
                )
        finally:
            mark("gemini", started)

    save_result, gemini_result = await asyncio.gather(
        blur_and_save(),
        ask_gemini(),
        return_exceptions=True,
    )


    # ====================================
    # HANDLE STORAGE RESULT
    # ====================================

    saved_filename: str | None = None

    if isinstance(save_result, ValueError):

        raise HTTPException(
            status_code=400,
            detail=str(save_result),
        ) from save_result

    if isinstance(save_result, BaseException):

        # A storage failure must not hide the answer
        # from a blind user: report it and continue.
        print(
            f"[IMAGE ERROR] {type(save_result).__name__}",
            flush=True,
        )

    else:

        saved_filename = save_result

        print(f"[IMAGE SAVED] {saved_filename}", flush=True)


    # ====================================
    # HANDLE GEMINI RESULT
    # ====================================

    if isinstance(gemini_result, vision.NoCloudVision):

        print(f"[AI ERROR] {gemini_result}", flush=True)
        raise HTTPException(
            status_code=503,
            detail="The AI service is busy or its daily quota is used up. Please try again shortly.",
        ) from gemini_result

    if isinstance(gemini_result, asyncio.TimeoutError):

        raise HTTPException(
            status_code=504,
            detail="Gemini took too long to answer.",
        ) from gemini_result

    if isinstance(gemini_result, errors.APIError):

        error = gemini_result

        print(f"[AI ERROR] Gemini HTTP {error.code}", flush=True)

        if error.code == 429:
            raise HTTPException(429, "Gemini quota reached.") from error

        if error.code == 404:
            raise HTTPException(502, "Gemini model is unavailable.") from error

        raise HTTPException(502, "Gemini API request failed.") from error

    if isinstance(gemini_result, BaseException):

        print(
            f"[AI ERROR] {type(gemini_result).__name__}: {gemini_result}",
            flush=True,
        )

        raise HTTPException(
            status_code=502,
            detail="Gemini request failed.",
        ) from gemini_result

    response = gemini_result


    # ====================================
    # VALIDATE GEMINI RESPONSE
    # ====================================

    raw_response = (response.text or "").strip()

    if not raw_response:

        raise HTTPException(
            status_code=502,
            detail="Gemini returned no answer.",
        )

    try:

        result = VisualAnswer.model_validate_json(raw_response)

    except ValidationError as error:

        finish = None
        try:
            finish = response.candidates[0].finish_reason
        except (AttributeError, IndexError, TypeError):
            pass

        print(
            f"[AI ERROR] Invalid structured response (finish={finish})",
            flush=True,
        )

        raise HTTPException(
            status_code=502,
            detail="Gemini returned an invalid structured answer.",
        ) from error

    answer = assistant_service.clean_script(result.answer.strip())

    answer_language = result.answer_language

    if not answer:

        raise HTTPException(
            status_code=502,
            detail="Gemini returned an empty answer.",
        )


    # ====================================
    # DIAGNOSTICS
    # ====================================

    usage = getattr(response, "usage_metadata", None)

    mark("total", request_started)

    print(
        f"[AI] {MODEL_NAME} | lang={answer_language} | "
        f"image={len(image_bytes) // 1024} KB | "
        f"answer={len(answer)} chars | "
        f"tokens in={getattr(usage, 'prompt_token_count', '?')} "
        f"out={getattr(usage, 'candidates_token_count', '?')} "
        f"thinking={getattr(usage, 'thoughts_token_count', '?')} | "
        f"timings={timings}",
        flush=True,
    )


    # ====================================
    # RETURN RESULT TO ANDROID
    # ====================================

    # ====================================
    # START SPEECH IMMEDIATELY (overlap)
    # ====================================
    #
    # Speech generation starts on the server the moment
    # the answer exists, instead of after the phone has
    # received the answer and sent a second request.

    speech = None

    if speak:
        job = create_speech_job(answer, answer_language)
        speech = job_urls(job)

    server_timing = ", ".join(
        f"{name};dur={seconds * 1000:.0f}"
        for name, seconds in timings.items()
    )

    return JSONResponse(
        content={
            "answer": answer,
            "answer_language": answer_language,
            "question_received": question,
            # What Gemini heard in the attached audio (verbatim,
            # all languages); empty when no audio was sent.
            "question_heard": (result.question_heard or "").strip()
            if audio_part is not None else "",
            "source": "gemini",
            "model": timings.get("model", MODEL_NAME),
            "saved_image": saved_filename,
            "image_blurred": saved_filename is not None,
            "timings": timings,
            "speech": speech,
        },
        headers={"Server-Timing": server_timing},
    )
