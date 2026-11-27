# Regression checkpoint: 2026-11-27, evening

Where work stopped after the phone-test fixes (language, warnings, hands-free,
current view, first-word latency). Nothing has been committed or pushed.
Code checkpoint taken before these fixes:
`C:\Multimedia Labs\Project-checkpoint-20261127-before-phone-fixes`.

## 1. Changes in this round (all implemented, typecheck clean)

| Area | Change | Files |
|---|---|---|
| Language | The prompt decides the language from the words, never the accent; formal Arabic → formal Arabic; no added languages. New schema field `spoken_language` written before the answer; a wrong-language answer is detected (`language_mismatch`) and corrected once before speaking | `backend/main.py` (prompt section 6), `backend/assistant_service.py` |
| Warnings | New `backend/hazards.py`: obstacles in the walking path only, position, labels in 4 languages; alert only while the live view still shows the scene (dHash), expires after 10 s, no repeat within 45 s, message in the user's language, uncertainty wording, never "safe" | `backend/hazards.py`, `backend/memory_service.py` (`/memory/alerts?device=&lang=`) |
| Current view | `/ask` uses the newest decoded frame (view age ~0.02 s); the stream's latest frame updates even while memory is busy; rule: present questions use only the current view | `backend/stream_service.py`, `backend/assistant_service.py` |
| Latency | `/ask` returns `qa_id`; `POST /ask/{qa_id}/first-word`; `qa.first_word_s` column (auto-migration); phone shows and reports "first word N s"; dashboard shows it | backend, `mobile/Services/aiService.ts`, `dashboard/src/LiveMemoryPanel.tsx` |
| Hands-free | Switch + automatic listening after each answer; voice-activity detection (`mobile/Services/voiceActivity.ts`); the microphone is never open while an answer or warning plays; main button "Stop Listening" | `mobile/Components/LiveAskPanel.tsx` |
| Incident | `npx expo lint` auto-installed ESLint (unapproved). Fully reverted: package.json and package-lock.json are byte-identical to the checkpoint, eslint.config.js removed, 239 packages pruned | - |

## 2. Regression results (final run)

| Suite | Result |
|---|---|
| `test_warnings.py --live` (warnings, language check, fresh view) | **24/24** |
| `bench_language.py` (12 spoken questions, native + accented, 5 languages) | **12/12**, 0 corrections needed (ran just before the full run) |
| `test_assistant_flow.py` | **15/15** |
| `test_multi_device.py` | **20/20** |
| `test_memory.py --live` | **22/22** |
| `test_stream.py` | **11/11** |
| `test_dashboard_sync.py` | **13/13** |
| `run_validation.py` (offline) | **26/26** |
| `bench_memory_db.py --quick` | **13/13** |
| `mobile/tests/speechPlayer.harness.cjs` | **7/7** |
| `mobile/tests/voiceActivity.harness.cjs` (hands-free) | **9/9** |
| mobile `tsc --noEmit`, Android `expo export`, dashboard `tsc -b` + `vite build` | pass |
| `test_live_latency.py` | 6/6 answers correct, 6/6 first-word values recorded on the dashboard; first word median **4.27 s**, 4/6 within 5 s (3.35–4.31 s); two slow ones: 6.09 s (Gemini 4.2 s) and 8.60 s (Gemini 6.1 s, plus the 2.5 s Fola → Rami switch) |
| `test_privacy.py --live` | **26/27**, see issue B |
| `bench_past_questions.py 3` | **FAILED**: Lebanese 1/3, English 0/3, see issue A |
| `test_dashboard_browser.py` | NOT run (needs port 8000, which the running backend uses) |

## 3. Remaining issues (to resume here)

### A. Past-question benchmark failed (OPEN, most likely quota)
- Full run: `q_lb_past` 1/3, `q_en_past` 0/3 (this morning: 5/5 each).
- An immediate rerun showed **every request refused with HTTP 429 "Gemini quota reached"**, and background frame analysis stayed `pending`. The long test day very probably used up the **daily** Gemini quota, so the failed answers were probably refusals, not wrong answers. The failing run only printed the totals, so this is **not yet proven**.
- Evidence against a code regression: the same past questions were answered correctly in the same run (`test_live_latency` q_en_past and q_lb_past, `test_assistant_flow` past questions 5/5, `test_multi_device` past questions 3/3).
- **Next step when quota is back (after the daily reset):** run ONLY `.venv\Scripts\python bench_past_questions.py 5` (shows each answer). If answers are wrong (not 429), investigate the new rules in `assistant_service.py` (present-question rule, `spoken_language` field order) before anything else.

### B. Face-privacy timing check failed (OPEN, likely CPU contention)
- `time per stored frame p95 < 150 ms`: 1024x576 with a face 159/285 ms (median/p95); earlier today 107/143 ms. All functional privacy checks passed (no face left, signs unchanged, fail-safe, legacy sweep, Photo Mode).
- The run happened with the backend on port 8000 running at the same time and right after other heavy suites. **Next step:** rerun ONLY `.venv\Scripts\python test_privacy.py` (offline, no quota) with nothing else running. If it still fails, reduce cost (e.g. detect on a 768 px copy for 1024 px frames) and re-verify recall on the sample photos.

### C. Not yet done
- Docs: write BENCHMARK_REPORT.md section 18 and CLAUDE_CHANGES.md section 10 for this round (README, RUNNING.md checklist 8/8b/8c/8d and DATABASE.md `first_word_s` are already updated).
- `test_dashboard_browser.py`: run once with port 8000 free.
- Arabic-accented French can only be tested by a real speaker (Azure voices cannot produce it; verified with Whisper).

## 4. Restart for the phone tests (after A and B are closed)

The backend currently running on port 8000 (started by this session earlier today) runs the code from BEFORE these fixes. It stops when the computer shuts down.

```powershell
# Terminal 1 - backend (new code)
cd "C:\Multimedia Labs\Project\backend"
.venv\Scripts\python -m uvicorn main:app --host 0.0.0.0 --port 8000

# Terminal 2 - phone app (JavaScript changes only: no Android rebuild needed)
cd "C:\Multimedia Labs\Project\mobile"
npx expo start --dev-client -c

# Terminal 3 - dashboard
cd "C:\Multimedia Labs\Project\dashboard"
npm run dev        # then open http://localhost:5173
```

Phone checklist for this round: `backend/RUNNING.md` steps 7, 8, 8b, 8c, 8d.

## 5. Plan after the phone tests are approved
Professional dashboard design and mobile interface improvements (user's plan).
