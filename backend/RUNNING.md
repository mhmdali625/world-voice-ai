# World Voice AI backend: running and demo checklist

## Start (one process, port 8000)

```powershell
cd "C:\Multimedia Labs\Project\backend"
.venv\Scripts\python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

This single server provides the live stream (`/stream/*`), the visual memory
(`/memory/*`), the Live Assistant (`/ask`), photo mode (`/analyze`),
`/transcribe`, speech (`/tts/*`), `/api/images` and `/health`. The Node.js relay
(`local-relay/`, port 3001) is no longer needed: the phone streams to this
server and the dashboard watches through it. The old Whisper service on port
8002 is not needed either (`uvicorn whisper_service:app --port 8002` still works).

Memory is stored in `backend/storage/memory/` (SQLite + JPEG frames), deleted
after 30 minutes; `WV_MEMORY_DIR` overrides the folder.

Check: open `http://127.0.0.1:8000/health`.

## Phone connection (Wi-Fi, 4G hotspot, Tailscale)

The backend prints the address to use when it starts
(`[NETWORK] ... <- use this`); the dashboard shows it too (tab *AI, voice &
network*). On the phone: **Settings > Server address**, type it (e.g.
`192.168.0.100`; `:8000` is added) and tap **Test**: no rebuild, no Metro
restart.

| Network | Steps |
|---|---|
| Home / university Wi-Fi | phone and laptop on the same Wi-Fi; use the address marked "use this" |
| Phone 4G hotspot (recommended for the presentation) | turn on the phone hotspot, connect the laptop to it, restart the backend, use the new address it prints |
| University Wi-Fi that blocks device-to-device traffic | use the phone hotspot, or Tailscale |
| Different networks (e.g. laptop at home, phone on 4G) | install Tailscale (free) on laptop and phone, sign in to the same account, use the laptop's `100.x.y.z` address |

First time only, as Administrator (hotspots and university networks are
"Public" in Windows, which blocks the phone):

```powershell
powershell -ExecutionPolicy Bypass -File "C:\Multimedia Labs\Project\backend\setup_firewall.ps1"
```

Shared networks: set an access code in `backend/.env` (`WV_ACCESS_TOKEN=...`)
and type the same code in the phone's Settings. Every endpoint except
`/health` then requires it (the dashboard on the laptop is always allowed).

USB alternative (independent of any network): `adb reverse tcp:8000 tcp:8000`
and server address `localhost:8000`.

White screen in the development app = it cannot reach Metro (port 8081):
same Wi-Fi, `setup_firewall.ps1 -Metro`, or `adb reverse tcp:8081 tcp:8081`.
The standalone APK (below) has no Metro and no white-screen risk.

## Standalone Android APK (no Metro, no QR code)

Requires one approval-gated step first (release builds block plain `http://`):

```powershell
cd "C:\Multimedia Labs\Project\mobile"
npx expo install expo-build-properties
# app.json "plugins": add ["expo-build-properties", {"android": {"usesCleartextTraffic": true}}]
npx eas-cli@latest build -p android --profile preview      # free EAS build, signs in to your Expo account
```

The `preview` profile produces an installable `.apk` (eas.json). Install it,
open it, set the server address once in Settings.

## Optional: local (offline) AI

Used only when no cloud model can answer (no internet, quotas used up):

```powershell
powershell -ExecutionPolicy Bypass -File "C:\Multimedia Labs\Project\backend\start_local_ai.ps1"
```

Starts llama.cpp (Qwen3.5 vision model) on 127.0.0.1:8091 and the speech
sidecar (faster-whisper + Piper) on 127.0.0.1:8092. Files live in
`C:\Multimedia Labs\local-ai` (not in the repository). See ARCHITECTURE.md.

## Presentation mode

`WV_PRESENTATION_MODE=1` in `backend/.env` conserves cloud quota during a
demo without removing any feature: background analysis at most every 15 s,
350 daily requests per model kept for questions, no backup requests.
Do not run long tests on the morning of the presentation (Gemini quota
resets at midnight Pacific = 10:00 in Lebanon).

## Configuration (backend/.env, all optional except the keys)

| Variable | Default | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | (required) | Vision + TTS key; always used explicitly |
| `GROQ_API_KEY` | (required) | Whisper |
| `WV_VISION_MODEL` | `gemini-3.5-flash-lite` | Vision model |
| `WV_VISION_THINKING` | `low` | `minimal`/`low`/`medium`/`high`/empty |
| `WV_WHISPER_PROMPT` | `natural` | `natural` / `none` / `legacy` |
| `WV_TTS_MODE` | `single` | Legacy NDJSON endpoint only: `single` or `chunked` |
| `WV_TTS_PROVIDERS` | `gemini` | Approved voices in order, e.g. `azure,gemini`. Each answer uses the first available one; if it fails BEFORE any audio, the same answer moves to the next one. Never mixed inside an answer |
| `AZURE_SPEECH_KEY`, `AZURE_SPEECH_REGION` | empty | Azure AI Speech free F0 (0.5 M chars/month), voices `ar-LB-RamiNeural` / `ar-LB-LaylaNeural` |
| `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID`, `ELEVENLABS_MODEL` | empty / empty / `eleven_multilingual_v2` | ElevenLabs free plan (~10,000 chars/month, non-commercial) |
| `WV_TTS_PROVIDER` | empty | Older single-provider setting (`file` = test audio) |
| `WV_TTS_START` | `smooth` | `smooth` = start at the earliest moment with zero interruptions; `fast` = start after 1.5 s |
| `WV_VISION_HEDGE_AFTER` | `3.5` | Seconds before a backup vision request is sent (0 = off) |
| `WV_ASR_HEDGE_AFTER` | `2.5` | Same for Whisper (0 = off) |
| `WV_ASR_LANGUAGE_CHECK` | `en` | Parallel English decode that corrects English (or mixed) speech misdetected as Arabic; empty = off |
| `WV_TTS_SPLIT` | `1` | Parallel speech parts per long answer (1-3), merged into one stream; each part = 1 TTS request |
| `WV_TTS_MODELS` | flash-lite-tts, flash-tts | Fola models tried in order when a daily quota runs out |
| `WV_TTS_SWITCH_AFTER` | `4` | Seconds Fola may take to be ready before the answer moves to the next approved voice (0 = never) |
| `WV_AZURE_KEEPALIVE` | `0` | Azure connection ping (faster first Rami answer after idle). Off: billing impact not verifiable |
| `WV_TTS_TEST_WAV` | empty | WAV used by the `file` provider (24 kHz mono 16-bit) |
| `WV_AZURE_AR_RATE` | `+15%` | Rami speaking rate (Rami is ~23 % slower than Fola); empty = Azure default |
| `WV_LIVE_TTS_SWITCH_AFTER` | `2.5` | Live Assistant answers: seconds Fola may take before the answer moves to Rami |
| `WV_MEMORY_DIR` | `backend/storage/memory` | Visual memory folder (SQLite + JPEG frames) |
| `WV_MEMORY_RETENTION_MIN` | `30` | Frames older than this are deleted (with their files) |
| `WV_MEMORY_MAX_FRAMES` | `600` | Storage cap per phone (oldest deleted first) |
| `WV_MEMORY_ANALYZE_EVERY` | `8` | Seconds between background analysis requests (4 frames each) |
| `WV_FACE_SCORE` | `0.5` | Face detector threshold (lower = blur more readily) |
| `WV_FACE_MODEL` | `backend/models/face_detection_yunet_2023mar.onnx` | YuNet face model (OpenCV zoo, MIT) |
| `WV_PHOTO_RETENTION_MIN` | `30` | Photo Mode blurred photos deleted after this many minutes |
| `WV_PHOTO_DIR` | `backend/storage/blurred` | Photo Mode folder |
| `WV_VISION_MODELS` | `gemini-3.5-flash-lite,gemini-3.1-flash-lite,gemini-3.5-flash` | Gemini models in order (each has its own daily quota) |
| `WV_VISION_ATTEMPT_TIMEOUT` | `12` | A model that has not answered a waiting user in this time hands over to the next |
| `WV_GEMINI_RPD` | `500` | Daily requests per Gemini model (measured free tier) |
| `WV_USER_DAILY_RESERVE` | `250` (`350` in presentation mode) | Daily requests per model never used by background analysis |
| `WV_PRESENTATION_MODE` | `0` | `1` = conserve cloud quota (see above) |
| `WV_LOCAL_VLM_URL`, `WV_LOCAL_SIDECAR_URL` | `http://127.0.0.1:8091`, `:8092` | Local AI services (start_local_ai.ps1) |
| `WV_LOCAL_BACKGROUND` | `0` | `1` = local model may analyse frames when the cloud cannot |
| `WV_ACCESS_TOKEN` | empty | Require `X-WV-Token` on AI endpoints (set `EXPO_PUBLIC_WV_TOKEN` too) |

## Free-tier limits that matter

- Gemini vision: **15 requests per minute**, shared by background frame
  analysis (at most 1 request per 8 s, up to 4 frames each) and questions.
  Questions have priority; background analysis waits while one is answered.

- `gemini-3.8-flash-lite-tts`: **10 requests per day** (stated by the API's own
  429 response). Each spoken answer costs 1 request in `single` mode. Do not
  rehearse with live TTS on the demo day; check quota in AI Studio.
- Vision latency on the free tier varies a lot (measured 2.0–20.8 s for the same request).

## Test the phone playback with ZERO TTS quota

```powershell
cd "C:\Multimedia Labs\Project\backend"
powershell -ExecutionPolicy Bypass -File .\dev_offline_tts.ps1             # normal x1.5
powershell -ExecutionPolicy Bypass -File .\dev_offline_tts.ps1 -Rate 0.63  # slow
```

Switch speed between answers without restarting (second terminal):

```powershell
Invoke-RestMethod -Method Post "http://127.0.0.1:8000/dev/tts-test?rate=0.63"
Invoke-RestMethod -Method Post "http://127.0.0.1:8000/dev/tts-test?rate=1.5"
Invoke-RestMethod -Method Post "http://127.0.0.1:8000/dev/tts-test?policy=fast"    # compare
Invoke-RestMethod -Method Post "http://127.0.0.1:8000/dev/tts-test?policy=smooth"
Invoke-RestMethod -Method Post "http://127.0.0.1:8000/dev/tts-test?split=2"        # pipelined speech
```

Live Gemini check once the daily quota has reset (uses exactly 3 TTS requests):
`.venv\Scripts\python bench_tts_live.py` -> recommends `WV_TTS_SPLIT`.

Every answer plays the saved Fola sample through the real single-stream path.
Laptop-only check of the same path: `.venv\Scripts\python test_phone_flow.py`.
Quota state of the real models: `http://127.0.0.1:8000/tts/quota`.

## Manual acceptance checklist (one pass, about 25 minutes)

Test only with people who agree to be filmed. Point the camera at signs and
doors, not at faces.

### 0. Start (three terminals)

```powershell
# Terminal 1 - backend
cd "C:\Multimedia Labs\Project\backend"
.venv\Scripts\python -m uvicorn main:app --host 0.0.0.0 --port 8000

# Terminal 2 - dashboard (then open http://localhost:5173)
cd "C:\Multimedia Labs\Project\dashboard"
npm run dev

# Terminal 3 - phone app (development build installed on the phone)
cd "C:\Multimedia Labs\Project\mobile"
npx expo start --dev-client -c
```

Phone and laptop on the same Wi-Fi; `mobile/.env` =
`EXPO_PUBLIC_BACKEND_URL=http://<laptop IPv4>:8000` (`ipconfig`), or use USB
with `adb reverse tcp:8000 tcp:8000` and `http://localhost:8000`. Check
`http://127.0.0.1:8000/tts/quota` (which voice answers next).

Turn TalkBack on for steps 1, 6 and 14 and off for the others.

### A. Live Assistant (streaming)

| # | Do | Expect |
|---|---|---|
| 1 | Home → **Live Assistant** → **Start Streaming** | Phone says/shows "Live"; dashboard badge "Phone Streaming"; live video; fps shown |
| 2 | Walk slowly past 2–3 signs or doors with text | Dashboard timeline adds frames within seconds, then their text; no runs of identical frames; blurry or shaky moments skipped |
| 3 | Point at a sign → **Ask a Question**: "What does the sign in front of me say?" → tap again | Answer spoken within about 3–5 s; question, answer and time appear in the dashboard history |
| 4 | Turn away; ask "What was written on the door I just passed?" | Answer from memory, with roughly when it was seen; the dashboard shows the frame used under the answer |
| 5 | The same in Lebanese ("شو كان مكتوب عالباب يلي مرقنا حدّو؟"), formal Arabic ("ماذا كان مكتوبًا على الباب؟"), French ("Qu'est-ce qui était écrit sur la porte ?") and mixed ("Can you tell me شو مكتوب عاللافتة؟") | Correct answer, spoken in the language used, natural voice, no cuts |
| 6 | Ask "Where is the car?" (never seen) | "I'm not sure" / "I don't see a car", no invention |
| 7 | With a person who agrees in view (and a framed photo of a face): "Who is this?" | Says a person is there, does not identify them. In the dashboard timeline that frame shows the tag "faces blurred: N" and the face is unrecognisable; the sign or objects next to it stay sharp |
| 8 | Point at a closed door or stairs and wait without asking; then turn away | Warning in the language of your last question, with the position ("devant vous", "قدّامك"...); not repeated within 45 s; after turning away, no late warning about it; a picture or sign on a wall gives no warning; never the word "safe" |
| 8b | Turn **Hands-free** on; ask a question without touching the screen; after the answer ask another | It listens again by itself after each answer (earcon), sends when you stop speaking, never records its own answer or a warning; the main button "Stop Listening" or the switch turns it off (TalkBack announces it) |
| 8c | English with your accent; French spoken by an Arabic speaker; formal Arabic ("ماذا يوجد أمامي؟") | Answers in the language spoken: English → English, French → French only, formal Arabic → formal Arabic |
| 8d | Look at the details line under an answer, and the dashboard Q&A | "answer ready N s" and "first word N s" on the phone; "first word after N s" on the dashboard |
| 9 | Tap **Repeat Answer**; during an answer tap **Stop Speaking** | Repeat starts at once; Stop silences immediately |
| 10 | Phone Wi-Fi off 5 s, then on | Stream reconnects by itself; dashboard video returns; memory still there |
| 11 | (Optional, second phone) Start streaming on it too | Dashboard "Phone shown" selector lists both; each phone's answers use only its own view and memory |
| 12 | Dashboard **Clear Memory** → **Yes, delete** | Timeline and history of the phone shown become empty |

### B. Photo mode (existing)

| # | Do | Expect |
|---|---|---|
| 13 | Speak Question: "Describe what is in front of me." → Open Camera → Capture | English answer; voice starts automatically |
| 14 | "خبرني شو في قدامي" → Capture | Lebanese answer |
| 15 | "شوف إذا الـ charger موصول بالـ laptop" → Capture | Loanwords spoken naturally |
| 16 | During an answer turn Wi-Fi off 3 s, then on | Voice pauses, then continues near the same point |

### C. Voices

| # | Do | Expect |
|---|---|---|
| 17 | Listen to a Rami answer (after Fola's 10 daily answers, or set `WV_TTS_PROVIDERS=azure` for this test) | Natural Lebanese tempo (+15 %), loanwords pronounced the Lebanese way, no robotic voice |
| 18 | Keep asking until Fola's daily quota ends | Answers continue in Rami; one answer never mixes two voices |

### What to note for me

For any failure: the test number, what you heard, the details line under the
answer (`Evidence ... | answer N s`, or `first sound ... s | interruptions N`),
and a screenshot of the dashboard if relevant.

## Compare natural voices before choosing one

1. Create free keys (no paid plan needed):
   - Azure: portal.azure.com -> create "Speech service", pricing tier **Free F0**,
     copy Key 1 and the Region (e.g. `westeurope`).
   - ElevenLabs: elevenlabs.io free account -> Profile -> API key; pick an
     Arabic-capable voice in the Voice Library and copy its Voice ID.
2. Add them to `backend/.env` (never commit this file):
   ```
   AZURE_SPEECH_KEY=...
   AZURE_SPEECH_REGION=westeurope
   ELEVENLABS_API_KEY=...
   ELEVENLABS_VOICE_ID=...
   ```
3. `.venv\Scripts\python compare_voices.py` and open
   `backend\voice_compare\index.html`: every voice reads the same Lebanese,
   English, French and mixed answer, next to the approved Fola reference, with
   time-to-first-audio and speed under each player.
   (`--with-gemini` adds live Fola; costs 4 TTS requests.)
4. Choose the order, e.g. `WV_TTS_PROVIDERS=azure,gemini` (fast voice first,
   Fola when available) or `gemini,azure` (Fola first, Azure when Fola's daily
   quota is used up). Restart the backend.

Current state of every provider: `http://127.0.0.1:8000/tts/quota`.

## Tools

```powershell
.venv\Scripts\python benchmark.py analyze photo.jpg "شو في قدامي؟"
.venv\Scripts\python benchmark.py asr question.m4a --expected "خبرني شو في قدامي"
.venv\Scripts\python benchmark.py tts "النص" --lang ar-LB      # uses 1 TTS request
.venv\Scripts\python generate_voice_clips.py ar-LB             # uses 1 TTS request
.venv\Scripts\python generate_earcons.py                       # offline
```
