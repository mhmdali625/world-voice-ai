# Physical phone acceptance test (about 30 minutes)

Run with the **standalone APK** (no Metro). Note any failure as: step number, what you heard,
the details line under the answer (`answer ready ... | first word ...`), a dashboard screenshot.

## 0. Setup

1. Laptop: `backend\setup_firewall.ps1` once (as Administrator).
2. `backend/.env`: `WV_ACCESS_TOKEN=<a code you choose>`.
3. Start the backend (it prints the address to use) and the dashboard (RUNNING.md).
4. Install the APK on the phone (download link from the EAS build page).

## A. Network and startup (APK, no Metro)

| # | Do | Pass if |
|---|---|---|
| A1 | Home Wi-Fi: open the app > Settings > Server address = the address the backend printed, Access code = your code > **Test** | "Connected to http://…" is spoken (TalkBack) / shown |
| A2 | Wrong access code > Test | "The laptop needs the access code" |
| A3 | Settings: turn on "Start the assistant hands-free when the app opens"; close and reopen the app | camera starts, "Assistant connected" earcon, it listens without any tap; dashboard: Phone live |
| A4 | Phone hotspot: connect the laptop to it, restart the backend, set the new address in Settings > Test, reopen the app | same as A3 |
| A5 | During streaming: phone Wi-Fi/hotspot off 5 s, then on | "connection problem" message once, then reconnects by itself; listening resumes |

## B. Conversation (hands-free)

| # | Say (no tap) | Pass if |
|---|---|---|
| B1 | "What is in front of me?" | short, correct description; it listens again after the answer (earcon) |
| B2 | Point at a sign: "شو مكتوب عاللافتة؟" | reads the sign exactly, Lebanese answer |
| B3 | Turn away: "What was written on the sign I just passed?" | correct, says roughly when |
| B4 | At a table: "If I want to put my laptop here, what should I move?" | names the visible objects that are in the way |
| B5 | "فيني مرق؟" in front of a closed door | says the door is closed and must be opened; never "safe" |
| B6 | "Where is the car?" (none) | "I don't see…" / not sure; no invention |
| B7 | French with a Lebanese accent: "Qu'est-ce qu'il y a devant moi ?" | answer entirely in French |
| B8 | Formal Arabic: "ماذا يوجد أمامي؟" | formal Arabic answer |
| B9 | Follow-up after B1: "and on the left?" | answers about the left side |
| B10 | Mumble / cough only | nothing sent, or "please repeat" |
| B11 | During an answer: main button "Stop Speaking"; then "Repeat Answer" | stops at once; repeat plays at once |
| B12 | Check the answer's details line + dashboard Q&A | "first word … s" shown on both; note the values |

## C. Warnings, privacy, enhancement

| # | Do | Pass if |
|---|---|---|
| C1 | Point at a closed door / chair in the way, stay silent | spoken warning in your last language, with position; not repeated within 45 s |
| C2 | Turn away right after the warning | no late warning about it |
| C3 | A picture or sign on a wall | no warning |
| C4 | Warning while hands-free is listening | the warning is spoken, the assistant does NOT answer the warning itself, listening resumes |
| C5 | A person who agrees, or a framed photo of a face | dashboard Visual memory: "faces blurred", face unrecognisable, sign next to it sharp |
| C6 | Dim room / dark corner | dashboard: "enhanced: brightened"; the answer still correct |

## D. Accessibility and stop

| # | Do | Pass if |
|---|---|---|
| D1 | TalkBack on: swipe through the Live screen | every control has a clear label; the order is logical |
| D2 | "Stop camera and microphone" | camera and listening stop at once; TalkBack confirms |
| D3 | Hands-free switch off | listening stops, TalkBack "Hands-free off" |
| D4 | Photo mode (home): take a photo, ask | answer spoken as before |
