# World Voice AI: benchmark report

All times in seconds. "Phone" = real Android device test by the user.
"Laptop" = measured on the HP ZBook 15u G5 (i5-8350U, 16 GB RAM, no CUDA).

## 1. Hardware conclusion

Local vision-language models, Lebanese TTS and Whisper-large are not practical
on this laptop (4-core 15 W CPU, AMD WX 3100 2 GB without CUDA, 16 GB free disk).
All AI inference stays in the cloud; the laptop acts as a relay.

## 2. Real-device test, 2026-11-26 (segmented playback, before the single-stream fix)

| Metric | Phone result |
|---|---|
| ASR (Groq Whisper) | 3.81, 6.00, 0.78, 0.95 |
| Image analysis round trip | 17.87 |
| Gemini processing (server) | 15.251 |
| First playable TTS segment | 7.81 |
| First spoken sound | 8.064 |
| Total audio reception | 33.64 |
| Audio duration | 21.15 |
| Segments | 9 |
| Playback interruptions | 3 |
| Total interruption duration | 5.943 |
| Replay start (cached) | 0.32 |
| Replay continuity | **still cut out and resumed** |

Derived: generation rate = 21.15 / 33.64 = **0.63x real time** (Gemini TTS
free tier). Required extra reserve for zero stalls at that rate:
21.15 x (1 - 0.63) = about 7.8 s.

## 3. Root-cause analysis of the cuts

| Hypothesis | Test | Result |
|---|---|---|
| Segments lose or duplicate samples | Real Fola PCM through backend segmenter, odd chunk sizes (1, 3, 961... bytes) | **Rejected**: 7 segments rejoin to exactly 825,120 bytes, identical |
| Segments contain extra silence | Cut-point energy check | **Rejected**: cuts only inside existing pauses (energy 13 to 226) |
| Slow generation only | User replayed fully cached audio | **Rejected as sole cause**: replay also cut |
| Player hand-offs | expo-audio 58.0.12 native source (AudioModule.kt) | **Confirmed mechanism**: removing a finished player with nothing else playing calls `releaseAudioFocusIfUnused()` and abandons Android audio focus; the next player re-requests focus and starts a new audio output. Status polling (100 ms) adds delay before each hand-off. 9 segments = 8 audible hand-offs. |

**Fix:** one answer = one continuous WAV stream = one native player
(`speech_jobs.py`, `mobile/Services/speechPlayer.ts`). No hand-offs remain.

## 4. Laptop tests of the fix (no API quota used)

Real Fola audio, streamed through the real `speech_jobs` code over HTTP
(`test_speech_stream.py` and HTTP test on port 8010):

| Check | Result |
|---|---|
| Live stream content | identical to generated PCM |
| Replay of finished job | complete WAV in 0.009 s, Content-Length exact, identical |
| Expired / unknown job | HTTP 404 |

ExoPlayer playback model (start at 2.5 s buffered, 5 s after a stall), 12 s of audio:

| Generation rate | Start policy | First sound | Interruptions | Finished |
|---|---|---|---|---|
| 0.63x | fast | 5.2 | 1 (8.2 s) | 25.4 |
| 0.63x | **smooth** | 9.6 | **0** | **21.7** |
| 1.5x | smooth | 2.9 | **0** | 14.9 |

With a single player, starting early is worse at 0.63x: ExoPlayer needs 5 s to
recover from a stall, so the answer cuts out AND finishes later.

## 5. Other measurements

| Item | Result |
|---|---|
| Gemini vision, identical request x5 | 2.7 / 6.7 / 7.8 / 20.8 / (English) 2.0; tokens identical, variance is free-tier queueing |
| Vision backup request (hedge) | Verified: fires after threshold, first answer wins, loser cancelled |
| Blur + save | 0.02 to 0.13, now parallel to Gemini (was sequential) |
| Gemini TTS single short request | first audio 2.6, ~1.1x real time after start |
| Gemini TTS single long request | 16.8 s audio in 157 s (0.11x) |
| Gemini TTS free-tier limit | **10 requests/day** (API 429 message) |
| gemini-omni-flash-preview (speech answers) | free-tier limit **0** requests/day |
| Gemini Live models | none listed for this key |
| Groq orpheus-arabic-saudi | needs terms acceptance by the account owner; Saudi dialect |

Whisper prompt comparison (same Fola audio, 17.2 s):

| Prompt | Observations |
|---|---|
| natural (new default) | keeps Lebanese forms (عاليمين, ورا), natural punctuation |
| none | normalises to MSA (على اليمين, وراء), no punctuation |
| legacy list | word errors (مفتوحة الطاولة, جاية) |

## 6. Real-device test 2, 2026-11-26 (after the single-stream change)

| Metric | Phone result |
|---|---|
| Whisper | 1.97, correct Lebanese transcription |
| Image analysis round trip | 6.38 |
| Gemini processing | 5.235 |
| Photo storage | successful |
| Automatic speech + manual retry | HTTP 429, daily TTS quota exhausted; 0 audio |

The continuous player could not be validated with live Gemini TTS.

### Repeated requests after the terminal 429 (cause and fix)

| Cause | Fix |
|---|---|
| The player was created before the job finished; after the job failed, ExoPlayer's default load-error policy retried the audio URL by itself | Player is created only when the server reports `ready_to_start`; a failed job never opens a player |
| Each manual retry created a new job = a new Gemini request | Daily-quota circuit breaker: after a "per day" 429 the model is blocked until the reset time stated in Google's message |
| 429 was marked retryable | 429 is never retried |

Laptop verification against the real API (quota already exhausted, so no cost):
answer 1 -> one request to Google, 429 in 1.51 s; answers 2 and 3 -> refused
locally in 0.02 s and 0.01 s with "available again in about 11 h"
(parsed from Google's "retry in 12h14m33s" format). The block is in memory:
a backend restart allows one rejected request again.

### Offline phone-client simulation (saved Fola audio, real HTTP endpoints)

`test_phone_flow.py` polls status every 350 ms, opens the stream on
`ready_to_start`, and models ExoPlayer buffering:

| Scenario | Player opened | First sound | Interruptions | Finished | Audio |
|---|---|---|---|---|---|
| Normal, x1.5 | 2.16 | 2.91 | 0 | 20.11 | 17.19 |
| Slow, x0.63 | 13.91 | 13.93 | 0 | 31.13 | 17.19 |
| Replay (cached) | 0.00 | 0.01 | 0 | 17.21 | 17.19 |

## 7. Real-device test 3: offline Fola audio (user, 2026-11-26)

Voice quality excellent; **no audible interruption** in normal playback or replay.
At simulated 0.63x the voice was still continuous but started too late.
A "playback interruption" warning was logged without any audible cut.

**Warning explained:** the detector counted every ExoPlayer `isBuffering`
report after start. ExoPlayer emits brief, inaudible buffering reports (e.g.
at the end of a live stream whose final length is unknown). It now counts an
interruption only if the playback position is frozen >= 300 ms before the end
of the audio, and logs position / length for any warning.

## 8. Latency investigation (laptop, 2026-11-26)

### Vision (interleaved A/B, production prompt + schema, same image)

| Configuration | Median | Min | Max | n |
|---|---|---|---|---|
| gemini-3.5-flash-lite, thinking low | 1.73 | 1.39 | 4.75 | 5 |
| gemini-3.5-flash-lite, thinking minimal | 1.96 | 1.18 | 2.44 | 5 |
| gemini-3.8-flash, thinking low | 503 "high demand" x5 | | | 0 |

Decision: keep flash-lite + low (no measurable speed gain from minimal,
better reasoning). Ordinary latency already meets the 2-4 s target; spikes
are handled by the backup request, now at 3.5 s (was 6 s).

### Pipelining vision -> speech (streamed vision, n = 5)

| First text | First full sentence | Complete answer |
|---|---|---|
| 1.70 | 1.71 | 1.76 (median) |

Gemini emits the whole answer within ~0.05-0.3 s of the first token, so
streaming would start speech only ~0.06 s earlier. **Not implemented**: no
meaningful gain.

### ExoPlayer start buffer (corrected)

expo-audio uses media3 1.9.1 `DefaultLoadControl` defaults: playback starts
at **1.0 s** buffered, **2.0 s** after a stall (earlier estimate of 2.5 s / 5 s
was wrong). Server start calculation and simulators updated.

### Pipelined speech (parts generated in parallel, merged into ONE stream)

In-process simulation, 4-sentence Lebanese answer, first audio 2.5 s:

| Per-request rate | 1 part | 2 parts | 3 parts | Interruptions |
|---|---|---|---|---|
| 0.63x | 15.9 | 8.7 | 6.4 | 0 / 0 / 0 |
| 1.1x | 3.7 | 3.7 | 3.7 | 0 / 0 / 0 |

Real HTTP phone-flow simulation (`test_phone_flow.py`, port 8010, first audio 1.0 s):

| Mode | First sound | Interruptions | Replay |
|---|---|---|---|
| 1 part, x0.63 | 14.62 | 0 | 0.01 s, 0 interruptions |
| 2 parts, x0.63 each | 7.18 | 0 | 0.01 s, 0 interruptions |

Caveat: an earlier live test with 2 parallel Gemini requests ran at 0.15x and
0.28x, i.e. the free tier may throttle parallel requests. `WV_TTS_SPLIT`
stays 1 until `bench_tts_live.py` (3 requests) shows otherwise.

### Floors with Gemini TTS

First sound after the answer >= Gemini first-audio latency (~2.5 s) + ~1.2 s
(rate measurement + 1.0 s ExoPlayer buffer) = **~3.7 s** even when generation
is faster than real time. The 1-2 s target needs a provider with sub-second
first audio (e.g. Azure neural voices), not more client-side work.

### Speech recognition backup request

Forced test (threshold 0.3 s): backup request sent, **backup answered first**
(winner 2), transcript correct. Default threshold 2.5 s.

## 9. Multilingual speech recognition (2026-11-27)

**Real-device bug:** an English question ("Describe what is in front of me,
identify the important objects, and explain what I can use them for.") came
back as an Arabic transcript; Gemini then answered in Lebanese Arabic.

Test set (`make_asr_testset.py --no-gemini`, no API): 4 English recordings
(two offline Windows voices, short and long), Lebanese short + long (real Fola
audio), Lebanese+English code-switch (Fola phrase + English), plus English
with leading silence / room noise. French: no offline French voice on this
laptop (one Fola request after the TTS quota reset will add it).

### Prompt comparison, automatic detection (`bench_asr_lang.py`)

| Prompt | Spoken language kept | Median latency |
|---|---|---|
| natural (Lebanese sentence) | 6/7 | 0.97 s |
| none | 6/7 | 0.97 s |
| legacy word list | 6/7 | 0.93 s |
| multilingual sentence | 6/7 | 0.87 s |

- English was detected correctly with **every** prompt, also with 1.5 s
  leading silence and 3 s room noise: the Lebanese prompt alone does not flip
  clear English. The real failure is most likely accent-driven, which
  synthetic voices do not reproduce.
- The code-switched sentence failed with every prompt: Whisper detected Arabic
  and **dropped the English half**.

### Root cause and verification method (`bench_asr_verify.py`)

Forcing Arabic on English speech reproduces the bug exactly: Whisper
**translates** instead of transcribing ("Describe what is in front of me" ->
"تصفح ما في أمامي"). Whisper's mean log-probability picked the spoken
language in **9/9** recordings:

| Recording | forced ar | forced en | Picks |
|---|---|---|---|
| English (4 files + 2 noisy) | -0.17 to -0.67 | -0.08 to -0.17 | en |
| Lebanese short | -0.121 | -0.172 | ar |
| Lebanese long | -0.114 | -0.307 | ar |
| Lebanese + English mix | -0.260 | -0.216 | en decode = both languages kept |

Gemini audio transcription was also correct for every file (dialect and mixed
speech kept) but slower (1.4-8.7 s), so it is not used as the primary ASR.

### Fix: parallel language check (`whisper_service.py`)

Main request (auto-detect + natural Lebanese prompt) and a `language=en`
request start together. Non-Arabic detection -> main result returned at once
(check cancelled, no added latency). Arabic detection -> the English decode is
used only if its log-probability is higher by > 0.02 and it contains Latin
words. Cost: +1 Groq request per question (free tier 2,000/day, 20/minute).

`test_asr_language.py`:

| Run | Spoken language kept |
|---|---|
| Normal pipeline | **7/7** (mixed sentence now keeps both languages) |
| Simulated misdetection (main request forced to Arabic) | **7/7** (all English recovered, Lebanese confirmed) |

### Answer language (real Gemini, `/analyze` with the new language hint)

| Question | Hint | Answer language |
|---|---|---|
| English (user's sentence) | english | en |
| Lebanese | arabic | ar-LB |
| Lebanese + English | mixed | ar-LB (dominant) |
| "What is this?" | none | en |

Note: a burst of ~28 test requests hit Groq's free limit of **20 requests per
minute**; normal use (2-3 requests per question) is far below it.

## 10. TTS reliability (2026-11-27)

### Real-device results

| Test | Result |
|---|---|
| English question | ASR 1.16 s, analysis 3.94 s (Gemini 2.95 s), **first sound 42.04 s**, 2 warnings at position 0.27 s |
| Arabic attempt 1 | first sound 3.23 s, continuous |
| Arabic attempt 2 | first sound 8.77 s |
| Arabic attempt 3 | no audio: daily TTS quota exhausted |

### Root cause of the 42 s

The running backend reported `gemini-3.8-flash-lite-tts` blocked (daily quota),
so the answer was generated by the fallback `gemini-3.8-flash-tts` (~0.34x real
time, measured earlier). A zero-interruption start at that speed has to wait
for most of a long answer. Fix: the slow model is no longer a default fallback.

The 0.27 s warnings were real ExoPlayer `STATE_BUFFERING` reports (expo-audio
keeps `playing=true` while buffering). Likely cause: bursty delivery made the
average generation rate look high. The start rule now uses the SLOWER of the
average and the last-3-s rate; the phone log now includes player state,
server audio and provider for any warning.

### Conclusion

Gemini's free TTS cannot give consistently fast speech (10 requests/day per
model, 0.6-1.1x for flash-lite, 0.34x for flash). Simulation with the real
player rules (1.0 s start buffer):

| Provider profile | First sound | Interruptions |
|---|---|---|
| fast streaming provider (x2.0, first audio 0.6 s) | **1.32 s** | 0 |
| Gemini free tier (x0.63, first audio 2.5 s), 1 / 2 / 3 parts | 16.1 / 9.0 / 7.1 s | 0 |

### Provider architecture

`WV_TTS_PROVIDERS` = ordered list of APPROVED providers (gemini, elevenlabs,
azure). Each answer uses the first available one; if it fails before any audio
(quota, bad key), the SAME answer moves to the next approved provider; failing
providers are paused (quota: until reset; key/access: 10 min). Voices are never
mixed inside an answer.

Verified offline: Fola available -> Fola; Fola exhausted -> next provider;
ElevenLabs (bad key) -> Azure (bad key) -> next provider, same answer, full
audio; all exhausted -> 429 in 0.001 s without any request. Real Azure and
ElevenLabs endpoints reached with deliberately invalid keys (correct error
handling, no quota).

Candidates considered: Azure AI Speech F0 (official, 0.5 M chars/month,
ar-LB voices), ElevenLabs free (official, ~10k chars/month, non-commercial),
Gemini Fola (kept). Excluded: unofficial Edge read-aloud endpoint (not an
official API), Gemini omni (free limit 0), Gemini Live (not available to this
key), Groq Orpheus Arabic (Saudi dialect, needs terms), local TTS (too slow on
this laptop). Quality must be judged by ear: `compare_voices.py`.

## 11. Code-switching (2026-11-27)

Test audio: real Fola Lebanese phrases spliced with offline English voices in
three orders (Arabic->English, English->Arabic, Arabic->English->Arabic). Both
Gemini TTS models were quota-exhausted, so natural French / mixed-French audio
could not be generated today (`make_asr_testset.py`, 1 request, after reset).

| Recording | Whisper pipeline | Gemini hearing the audio |
|---|---|---|
| Lebanese -> English | both kept (language check) | both kept |
| English -> Lebanese | **"هل يمكنك أن تخبرني؟"**: English translated, Lebanese dropped | both kept |
| Lebanese -> English -> Lebanese | all kept | all kept |

Whisper decodes one language per recording, so English-first code-switching
cannot be fixed inside Whisper. Fix: the phone sends the original question
audio with the photo (same `/analyze` request, no extra API call); Gemini hears
it, fills `question_heard` verbatim and answers all parts.

Prompt layout (Gemini, mixed questions, 2 runs each):

| Layout | Every part kept |
|---|---|
| Whisper transcript first, then audio | **0/2** (English opening dropped: the draft already held its Arabic translation) |
| Audio first + "draft not trustworthy, transcribe all" | **4/4** |
| Audio only | 4/4 |

Chosen: audio first + draft (keeps the draft as a hint when audio is unclear).
Gemini vision with audio attached: 2.5-2.9 s.

Also found during validation: Gemini returned 503 "high demand" -> `/analyze`
now retries 5xx twice (1 s, 2 s) before failing.

## 12. Final validation (2026-11-27)

`run_validation.py` (offline, no API quota):

| Section | Checks | Result |
|---|---|---|
| Unit: splitting, quota parsing, segmentation integrity, language decision | 7 | 7/7 |
| Speech jobs: 10 consecutive answers, deadline switch, keep fast preferred voice, network error before audio, quota error, all exhausted, mid-stream loss | 7 | 7/7 |
| Playback model: fast provider / Gemini good day / Gemini free tier | 3 | 3/3 (0 interruptions; first sound 1.33 / 4.29 / 17.45 s) |
| HTTP: health, live stream exact, replay exact (0.019 s), Range resume 206, 404, 10 consecutive, 3 concurrent, disconnect + reconnect | 8 | 8/8 |

`run_validation.py --live` (Groq + Gemini vision, no TTS quota): **12/12**
(8 Whisper recordings in English / Lebanese / mixed, 3 mixed or English
questions answered from audio, Groq outage -> clean HTTP 502). English-first
code-switching through Whisper alone is reported as a known limitation and is
covered by the audio path.

`mobile/tests/speechPlayer.harness.cjs` (real `speechPlayer.ts`, simulated
native player and server): **7/7 on 3 consecutive runs** - normal answer
(1 player), quota error (no player created, no retries), network blip while
preparing, network drop mid-answer (resume at last position, single active
player), inaudible end-of-stream buffering (not counted), real stall (counted
once), stop.

Bugs found and fixed by this validation:

| Bug | Fix |
|---|---|
| Network error while preparing was treated as "job not found" (404) -> answer failed after a 1 s Wi-Fi blip | network errors keep polling; only a real 404 fails |
| Recovery and the status poller could both create a player | single recovery path, guarded |
| Stream generator waited forever for a job that failed with no audio | returns immediately |
| Gemini dropped the English part of English-first questions | audio-first prompt layout |
| Gemini 503 "high demand" failed the question | 2 retries |
| Unretrieved exception warning when Groq was unreachable | handled |
| Burst-y generation could start playback too early | burst-proof rate (min of average and last 3 s) |

## 13. Azure / Rami round (2026-11-27)

Checkpoint before this round: `C:\Multimedia Labs\Project-checkpoint-20261127-stable-before-azure`
(contains backend/.env: keep local).

### Rami loanword pronunciation (SSML `<sub alias>`, integrated)

Real Azure, scored by Whisper (understandability only, NOT naturalness), 5 mixed sentences, 2 runs:
mean similarity **0.890 -> 0.949**, better in 5/5 sentences; both runs identical.

### Rami naturalness (samples only, NOT integrated)

User report: words disconnected, wrong word endings, weak rhythm.
`compare_rami_naturalness.py` -> `voice_compare/rami/index.html`.

| Finding | Evidence |
|---|---|
| Our processing is NOT the cause | Pure Lebanese text: raw Azure REST, app provider code and full speech job produced byte-identical audio (1,097,164 bytes) |
| No Azure styles / HD for Arabic; multilingual voices support only ar-EG / ar-SA (not Lebanese) | Azure voice list for region uaenorth |
| Clearest measured difference: tempo | Same text: Fola 12.8 chars/s, Rami 9.9 chars/s (about 23 % slower); Rami +25 % -> about Fola's tempo |
| Pausal endings, no commas, +8 % rate | Only marginal metric changes |
| Short-pause and pitch metrics do not capture the complaint | Fola itself has more short pauses (1.49/s vs 1.11/s); pitch variation similar (Fola 4.1, Rami 4.9 semitones) |

Samples to judge by ear: Rami raw, Rami with endings / no commas / +8 / +15 / +25 %,
Layla, Syrian Laith, Jordanian Taim, Onyx Turbo multilingual, Fola reference.

### Azure connection ping

Measured: first Rami audio 2.57 s cold -> 0.23 s with the ping. It could not be
verified that Azure never counts or bills these requests, so it is **OFF by
default** (`WV_AZURE_KEEPALIVE=0`).

### Multilingual recognition with Azure-generated speech (15 recordings)

Pure French: native Azure voices. Code-switching: ONE multilingual Azure speaker
switching language inside the sentence.

| Combination | Understood question kept every language and key word |
|---|---|
| French (3) | 3/3 (Whisper also 3/3) |
| Lebanese words قدامك, كباية, عاليمين, هلق, شوي (Rami) | pass |
| Lebanese (Layla): هيدا heard as هادا (valid Levantine variant) | fail by strict key word |
| Arabic + English (3, user examples) | 3/3 |
| Arabic + French: "شو في قدامي؟ Est-ce qu'il y a..." | pass |
| Arabic + French: "...عالـ document en français?" | **fail** (en français lost) |
| French + Arabic: "Dis-moi, شو لون الكباية؟" | **fail** (Dis-moi -> ديما) |
| English + French: "...label, s'il vous plaît, ..." | pass |
| French + English: "Est-ce que le charger..." | fail (charger -> chargeur) |
| French + English + Arabic: "Bonjour, can you tell me شو..." | pass |
| Arabic + English + French: "...screen ... en français" | **fail** (en français lost) |
| **Total** | **10/15** with the production hint (9/15 no hint, 10/15 neutral hint) |

Language hint is not the cause (no variant better) -> app unchanged. Short
French phrases inside Arabic speech are the weak point (Whisper also misses
them). Test speakers are synthetic; real human speech not verified.

New limit found: Gemini vision free tier **15 requests/minute** (429 message).

### Regression after this round

| Suite | Result |
|---|---|
| `run_validation.py` offline (after the keep-alive change) | 26/26 |
| `run_validation.py` live section | 12/12 |
| `test_live_voice_chain.py` (real Azure + Gemini) | 22/23 |
| `mobile/tests/speechPlayer.harness.cjs` | 7/7 |
| `npx tsc --noEmit` | 0 errors |

**Failed target (reported, threshold NOT relaxed):** first sound within 2 s for
every Rami answer. Measured: median 1.10 s; the FIRST answer after a backend
start 3.90 s (3.56 s in the chain test) because the connection is cold now that
the ping is off; 2.16 s with the ping on.

## 14. Still to measure on the phone

1. Same answer with the single-stream player: interruptions should be 0 on
   both first playback and replay.
2. End-to-end with the speech job started inside `/analyze`.
3. ASR on 10 to 20 real Lebanese / English / French / mixed recordings
   (`benchmark.py suite`).

## 15. Live Assistant: speed, search and answer accuracy (2026-11-27)

Laptop, real server, real Gemini / Azure calls. The phone is simulated by an
aiortc client streaming a scripted walk (EXIT sign -> door "ROOM 12" -> wall ->
CAFE sign). Spoken questions are real audio (Azure voices, cached).
Voice: Rami (Fola's daily quota was exhausted).

### End-to-end speed (`test_live_latency.py`, 6 consecutive spoken questions)

Measured from the moment the recording is sent (the user finished speaking)
to the first sound, with the phone's rule (playback starts at 1.0 s buffered).

| Version | Answer ready (median / max) | First sound (median / max) | Within 5 s |
|---|---|---|---|
| Before: transcription + embedding search, then Gemini | 3.58 / 4.56 s | 5.35 / 6.41 s | 2/6 |
| Fast path (Gemini hears the audio; no separate transcription or search) | 2.70 / 3.23 s | 4.18 / 4.59 s | 6/6, but 2 answers wrong (see below) |
| Final (scene-based memory + "where is" rule), clean memory | **2.82 / 3.56 s** | **4.11 / 5.94 s** | **5/6**, 6/6 correct |

Breakdown of the final run: memory retrieval <= 0.01 s; Gemini 2.1-3.5 s with 3
images (current view + 2 remembered scenes); speech start after the answer
1.1-3.0 s. The 5.94 s outlier was the first question (cold TTS connection,
answer ready at 2.91 s). What still limits the 3-5 s target: Gemini vision
time on the free tier (varies by request) and the 1.0 s buffer the player
needs to play without interruptions.

Accuracy problems found while optimising, fixed before the final run (not
by relaxing the checks):

| Problem | Cause | Fix |
|---|---|---|
| French question answered in Lebanese Arabic | Fast path lost the language hint | Explicit rule: answer in the language spoken in the audio |
| "What was on the door I just passed?" (Lebanese) -> "CAFE" | The 2 newest remembered frames were both the CAFE wall | Memory grouped into scenes; the 2 newest scenes other than the current view are attached; the index lists one line per scene |
| "Where is the emergency exit?" -> "the sign says CAFE" | Model described the object in view | Rule + final reminder: find the asked object in memory, say it is not in view and when it was seen |
| Earlier runs measured on stale memory | Test cleanup failed silently on Windows | Cleanup retries and fails loudly (`test_helpers.wipe`) |

### Search quality (`test_memory.py --live`)

| Query | Result |
|---|---|
| "where is the way out of the building" (meaning) | Top hit: EXIT sign, cosine 0.643 |
| "chambre numéro douze" (French, meaning) | Top hit: ROOM 12 |
| last 90 s (time) | Only the newest sign |
| OCR of EXIT, مخرج الطوارئ, SORTIE, ROOM 12 in one batched request | 4/4 exact |

### Answer accuracy

| Suite | Correct |
|---|---|
| `test_assistant_flow.py` (typed questions: now, past, "where is", never seen, Lebanese, French) | 6/6 (13/13 checks) |
| `test_live_latency.py` final run (spoken: now and past in English, Lebanese, French; "where is the exit") | 6/6 |

### Frame selection (`test_assistant_flow.py`)

46 samples seen during the walk -> 4 frames kept (one per scene), 11
rejected as duplicates. Background analysis: 4 requests in 24 s, within the
rate limit.

### Regression after this round

| Suite | Result |
|---|---|
| `test_stream.py` | 11/11 |
| `test_memory.py --live` | 21/21 |
| `test_assistant_flow.py` | 13/13 |
| `test_dashboard_sync.py` (new) | 13/13 |
| `run_validation.py --live` | 38/38 |
| `test_live_voice_chain.py` | 22/23 (strict 2 s first-sound target missed once: 2.68 s; median 1.09 s) |
| `mobile/tests/speechPlayer.harness.cjs` | 7/7 |
| mobile `tsc --noEmit`, dashboard `tsc -b` + `vite build`, `expo export --platform android` | pass |

Not yet verified: the real phone streaming and asking, and the dashboard in a
real browser (only its HTTP requests are tested, from the browser's origin).

## 16. Final mandatory round (2026-11-27, afternoon)

### Database (bench_memory_db.py; full results in DATABASE.md)

4 phones at once, 60 s real time, every question step < 5 ms p95 even
when every sample is kept; WAL inserts 1.0 ms vs 10.5 ms with the default
journal; database work 2-4.6 % of one core; integrity, isolation, retention
(rows + files + index) and per-phone cap all verified (13/13). Meaning
search: 150 frames 22 ms, 600 (per-phone cap) 72-102 ms, 2,400 271 ms,
10,000 1.25 s (was 175 ms at 600 before `math.sumprod`).
**Verdict: SQLite stays; PostgreSQL + pgvector would bring no measurable gain.**

### Multiple phones (test_multi_device.py, 20/20)

Before this round a second phone silently disconnected the first, and both
would have shared one frame selector and one memory. Now: per-device
streams, selectors, memory and questions. 3 phones streaming and asking at
the same time: all answers from their own view/memory. Server: 51 % of one
core, 173 MB RAM.

### Accuracy fixes found by repeated measurement

| Problem | Evidence | Fix | After |
|---|---|---|---|
| Lebanese spoken past question answered from the current view, or echoed | `bench_past_questions.py`: 2/5 | Answer schema order: question_heard -> focus -> used -> answer (the model decides what is asked before answering) | 5/5 (English 5/5) |
| Same order, latency? | `bench_schema_ab.py`, interleaved: new 2.6 / 5.1 / 7.7 s, old 4.7 / 19.5 / 17.7 s (then Gemini 503) | none needed | - |
| A scene missing from memory | Gemini 5xx marked frames "failed" for good | Retry transient failures, failed after 3 attempts | test_memory 22/22 |
| Hebrew word "על" in a Lebanese answer | multi-phone run | Script rule + server-side safeguard + script check in tests | 0 foreign-script answers since |

### Speed (test_live_latency.py, final code)

| Gemini conditions | Answer ready median / max | First sound median / max | <= 5 s |
|---|---|---|---|
| Normal (morning, Rami) | 2.82 / 3.56 s | 4.11 / 5.94 s | 5/6 |
| Degraded (afternoon, Gemini 503 "high demand") | 5.17 / 8.24 s | 6.95 / 9.34 s | 2/6 |

Answers correct 6/6 in both. Gemini time alone ranged 2.5-8.1 s in the
afternoon. Live answers now wait at most 2.5 s for Fola before moving to
Rami (was 4 s); Fola's daily quota was exhausted by the day's tests.

### Rami backup voice

Speaking rate +15 % (`WV_AZURE_AR_RATE`), closing most of the measured 23 %
tempo gap with Fola. Loanword test 5/5 (similarity 0.89 -> 0.95), voice
chain 22/23 (the strict 2 s target missed once: 2.52 s).

### Dashboard in a real browser (test_dashboard_browser.py, 9/9)

Headless Microsoft Edge: live WebRTC video connected, timeline with OCR text
and images, Q&A with the frames used, statistics. Screenshot:
`test_dashboard_browser.png`.

## 17. Face privacy of stored images (2026-11-27, evening)

Detector: OpenCV YuNet (local, `privacy.py`), threshold 0.5 chosen by
measurement: it also finds a man turned away in near-profile (0.58) with 0
false alarms on 13 face-free sign/door/wall frames (0.6 missed him).
Faces are pixelated + blurred, then detected again until none remains
(one pass left a borderline crowd face detectable in messi5.jpg).

| Measure | Result |
|---|---|
| test_privacy.py --live | 27/27 |
| Faces left detectable after protection | 0 (2 sample photos, portrait on a wall, live stream) |
| Sign next to a portrait | pixel-identical; OCR read PHARMACY and ROOM 12 after protection |
| Time per stored frame (median / p95) | 640x480: 27/35 ms (no face), 52/68 ms (face); 1024x576: 51/59 ms, 107/143 ms |
| Answer speed after the change (test_live_latency) | answer median 2.85 s, first sound median 4.21 s, 6/6 within 5 s, 6/6 correct |
| Real storage at restart | 57 existing frames checked: 12 had faces, all anonymised in place, 0 detectable afterwards; 67 Photo Mode photos older than 30 min deleted |

Regression: stream 11/11, memory 22/22, validation 26/26, dashboard sync
13/13, assistant flow 15/15 (first run 13/15: one Gemini 429 per-minute
limit, passed on rerun), multi-phone 20/20, database 13/13. The browser
test was not rerun (port 8000 is used by the running backend).

## 18. Final plan, phase 1-3: quota, hybrid providers, offline (2026-11-27 night)

### Checkpoint issues closed
- Past-question benchmark failure = **Gemini daily quota exhausted**, proven by the 429:
  `GenerateRequestsPerDayPerProjectPerModel-FreeTier`, limit **500/day**, retryDelay 55,328 s.
- Face-privacy timing in isolation: 20/20 (face frame 105/132 ms). Under load (downloads,
  other tests) face frames reach ~120/153 ms (limit 150): noted, not optimised further.

### Provider limits measured (not assumed)
| Provider / model | Vision | Limit (source) | Result on our test images |
|---|---|---|---|
| gemini-3.5-flash-lite | yes | 500/day, 15/min (429 message) | primary; 15/15 assistant flow earlier |
| gemini-3.1-flash-lite | yes | own daily quota | once 167 s; as primary 11/15 (Arabic past questions wrong, 5-16 s) |
| gemini-3.5-flash, gemma-4-26b/31b | yes | - | 503 "high demand" today |
| gemini-2.5-flash(-lite) | - | 404: not available to new users | - |
| Groq qwen/qwen3.8-27b | yes | 1,000/day, 7,000 input tokens/min (headers); ~1,800 tokens per image whatever its size | ROOM 12 (1.3 s), مخرج الطوارئ + Lebanese answer (1.9 s) |
| Groq whisper-large-v3 | - | 2,000/day (headers) | ~1 s per question |
| Groq Orpheus Arabic (Saudi TTS) | - | not evaluated yet | - |

### Quota management
`test_quota.py` **17/17** (simulated errors, no quota used): per-day 429 -> exhausted until
retryDelay and never called again; 503 / per-minute 429 / 404 / timeout -> next model at once;
timed-out model paused 10 min; background never uses the 250-request daily reserve; books survive
a restart; Pacific day (winter + summer); unchanged scenes reuse their analysis (no request).

### Scenario "Gemini unavailable" (bench_voice_to_voice.py gemini_out), 5 spoken questions
| Run | Correct | Answer ready | First word | Note |
|---|---|---|---|---|
| 1 | 2/5 | 3.4-6.0 s | 5.9-7.6 s | Groq per-minute tokens exceeded by background analysis; collage missing |
| final | **5/5** | 3.6-6.8 s (12-13 s with a token wait) | median 9.0 s, max 14.8 s | token planning, scene grouping of unanalysed frames, memory collage |
Speech start was slowed by model downloads saturating the connection (~120 KB/s, later near 0).

### Dashboard in a real browser
`test_dashboard_browser.py` **10/10** over all tabs (Live, Visual memory, Q&A, Warnings,
AI-voice-network): live WebRTC video, OCR text, thumbnails, first-word latency, evidence frames,
answer order, phone addresses.

### Voices (voice_compare/final/index.html, same 8 sentences)
Fola not generated (daily quota used up). Piper 1.8.0 local: 0.2-0.7 s per sentence once loaded
(first load 4.5 s), 300-550 MB RAM with 3 voices. Whisper intelligibility (objective filter only):
Azure 0.84, Piper 0.82; Piper clearly worse on Lebanese with English/French words (0.38 vs 0.53);
Piper's only Arabic voice is Jordanian. Recommendation: Piper = offline last resort only.
