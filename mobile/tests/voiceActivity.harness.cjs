/*
 * Automated tests for mobile/Services/voiceActivity.ts (hands-free mode).
 * Compiles the REAL module and feeds simulated microphone levels.
 *
 *   node tests/voiceActivity.harness.cjs        (from the mobile folder)
 */

const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const MOBILE = path.resolve(__dirname, '..');
const OUT = fs.mkdtempSync(path.join(os.tmpdir(), 'wv-vad-test-'));
execFileSync(process.execPath, [
  path.join(MOBILE, 'node_modules', 'typescript', 'bin', 'tsc'),
  '--ignoreConfig', '--outDir', OUT, '--module', 'commonjs', '--target', 'es2020',
  '--skipLibCheck', path.join(MOBILE, 'Services', 'voiceActivity.ts'),
], { stdio: 'pipe' });
const { VoiceActivityDetector } = require(path.join(OUT, 'voiceActivity.js'));

let passed = 0;
let failed = 0;
function check(ok, name, detail = '') {
  if (ok) passed += 1; else failed += 1;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  -> ${detail}` : ''}`);
}

// A level script: [[durationMs, levelDb], ...]; small deterministic jitter.
function run(script, step = 100) {
  const vad = new VoiceActivityDetector();
  const events = [];
  let t = 0;
  let i = 0;
  for (const [duration, level] of script) {
    for (let elapsed = 0; elapsed < duration; elapsed += step) {
      const value = level === null ? undefined : level + ((i++ % 5) - 2);
      const event = vad.push(value, t);
      if (event !== 'none') events.push({ event, t, lastSpeechAt: vad.lastSpeechAt });
      if (event === 'speech_end' || event === 'no_speech' || event === 'max_length') return events;
      t += step;
    }
  }
  return events;
}
const names = events => events.map(e => `${e.event}@${e.t}`).join(', ');

// 1. Quiet room: 2 s question, then silence.
let ev = run([[600, -60], [2000, -25], [3000, -60]]);
let end = ev.find(e => e.event === 'speech_end');
check(ev[0]?.event === 'speech_start' && end && end.t >= 3700 && end.t <= 4000 && end.lastSpeechAt >= 2400,
  'quiet room: question detected, sent ~1.2 s after the user stops', names(ev));

// 2. Noisy street: noise -35 dB, speech -18 dB (threshold adapts to the room).
ev = run([[600, -35], [2500, -18], [3000, -35]]);
check(ev.some(e => e.event === 'speech_start') && ev.some(e => e.event === 'speech_end'),
  'noisy place: speech still separated from background noise', names(ev));

// 3. Pauses inside a sentence (700 ms) do not cut the question.
ev = run([[600, -60], [1200, -25], [700, -60], [1500, -25], [3000, -60]]);
end = ev.find(e => e.event === 'speech_end');
check(end && end.lastSpeechAt >= 3900, 'a 0.7 s pause mid-sentence does not end the question', names(ev));

// 4. Nobody speaks: discard after 8 s and listen again.
ev = run([[10000, -60]]);
check(ev.length === 1 && ev[0].event === 'no_speech' && ev[0].t >= 8000, 'silence: no_speech after 8 s', names(ev));

// 5. A short click or cough is not a question.
ev = run([[600, -60], [300, -20], [9000, -60]]);
check(!ev.some(e => e.event === 'speech_end'), 'a 0.3 s cough is ignored (nothing sent)', names(ev));

// 6. Cough, then a real question: the question is sent.
ev = run([[600, -60], [300, -20], [1500, -60], [1800, -25], [3000, -60]]);
check(ev.some(e => e.event === 'speech_end'), 'cough then a real question: the question is sent', names(ev));

// 7. Phone gives no levels: fixed 7 s window.
ev = run([[9000, null]]);
check(ev.length === 1 && ev[0].event === 'max_length' && ev[0].t >= 7000 && ev[0].t < 7200,
  'no metering available: recording sent after a fixed 7 s', names(ev));

// 8. Never-ending sound: safety stop at 15 s.
ev = run([[600, -60], [20000, -25]]);
check(ev.some(e => e.event === 'max_length' && e.t >= 15000), 'continuous sound: safety stop at 15 s', names(ev));

// 9. Loud room calibrated too high is still bounded (min level -50 dB rule).
ev = run([[600, -80], [1500, -45], [3000, -80]]);
check(ev.some(e => e.event === 'speech_end'), 'very quiet room: normal speech above -50 dB detected', names(ev));

fs.rmSync(OUT, { recursive: true, force: true });
console.log(`\n${passed}/${passed + failed} voice-activity scenarios passed`);
process.exit(failed ? 1 : 0);
