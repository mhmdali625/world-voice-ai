# World Voice AI: presentation and live demo guide (2026-12-02)

## The day before

1. Charge the phone and the laptop; bring the USB cable (fallback network: `adb reverse`).
2. Do **not** run long tests on the day: Gemini's free quota (500 requests/day per model)
   resets at **10:00 Lebanon time** (midnight Pacific). A full test run can use it up.
3. In `backend/.env`: `WV_PRESENTATION_MODE=1` (conserves quota, no feature removed) and an
   access code `WV_ACCESS_TOKEN=...` (university Wi-Fi is shared). Type the same code in the
   phone's Settings.
4. Run once, as Administrator: `backend\setup_firewall.ps1` (hotspots are "Public" networks).
5. Record the backup video (below).
6. Print or open `backend/RUNNING.md` (checklist) as a cheat sheet.

## Network for the demo (most reliable first)

1. **Phone 4G hotspot**: laptop joins the phone's hotspot. Independent of the university Wi-Fi.
2. University Wi-Fi only if phone and laptop can reach each other (Settings > Test on the phone).
3. USB: `adb reverse tcp:8000 tcp:8000`, server address `localhost:8000`.

## Start (10 minutes before)

```powershell
# 1. backend (prints the phone address + the answer order)
cd "C:\Multimedia Labs\Project\backend"
.venv\Scripts\python -m uvicorn main:app --host 0.0.0.0 --port 8000

# 2. dashboard -> http://localhost:5173 (projector)
cd "C:\Multimedia Labs\Project\dashboard"
npm run dev

# 3. optional offline fallback (local AI)
powershell -ExecutionPolicy Bypass -File "C:\Multimedia Labs\Project\backend\start_local_ai.ps1"
```

Phone: open World Voice AI. With "Start the assistant hands-free" on, it starts the camera,
streams and listens by itself. Check the dashboard header: Backend online, Phone live, AI
model, Voice.

## Demo script (about 8 minutes)

| # | Show | Say / do | Dashboard tab |
|---|---|---|---|
| 1 | Hands-free start | Open the app: no button pressed; earcon "connected" | Live: video appears |
| 2 | Current view | "What is in front of me?" | Live: latest answer + first-word time |
| 3 | Reading a sign | point at a printed sign: "What does the sign say?" (Lebanese: "شو مكتوب عاللافتة؟") | Visual memory: the text |
| 4 | Practical question | at a table: "What must I move to put my laptop here?" | Q&A |
| 5 | Visual memory | turn away from the sign: "What was written on the sign I just passed?" | Q&A: the frame used |
| 6 | Multilingual | same question in English, French, Lebanese | Q&A: language chips |
| 7 | Warning (bonus) | point at a closed door / chair in the way, stay silent | Warnings: spoken, then withdrawn when you turn away |
| 8 | Privacy | a framed photo of a face (or a volunteer who agrees) in view | Visual memory: "faces blurred" tag |
| 9 | Image enhancement (bonus) | dim the light / point at a dark corner | Visual memory: "enhanced: brightened" |
| 10 | Fallback | tab *AI, voice & network*: answer order and quotas; explain Groq / local fallback | System |
| 11 | Stop | "Stop camera and microphone" button (TalkBack) | Live: offline |

## If something fails

| Problem | Do |
|---|---|
| Phone cannot connect | phone Settings > Test; use the hotspot; or USB `adb reverse` |
| "The AI services are busy" | quota or outage: the next provider answers automatically; show the System tab; if all fail, play the backup video |
| Slow answers | normal on degraded networks (Gemini 2-8 s); mention the measured medians |
| No sound | Repeat Answer; check the phone volume |

## Backup video (record the evening before)

Screen-record the phone (Android screen recorder, with sound) and the laptop (Xbox Game Bar:
Win+Alt+R) while doing steps 1-9. Keep it on the laptop desktop.

## Limitations to state honestly

- An assistive tool, not a replacement for a white cane, guide dog or mobility training; it never
  says a path is safe.
- Free cloud tiers: Gemini 500 requests/day per model, Groq 1,000/day and 7,000 tokens/min,
  Gemini TTS 10 answers/day per model. Answers take ~3-5 s normally, longer on slow networks or
  when a fallback provider answers.
- Face detection can miss very small or turned-away faces (those are not recognisable either).
- Offline mode works on the laptop CPU with smaller models: slower and less accurate.

## Team responsibilities

Fill in `README.md` > Team responsibilities before submission.
