"""
Live assistant (spec steps 3-4: "Ask" and "Answer").

POST /ask  (multipart)
    question          speech-recognition transcript
    question_language Whisper's detected language (hint)
    question_audio    optional original recording (Gemini hears it)
    speak             start natural speech immediately (same as /analyze)

Evidence given to the vision-language model:
    * CURRENT VIEW : latest live frame from the phone stream
    * MEMORY       : stored frames retrieved by TIME (questions about the
                     past: "the door I just passed") and by MEANING
                     (embedding search), each with when it was seen and
                     the text/objects recorded for it
The model answers ONLY from these images, says roughly when something
was seen, and says "I'm not sure" when the evidence is weak.
Every question/answer is stored for the dashboard history.
"""

import asyncio
import io
import os
import json
import re
import time
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from google.genai import errors, types
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, Field

import config
import whisper_service
from hedge import hedged
from memory_service import memory
from quota import budget
import groq_vision
from neterrors import probably_offline
import quota
import vision
from stream_service import device_name, hub

router = APIRouter(tags=["Live Assistant"])

FRESH_FRAME_S = 4.0          # current view must be at most 4 s old
MAX_MEMORY_FRAMES = 4
RECENT_WINDOW_S = 300        # "just passed" = last 5 minutes
SEARCH_TIMEOUT_S = 1.5       # meaning search may never stall an answer
CURRENT_SCENE_S = 8.0        # frames younger than this ARE the current view, not memory
RETENTION_INDEX_S = 1800     # memory index covers the 30-minute retention
INDEX_LIMIT = 40             # newest 40 remembered frames (~40 tokens each)
STRONG_MATCH = 0.6           # meaning score that justifies attaching the image
MAX_AUDIO_BYTES = 6 * 1024 * 1024

# Words that signal a question about the PAST (en / fr / Lebanese & MSA).
PAST_HINTS = re.compile(
    r"\b(just|earlier|before|ago|passed|previous|was|were|did|saw|seen|last)\b"
    r"|tout à l'heure|avant|précédent|passé|était|vu\b|déjà"
    r"|قبل|مرقنا|مرقت|مرّينا|كان|كانت|شفت|شفنا|من شوي|هلّق مرق|السابق",
    re.IGNORECASE,
)

_deps: dict = {}


def configure(**deps) -> None:
    """Called by main.py with the shared client and helpers."""
    _deps.update(deps)


class AssistantAnswer(BaseModel):
    # Field ORDER is the order Gemini writes them: first what was heard,
    # then what is asked about (now or past), then the matching evidence,
    # and only then the answer. With the answer first, Lebanese past
    # questions were answered from the current view (bench_past_questions.py).
    question_heard: str = Field(default="", description=(
        "Only when question audio is attached: verbatim transcript in the original "
        "languages and scripts, never translated. Empty otherwise."))
    spoken_language: Literal["en", "fr", "ar", "ar-LB"] = Field(description=(
        "Language of the question's WORDS and grammar (not the accent): en, fr, "
        "ar = formal/Modern Standard Arabic, ar-LB = Lebanese/Levantine dialect. For a "
        "mixed sentence, the language of its grammar. The answer must use this language."))
    focus: str = Field(default="", description=(
        "3-8 English words: the thing the user asks about and NOW or PAST, "
        "e.g. 'text on the door, PAST' or 'sign in front, NOW'."))
    used: list[str] = Field(default_factory=list, description=(
        "Labels of what the answer is based on: 'CURRENT', 'MEMORY 2', or memory index "
        "scene labels such as 'S3'. For a PAST focus, the remembered scene showing that "
        "thing, not the current view. Empty if nothing was used."))
    answer: str = Field(description=(
        "The natural spoken answer to the question (never a repetition of the question)."))
    answer_language: Literal["en", "ar", "ar-LB", "fr", "und"] = Field(
        description="Language of the answer; must equal spoken_language.")
    evidence: Literal["strong", "weak", "none"] = Field(description=(
        "strong: the images clearly show the answer; weak: partly visible, unclear or "
        "uncertain; none: the images do not contain the answer."))


ASSISTANT_RULES = """

11. LIVE VIEW AND MEMORY

You may receive a CURRENT VIEW image (what the camera sees now) and
MEMORY images (frames seen earlier, each with how many seconds ago it
was seen and the text/objects recorded for it).

Answer ONLY from what these images show. For questions about the past
("what was written on the door I just passed?"), use the MEMORY images
and say roughly when it was seen (for example "about a minute ago").
A question in the past tense ("was", "était", "كان") refers to the
MEMORY images, not to the current view, unless the user says "now".
A question about the present ("what is in front of me", "what does the
sign say", "شو في قدامي") is answered from the CURRENT VIEW only; memory
is never used to describe what is in front of the user now. If there is
no current view, say the camera view is not available.
Match the object the user names (a door, a sign, a screen) to the
memory image whose description shows that object.

When the user asks WHERE something is (an exit, a door, a sign) and it
is not in the CURRENT VIEW, do not describe an unrelated object instead.
Look for it in the MEMORY images and the MEMORY INDEX: if it was seen,
say that it is not in view now and when it was seen (for example "I
don't see an exit now; you passed an EXIT sign about a minute ago").
If it was never seen, say you have not seen it.

If the images do not clearly contain the answer, say clearly that you
are not sure (in the user's language: "I'm not sure", "Je ne suis pas
sûr", "مش متأكد") and briefly say why. Never guess, and never say that
anything is safe.

Rate the evidence honestly: strong, weak or none.

Write Arabic only in Arabic script (never Hebrew or any other script),
and English and French in Latin script.

Never identify people (no face recognition): only say that a person is
present and where. If asked who someone is, say you do not identify people.

12. HELPING A BLIND USER (intent, not just objects)

The user cannot see. Understand what they want to DO and answer that,
in one to three short spoken sentences, most important first.

- "What is in front of me?" / "شو في قدامي؟": the important things
  directly ahead first (obstacles, doors, steps, people), then the rest.
- "Can I pass / walk forward?" / "فيني مرق؟": name what blocks or may
  block the way and what to do (a closed door ahead -> it must be opened
  first: "في باب مسكّر قدّامك، لازم تفتحو قبل ما تمرق"). If nothing is
  visible in the way, say you see no obstacle in the camera view, and
  that the camera cannot guarantee the path is clear. Never say "safe".
- "Where is X?": its position relative to the camera, using only what
  the image shows: left / right / straight ahead / centre, on / under /
  next to another object, near the top / bottom of the view. Never invent
  a distance in metres or steps; say "close" or "far" only when obvious.
  If X is not visible, say so (and when it was last seen, if in memory).
- Reading ("what is written…"): read the text exactly as written, in its
  own language and script; say if part of it is unreadable.
- Practical questions ("to make room for the laptop, what must I move?"):
  answer with the visible objects that matter for that task.
- Follow-up questions ("and on the left?", "what colour is it?", "w
  hayda?") refer to the CONVERSATION SO FAR when it is given.
- If the spoken question is unintelligible or too unclear to answer, ask
  the user briefly to repeat it (in their language). Never guess a question.
"""

# Recent questions and answers per phone, for follow-up questions.
CONVERSATION_TURNS = 3
CONVERSATION_MAX_AGE_S = 180.0
_conversation: dict[str, list[dict]] = {}


def conversation_context(device: str, now: float) -> str:
    turns = [t for t in _conversation.get(device, []) if now - t["at"] <= CONVERSATION_MAX_AGE_S]
    if not turns:
        return ""
    lines = [f"- {int(now - t['at'])} s ago, user: {t['q']!r} -> assistant: {t['a']!r}" for t in turns]
    return ("CONVERSATION SO FAR (for follow-up questions only; it is NOT visual evidence, the "
            "images are):\n" + "\n".join(lines))


def remember_turn(device: str, question: str, answer: str, now: float) -> None:
    turns = _conversation.setdefault(device, [])
    turns.append({"at": now, "q": question[:200], "a": answer[:300]})
    del turns[:-CONVERSATION_TURNS]


def _jpeg(image: Image.Image, max_side: int) -> bytes:
    copy = image.convert("RGB")
    copy.thumbnail((max_side, max_side))
    buffer = io.BytesIO()
    copy.save(buffer, format="JPEG", quality=82)
    return buffer.getvalue()


async def retrieve(question: str, now: float, device: str | None = None) -> tuple[list[dict], str]:
    """
    Memory frames relevant to the question. Each frame is marked
    with "image": attach the picture (past questions, strong meaning
    matches) or only its recorded facts (keeps Gemini fast).
    """
    chosen: dict[str, dict] = {}
    reason = []
    past = bool(PAST_HINTS.search(question or ""))
    if past:
        recent = [f for f in await memory.recent(RECENT_WINDOW_S, 12, device)
                  if f["status"] == "analyzed" and (f["text"] or f["objects"])
                  and now - f["captured_at"] >= CURRENT_SCENE_S]
        for f in recent[:3]:
            chosen[f["id"]] = dict(f, why="recent", image=True)
        reason.append(f"time ({len(recent[:3])})")
    hits = []
    if question.strip():
        try:
            hits = await asyncio.wait_for(memory.search(question, k=4, device=device), timeout=SEARCH_TIMEOUT_S)
        except asyncio.TimeoutError:
            reason.append("meaning search timed out")
    strong_images = 0
    for f in hits:
        if f["id"] in chosen or not (f["score"] is None or f["score"] >= 0.45):
            continue
        strong = past or (f["score"] is not None and f["score"] >= STRONG_MATCH and strong_images < 2)
        strong_images += 1 if strong and not past else 0
        chosen[f["id"]] = dict(f, why=f"meaning {f['score']}" if f["score"] else "keyword", image=strong)
    reason.append(f"meaning ({len(hits)})")
    frames = sorted(chosen.values(), key=lambda f: -f["captured_at"])[:MAX_MEMORY_FRAMES]
    return frames, ", ".join(reason)


async def fast_memory(now: float, device: str | None = None) -> tuple[list[dict], str, list[str], list[dict]]:
    """
    Spoken-question path: no transcript is needed. Attach the newest
    frame of the two most recent remembered SCENES (not the current
    view) as images, and give the model a compact one-line-per-scene
    text index of everything remembered.
    """
    # Analysed frames AND frames still waiting for analysis (when the cloud is
    # busy, the image itself still shows the door the user passed).
    analyzed = [f for f in await memory.recent(RETENTION_INDEX_S, INDEX_LIMIT, device)
                if f["status"] in ("analyzed", "pending")]
    # Consecutive frames showing the same thing form one SCENE (the
    # camera often rests on one wall for a long time; repeating it would
    # bury the door the user passed under copies of the current view).
    scenes: list[dict] = []
    for f in analyzed:                                   # newest first
        key = _scene_key(f)
        if (f["status"] == "pending" and scenes and scenes[-1]["key"].startswith("p:")
                and _same_view(f, scenes[-1]["frame"])):
            key = scenes[-1]["key"]                       # same view, not analysed yet
        if scenes and scenes[-1]["key"] == key:
            scenes[-1]["oldest"] = f["captured_at"]
            scenes[-1]["count"] += 1
        else:
            scenes.append({"key": key, "frame": f, "newest": f["captured_at"],
                           "oldest": f["captured_at"], "count": 1})
    # The newest scene is still the current view if it was seen recently.
    current_key = scenes[0]["key"] if scenes and now - scenes[0]["newest"] <= CURRENT_SCENE_S * 2.5 else None
    past = [s for s in scenes if s["key"] != current_key and now - s["newest"] >= CURRENT_SCENE_S]
    attached = [dict(s["frame"], why="recent scene", image=True) for s in past[:2]]
    lines = []
    for n, s in enumerate(scenes):
        f = s["frame"]
        newest, oldest = now - s["newest"], now - s["oldest"]
        when = (_describe_age(newest) if s["count"] == 1 or oldest - newest < 5
                else f"from {_describe_age(oldest)} until {_describe_age(newest)}")
        caption = f["caption"] or "(not analysed yet - see its image if attached)"
        line = f"- S{n + 1}, {when}{' (still in view)' if n == 0 and current_key else ''}: {caption}"
        if f["text"]:
            line += f" | text: {f['text']!r}"
        if f["objects"]:
            line += f" | objects: {', '.join(f['objects'][:6])}"
        lines.append(line)
    return (attached, f"fast ({len(attached)} scene images, {len(lines)} scenes)", lines,
            [s["frame"] for s in scenes])


def _same_view(a: dict, b: dict) -> bool:
    """Two frames show the same view (64-bit fingerprints within 12 bits)."""
    if not a.get("dhash") or not b.get("dhash"):
        return False
    return bin(int(a["dhash"], 16) ^ int(b["dhash"], 16)).count("1") <= 12


def _scene_key(frame: dict) -> str:
    """Same visible text (or, without text, same main objects) = same scene."""
    if frame.get("status") == "pending":
        return "p:" + frame["id"]                 # not analysed yet: its own scene
    text = re.sub(r"\W+", " ", (frame.get("text") or "")).strip().lower()
    if text:
        return "t:" + text
    return "o:" + ",".join(sorted(o.lower() for o in (frame.get("objects") or [])[:3]))


# Arabic answers occasionally contain a Hebrew look-alike word (seen once
# in testing: "על" for "على"). The voices cannot say it, so it is fixed or
# removed before speaking; the rule above prevents it in the first place.
_HEBREW_WORD = re.compile(r"[\u0590-\u05FF\uFB1D-\uFB4F]+")
_HEBREW_TO_ARABIC = {"על": "على", "עם": "مع", "של": "تبع", "את": "", "זה": "هيدا"}


def clean_script(text: str) -> str:
    """Replace/remove Hebrew-script words so the answer is speakable."""
    if not _HEBREW_WORD.search(text):
        return text
    fixed = _HEBREW_WORD.sub(lambda m: _HEBREW_TO_ARABIC.get(m.group(0), ""), text)
    fixed = re.sub(r"\s{2,}", " ", fixed).strip()
    print(f"[ASK] foreign script removed from answer: {text!r} -> {fixed!r}", flush=True)
    return fixed


_ARABIC_LETTER = re.compile(r"[\u0600-\u06FF]")


def language_mismatch(spoken: str, answer_language: str, answer: str) -> bool:
    """
    True when the answer is not in the language the user spoke:
    an English/French question answered in Arabic (or mostly Arabic words,
    beyond a quoted sign), or formal Arabic vs Lebanese the wrong way round.
    """
    words = answer.split()
    arabic_words = sum(bool(_ARABIC_LETTER.search(w)) for w in words)
    if spoken in ("en", "fr"):
        return answer_language != spoken or (bool(words) and arabic_words / len(words) > 0.3)
    if spoken in ("ar", "ar-LB"):
        return answer_language != spoken
    return False


language_stats = {"answers": 0, "mismatches": 0, "corrected": 0}


# ---------------- answers when Gemini cannot answer ----------------

UNAVAILABLE = {
    "en": "Sorry, I can't look at the camera image right now: the AI services are busy or have reached "
          "their daily limit. Please try again in a moment.",
    "fr": "Désolé, je ne peux pas analyser l'image de la caméra pour le moment : les services d'IA sont "
          "occupés ou ont atteint leur limite du jour. Réessayez dans un instant.",
    "ar": "عذرًا، لا أستطيع تحليل صورة الكاميرا الآن، لأن خدمات الذكاء الاصطناعي مشغولة أو بلغت حدّها "
          "اليومي. حاول مرة أخرى بعد قليل.",
    "ar-LB": "عفوًا، ما فيني حلّل صورة الكاميرا هلّق، لأنو خدمات الذكاء الاصطناعي مشغولة أو خلص حدّها "
             "اليومي. جرّب كمان شوي.",
}
NOT_UNDERSTOOD = {
    "en": "Sorry, I didn't catch your question. Could you say it again?",
    "fr": "Désolé, je n'ai pas compris votre question. Pouvez-vous la répéter ?",
    "ar": "عذرًا، لم أفهم سؤالك. هل يمكنك تكراره؟",
    "ar-LB": "عفوًا، ما فهمت سؤالك. فيك تعيدو؟",
}
_LANGS = ("en", "fr", "ar", "ar-LB")


def _lang_code(value: str | None, default: str = "en") -> str:
    v = (value or "").strip().lower()
    if v in ("ar-lb", "lebanese"):
        return "ar-LB"
    if v.startswith("ar"):
        return "ar-LB"            # Whisper says "Arabic"; most users speak Lebanese
    if v.startswith("fr"):
        return "fr"
    if v.startswith("en"):
        return "en"
    return default if default in _LANGS else "en"


def _plain(answer: str, lang: str, model: str | None = None) -> "AssistantAnswer":
    return AssistantAnswer(question_heard="", spoken_language=lang, focus="", used=[], answer=answer,
                           answer_language=lang, evidence="none")


async def _fallback_answer(*, client, device: str, now: float, audio_bytes: bytes, audio_mime: str,
                           question: str, question_language: str, current: bytes | None,
                           memory_frames: list[dict], index_lines: list[str], contents: list,
                           generation_config, timings: dict) -> "AssistantAnswer":
    """
    Gemini's main model could not answer. In order:
      1. Groq (independent free quota): Whisper transcript + Qwen 3.8 27B with the
         current view and at most one memory image (memory index as text);
      2. the other Gemini models of the chain (transcript instead of audio);
      3. the local model, when installed (_deps["local_answer"]);
      4. a short spoken explanation - never silence, never an invented answer.
    """
    lang = _lang_code(question_language, memory.device_language.get(device, "en"))
    offline = probably_offline()           # cloud just failed with a network error: go local at once
    timings["offline"] = offline
    if not question and audio_bytes and not offline:
        t = time.perf_counter()
        try:
            heard = await asyncio.wait_for(whisper_service.transcribe_bytes(audio_bytes, audio_mime), 10)
            question = heard["transcript"][:500]
            lang = _lang_code(heard.get("detected_language"), lang)
        except HTTPException as error:
            if error.status_code == 422:                    # nothing intelligible was said
                timings["asr"] = round(time.perf_counter() - t, 3)
                return _plain(NOT_UNDERSTOOD[lang], lang)
        except Exception as error:  # noqa: BLE001 - network down: local ASR may still answer
            print(f"[ASK] fallback transcription failed: {type(error).__name__}", flush=True)
        timings["asr"] = round(time.perf_counter() - t, 3)

    if question:
        past = bool(PAST_HINTS.search(question))
        memory_image = None
        if past:
            # The remembered frame that best matches the question (meaning + keywords,
            # any language), not just the most recent one.
            candidates = list(memory_frames)
            try:
                hits = await asyncio.wait_for(memory.search(question, k=3, device=device), SEARCH_TIMEOUT_S)
                best = [h for h in hits if now - h["captured_at"] >= CURRENT_SCENE_S]
                candidates = best + [f for f in candidates if f["id"] not in {h["id"] for h in best}]
            except Exception:  # noqa: BLE001 - search unavailable: recent frames
                pass
            picked = []
            for f in candidates:
                row = await memory.store.run(memory.store._frame, f["id"])
                if row is not None and Path(row["path"]).is_file() and f["id"] not in {x[0]["id"] for x in picked}:
                    picked.append((f, row["path"]))
                if len(picked) == 3:
                    break
            if picked:
                data = await asyncio.to_thread(_collage, [p for _, p in picked])
                labels = "; ".join(
                    f"MEMORY {i + 1} ({['left', 'middle', 'right'][i] if len(picked) == 3 else ['left', 'right'][i] if len(picked) == 2 else 'whole image'}): "
                    f"seen {_describe_age(now - f['captured_at'])}" + (f", text {f['text']!r}" if f.get("text") else "")
                    for i, (f, _) in enumerate(picked))
                memory_image = (labels, data)
        memory_text = ("MEMORY INDEX (newest first):\n" + "\n".join(index_lines)) if index_lines else "\n".join(
            f"- MEMORY: seen {_describe_age(now - f['captured_at'])}: {f.get('caption', '')} | text: {f.get('text', '')!r}"
            for f in memory_frames)

        if groq_vision.usable() and not offline:
            t = time.perf_counter()
            try:
                async with budget.user_request():
                    # Each image costs ~1,800 of Groq's 7,000 tokens/minute: a question
                    # about the past sends the remembered image INSTEAD of the current view.
                    data = await groq_vision.answer(question, lang, None if memory_image else current,
                                                    memory_image, memory_text, conversation_context(device, now))
                timings["groq"] = round(time.perf_counter() - t, 3)
                timings["model"] = groq_vision.BUDGET_NAME
                spoken = data.get("spoken_language") if data.get("spoken_language") in _LANGS else lang
                answer_lang = data.get("answer_language") if data.get("answer_language") in _LANGS else spoken
                evidence = data.get("evidence") if data.get("evidence") in ("strong", "weak", "none") else "weak"
                used = [str(u) for u in data.get("used", []) if isinstance(u, str)][:5]
                return AssistantAnswer(question_heard=question, spoken_language=spoken, focus="", used=used,
                                       answer=str(data["answer"]).strip(), answer_language=answer_lang,
                                       evidence=evidence)
            except groq_vision.GroqUnavailable as error:
                timings["groq_failed"] = round(time.perf_counter() - t, 3)
                print(f"[ASK] Groq could not answer: {error}", flush=True)

        rest = vision.chain()[1:]
        if rest and not offline:
            alt = [f"Question (from speech recognition, the user spoke {lang}): {question}"] + list(contents)
            try:
                async with budget.user_request():
                    response, model = await vision.generate(client, alt, generation_config, "user",
                                                            8.0, models=rest)
                timings["model"] = model
                result = AssistantAnswer.model_validate_json(response.text or "")
                return result.model_copy(update={"question_heard": question})
            except Exception as error:  # noqa: BLE001
                print(f"[ASK] other Gemini models could not answer: {type(error).__name__}", flush=True)

    local = _deps.get("local_answer")
    if local is not None:
        try:
            return await local(device=device, now=now, audio_bytes=audio_bytes, audio_mime=audio_mime,
                               question=question, lang=lang, current=current, memory_frames=memory_frames,
                               index_lines=index_lines, timings=timings)
        except Exception as error:  # noqa: BLE001
            print(f"[ASK] local model could not answer: {type(error).__name__}: {error}", flush=True)

    timings["model"] = "none"
    return _plain(UNAVAILABLE[lang], lang)


def _evidence_frames(used: list[str], memory_frames: list[dict], index_frames: list[dict],
                     now: float) -> list[dict]:
    """The stored frames the answer is based on ('MEMORY 2' / 'S3' -> frame)."""
    out, seen = [], set()
    for label in used or []:
        label = label.strip().upper()
        frame = None
        m = re.fullmatch(r"MEMORY\s*(\d+)", label)
        if m and 1 <= int(m.group(1)) <= len(memory_frames):
            frame = memory_frames[int(m.group(1)) - 1]
        m = re.fullmatch(r"S\s*(\d+)", label)
        if m and 1 <= int(m.group(1)) <= len(index_frames):
            frame = index_frames[int(m.group(1)) - 1]
        if frame and frame["id"] not in seen:
            seen.add(frame["id"])
            out.append({"id": frame["id"], "label": label, "age_s": round(now - frame["captured_at"], 1),
                        "image_url": f"/memory/frames/{frame['id']}.jpg", "text": frame.get("text", "")})
    return out


class FirstWord(BaseModel):
    first_word_s: float = Field(ge=0, le=120)


@router.post("/ask/{qa_id}/first-word")
async def report_first_word(qa_id: str, body: FirstWord):
    """
    The phone reports the real end-to-end latency: from the moment the user
    stopped speaking to the first sound of the answer (shown on the dashboard).
    """
    if not re.fullmatch(r"[0-9a-f]{32}", qa_id) or memory.store is None:
        raise HTTPException(404, "Unknown question.")
    updated = await memory.store.run(memory.store._set_first_word, qa_id, round(body.first_word_s, 2))
    if not updated:
        raise HTTPException(404, "Unknown question.")
    return {"ok": True}


def _collage(paths: list[str], tile: int = 448) -> bytes:
    """Up to three remembered frames side by side, numbered: one image for the model."""
    images = []
    for path in paths:
        with Image.open(path) as im:
            im = im.convert("RGB")
            im.thumbnail((tile, tile))
            images.append(im.copy())
    height = max(im.height for im in images)
    out = Image.new("RGB", (sum(im.width for im in images) + 8 * (len(images) - 1), height), (0, 0, 0))
    x = 0
    draw = ImageDraw.Draw(out)
    for i, im in enumerate(images, 1):
        out.paste(im, (x, 0))
        draw.rectangle([x, 0, x + 34, 30], fill=(0, 0, 0))
        draw.text((x + 8, 4), str(i), fill=(255, 255, 0), font=_label_font())
        x += im.width + 8
    buffer = io.BytesIO()
    out.save(buffer, format="JPEG", quality=82)
    return buffer.getvalue()


def _label_font():
    try:
        return ImageFont.truetype("arial.ttf", 22)
    except OSError:
        return ImageFont.load_default()


def _describe_age(seconds: float) -> str:
    if seconds < 90:
        return f"{int(seconds)} seconds ago"
    return f"about {round(seconds / 60)} minutes ago"


@router.post("/ask")
async def ask(
    question: str = Form(""),
    question_language: str = Form(""),
    speak: bool = Form(False),
    question_audio: UploadFile | None = File(None),
    device_id: str = Form(""),
):
    started = time.perf_counter()
    # The asking phone's own stream and memory (another phone's are never used).
    if device_id.strip():
        device = device_name(device_id)
    else:
        primary = hub.primary()
        device = primary.device if primary else "phone"
    timings: dict[str, float] = {}
    client = _deps.get("client")
    if client is None:
        raise HTTPException(503, "Gemini API key is not configured.")

    question = question.strip()[:500]
    if not question and question_audio is None:
        raise HTTPException(400, "Ask a question first.")

    now = time.time()

    # ---- the recording is read ONCE (Whisper and Gemini share it) ----
    audio_bytes, audio_mime = b"", ""
    if question_audio is not None:
        try:
            audio_mime = (question_audio.content_type or "").split(";")[0].strip().lower()
            audio_bytes = await question_audio.read(MAX_AUDIO_BYTES + 1)
        finally:
            await question_audio.close()
        if not audio_bytes or len(audio_bytes) > MAX_AUDIO_BYTES:
            audio_bytes = b""

    # ---- in parallel: speech recognition + current view ----
    async def current_view():
        # The newest decoded frame at the moment of the question (not the
        # last 0.5 s memory sample): "now" really means now.
        latest = await asyncio.to_thread(hub.fresh, device)
        if latest and time.time() - latest[1] <= FRESH_FRAME_S:
            timings["view_age"] = round(time.time() - latest[1], 2)
            return await asyncio.to_thread(_jpeg, latest[0], 1024)
        return None

    # Spoken question: Gemini hears the audio itself, so Whisper and the
    # embedding search are NOT on the critical path (measured ~1.5 s).
    fast = bool(audio_bytes) and not question

    async def transcribe():
        if question or not audio_bytes or fast:
            return None
        t0 = time.perf_counter()
        try:
            return await whisper_service.transcribe_bytes(audio_bytes, audio_mime)
        finally:
            timings["asr"] = round(time.perf_counter() - t0, 3)

    current, heard = await asyncio.gather(current_view(), transcribe())
    if heard:
        question = heard["transcript"][:500]
        question_language = heard.get("detected_language") or question_language

    # ---- memory ----
    t = time.perf_counter()
    if fast:
        memory_frames, retrieval, index_lines, index_frames = await fast_memory(now, device)
    else:
        memory_frames, retrieval = await retrieve(question, now, device)
        index_lines, index_frames = [], []
    timings["retrieval"] = round(time.perf_counter() - t, 3)
    if index_lines and os.environ.get("WV_DEBUG_ASK") == "1":
        print("[ASK] memory index:\n" + "\n".join(index_lines), flush=True)

    if current is None and not memory_frames:
        raise HTTPException(409, "No live camera view and nothing in memory yet. Start streaming first.")

    gemini_mime = _deps["audio_types"].get(audio_mime) if audio_bytes else None
    audio_part = types.Part.from_bytes(data=audio_bytes, mime_type=gemini_mime) if gemini_mime else None

    hint = _deps["language_hints"].get((question_language or "").strip().lower())
    contents: list = []
    if audio_part is not None:
        contents += [audio_part, (
            "Above is the user's spoken question. It is AUTHORITATIVE: transcribe ALL of it into "
            "question_heard, keeping each language, and answer all of it. Answer in the language "
            "the user SPOKE in this audio (French audio -> French answer, English -> English, "
            "Lebanese Arabic -> Lebanese Arabic), and set answer_language accordingly."
            + (" Automatic speech-recognition draft (often wrong for mixed languages, do not trust it): "
               f"{question}" if question else "")
            + (f"\n[Speech recognition guessed: {hint}.]" if hint else ""))]
    else:
        contents.append((f"[Speech recognition: the user spoke {hint}.]\n" if hint else "") +
                        f"Question: {question}")

    context = conversation_context(device, now)
    if context:
        contents.append(context)
    if current is not None:
        contents += ["CURRENT VIEW (what the camera sees right now; use it for every question about "
                     "the present):", types.Part.from_bytes(data=current, mime_type="image/jpeg")]
    images = 0
    for i, f in enumerate(memory_frames, 1):
        facts = f"seen {_describe_age(now - f['captured_at'])}"
        if f.get("caption"):
            facts += f"; scene: {f['caption']}"
        if f["text"]:
            facts += f"; text recorded: {f['text']!r}"
        if f["objects"]:
            facts += f"; objects: {', '.join(f['objects'][:8])}"
        row = await memory.store.run(memory.store._frame, f["id"]) if f.get("image") else None
        if row is not None and Path(row["path"]).is_file():
            data = await asyncio.to_thread(lambda p=row["path"]: _jpeg(Image.open(p), 640))
            contents += [f"MEMORY {i} ({facts}):", types.Part.from_bytes(data=data, mime_type="image/jpeg")]
            images += 1
        else:
            contents.append(f"MEMORY {i} ({facts}) [recorded description only, image not attached]")
    if index_lines:
        contents.append(
            "MEMORY INDEX (every scene remembered in the last 30 minutes, newest first, "
            "labelled S1, S2, ...; use it to find what the user asks about, say when it was "
            "seen, and list the labels you used in 'used'):\n"
            + "\n".join(index_lines))
        contents.append(
            "Before answering: find the exact thing the user asked about. If it is not in "
            "the CURRENT VIEW but appears in MEMORY or the MEMORY INDEX, say it is not in "
            "view now and when it was seen. Never answer with a different object.")
    timings["images"] = images + (1 if current is not None else 0)

    generation_config = types.GenerateContentConfig(
        system_instruction=_deps["system_instruction"] + ASSISTANT_RULES,
        temperature=0.2,
        max_output_tokens=config.VISION_MAX_OUTPUT_TOKENS,
        response_mime_type="application/json",
        response_schema=AssistantAnswer,
        thinking_config=types.ThinkingConfig(thinking_level=config.VISION_THINKING_LEVEL),
    )

    primary = vision.chain()[0]

    async def one_request():
        # vision.generate: model chain + daily quota books; an overloaded,
        # exhausted or slow model hands over to the next one at once.
        response, model = await vision.generate(client, contents, generation_config, "user",
                                                config.VISION_ATTEMPT_TIMEOUT, models=[primary])
        timings["model"] = model
        return response

    # A backup request doubles quota use: only while the day is far from its limit.
    hedge_after = (config.VISION_HEDGE_AFTER_SECONDS
                   if budget.remaining_today(primary) > quota.daily_limit(primary) // 2 else 0)

    t = time.perf_counter()
    fallback_used = False
    try:
        if budget.exhausted(primary):
            raise vision.NoCloudVision(f"{primary} daily quota used up")
        async with budget.user_request():          # pauses background analysis
            response, _ = await asyncio.wait_for(
                hedged(one_request, hedge_after), config.VISION_TIMEOUT_SECONDS)
        result = AssistantAnswer.model_validate_json(response.text or "")
        timings["gemini"] = round(time.perf_counter() - t, 3)
    except (vision.NoCloudVision, asyncio.TimeoutError, errors.APIError, ValueError) as error:
        # The main model cannot answer (quota, overload, timeout, bad reply):
        # Groq -> other Gemini models -> local model -> spoken explanation.
        print(f"[ASK] {primary} unavailable ({type(error).__name__}: {str(error)[:80]}); using fallback",
              flush=True)
        timings["primary_failed"] = round(time.perf_counter() - t, 3)
        fallback_used = True
        result = await _fallback_answer(
            client=client, device=device, now=now, audio_bytes=audio_bytes, audio_mime=audio_mime,
            question=question, question_language=question_language, current=current,
            memory_frames=memory_frames, index_lines=index_lines,
            contents=contents[2:] if audio_part is not None else contents[1:],      # images + memory only
            generation_config=generation_config, timings=timings)
        if result.question_heard:
            question = result.question_heard

    # ---- the answer must be in the language the user spoke ----
    language_stats["answers"] += 1
    if not fallback_used and language_mismatch(result.spoken_language, result.answer_language, result.answer):
        language_stats["mismatches"] += 1
        names = {"en": "English", "fr": "French", "ar": "formal Modern Standard Arabic",
                 "ar-LB": "Lebanese Arabic"}
        wanted = names.get(result.spoken_language, result.spoken_language)
        print(f"[ASK] language mismatch: spoken {result.spoken_language}, answer "
              f"{result.answer_language}: {result.answer[:60]!r} -> asking again in {wanted}", flush=True)
        contents.append(
            f"CORRECTION: the user spoke {wanted}. Your answer was in another language. "
            f"Give the same answer again ONLY in {wanted} (quote sign text exactly as written).")
        t2 = time.perf_counter()
        try:
            async with budget.user_request():
                retry, _ = await asyncio.wait_for(hedged(one_request, 0), config.VISION_TIMEOUT_SECONDS)
            fixed = AssistantAnswer.model_validate_json(retry.text or "")
            if not language_mismatch(result.spoken_language, fixed.answer_language, fixed.answer):
                result = fixed.model_copy(update={"question_heard": result.question_heard or fixed.question_heard,
                                                  "spoken_language": result.spoken_language})
                language_stats["corrected"] += 1
        except Exception as error:   # keep the first answer rather than failing
            print(f"[ASK] language correction failed: {type(error).__name__}", flush=True)
        timings["language_fix"] = round(time.perf_counter() - t2, 3)

    answer = clean_script(result.answer.strip())
    # The language the user really spoke (decided from the audio itself).
    question_language = result.spoken_language
    memory.device_language[device] = result.spoken_language     # warnings use it
    if fast and result.question_heard:
        question = result.question_heard.strip()[:500]     # Gemini's transcript of the audio
    speech = None
    if speak and answer:
        job = _deps["create_speech_job"](answer, result.answer_language, config.LIVE_TTS_SWITCH_AFTER)
        speech = _deps["job_urls"](job)

    timings["total"] = round(time.perf_counter() - started, 3)
    frames_used = [{"id": f["id"], "age_s": round(now - f["captured_at"], 1), "why": f["why"]}
                   for f in memory_frames]
    evidence_frames = _evidence_frames(result.used, memory_frames, index_frames, now)

    remember_turn(device, question, answer, now)
    qa_id = uuid.uuid4().hex
    await memory.record_qa({
        "id": qa_id, "device": device, "asked_at": now, "question": question,
        "question_heard": (result.question_heard or "").strip() if audio_part is not None else "",
        "answer": answer, "answer_language": result.answer_language,
        "frames_used": json.dumps({"current": current is not None, "memory": frames_used, "used": result.used,
                                   "evidence": evidence_frames, "model": timings.get("model"),
                                   "timings": {k: v for k, v in timings.items() if k != "model"}}),
        "evidence": result.evidence, "latency_s": timings["total"], "mode": "live"})

    print(f"[ASK] evidence={result.evidence} current={current is not None} memory={len(memory_frames)} "
          f"({retrieval}) timings={timings}", flush=True)

    return {
        "answer": answer,
        "answer_language": result.answer_language,
        "question_received": question,
        "question_language": question_language,
        "question_heard": (result.question_heard or "").strip() if audio_part is not None else "",
        "evidence": result.evidence,
        "used": result.used,
        "current_view": current is not None,
        "memory_frames": frames_used,
        "evidence_frames": evidence_frames,
        "qa_id": qa_id,
        "device": device,
        "retrieval": retrieval,
        "source": "gemini",
        "model": timings.get("model", config.VISION_MODEL),
        "timings": timings,
        "speech": speech,
    }
