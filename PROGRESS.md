# >>> LATEST STATE: see CHECKPOINT_NOW.md (2026-11-27 night). Offline regression after local-first: warnings 17/17, memory 14/14, quota 17/17. <<<

# World Voice AI: progress file (resume point)

Presentation: **2026-12-02**. Keep this file updated after every finished step.
Nothing is committed or pushed. Checkpoints (local copies, contain `backend/.env`, keep private):
- `C:\Multimedia Labs\Project-checkpoint-20261127-before-phone-fixes`
- `C:\Multimedia Labs\Project-checkpoint-20261127-phase1-start`  <- start of the final plan

## Phase 1: checkpoint issues: DONE
- A. Past-question benchmark failure = **Gemini daily quota exhausted**, proven: 429 `GenerateRequestsPerDayPerProjectPerModel-FreeTier`, limit **500/day** for gemini-3.5-flash-lite, retryDelay 55,328 s (resets midnight Pacific = 10:00 Lebanon). Not a code regression (same questions passed in other suites). Accuracy re-run pending quota (`bench_past_questions.py 5`).
- B. Face-privacy timing: **20/20 in isolation** (1024x576 face 105/132 ms median/p95). The earlier failure was CPU contention.

## Phase 2/3: quota + hybrid AI: IN PROGRESS
Done:
- `backend/quota.py`: per-model daily accounting (saved to `storage/quota_state.json`), Pacific day without a tz database, exhaustion until Google's retryDelay, daily reserve for questions (`WV_USER_DAILY_RESERVE`=250 of `WV_GEMINI_RPD`=500).
- `backend/vision.py`: model chain `WV_VISION_MODELS` (3.5-flash-lite -> 3.1-flash-lite -> 3.5-flash), per-attempt timeout 12 s (`WV_VISION_ATTEMPT_TIMEOUT`), 429/503/404/timeout handling; used by /ask, /analyze and background analysis.
- Background: unchanged "periodic refresh" frames reuse the previous analysis (frames.dhash, analysis_source).
- Hedged backup requests only while the day has > 50 % quota left.
- `test_quota.py` 16/16 (simulated errors, no quota used).
- Probe today: 3.1-flash-lite answered correctly but in 167 s; 2.5-flash(-lite) = 404 for new users; 3.5-flash, gemma-4-26b/31b = 503 high demand.

To do:
- Local VLM benchmark: llama.cpp b12328 (CPU) in `C:\Multimedia Labs\local-ai\llama.cpp`, model Qwen3.5-2B Q4_K_M + mmproj-F16 (unsloth) in `...\local-ai\models`. Script: `backend/bench_local_vlm.py MODEL MMPROJ`.
- Decide the local role (fallback answers / background analysis) from the numbers; implement `local_fallback` in assistant_service (hook `_deps["local_fallback"]`; currently NoCloudVision -> HTTP 503).
- Presentation mode.

### Local AI (decisions so far)
- Network is slow (~120 KB/s). Qwen3.5-2B paused at 343 MB (resumable: `curl -C -`). Switched to
  **Qwen3.5-0.8B Q4_K_M + mmproj-F16** (738 MB) = realistic CPU fallback. Downloading.
- Piper 1.8.0 in `local-ai\piper-venv` (separate from backend). Voices: ar_JO-kareem, fr_FR-siwis, en_US-lessac.
- Planned: faster-whisper base (int8, CPU) in the same local venv; a small local sidecar (ASR + Piper)
  so the backend venv is unchanged until the user approves.
- Sample generator ready: `backend/make_voice_samples.py` (Fola / Azure / Piper, same 8 sentences) ->
  `backend/voice_compare/final/index.html`.

## Done since (2026-11-27 evening)
- Mobile: hands-free AUTO-START (Settings switch; opening the app -> camera -> stream -> hands-free),
  spoken connection status, server address + access code in the app (no rebuild), `Services/settings.ts`,
  `Components/AssistantSettings.tsx`. Typecheck clean.
- Backend: conversation context (last 3 turns, 3 min) + blind-user intent rules (pass? / where? / read /
  practical / follow-up / unintelligible -> ask to repeat); answer model stored per question.
- Security/network: access code now protects EVERY endpoint except /health (loopback = dashboard allowed);
  `/network` + startup banner with the recommended phone address; `backend/setup_firewall.ps1` (admin, narrow rules).
- APK: eas.json `preview` -> APK; app name "World Voice AI". **BLOCKED (needs user approval):**
  `npx expo install expo-build-properties` + `usesCleartextTraffic` (release APK must reach http://laptop).
- Dashboard redesigned (tabs Live / Visual memory / Q&A / Warnings / AI-voice-network / Photo mode), real data only;
  `src/api.ts`, `src/LiveCamera.tsx` (Adam's receiver moved), `src/views/*`, new `index.css`. tsc clean.
  New backend endpoint `/memory/alerts/history`. Browser test must be updated to the new UI.

### Hybrid answer architecture (implemented 2026-11-27 night)
Answer order in /ask: **Gemini 3.5 Flash Lite** (hears the audio) -> **Groq**: Whisper + qwen/qwen3.8-27b
(measured: vision OK, EN+AR OCR correct, 1.3-1.9 s; limits 1,000/day, 7,000 input tokens/min, ~1,800 tokens
per image; Whisper 2,000/day ~1 s) -> other Gemini models (paused 10 min after a timeout/503) ->
**local** Qwen (start_local_ai.ps1; local_ai.py) -> spoken "services busy" message (4 languages).
Background analysis: Gemini (reserve kept for questions) -> local (if WV_LOCAL_BACKGROUND=1) -> Groq
throttled (1 frame / 30 s, never near a question, <=300/day). Unchanged scenes reuse analysis.
- Scenario 2 (Gemini exhausted, REAL today): present questions correct via Groq (3.4-5.1 s answer);
  memory analysed by Groq; Lebanese past correct; fixes made after run 1 (best-matching memory image
  via search, token-limit wait 15 s, cool-down of failing Gemini models). Re-run pending.
- TTS during downloads is slow (connection saturated ~120 KB/s) -> re-measure latency when downloads end.
- Voices: `voice_compare/final/index.html` (Azure + Piper; Fola quota used up today -> regenerate Fola
  tomorrow). Whisper intelligibility: Azure 0.84, Piper 0.82 (Piper worse on Lebanese + English/French
  words, Jordanian voice). Recommendation: Piper = offline last resort only (needs user approval:
  add `piper` to WV_TTS_PROVIDERS).
- Local files: `backend/local_ai.py`, `backend/local_sidecar.py`, `backend/start_local_ai.ps1`,
  `backend/groq_vision.py`, `backend/check_quota.py`, `probe_models.py`, `probe_groq.py`,
  `check_voice_intelligibility.py`, `make_voice_samples.py`, `piper_synth.py`.
- Pending: Qwen 0.8B download (~1 h), faster-whisper base download, then bench_local_vlm.py and
  the 3-scenario voice-to-voice benchmark.

### Results so far (bench_voice_to_voice.py, 5 spoken questions, connection busy with downloads)
- Scenario 2 "gemini_out" (Groq): **5/5 correct** (now + past, EN/LB/FR); answer ready 3.6-6.8 s
  (12-13 s when Groq's per-minute token budget forces a wait); first word median 9.0 s (TTS slowed by downloads).
  Fixes that made it pass: Groq token planning (sliding minute, wait before sending, background only with room),
  unanalysed frames grouped by fingerprint into scenes, Groq memory collage (3 images for 1 image's cost).
- Dashboard: real-browser test 10/10 over all tabs (`test_dashboard_browser.py`, screenshots test_dashboard_<tab>.png);
  tabs deep-linkable (#memory, #system...); header + System tab show the real answer order (Gemini -> Groq -> ... -> local).
- Presentation mode: `WV_PRESENTATION_MODE=1` (background >= 15 s, 350/day reserve, no backup requests).
- Mobile: home = Live Assistant first (big), Photo mode second, Settings collapsible; live screen = red
  "Stop camera and microphone", manual start buttons only as fallback. tsc clean, harnesses 7/7 + 9/9.
- Privacy timing under load: face frames ~119/153 ms (limit 150) -> known note, not optimised further (user asked).
- Offline regression: memory 14/14, quota 17/17, warnings 17/17, stream 11/11, privacy 19/20 (timing note).

### State at the end of the night session (2026-11-27 ~late)
- Internet nearly down (speed test: 0 KB in 25 s). Downloads stalled but resumable:
  mmproj-Qwen3.5-0.8B-F16.gguf at 17 MB / 205 MB (`curl -C -` resumes), faster-whisper base
  (sidecar downloads it on start). Qwen3.5-0.8B-Q4_K_M.gguf COMPLETE (533 MB).
- Sidecar process may still be running (port 8092); llama-server not started yet.
- Docs written: backend/ARCHITECTURE.md, PRESENTATION.md, RUNNING.md (networks/APK/local AI/presentation),
  BENCHMARK_REPORT s.18, CLAUDE_CHANGES s.10, README (architecture, requirement rows).

### User approvals (latest message)
- APPROVED: expo-build-properties + standalone APK -> installed (~58.0.12), app.json plugin
  `usesCleartextTraffic: true` (verified in `expo config`), EAS build `preview` (APK) started:
  https://expo.dev/accounts/world-voice-ai-team/projects/mobile/builds/dff6de62-2c75-4786-9004-59337c5b4dd3
- Keep the Qwen 2B partial download (do not delete); Piper = offline last-resort candidate (user will listen);
  Tailscale postponed.
- Found and fixed: `backend/.venv` (350 MB) was NOT git-ignored (it went into the EAS upload; would have been
  committed). `.venv/` now in .gitignore. Nothing secret was in it.
- `test_access_code.py` **13/13**: phone (network address) needs the code for everything except /health;
  WebRTC publish, /ask, speech audio, warnings work with it; dashboard (loopback) works without it.

### RESUME HERE (in order)
1. When the download finishes: `start_local_ai.ps1`; `bench_local_vlm.py` with the 0.8B files;
   `bench_local_asr.py`; decide local roles (answers only if acceptable; background only if accurate).
2. `bench_voice_to_voice.py offline` (needs local services) and, after 10:00 (Gemini reset),
   `bench_voice_to_voice.py normal` + `make_voice_samples.py` (Fola samples) + `test_live_latency.py`.
3. Full regression (network up): assistant_flow, multi_device, warnings --live, privacy --live,
   dashboard_sync, run_validation --live, voice chain; mobile harnesses + tsc + export; dashboard build.
4. README test-results + limitations update; ask user: approvals (expo-build-properties for APK,
   Piper as offline voice, Tailscale), voice samples, phone tests.

## Next phases (plan order)
4. Hands-free auto-start, practical intelligence prompts, warnings with local support.
5. Voices: Piper evaluation + samples for approval (do NOT switch voice without approval).
6. Targeted accuracy/latency tests.
7. Professional dashboard + accessible mobile UI.
8. Docs (README, DATABASE, RUNNING, BENCHMARK_REPORT s.18+, CLAUDE_CHANGES s.10+), GitHub prep, presentation guide.
