import {
  createAudioPlayer,
  setAudioModeAsync,
} from 'expo-audio';

import type { AudioPlayer, AudioStatus } from 'expo-audio';

import { BackendError, authHeaders } from './config';

import { getSpeechJobStatus } from './ttsService';

import type { SpeechJobRef, SpeechJobStatus } from './ttsService';


// ========================================
// ROOT CAUSE OF THE CUTS (verified)
// ========================================
//
// Real-device tests: the voice cut out even when
// REPLAYING fully downloaded audio. The backend audio was
// verified sample-exact, so the fault was playback:
//
//   9 WAV files -> 9 native players. At each boundary
//   the finished player was removed; with nothing playing,
//   expo-audio abandoned Android audio focus, and the next
//   player had to request focus again and start its own
//   audio output. Each hand-off = an audible gap.
//
// FIX: one answer = one continuous WAV stream = ONE player.
// There are no boundaries left. The backend only starts
// the stream when it can run to the end without stalling
// (see backend/speech_jobs.py "START POLICY"), and replay
// plays the same URL as one complete file.


export type SpeechPhase =
  | 'idle'
  | 'preparing'   // waiting for the first sound
  | 'playing'
  | 'waiting'     // player ran out of audio (should not happen)
  | 'finished'
  | 'failed';

export type SpeechMetrics = {
  requestedAt: number;
  firstSoundAt: number | null;
  stalls: number;
  stalledMs: number;
  job: SpeechJobStatus | null;
};

export type SpeechCallbacks = {
  onPhase?: (phase: SpeechPhase) => void;
  onError?: (error: unknown, heardAnything: boolean) => void;
  onFinished?: (metrics: SpeechMetrics) => void;
};

// Before playback: check often so playback starts as soon
// as the server says it is safe. After: only for metrics.
const STATUS_POLL_BEFORE_START_MS = 200;
const STATUS_POLL_AFTER_START_MS = 1500;
const START_TIMEOUT_MS = 120000;

// Position must be frozen this long to count as an audible cut.
const STALL_THRESHOLD_MS = 300;

// Network-drop recovery: how many times, and for how long
// the app keeps trying to reach the server.
const MAX_RECOVERIES = 3;
const RECOVERY_WINDOW_MS = 60000;


export class ContinuousSpeech {

  readonly job: SpeechJobRef;

  private callbacks: SpeechCallbacks;
  private player: AudioPlayer | null = null;
  private subscription: { remove: () => void } | null = null;
  private pollTimer: ReturnType<typeof setTimeout> | null = null;
  private startTimer: ReturnType<typeof setTimeout> | null = null;
  private stopped = false;
  private finished = false;
  private started = false;
  private stallStartedAt: number | null = null;
  private stallCheck: ReturnType<typeof setTimeout> | null = null;
  private lastPosition = 0;
  private lastPlaybackState = '';
  private lastAdvanceAt = Date.now();
  private recoveries = 0;
  private recovering = false;

  readonly metrics: SpeechMetrics;

  constructor(
    job: SpeechJobRef,
    callbacks: SpeechCallbacks,
    requestedAt: number = Date.now()
  ) {
    this.job = job;
    this.callbacks = callbacks;
    this.metrics = {
      requestedAt,
      firstSoundAt: null,
      stalls: 0,
      stalledMs: 0,
      job: null,
    };
  }


  // ======================================
  // START
  // ======================================

  async start(): Promise<void> {

    try {
      await setAudioModeAsync({
        playsInSilentMode: true,
        allowsRecording: false,
        interruptionMode: 'duckOthers',
        shouldPlayInBackground: false,
      });
    } catch (error) {
      console.warn('[SPEECH] Audio mode:', error);
    }

    if (this.stopped) return;

    this.setPhase('preparing');

    // The player is NOT created yet. Creating it before
    // the job has audio meant that, after a quota error,
    // Android's ExoPlayer kept retrying the failed audio
    // URL by itself. pollJob() creates the player only
    // when the server reports ready_to_start.
    this.pollJob();

    this.startTimer = setTimeout(() => {
      if (!this.started && !this.stopped) {
        this.fail(new BackendError(504, 'Speech did not start in time.'));
      }
    }, START_TIMEOUT_MS);
  }

  stop(): void {
    if (this.stopped) return;
    this.stopped = true;
    this.cleanup();
    this.setPhase('idle');
  }


  // ======================================
  // PLAYER
  // ======================================

  private createPlayer(resumeAt = 0): void {

    this.releasePlayer();

    try {
      // ExoPlayer streams the URL progressively and
      // starts as soon as it has buffered audio.
      this.player = createAudioPlayer(
        { uri: this.job.audioUrl, headers: authHeaders() },
        { updateInterval: 250 }
      );
    } catch (error) {
      this.fail(error);
      return;
    }

    const player = this.player;

    this.subscription = player.addListener(
      'playbackStatusUpdate',
      (status: AudioStatus) => this.onStatus(player, status)
    );

    if (resumeAt > 0) {
      // Resume after a network drop: the complete file is
      // seekable (HTTP Range), so continue where we stopped.
      player
        .seekTo(resumeAt)
        .catch((error) => console.warn('[SPEECH] Resume seek failed:', error))
        .finally(() => {
          if (this.player === player && !this.stopped) player.play();
        });
      return;
    }

    player.play();
  }

  private onStatus(player: AudioPlayer, status: AudioStatus): void {

    if (this.stopped || this.finished || player !== this.player) {
      return;
    }

    const nativeError = (status as AudioStatus & { error?: string | null }).error;

    if (nativeError) {
      void this.onPlayerError(nativeError);
      return;
    }

    if (status.playing && !status.isBuffering) {

      if (!this.started) {
        this.started = true;
        this.lastAdvanceAt = Date.now();
        this.metrics.firstSoundAt = Date.now();
        console.log(
          '[SPEECH] First sound after',
          ((this.metrics.firstSoundAt - this.metrics.requestedAt) / 1000).toFixed(2),
          's'
        );
      }

      if (this.stallStartedAt !== null) {
        this.metrics.stalledMs += Date.now() - this.stallStartedAt;
        this.stallStartedAt = null;
      }

      this.setPhase('playing');
    }

    // ExoPlayer briefly reports "buffering" in situations
    // that are NOT audible (e.g. the last moments of a live
    // stream whose final length is not known yet). The old
    // check counted every such report as an interruption,
    // which is why a warning appeared without any audible
    // cut. A real interruption = the playback POSITION stops
    // advancing for >= 300 ms before the end of the audio.
    const position = status.currentTime ?? 0;
    this.lastPlaybackState = status.playbackState;

    if (position > this.lastPosition + 0.01) {
      this.lastPosition = position;
      this.lastAdvanceAt = Date.now();
      this.clearSuspectedStall();
    }

    if (this.started && status.isBuffering && this.stallCheck === null) {

      this.stallCheck = setTimeout(() => {

        this.stallCheck = null;

        if (this.stopped || this.finished) return;

        const totalAudio = this.metrics.job?.done
          ? this.metrics.job.audioSeconds
          : Number.POSITIVE_INFINITY;

        const nearEnd = this.lastPosition >= totalAudio - 0.4;
        const frozenMs = Date.now() - this.lastAdvanceAt;

        if (!nearEnd && frozenMs >= STALL_THRESHOLD_MS && this.stallStartedAt === null) {
          this.stallStartedAt = this.lastAdvanceAt;
          this.metrics.stalls += 1;
          console.warn(
            '[SPEECH] Audible interruption',
            this.metrics.stalls,
            '| position',
            this.lastPosition.toFixed(2),
            's of',
            Number.isFinite(totalAudio) ? totalAudio.toFixed(2) : 'unknown',
            's | frozen',
            frozenMs,
            'ms | player state',
            this.lastPlaybackState,
            '| server audio ready',
            this.metrics.job?.audioSeconds ?? '?',
            's | provider',
            this.metrics.job?.provider ?? '?',
            this.metrics.job?.model ?? ''
          );
          this.setPhase('waiting');
        } else {
          console.log(
            '[SPEECH] Ignored inaudible buffering report | position',
            this.lastPosition.toFixed(2),
            '| near end:',
            nearEnd
          );
        }
      }, STALL_THRESHOLD_MS);
    }

    if (status.didJustFinish) {
      this.clearSuspectedStall();
      this.complete();
    }
  }

  private clearSuspectedStall(): void {
    if (this.stallCheck !== null) {
      clearTimeout(this.stallCheck);
      this.stallCheck = null;
    }
  }

  private async onPlayerError(message: string): Promise<void> {

    console.warn('[SPEECH] Player error:', message);

    // Stop the failed player now, so it cannot keep retrying
    // the URL in the background while we recover.
    if (this.recovering) return;          // one recovery at a time
    this.recovering = true;

    const resumeAt = this.started ? Math.max(0, this.lastPosition - 0.3) : 0;
    this.releasePlayer();
    this.setPhase('waiting');

    if (this.recoveries >= MAX_RECOVERIES) {
      this.fail(new Error(`Audio playback failed: ${message}`));
      return;
    }
    this.recoveries += 1;

    // Typical cause: the Wi-Fi dropped for a moment. Keep
    // checking (the network may be down) until the server has
    // the COMPLETE answer, then continue from the last
    // position in the same voice.
    const giveUpAt = Date.now() + RECOVERY_WINDOW_MS;

    while (!this.stopped && Date.now() < giveUpAt) {
      const latest = await this.safeStatus();

      if (latest?.error && !latest.audioSeconds) {
        this.fail(new BackendError(latest.error.status, latest.error.message));
        return;
      }

      if (latest?.done && latest.audioSeconds > 0) {
        console.log('[SPEECH] Recovering playback at', resumeAt.toFixed(2), 's');
        this.recovering = false;
        this.createPlayer(resumeAt);
        return;
      }

      await new Promise((resolve) => setTimeout(resolve, 700));
    }

    if (!this.stopped) {
      this.fail(new Error(`Audio playback failed: ${message}`));
    }
  }


  // ======================================
  // JOB STATUS (errors + metrics)
  // ======================================

  private async safeStatus(): Promise<SpeechJobStatus | null> {
    try {
      return await getSpeechJobStatus(this.job);
    } catch {
      return null;
    }
  }

  private pollJob(): void {

    this.pollTimer = setTimeout(async () => {

      if (this.stopped || this.finished) return;

      let status: SpeechJobStatus | null = null;
      let networkError = false;

      try {
        status = await getSpeechJobStatus(this.job);
      } catch (error) {
        // Network blip: keep polling (the start timeout still
        // applies). Previously this was mistaken for a 404.
        networkError = true;
        console.warn('[SPEECH] Status check failed:', error);
      }

      if (this.stopped || this.finished) return;

      if (status === null && !networkError && !this.started) {
        // A real 404: job expired or the server restarted.
        this.fail(new BackendError(404, 'Speech job not found.'));
        return;
      }

      if (status) {
        this.metrics.job = status;

        // Terminal error before any audio (e.g. daily
        // quota): stop immediately. No player was created,
        // so nothing retries in the background.
        if (status.error && !this.player) {
          this.fail(new BackendError(status.error.status, status.error.message));
          return;
        }

        // Only the FIRST start happens here; after a network
        // drop, onPlayerError() resumes at the saved position.
        if (
          !this.player &&
          !this.started &&
          !this.recovering &&
          (status.readyToStart || (status.done && !status.error))
        ) {
          this.createPlayer();
        }
      }

      if (!status?.done || !this.started) {
        this.pollJob();
      }

    }, this.player ? STATUS_POLL_AFTER_START_MS : STATUS_POLL_BEFORE_START_MS);
  }


  // ======================================
  // END STATES
  // ======================================

  private complete(): void {

    if (this.finished) return;

    this.finished = true;

    if (this.stallStartedAt !== null) {
      this.metrics.stalledMs += Date.now() - this.stallStartedAt;
      this.stallStartedAt = null;
    }

    console.log('[SPEECH] Metrics:', JSON.stringify({
      firstSound_s: this.metrics.firstSoundAt
        ? (this.metrics.firstSoundAt - this.metrics.requestedAt) / 1000
        : null,
      interruptions: this.metrics.stalls,
      interrupted_s: this.metrics.stalledMs / 1000,
      job: this.metrics.job,
    }));

    this.cleanup();
    this.setPhase('finished');
    this.callbacks.onFinished?.(this.metrics);
  }

  private fail(error: unknown): void {

    if (this.finished || this.stopped) return;

    this.finished = true;
    this.cleanup();
    this.setPhase('failed');
    this.callbacks.onError?.(error, this.started);
  }

  private setPhase(phase: SpeechPhase): void {
    this.callbacks.onPhase?.(phase);
  }

  private releasePlayer(): void {
    try {
      this.subscription?.remove();
    } catch {
      // Ignore.
    }
    this.subscription = null;

    if (this.player) {
      try {
        this.player.pause();
      } catch {
        // Ignore.
      }
      try {
        this.player.remove();
      } catch {
        // Ignore.
      }
      this.player = null;
    }
  }

  private cleanup(): void {
    this.clearSuspectedStall();
    if (this.pollTimer !== null) {
      clearTimeout(this.pollTimer);
      this.pollTimer = null;
    }
    if (this.startTimer !== null) {
      clearTimeout(this.startTimer);
      this.startTimer = null;
    }
    this.releasePlayer();
  }
}
