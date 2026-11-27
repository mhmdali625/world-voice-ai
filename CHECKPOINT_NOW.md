# CHECKPOINT: 2026-11-27 night (read this first, then PROGRESS.md)

Nothing committed or pushed. Code checkpoint copies: `C:\Multimedia Labs\Project-checkpoint-20261127-phase1-start`
(start of the final plan) and earlier ones (see PROGRESS.md).

## JUST IMPLEMENTED, NOT YET TESTED (test these first)
1. **Local-first background analysis** (`backend/memory_service.py`: `_local_analyzer`, `_confirm_hazards`,
   `_pending(limit, overflow_before)`, `_pending_local`, `LOCAL_OVERFLOW_S=45`; `local_ai.py` BACKGROUND default ON,
   new ANALYSIS_RULES + `_clean()` against copied placeholders; `hazards.MAX_FRAME_AGE_S` 20 -> 45 s
   (freshness still guaranteed by the live-view check); `test_warnings.py` updated to use the constant).
   - Run: `test_warnings.py` (offline), `test_memory.py`, `test_quota.py`, `test_stream.py`;
     then with local AI running (`start_local_ai.ps1`) a live walk: frames indexed by "local", door obstacle confirmed
     by cloud, overflow to cloud after 45 s; then `test_live_latency.py` to check answers are NOT slowed by
     background local inference (CPU). If they are: lower llama-server priority (BelowNormal, add to
     start_local_ai.ps1) or reduce threads.
2. **Offline detection** (`backend/neterrors.py`: `is_network_error`, `probably_offline`; used in `vision.py`,
   `speech_jobs.py`, `assistant_service._fallback_answer`). Offline scenario now answers (no HTTP 500).
3. **Network errors in speech jobs** move to the next voice (no failure).
4. Mobile: 401 (wrong access code) messages (LiveAskPanel, WebRTCCameraTest). tsc was clean before these;
   re-run `npx tsc --noEmit` in mobile.

## APK BUILD #2: FINISHED (standalone, no Metro)
Download: https://expo.dev/artifacts/eas/ANBUfltzB5NtPfYwrJLkCZo1h334WK_1CUAVBPPk0NA.apk
(Does not yet include the 401 "access code" messages; rebuild later with
`npx eas-cli@latest build -p android --profile preview --non-interactive --no-wait`.)

## ACTIVE / PENDING
- **APK build #2** (WebRTC R8 fix: extraProguardRules keep org.webrtc) running on EAS:
  https://expo.dev/accounts/world-voice-ai-team/projects/mobile/builds/4348a8fe-62a6-4b5a-96be-f1c9f6fa0f1a
  Build #1 failed: R8 "Missing class org.webrtc.Dav1dDecoder$Natives". Check:
  `npx eas-cli@latest build:list --limit 1 --platform android --non-interactive --json`.
  Logs: use Git Bash `curl --compressed <logFiles url>` (logs are Brotli-compressed).
  NOTE: the 401 message changes are NOT in build #2 (rebuild later).
- **Qwen3.5-2B download resuming** (background curl with retries) into `C:\Multimedia Labs\local-ai\models`
  (gguf was 343 MB / 1,281 MB; mmproj-Qwen3.5-2B-F16.gguf 121 MB / 668 MB). Resume command if stopped:
  `curl -sSL -C - -o <file> https://huggingface.co/unsloth/Qwen3.5-2B-GGUF/resolve/main/<Qwen3.5-2B-Q4_K_M.gguf | mmproj-F16.gguf>`
  Then: `bench_local_vlm.py <2B gguf> <2B mmproj>` and `bench_local_analysis.py Qwen3.5-2B`; decide if 2B is used for
  offline ANSWERS (Arabic) — too slow for continuous background (~2.5x 0.8B).
- Running processes: local sidecar (python local_sidecar.py, port 8092, faster-whisper base + Piper loaded) and
  llama-server 0.8B (port 8091). Stop: `start_local_ai.ps1 -Stop`.
- Gemini 3.5 Flash Lite daily quota resets ~10:20 Lebanon time (2026-11-28). Then: `bench_voice_to_voice.py normal`,
  `make_voice_samples.py` (Fola column), `test_live_latency.py`, full regression.

## BENCHMARK RESULTS TONIGHT
| Test | Result |
|---|---|
| access code (`test_access_code.py`) | 13/13 (phone needs code; WebRTC/ask/audio/warnings OK; dashboard loopback OK) |
| dashboard real browser | 10/10 |
| quota (simulated) | 17/17 |
| scenario Gemini-unavailable (Groq) | 5/5 correct, first word median 9.0 s |
| offline scenario (local 0.8B + faster-whisper + Piper) | 2/5 (EN/FR now correct; Lebanese -> honest "unavailable" since 0.8B answers Arabic badly; past questions fail: no memory analysis offline before local-first) ; 29-40 s with the simulated phone encoding on the same CPU (isolated: ASR 3.4 s + VLM ~5 s) |
| local Q&A 0.8B (`bench_local_vlm.py`) | 5/8; EN/FR OCR OK; Arabic misread; Lebanese answered in English; ~5 s/answer, 1.6 GB RAM |
| local ANALYSIS 0.8B (`bench_local_analysis.py`, 1024 px) | OCR Latin 6/6, Arabic 1/1, obstacles 4/4 (768 px: 3/4); ~17-20 s per frame |
| faster-whisper base vs Groq Whisper | 0.74 vs 0.89 similarity; 3.4 s vs 2.0 s; weak on accents/mixed |
| Piper | rejected by the user as robotic (no voice integration without approval) |

## VOICES (user rejected the robotic voices; integrate NOTHING without approval)
Research done (agent): Habibi-TTS (F5-TTS based, free/non-commercial, LEV id untested, HF Space
https://huggingface.co/spaces/chenxie95/Habibi-TTS, cloning), Audar-TTS Flash (0.55B, open licence, cloning 5-15 s,
Lebanese not claimed, no hosted demo), Hamsa (tryhamsa.com: 4 Lebanese voices karim-lb/lara-lb/tarek-lb/rita-lb,
free tier unclear 25 min/month vs 50 credits, needs an account -> USER must sign up to get samples).
Downloaded small official samples: `backend/voice_compare/candidates/audar/*.wav` (Arabic/English, clones of the
*_source.wav refs) and `backend/voice_compare/candidates/habibi_refs/*` (reference prompts, NOT Habibi outputs).
Next: make an index.html for them; try Habibi HF Space API for Lebanese sentences (no model download);
the user wants to hear samples before ANY integration.

## NEEDS THE USER
1. Install the APK when build #2 succeeds; run PHONE_TEST.md section A (Wi-Fi + hotspot, no Metro).
2. Listen to `backend/voice_compare/candidates/audar/` samples; Hamsa needs a free account to hear Lebanese voices.
3. Physical phone tests (PHONE_TEST.md).
4. Team-responsibilities table in README.
