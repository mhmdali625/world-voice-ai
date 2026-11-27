// ========================================
// VOICE ACTIVITY DETECTION (hands-free mode)
// ========================================
//
// Decides from the microphone level (expo-audio metering, dB) when the
// user STARTED and FINISHED speaking, so hands-free mode can send the
// question without a tap. Pure logic (no React / native code), tested
// on the laptop by tests/voiceActivity.harness.cjs.
//
// The level that counts as speech adapts to the room: the first
// 400 ms set the noise floor; speech must be clearly above it.

export type VadEvent =
  | 'none'
  | 'speech_start'
  | 'speech_end'      // the user finished: send the question
  | 'no_speech'       // nothing said for noSpeechMs: discard, listen again
  | 'max_length';     // safety stop: send what was recorded

export type VadConfig = {
  calibrationMs: number;
  marginDb: number;       // speech = noise floor + marginDb
  minLevelDb: number;     // ... and at least this loud
  startMs: number;        // loud this long -> speech started
  silenceMs: number;      // quiet this long after speech -> finished
  minSpeechMs: number;    // shorter bursts (a cough, a click) are ignored
  noSpeechMs: number;
  maxMs: number;
  noMeteringMs: number;   // phone gives no levels: fixed recording window
};

export const DEFAULT_VAD: VadConfig = {
  calibrationMs: 400,
  marginDb: 10,
  minLevelDb: -50,
  startMs: 250,
  silenceMs: 1200,
  minSpeechMs: 400,
  noSpeechMs: 8000,
  maxMs: 15000,
  noMeteringMs: 7000,
};

export class VoiceActivityDetector {
  private readonly config: VadConfig;
  private startedAt: number | null = null;
  private calibration: number[] = [];
  private threshold: number | null = null;
  private loudSince: number | null = null;
  private quietSince: number | null = null;
  private metering = false;

  /** Time (ms) the user started speaking, or null. */
  speechStartedAt: number | null = null;
  /** Time (ms) of the last loud sample: when the user stopped speaking. */
  lastSpeechAt: number | null = null;

  constructor(config: Partial<VadConfig> = {}) {
    this.config = { ...DEFAULT_VAD, ...config };
  }

  get speaking(): boolean {
    return this.speechStartedAt !== null;
  }

  /** Feed one level sample (dB, undefined if not available) at time `now` (ms). */
  push(levelDb: number | undefined | null, now: number): VadEvent {
    const c = this.config;
    if (this.startedAt === null) this.startedAt = now;
    const elapsed = now - this.startedAt;

    if (typeof levelDb !== 'number' || !Number.isFinite(levelDb)) {
      if (!this.metering && elapsed >= c.noMeteringMs) return 'max_length';
      return elapsed >= c.maxMs ? 'max_length' : 'none';
    }
    this.metering = true;

    if (this.threshold === null) {
      this.calibration.push(levelDb);
      if (elapsed < c.calibrationMs) return 'none';
      const sorted = [...this.calibration].sort((a, b) => a - b);
      const floor = sorted[Math.floor(sorted.length / 2)];
      this.threshold = Math.max(floor + c.marginDb, c.minLevelDb);
    }

    const loud = levelDb >= this.threshold;

    if (this.speechStartedAt === null) {
      if (loud) {
        this.loudSince = this.loudSince ?? now;
        if (now - this.loudSince >= c.startMs) {
          this.speechStartedAt = this.loudSince;
          this.lastSpeechAt = now;
          return 'speech_start';
        }
      } else {
        this.loudSince = null;
      }
      return elapsed >= c.noSpeechMs ? 'no_speech' : 'none';
    }

    if (loud) {
      this.lastSpeechAt = now;
      this.quietSince = null;
    } else {
      this.quietSince = this.quietSince ?? now;
      const spoke = (this.lastSpeechAt ?? now) - this.speechStartedAt;
      if (now - this.quietSince >= c.silenceMs) {
        if (spoke >= c.minSpeechMs) return 'speech_end';
        // Too short to be a question: forget it and keep listening.
        this.speechStartedAt = null;
        this.lastSpeechAt = null;
        this.loudSince = null;
        this.quietSince = null;
      }
    }
    return elapsed >= c.maxMs ? 'max_length' : 'none';
  }
}
