# World Voice AI: architecture

How the assistant works, why each technology was chosen, and what leaves
the laptop. Database details: [DATABASE.md](DATABASE.md). Setup and the
acceptance checklist: [RUNNING.md](RUNNING.md). Measurements:
[BENCHMARK_REPORT.md](BENCHMARK_REPORT.md).

## 1. The pipeline

```
 Phone (Android, React Native/Expo)                    Laptop (FastAPI backend, port 8000)
 ─────────────────────────────────                     ───────────────────────────────────
 camera ──WebRTC video──────────────────────────────▶ stream_service   one stream per phone (max 4)
                                                         │ newest frame kept for questions
                                                         │ 2 samples/s
                                                         ▼
                                                       memory_service   frame selection: sharp, new,
                                                         │              informative; dark/soft frames
                                                         │              enhanced; faces blurred (privacy.py)
                                                         │              BEFORE storage
                                                         ▼
                                                       SQLite + JPEG    text (OCR), objects, caption,
                                                         │              obstacles, embedding, time
                                                         │ background analysis (quota-aware)
 microphone ─ hands-free speech detection ─ recording ─▶ /ask           current view + memory scenes
   (voiceActivity.ts; never while audio plays)           │              + memory index + conversation
                                                         ▼
                                                       answer providers (see 3)
                                                         ▼
 ContinuousSpeech player ◀──── one WAV stream ───────── speech_jobs    natural voice (see 5)
 first word measured ─────────▶ /ask/{id}/first-word    dashboard shows it
 warnings poll ◀───────────────── /memory/alerts         only obstacles still in view (hazards.py)

 Web dashboard (React/Vite, laptop) ◀── WebRTC + REST ── live video, memory, Q&A, warnings, quotas
```

## 2. Technology choices

| Part | Choice | Why |
|---|---|---|
| Streaming | WebRTC (aiortc server peer, react-native-webrtc) | real-time video with low latency; the server receives frames even without a dashboard; LAN-only ICE connects in ~0.1 s |
| Frame selection | Pillow (Laplacian variance, edges, brightness, dHash) | cheap enough for 2 samples/s per phone on a laptop CPU |
| Privacy | OpenCV YuNet face detector (local) | finds faces (incl. turned away, portraits) in ~25-60 ms; nothing identifies anyone |
| Database | SQLite (WAL) + FTS5 + embeddings | every question-time DB step < 5 ms p95 with 4 phones; no server to install (DATABASE.md) |
| Vision + answers | Gemini 3.5 Flash Lite first | hears the spoken question itself (accents, mixed languages), answers from several images, structured JSON; ~2-3.5 s normally |
| Second provider | Groq: Whisper large-v3 + Qwen 3.8 27B | independent free quota (1,000/day), very fast, reads English and Arabic signs |
| Offline | llama.cpp + Qwen3.5 0.8B, faster-whisper, Piper | runs on this CPU without internet; quality limits in BENCHMARK_REPORT |
| Voice | Fola (Gemini TTS) preferred, Rami (Azure ar-LB) backup | natural Lebanese; one voice per answer; Piper only as offline last resort (if approved) |
| Phone app | React Native / Expo, expo-audio | one code base; TalkBack accessibility; ExoPlayer streaming playback |
| Dashboard | React + Vite | fast to build, real backend data only |

## 3. Answer providers and quota management

A question is answered by the first provider that can (assistant_service.py):

1. **Gemini 3.5 Flash Lite**: audio + current view + memory images + memory index.
2. **Groq**: Whisper transcribes; Qwen 3.8 27B answers from the current view
   (or, for a past question, a collage of up to three remembered scenes:
   one image's token cost) + the memory index as text.
3. **Other Gemini models** (3.1 Flash Lite, 3.5 Flash), each with its own quota.
4. **Local Qwen** (offline), when `start_local_ai.ps1` is running.
5. A short **spoken message** that the AI is busy, in the user's language:
   never silence, never an invented answer.

Quota rules (quota.py, vision.py, groq_vision.py):

| Rule | Detail |
|---|---|
| Measured limits | Gemini vision 500 requests/day per model and 15/min; Groq Qwen 1,000/day and 7,000 input tokens/min (~1,800 per image); Groq Whisper 2,000/day; Gemini TTS 10/day per model |
| Daily books | per model, saved in `storage/quota_state.json`, reset at midnight Pacific |
| Questions first | background analysis never uses the last 250 (350 in presentation mode) daily requests of a model; it pauses during and 3 s after a question |
| Exhausted | a per-day 429 marks the model exhausted until Google's retry time: no request is wasted on it |
| Slow / overloaded | a model that times out (12 s) or answers 5xx is skipped for 10 minutes |
| Groq tokens | the sliding minute is tracked; a question waits up to 15 s before sending instead of being refused |
| Fewer requests | an unchanged scene ("periodic refresh") reuses the previous analysis; backup requests only while > 50 % of the day is left |
| Presentation mode | `WV_PRESENTATION_MODE=1`: background at most every 15 s, larger reserve, no backup requests |

Background analysis order: Gemini (within its share) -> local model (if
`WV_LOCAL_BACKGROUND=1`) -> Groq (one frame per 30 s, only with token room,
at most 300/day). Frames not analysed yet still answer past questions:
their images are sent to the model.

## 4. Intelligence for blind users

- Present questions use only the newest camera frame (≤ 0.3 s old); past
  questions use the remembered scenes; "where is X" says when X was seen.
- Intent rules: "can I pass?" names what blocks the way and never says
  "safe"; locations only as the image supports (no invented distances);
  text read exactly; practical questions answered with the relevant objects;
  follow-ups use the last 3 turns (3 minutes); unclear speech -> "please repeat".
- Language: decided from the words, never the accent; English -> English,
  French -> French, formal Arabic -> formal Arabic, Lebanese -> Lebanese; a
  wrong-language answer is corrected once before speaking.
- Warnings (hazards.py): only obstacles in the walking path; spoken only while
  the camera still shows them; expire after 10 s; not repeated within 45 s;
  user's language; "position uncertain" when unknown; never "safe".

## 5. Voice pipeline

Hands-free (phone): speech detection adapts to room noise, ends the question
after 1.2 s of silence, ignores coughs, and never records while an answer or
a warning plays (the microphone opens 0.7 s after the speaker stops).
Server: one speech job per answer, one voice per answer (Fola, or Rami if Fola
is not ready within 2.5 s / out of quota), streamed as one WAV to one player.
The phone measures and reports the first audible word.

## 6. Network and security

| Network | How |
|---|---|
| Home / university Wi-Fi | phone -> `http://<laptop IPv4>:8000` (shown at startup, on the dashboard and testable in the app). University networks that isolate devices: use the phone hotspot or Tailscale |
| Phone 4G hotspot | laptop joins the phone's hotspot; same as Wi-Fi (run `setup_firewall.ps1` once: hotspots are "Public" networks) |
| Separate connections | Tailscale (free personal plan) on both devices; use the laptop's 100.x address. WebRTC works over it (host candidates) |

- Access code (`WV_ACCESS_TOKEN`): required on every endpoint except `/health`
  when set; the phone sends it (Settings > Access code); the dashboard on the
  laptop itself is always allowed. Recommended on any shared network.
- Firewall script: only the backend's Python, only TCP 8000 + WebRTC UDP,
  only from the local subnet and Tailscale.
- The backend is never exposed to the public internet.

## 7. What leaves the laptop (cloud data)

| Data | Sent to | When |
|---|---|---|
| Question audio + current frame + up to 2 memory frames + memory text | Google Gemini API | each question (main path) |
| Question audio | Groq (Whisper) | when Gemini cannot answer |
| Current frame or a collage of memory frames + text | Groq (Qwen) | when Gemini cannot answer |
| Stored frames (faces already blurred) | Gemini, or Groq when Gemini cannot | background analysis |
| Text of the frame analysis | Gemini embeddings | background analysis, search |
| Answer text | Gemini TTS or Azure Speech | each answer (voice) |

Nothing is sent in offline mode. Stored frames have faces blurred before
storage; the live frame for the current view is NOT blurred (it is needed to
answer, e.g. "is someone in front of me?") and is never stored. Free cloud
tiers may use data to improve their products: test only with people who agree.

## 8. Privacy limits

- Face detection can miss very small (< ~10 px), heavily covered or back-turned
  faces; those are not recognisable either. It sometimes blurs a doll's face
  (acceptable). Nothing ever identifies a person (prompt rules + no recognition code).
- Frames and Photo-mode photos are deleted after 30 minutes; Clear Memory deletes
  frames, files, index entries and questions.
