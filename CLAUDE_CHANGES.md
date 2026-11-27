# Changes made by Claude (speech, voice and recognition work)

Period: 2026-11-26 to 2026-11-27. Nothing committed or pushed. No dependency
added (`mobile/package.json` unchanged), no Android rebuild required, no paid
service enabled. Full measurements: `backend/BENCHMARK_REPORT.md`.

Sections 1-6 cover the speech work, during which `dashboard/`,
`local-relay/`, `WebRTCCameraTest.tsx` and `mobile/package.json` were not
touched. Section 7 covers the Live Assistant round, which (with the user's
permission) modified `WebRTCCameraTest.tsx` and the dashboard; `local-relay/`
and `mobile/package.json` are still unchanged by that round.

## 1. What the app does now

| Area | Behaviour |
|---|---|
| Voice | Natural neural voice only. Expo Speech is no longer used anywhere. Fola (Gemini) stays available. |
| Playback | One answer = one continuous WAV stream = one native player (no segment joins). Replay serves the same audio from the server cache. |
| Start time | Server starts the stream at the earliest moment that guarantees no interruption (measured, burst-proof generation rate). |
| Provider chain | `WV_TTS_PROVIDERS` = ordered list of approved voices. Fails before any audio, or not ready within `WV_TTS_SWITCH_AFTER` (4 s) -> same answer moves to the next approved voice. Never two voices inside one answer. |
| Quota | Daily-quota circuit breaker (no repeated 429 requests); provider pause after bad key; clear spoken + on-screen message when every voice is exhausted. |
| Network | Phone keeps waiting through blips; after a drop during playback it resumes at the last position (HTTP Range) instead of restarting. |
| Recognition | Whisper large-v3 + natural Lebanese prompt + parallel English check (fixes English returned as an Arabic translation). |
| Code-switching | The original question audio is sent with the photo; Gemini hears it, transcribes every language verbatim (`question_heard`) and answers all parts. |
| Latency | Backup requests for slow vision (3.5 s) and Whisper (2.5 s); 2048 px photos; connections kept warm; speech generation starts inside `/analyze`. |
| Accessibility | Earcons, TalkBack announcements, automatic spoken answers, Repeat, Cancel, accessible error box. |

## 2. Files

### Backend (`backend/`, new folder)
| File | Purpose |
|---|---|
| `config.py` | All settings (keys, models, providers, timeouts). Removes the competing GOOGLE_API_KEY from this process only. |
| `main.py` | `/analyze`: parallel blur/save, timings, backup request, 5xx retry, language hint, optional question audio, `question_heard`, starts speech job. Mounts all routers. |
| `whisper_service.py` | `/transcribe` (also standalone on 8002): natural prompt, backup request, parallel English language check. |
| `speech_jobs.py` | `/tts/jobs` single-stream speech: start rule, provider chain + deadline switch, parts, replay, HTTP Range, dev test endpoint, `/tts/quota`. |
| `tts_providers.py` | Providers: gemini (Fola), azure (ar-LB), elevenlabs, file (test). |
| `tts_service.py` | Gemini TTS streaming, quota breaker, legacy `/tts` + `/tts/stream`. |
| `hedge.py` | Backup-request helper. |
| `image_storage.py`, `photo_history.py` | Unchanged logic (existing blurred-image storage). |
| `RUNNING.md` | How to run, configure providers, test offline, demo checklist. |
| `requirements.txt` | Exact installed versions. |
| Tools | `run_validation.py`, `benchmark.py`, `bench_*.py`, `test_*.py`, `compare_voices.py`, `make_asr_testset.py`, `generate_voice_clips.py`, `generate_earcons.py`, `dev_offline_tts.ps1`. |

### Mobile (`mobile/`)
| File | Change |
|---|---|
| `App.tsx` | Expo Speech removed; `ContinuousSpeech`; automatic analysis after capture and automatic speech; Repeat/Cancel; error box; 2048 px capture; question audio + language sent to `/analyze`; shows `question_heard`. |
| `Components/VoiceRecorder.tsx` | Expo Speech removed; earcons; 16 kHz mono; detected language + recording passed to App. Manual recording feature kept. |
| `Services/config.ts` | Backend URL from `mobile/.env`, timeouts, errors with HTTP status. |
| `Services/speechPlayer.ts` | Single-player continuous playback, stall detection, network recovery with resume. |
| `Services/ttsService.ts` | Speech-job client (plus legacy NDJSON client). |
| `Services/aiService.ts`, `speechService.ts` | Timeouts, timings, language, question audio. |
| `Services/feedback.ts`, `voiceClips.generated.ts`, `assets/sounds/`, `assets/voice/phrases.json` | Earcons, natural clips (when generated), TalkBack phrases. |
| `tests/speechPlayer.harness.cjs` | Automated player tests (7 scenarios). |
| `.env` (git-ignored), `.env.example` | Backend URL. |

## 3. How to validate

```powershell
cd "C:\Multimedia Labs\Project\backend"
.venv\Scripts\python run_validation.py          # 25 offline checks, no API quota
.venv\Scripts\python run_validation.py --live   # + 12 Groq / Gemini-vision checks (no TTS quota)
cd ..\mobile
node tests\speechPlayer.harness.cjs             # 7 player scenarios
npx tsc --noEmit                                # typecheck
```

## 4. Limits of the free technologies (cannot be removed by code)

| Limit | Effect | What is implemented |
|---|---|---|
| Gemini TTS free tier: 10 requests/day per model, speed 0.34x-1.1x | Fola alone cannot be consistently fast or last a full day of testing | Breaker, clean message, provider chain + 4 s deadline switch to an approved fast voice |
| Whisper decodes one language per recording | English-first code-switching is translated/omitted by Whisper | Gemini hears the original audio (verified 8/8 on mixed questions) |
| Groq free tier: 20 requests/minute | ~6-10 questions/minute maximum (2-3 requests each) | Check request is optional; failures fall back cleanly |
| Gemini vision free tier: variable queueing, occasional 503 "high demand" | Occasional slow or failed analysis | Backup request at 3.5 s, 2 retries on 5xx |

## 5. Azure / Rami round (2026-11-27)

Checkpoint first: `C:\Multimedia Labs\Project-checkpoint-20261127-stable-before-azure`.

| File | Change |
|---|---|
| `backend/tts_providers.py` | Rami: Lebanese pronunciation of ~45 English/French loanwords via SSML `<sub alias>` (incl. `عالـ`/`بالـ`/`الـ` prefixes); English/French voices untouched |
| `backend/main.py` | Optional Azure connection ping (9 lines), **disabled by default** |
| `backend/config.py` | `WV_AZURE_KEEPALIVE` (default 0) |
| `backend/.env` | `WV_TTS_PROVIDERS=gemini,azure`, `WV_TTS_SWITCH_AFTER=4` (keys untouched) |
| new tools | `make_azure_testset.py`, `test_multilingual_azure.py`, `test_azure_pronunciation.py`, `test_live_voice_chain.py`, `compare_rami_naturalness.py` |

Everything else (mobile app, dashboard, relay, WebRTC, recognition, image
analysis, playback) byte-identical to the checkpoint.

Not integrated (needs the user's approval after listening): any Rami
naturalness change (tempo, endings, punctuation) or a different Arabic voice.

## 7. Live Assistant: stream, visual memory, questions, dashboard (2026-11-27)

Checkpoint first: `C:\Multimedia Labs\Project-checkpoint-20261127-before-memory-features`
(contains `backend/.env`: keep it local). Verified unchanged against it:
`local-relay/`, `mobile/package.json`, `mobile/package-lock.json`.
New backend dependencies (already installed, pinned in `requirements.txt`): `aiortc==1.17.0`, `av==19.0.2`.

| File | Change |
|---|---|
| `backend/stream_service.py` (new) | Server-side WebRTC peer: `/stream/publish` (phone), `/stream/view` (dashboard), `/stream/status`, `/stream/latest.jpg` (local only). LAN-only ICE (negotiation 10 s -> 0.12 s). 2 samples/s to memory |
| `backend/memory_service.py` (new) | Frame selection (sharpness, brightness, edges, dHash novelty, 1.5 s windows, 20 s refresh), enhancement of dark/soft frames, SQLite + FTS5 + embeddings, Gemini batch OCR/objects/hazards, 30 min / 600 frame retention, `/memory/*` endpoints. Clear Memory also deletes questions and alerts |
| `backend/assistant_service.py` (new) | `/ask`: spoken question + current view + memory -> grounded answer, evidence rating, "I'm not sure", speech job. Spoken questions use a fast path (no separate transcription): Gemini hears the audio, sees the current view plus the two most recent remembered *scenes*, and gets a one-line-per-scene memory index |
| `backend/quota.py` (new) | Gemini budget: 15 RPM, 6 reserved for questions, background paused during/after questions and after 429 |
| `backend/main.py` | Mounts the three routers, starts/stops memory and stream, quota accounting in `/analyze`, CORS DELETE |
| `backend/tts_service.py` | HTTP keep-alive 120 s (faster first answer after a pause) |
| `mobile/Components/WebRTCCameraTest.tsx` | Streams to the backend over HTTP signalling (relay no longer needed), automatic reconnect with backoff, watchdog, "Live Assistant" UI with `LiveAskPanel`; stale hardcoded IP removed |
| `mobile/Components/LiveAskPanel.tsx` (new) | Ask by voice while streaming, spoken answer, Repeat, Stop, clear error announcements, unprompted hazard warnings |
| `mobile/Services/aiService.ts` | `requestLiveAnswer` |
| `mobile/Services/speechPlayer.ts` | Status poll before start 350 -> 200 ms |
| `mobile/App.tsx` | Home button "Test WebRTC Camera" -> "Live Assistant" |
| `dashboard/src/App.tsx` | Video from the backend (`/stream/view`); status card and header from `/stream/status` instead of the relay; mounts the memory panel |
| `dashboard/src/LiveMemoryPanel.tsx` (new) | Timeline of stored frames (thumbnail, time, text, objects, warnings, enhancement), Q&A history, memory and quota statistics, Clear Memory with in-page confirmation |
| `README.md` (new) | Project overview, requirement mapping, test results, limitations, team table to fill in |
| Tests (new) | `test_stream.py`, `test_memory.py`, `test_assistant_flow.py`, `test_live_latency.py`, `test_dashboard_sync.py`, `test_helpers.py` |

Bugs found and fixed by the tests in this round: Clear Memory kept the Q&A
history; test memory from a previous run survived cleanup and contaminated the
next run (cleanup now retries and fails loudly); the "(still in view)" label
was given to every scene with the same text; spoken French questions were
answered in Lebanese Arabic; "where is the exit?" was answered with the
unrelated sign in view. The Arabic sign in `test_memory.py` was drawn unjoined
and reversed (Pillow has no text shaping here); it is now drawn correctly with
the same expected text.

## 8. Final mandatory round (2026-11-27, afternoon)

Checkpoint first: `C:\Multimedia Labs\Project-checkpoint-20261127-before-final-mandatory`.
No dependency added, nothing committed or pushed, no paid service.

| File | Change |
|---|---|
| `backend/stream_service.py` | Per-device streams (`device_id`, max 4 phones); a reconnect replaces only that phone; `/stream/status` lists all devices |
| `backend/memory_service.py` | `device` column (+ automatic migration) and per-device reads, selectors, cap and Clear Memory; WAL journal; transient analysis failures retried (3 attempts); `rank_by_meaning` with `math.sumprod` |
| `backend/assistant_service.py` | `/ask` uses the asking phone's view and memory; answer schema order question_heard -> focus -> used -> answer; scene labels S1.. -> `evidence_frames` stored with the answer; Arabic-script rule + `clean_script`; live speech switch 2.5 s; no-identification rule |
| `backend/main.py` | Privacy rule (no face recognition) and Arabic-script rule in the system prompt; photo-mode answers pass through `clean_script` |
| `backend/speech_jobs.py`, `config.py` | Per-job voice switch deadline (`WV_LIVE_TTS_SWITCH_AFTER`); Rami rate `WV_AZURE_AR_RATE` (+15 %) |
| `backend/tts_providers.py` | Rami SSML `<prosody rate>` (Arabic voices only) |
| `mobile/Services/device.ts` (new), `WebRTCCameraTest.tsx`, `aiService.ts`, `LiveAskPanel.tsx` | Stable device id sent with the stream, questions and warning polling |
| `dashboard/src/App.tsx`, `LiveMemoryPanel.tsx` | Phone selector, per-phone memory, frames used under each answer, accessibility (video label, live status, footer contrast), outdated texts replaced |
| `backend/DATABASE.md` (new) | Schema, storage, concurrency, benchmark, SQLite vs PostgreSQL |
| `README.md`, `backend/RUNNING.md`, `backend/BENCHMARK_REPORT.md` | Requirement table, results, limitations; one consolidated acceptance checklist; section 16 |
| Tests (new) | `bench_memory_db.py`, `test_multi_device.py`, `test_dashboard_browser.py`, `bench_past_questions.py`, `bench_schema_ab.py`; script checks and formal-Arabic / mixed questions added to existing tests |

## 9. Face privacy and photo retention (2026-11-27, evening)

Checkpoint: `C:\Multimedia Labs\Project-checkpoint-20261127-before-face-privacy`.
New dependency, with your approval: `opencv-python-headless==5.1.0.95` and `numpy==2.5.5`
(pinned in requirements.txt), plus the model `backend/models/face_detection_yunet_2023mar.onnx`
(OpenCV zoo, MIT, SHA-256 8F2383E4...2FA4).

| File | Change |
|---|---|
| `backend/privacy.py` (new) | YuNet face detection; faces pixelated + blurred, re-checked until none remains; raises if the check is impossible |
| `backend/memory_service.py` | Faces protected before a frame is written (else the frame is not stored); `faces` column + migration; start-up sweep of older frames and orphan files; privacy stats in `/memory/stats` |
| `backend/image_storage.py`, `main.py` | Photo Mode photos deleted after 30 min (`WV_PHOTO_RETENTION_MIN`), at start and every minute; `WV_PHOTO_DIR` |
| `dashboard/src/LiveMemoryPanel.tsx`, `App.tsx`, `SavedPhotoGallery.tsx` | "faces blurred: N" tag; texts: photos are deleted after 30 minutes |
| `backend/test_privacy.py` (new) | 27 checks (offline + live) |
| Docs | DATABASE.md (faces column, privacy), README, RUNNING.md (settings, checklist step 7), BENCHMARK_REPORT section 17 |

The backend on port 8000 was restarted (with your approval) to apply the change;
it now runs in the background from this session (log: `backend/backend_server.log`).

## 10. Final development plan (2026-11-27 night)

Checkpoint: `C:\Multimedia Labs\Project-checkpoint-20261127-phase1-start`. Resume file: `PROGRESS.md`.
Nothing committed or pushed; no paid service. Local AI files are outside the repository
(`C:\Multimedia Labs\local-ai`: llama.cpp b12328, Qwen3.5 GGUF, Piper 1.8.0 + voices, faster-whisper,
in their own Python environment, the backend's packages unchanged).

| Area | Files | Change |
|---|---|---|
| Quota | `backend/quota.py`, `backend/vision.py`, `config.py` | per-model daily books (saved, Pacific day), exhaustion until Google's retry time, reserve for questions, model chain with per-attempt timeout and 10-min pause of failing models, presentation mode |
| Hybrid answers | `backend/groq_vision.py`, `assistant_service.py` | Gemini -> Groq (Whisper + Qwen 3.8 27B, token planning, memory collage) -> other Gemini -> local -> spoken "busy" message; unanalysed frames usable as memory |
| Background | `memory_service.py` | analysis via the chain; reuse of unchanged scenes (`dhash`, `analysis_source` columns); local / throttled Groq fallback |
| Offline | `backend/local_ai.py`, `local_sidecar.py`, `start_local_ai.ps1`, `tts_providers.py` (`piper`) | local Qwen (llama.cpp), faster-whisper, Piper; Piper only if added to WV_TTS_PROVIDERS |
| Intelligence | `assistant_service.py` | blind-user intent rules, 3-turn conversation context, answer model stored per question |
| Network/security | `backend/network.py`, `setup_firewall.ps1`, `main.py` | address banner + `/network`; access code on every endpoint except /health (loopback allowed); narrow firewall rules |
| Phone | `Services/settings.ts`, `Components/AssistantSettings.tsx`, `WebRTCCameraTest.tsx`, `LiveAskPanel.tsx`, `App.tsx`, `Services/config.ts`, `eas.json`, `app.json` | hands-free auto-start, spoken connection status, server address + access code in the app, Live Assistant first, red emergency stop, APK build profile, app name |
| Dashboard | `src/App.tsx`, `src/api.ts`, `src/LiveCamera.tsx` (Adam's receiver, moved), `src/views/*`, `src/index.css` | professional tabbed dashboard, real data only, answer order + quotas, warning history, memory search, deep links |
| Docs | `backend/ARCHITECTURE.md` (new), RUNNING.md, BENCHMARK_REPORT.md s.18 | architecture, networks, APK, local AI, presentation mode, measurements |
| Tests/tools | `test_quota.py`, `bench_voice_to_voice.py`, `bench_local_vlm.py`, `bench_local_asr.py`, `probe_groq.py`, `probe_models.py`, `check_quota.py`, `make_voice_samples.py`, `piper_synth.py`, `check_voice_intelligibility.py` | |

Blocked, needs the user's approval: `npx expo install expo-build-properties` (APK cleartext HTTP),
a voice change (Piper as offline voice), Tailscale installation (optional).

## 6. Needs the user

1. **Fast backup voice (strongly recommended):** create a free Azure Speech key
   (F0) and/or ElevenLabs key, run `compare_voices.py`, listen, then set e.g.
   `WV_TTS_PROVIDERS=gemini,azure` in `backend/.env`. Without a second
   approved voice, speech remains limited by the Fola free tier.
2. **French / mixed-French test audio:** after the TTS quota resets, run
   `make_asr_testset.py` (1 request) and `run_validation.py --live`, or record
   a few real questions. Not available offline on this laptop.
3. **Final Android acceptance test** (steps in `RUNNING.md`).
