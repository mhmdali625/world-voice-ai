# World Voice AI

An accessible visual assistant for blind and low-vision users. The phone streams
live video to a server. The server remembers the useful moments: it keeps sharp,
new frames, reads their text and names their objects. The user asks questions by
voice, such as "What does the sign in front of me say?" or "شو كان مكتوب عالباب
يلي مرقنا حدّو؟". The answer is spoken in a natural voice and is based only on
what the camera actually saw.

Course: Multimedia Database and Image Processing, 2026–2027.

## Architecture

Full description, technology choices, quota rules, network/security and what is sent to the
cloud: [backend/ARCHITECTURE.md](backend/ARCHITECTURE.md).

```
phone camera ─WebRTC─▶ backend ─▶ frame selection ─▶ face blur ─▶ SQLite memory (OCR, objects, time, meaning)
phone mic (hands-free) ─▶ /ask ─▶ Gemini 3.5 Flash Lite ─▶ Groq (Whisper + Qwen 3.8 27B) ─▶ other Gemini
                                   ─▶ local Qwen (offline) ─▶ spoken "busy" message
answer ─▶ natural voice (Fola / Rami) ─▶ phone speaker; obstacle warnings while still in view
dashboard (laptop) ◀─ live video, memory, Q&A, warnings, quotas, network
```

## How the requirements are met

| Requirement (course PDF) | Implementation | Verified by |
|---|---|---|
| Stream the phone camera live to the server (WebRTC) | WebRTC phone → server (`/stream/publish`). The server is a WebRTC peer (aiortc) and relays the video to the dashboard. LAN-only ICE: connection in about 0.1 s. Automatic reconnect with backoff. Up to 4 phones at once, each with its own stream. | `test_stream.py`, `test_multi_device.py` |
| Listen to the question, speak the answer aloud | Hands-free: the app starts the camera, streams and listens by itself (setting); speech detection ends the question after 1.2 s of silence; the answer is spoken in a natural voice (Fola, Rami backup) and listening resumes. The microphone is never open while audio plays. Manual mode kept. The phone measures the first audible word. | `voiceActivity.harness.cjs` 9/9, `test_live_latency.py`, `speechPlayer.harness.cjs` 7/7 |
| Keep only useful frames (sharp, new, informative) | 1.5 s windows. The sharpest frame is kept only if bright enough, informative (edge density) and new (dHash, ≥10 of 64 bits), with a refresh every 20 s. Each phone has its own selector. | `test_memory.py`, `test_assistant_flow.py` (46–57 frames seen → 4 kept) |
| Improve poor frames (bonus) | Dark frames: autocontrast + gamma. Soft frames: unsharp mask. Recorded per frame. | `test_memory.py` |
| Read signs (OCR) and spot objects | Gemini vision, up to 4 frames per request: caption, text in its own script (Arabic, French, English), objects, hazards. Transient errors are retried. | `test_memory.py --live` (4/4 signs in 3 languages) |
| Store frames with text, objects and time; find by time or meaning | SQLite with WAL: `frames` + FTS5 keyword index + 768-d multilingual embeddings. [Schema and benchmark](backend/DATABASE.md). | `bench_memory_db.py` (13/13), `test_memory.py` |
| Speech to text, find the most relevant stored frames | Gemini transcribes the spoken question (`question_heard`), then selects the relevant remembered scenes (`used`: S1, MEMORY 2 …). Those frames are stored with the answer and shown on the dashboard. Typed questions use time and meaning search. | `test_assistant_flow.py`, `bench_past_questions.py` |
| Short answer based only on what the camera saw | The current view is the newest decoded frame at the moment of the question (≤ 0.3 s old). Questions about the present use only it; past-tense questions use memory; "where is X" says when X was seen; the model never answers with another object. | `test_warnings.py` (fresh view), `test_assistant_flow.py`, `test_live_latency.py` |
| "I'm not sure" when evidence is weak; never "safe" | Evidence rated strong / weak / none; the answer says "I'm not sure" / "Je ne suis pas sûr" / "مش متأكد"; never says "safe". | `test_assistant_flow.py` ("Where is the car?" → "I don't see a car") |
| At least two languages | English, French, formal Arabic, Lebanese Arabic and mixed sentences, including accented speech. The language is decided from the words, never the accent (`spoken_language`). An answer in the wrong language is detected and corrected once before it's spoken. | `bench_language.py` (12/12), `test_warnings.py`, `test_assistant_flow.py` |
| Dashboard: live status, timeline of stored frames, past Q&A, live stream | Live video, stream status (fps, resolution, viewers), phone selector, timeline (image, time, text, objects, warnings), Q&A history with the frames used, memory and quota statistics, Clear Memory. | `test_dashboard_sync.py`, `test_dashboard_browser.py` (real Edge) |
| No face recognition; privacy of stored images | Prompts forbid identifying people (only "a person" and where). Faces are only located, never recognised: in Live Assistant frames every face (including faces in portraits) is pixelated and blurred **before** storage, and signs and objects keep their exact pixels. A frame whose faces can't be checked is not stored. Photo Mode stores only fully blurred photos. | `test_privacy.py` (27/27) |
| Keep only what you need, delete old frames | Both stores delete after 30 minutes: memory frames (rows, files, index; plus a 600-frame cap per phone) and Photo Mode photos. Clear Memory per phone or for all. Recorded questions are deleted on the phone after use. | `bench_memory_db.py`, `test_privacy.py`, `test_dashboard_sync.py` |
| Warnings without being asked (bonus) | Only obstacles in the walking path, with their position. A warning is spoken only while the camera still shows that scene (checked by image fingerprint), expires after 10 s, isn't repeated within 45 s, uses the user's language, says when the position is uncertain, and never says "safe". | `test_warnings.py --live` |
| Hands-free conversation (optional) | After each answer the phone listens again by itself. The question ends after 1.2 s of silence, the microphone is never open while an answer or warning plays, and an accessible switch and the main button stop it. Manual mode is unchanged. | `voiceActivity.harness.cjs` (9/9) |
| End-to-end speed | The phone shows and reports "first word after N s" (user stops speaking → first audio). The dashboard shows it for every answer. | `test_live_latency.py` |
| Questions have priority over background work | The quota governor reserves Gemini capacity for questions; background analysis pauses during and after a question and after a rate-limit error. | `test_assistant_flow.py` (analysis rate limited) |
| Reliability despite free-tier limits | Daily quota books per model, reserve for questions, exhausted/slow models skipped, Groq and local fallbacks, a spoken message instead of silence; presentation mode. | `test_quota.py` 17/17, `bench_voice_to_voice.py` |
| Works on Wi-Fi, 4G hotspot, separate networks | Server address and access code set in the app; address shown at startup and on the dashboard; firewall script; Tailscale for separate networks; access code on every endpoint. | `network.py`, `setup_firewall.ps1` |

## Running

Details, configuration and the acceptance checklist are in [backend/RUNNING.md](backend/RUNNING.md).

```powershell
# 1. backend (keys in backend/.env, see backend/RUNNING.md)
cd backend
.venv\Scripts\python -m uvicorn main:app --host 0.0.0.0 --port 8000

# 2. dashboard
cd dashboard
npm run dev                   # http://localhost:5173

# 3. phone app (development build)
cd mobile
npx expo start --dev-client   # mobile/.env: EXPO_PUBLIC_BACKEND_URL=http://<laptop IP>:8000
```

On the phone, tap **Live Assistant**, then **Start Streaming**, then **Ask a Question**. Speak,
tap again, and the answer is spoken.

## Test results (laptop, 2026-11-27)

All tests use real servers and real API calls unless noted. The phone is
simulated by an aiortc client streaming a scripted walk (EXIT sign → door
"ROOM 12" → wall → CAFE sign). For the multi-phone test, each phone walks
past its own door and sign.

| Suite | Result | What it covers |
|---|---|---|
| `test_stream.py` | 11/11 | Publish over HTTP, ~15 fps received, 2 samples/s for memory, viewer forwarding, republish, stop detection |
| `test_memory.py --live` | 22/22 | Frame selection, enhancement, database, retention, analysis retry, OCR in 3 languages, search by time, keyword and meaning |
| `test_assistant_flow.py` | 15/15 | Stream → memory → 8 questions: now, past, "where is", never seen; English, Lebanese, formal Arabic, French, mixed |
| `test_multi_device.py` | 20/20 | 3 phones streaming and asking at the same time: isolation of video, memory, answers and history; reconnect; 4-phone limit |
| `test_dashboard_sync.py` | 13/13 | Every dashboard request from the browser origin, CORS, Clear Memory |
| `test_dashboard_browser.py` | 9/9 | The dashboard rendered in headless Microsoft Edge: live WebRTC video, timeline, Q&A, statistics |
| `test_privacy.py --live` | 27/27 | Face detection (incl. a person turned away, a portrait on the wall), anonymisation, signs unchanged, fail-safe, legacy frames, Photo Mode blur and retention, end to end with OCR |
| `bench_memory_db.py` | 13/13 | Database latency, CPU, RAM, storage, integrity, isolation, retention, search scaling |
| `bench_past_questions.py` | 10/10 | Spoken past questions repeated 5× (Lebanese, English) |
| `run_validation.py --live` | 38/38 | Speech pipeline, recognition, quotas, network failures |
| `test_live_voice_chain.py` | 22/23 | Natural voices; misses the strict "first sound ≤ 2 s on every answer" target (max 2.52 s, median 1.09 s) |
| `test_azure_pronunciation.py` | 5/5 | Rami's Lebanese loanwords: similarity 0.89 → 0.95 |
| `mobile/tests/speechPlayer.harness.cjs` | 7/7 | Phone audio player scenarios |
| TypeScript (mobile, dashboard), Android bundle export, dashboard build | pass | |

**Speed** (the user stops speaking → first sound of the answer; 6 consecutive
spoken questions in English, Lebanese Arabic and French):

| Gemini conditions | Answer ready (median / max) | First sound (median / max) | Within 5 s |
|---|---|---|---|
| Normal (morning, Rami voice) | 2.82 / 3.56 s | **4.11** / 5.94 s | 5/6 |
| Degraded (afternoon; Gemini returned 503 "high demand") | 5.17 / 8.24 s | 6.95 / 9.34 s | 2/6 |

The only variable part is Gemini's response time (2.1–3.5 s normally,
2.5–8.1 s under load). Everything else is fixed: database < 5 ms, memory
selection < 10 ms, speech start 1.1–1.5 s with Rami and about 2 s with Fola.

**Search quality:** "where is the way out of the building" → EXIT sign (top hit,
cosine 0.64). French "chambre numéro douze" → "ROOM 12". "Last 90 s" → only the
newest sign. Each phone's search returns only its own frames.

**Answer accuracy:** 8/8 (assistant flow), 6/6 (spoken, latency test), 6/6
(three phones asking at once), 10/10 (repeated past questions). Before the
answer-schema fix, Lebanese past questions were correct 2/5 times.

**User feedback:** to be collected during the phone tests and the live demo.

Full measurements and history: [backend/BENCHMARK_REPORT.md](backend/BENCHMARK_REPORT.md).
Database: [backend/DATABASE.md](backend/DATABASE.md).

## Known limitations

- **Gemini free tier:** 15 vision requests per minute, shared by background analysis and questions. Response time varies (2–8 s per answer measured today) and the service sometimes answers 503 "high demand". This decides whether the 3–5 s target is met.
- **Fola (Gemini TTS):** 10 answers per day per model. After that, answers are spoken by Rami (Azure), which starts faster. A live answer waits at most 2.5 s for Fola before switching.
- **Rami's first sound** is above 2 s in about 1 answer out of 8 (strict target; median 1.1 s).
- Short French phrases inside an Arabic sentence are sometimes missed by speech recognition.
- Face detection (YuNet) can miss faces that are very small (under ~10 px), seen from behind, or heavily occluded; those are not recognisable either. Measured: a person turned away in near profile is found; out-of-focus crowd faces of ~8 px are not.
- At most 4 phones stream at once (each stream is decoded on the laptop: about 17 % of one CPU core per phone).
- Not yet tested on the real phone: streaming and asking from Android, and the spoken hazard warnings. The laptop tests use simulated phones. The manual checklist is in [backend/RUNNING.md](backend/RUNNING.md).

## Team responsibilities

| Member | Responsibilities |
|---|---|
| _(name)_ | _(fill in)_ |
| Adam Shames | _(fill in)_ |

## Repository

| Folder | Content |
|---|---|
| `backend/` | FastAPI server: streaming, memory, assistant, speech, tests |
| `mobile/` | Android app (React Native / Expo) |
| `dashboard/` | Web dashboard (React / Vite) |
| `local-relay/` | Earlier Node.js WebRTC relay (replaced by the backend stream) |

Technical documents: [backend/DATABASE.md](backend/DATABASE.md) (schema, storage,
benchmark), [backend/RUNNING.md](backend/RUNNING.md) (setup, configuration, acceptance
checklist), [backend/BENCHMARK_REPORT.md](backend/BENCHMARK_REPORT.md) (all measurements).
