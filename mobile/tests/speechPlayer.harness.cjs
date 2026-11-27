/*
 * Automated tests for mobile/Services/speechPlayer.ts (no phone needed).
 *
 * Compiles the REAL speechPlayer.ts / ttsService.ts / config.ts with
 * TypeScript, replaces the native modules (expo-audio, expo/fetch,
 * expo-file-system) with simulations, and runs every scenario:
 *
 *   node tests/speechPlayer.harness.cjs        (from the mobile folder)
 */

const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const Module = require('node:module');

const MOBILE = path.resolve(__dirname, '..');
const OUT = fs.mkdtempSync(path.join(os.tmpdir(), 'wv-speech-test-'));

// ---------- 1. compile the real sources ----------
try {
  execFileSync(
    process.execPath,
    [
      path.join(MOBILE, 'node_modules', 'typescript', 'bin', 'tsc'),
      '--ignoreConfig',
      '--outDir', OUT, '--module', 'commonjs', '--target', 'es2020',
      '--moduleResolution', 'node', '--skipLibCheck', '--esModuleInterop',
      '--noEmitOnError', 'false',
      path.join(MOBILE, 'Services', 'speechPlayer.ts'),
    ],
    { stdio: 'pipe' }
  );
} catch (error) {
  // Type errors from missing React Native typings are expected
  // here (the real typecheck is `npx tsc --noEmit`); only fail if
  // no JavaScript was produced.
  if (!fs.existsSync(path.join(OUT, 'speechPlayer.js'))) {
    console.error(String(error.stdout || error));
    process.exit(2);
  }
}

// ---------- 2. simulated native world ----------
const world = {
  status: null,          // (n) => status object | 'NETWORK' | 404
  players: [],
  player: null,          // scenario behaviour for players
  statusCalls: 0,
};

class FakePlayer {
  constructor(source) {
    this.source = source;
    this.listeners = [];
    this.position = 0;
    this.removed = false;
    this.playing = false;
    this.seeks = [];
    this.timer = null;
    world.players.push(this);
  }
  addListener(_event, cb) {
    this.listeners.push(cb);
    return { remove: () => { this.listeners = this.listeners.filter((l) => l !== cb); } };
  }
  emit(status) {
    for (const l of [...this.listeners]) l({
      playing: true, isBuffering: false, didJustFinish: false, isLoaded: true,
      playbackState: 'ready', currentTime: this.position, ...status,
    });
  }
  async seekTo(t) { this.position = t; this.seeks.push(t); }
  play() {
    if (this.removed) return;
    this.playing = true;
    const behaviour = world.player;
    const step = 0.05;
    this.timer = setInterval(() => {
      if (this.removed) return clearInterval(this.timer);
      const index = world.players.indexOf(this);
      const action = behaviour(this.position, index);
      if (action === 'error') { clearInterval(this.timer); return this.emit({ error: 'network' }); }
      if (action === 'buffer') return this.emit({ isBuffering: true, playbackState: 'buffering' });
      this.position = +(this.position + step).toFixed(3);
      if (this.position >= world.duration) {
        clearInterval(this.timer);
        return this.emit({ didJustFinish: true, playing: false });
      }
      this.emit({});
    }, 10);   // 10 ms real time = 50 ms of audio (5x speed)
  }
  pause() { this.playing = false; }
  remove() { this.removed = true; clearInterval(this.timer); }
}

const mocks = {
  'expo-audio': {
    createAudioPlayer: (source) => new FakePlayer(source),
    setAudioModeAsync: async () => {},
  },
  'expo/fetch': {
    fetch: async (url) => {
      world.statusCalls += 1;
      const s = world.status(world.statusCalls);
      if (s === 'NETWORK') throw new Error('Network request failed');
      if (s === 404) return { ok: false, status: 404, json: async () => ({}) };
      return { ok: true, status: 200, json: async () => s };
    },
  },
  'expo-file-system': {
    File: class { constructor() { this.exists = true; this.size = 1; } delete() {} },
    Paths: { cache: '/tmp' },
  },
};

const originalLoad = Module._load;
Module._load = function (request, parent, isMain) {
  if (mocks[request]) return mocks[request];
  return originalLoad.call(this, request, parent, isMain);
};

const { ContinuousSpeech } = require(path.join(OUT, 'speechPlayer.js'));

// ---------- 3. helpers ----------
const job = { id: 'j', audioUrl: 'http://x/tts/jobs/j/audio', statusUrl: 'http://x/tts/jobs/j' };

const statusDone = (audio = 4) => ({
  done: true, ready_to_start: true, error: null, audio_seconds: audio,
  provider: 'test', model: null,
});
const statusPreparing = () => ({
  done: false, ready_to_start: false, error: null, audio_seconds: 0, provider: 'test',
});

function run(name, setup, check, timeoutMs = 20000) {
  return new Promise((resolve) => {
    world.players = [];
    world.statusCalls = 0;
    world.duration = 4;
    setup();
    const phases = [];
    let finished = null;
    let failed = null;
    const speech = new ContinuousSpeech(job, {
      onPhase: (p) => phases.push(p),
      onFinished: (m) => { finished = m; },
      onError: (e, heard) => { failed = { e, heard }; },
    });
    const originalWarn = console.warn;
    const originalLog = console.log;
    console.warn = () => {};
    console.log = () => {};
    const done = (ok, detail) => {
      clearInterval(poll);
      console.warn = originalWarn;
      console.log = originalLog;
      speech.stop();
      resolve({ name, ok, detail });
    };
    const startedAt = Date.now();
    const poll = setInterval(() => {
      const verdict = check({ finished, failed, phases, speech, elapsed: Date.now() - startedAt });
      if (verdict === true) done(true, '');
      else if (typeof verdict === 'string') done(false, verdict);
      else if (Date.now() - startedAt > timeoutMs) done(false, 'timeout');
    }, 50);
    speech.start();
  });
}

// ---------- 4. scenarios ----------
const scenarios = [
  () => run('normal answer: one player, created only when ready, 0 interruptions',
    () => {
      world.status = (n) => (n < 3 ? statusPreparing() : statusDone());
      world.player = () => 'play';
    },
    ({ finished, failed }) => {
      if (failed) return `failed: ${failed.e.message}`;
      if (!finished) return null;
      if (world.players.length !== 1) return `players=${world.players.length}`;
      if (finished.stalls !== 0) return `stalls=${finished.stalls}`;
      return true;
    }),

  () => run('quota error before audio: fails cleanly, NO player created (no retries)',
    () => {
      world.status = () => ({ ...statusPreparing(), done: true,
        error: { status: 429, message: 'Natural voice quota reached.' } });
      world.player = () => 'play';
    },
    ({ failed, finished }) => {
      if (finished) return 'should not finish';
      if (!failed) return null;
      if (failed.e.status !== 429) return `status=${failed.e.status}`;
      if (world.players.length !== 0) return `players=${world.players.length}`;
      return true;
    }),

  () => run('network blip while preparing: keeps waiting, then plays (not a false 404)',
    () => {
      world.status = (n) => (n <= 2 ? statusPreparing() : n <= 5 ? 'NETWORK' : statusDone());
      world.player = () => 'play';
    },
    ({ finished, failed }) => {
      if (failed) return `failed: ${failed.e.message}`;
      return finished ? true : null;
    }),

  () => run('network drop mid-answer: resumes at last position, single active player',
    () => {
      world.status = (n) => (n <= 2 ? statusDone() : n <= 6 ? 'NETWORK' : statusDone());
      // First player errors at 2.0 s; the replacement plays normally.
      world.player = (position, index) => (index === 0 && position >= 2.0 ? 'error' : 'play');
    },
    ({ finished, failed }) => {
      if (failed) return `failed: ${failed.e.message}`;
      if (!finished) return null;
      if (world.players.length !== 2) return `players=${world.players.length}`;
      const seek = world.players[1].seeks[0];
      if (!(seek > 1.5 && seek < 2.0)) return `resume seek=${seek}`;
      const active = world.players.filter((p) => !p.removed).length;
      if (active > 1) return `active players=${active}`;
      return true;
    }),

  () => run('inaudible buffering report at the very end: NOT counted as interruption',
    () => {
      // 0.5 s "buffering" report 0.15 s before the end (longer
      // than the 300 ms threshold, so the near-end rule is tested).
      let until = null;
      world.status = () => statusDone(4);
      world.player = (position) => {
        if (position >= 3.85 && until === null) until = Date.now() + 500;
        if (until && Date.now() < until) return 'buffer';
        return 'play';
      };
    },
    ({ finished }) => (finished ? (finished.stalls === 0 ? true : `stalls=${finished.stalls}`) : null)),

  () => run('real stall mid-answer (position frozen 0.6 s): counted exactly once',
    () => {
      let frozenUntil = null;
      world.status = () => statusDone(4);
      world.player = (position) => {
        if (position >= 1.5 && frozenUntil === null) frozenUntil = Date.now() + 600;
        if (frozenUntil && Date.now() < frozenUntil) return 'buffer';
        return 'play';
      };
    },
    ({ finished }) => (finished ? (finished.stalls === 1 ? true : `stalls=${finished.stalls}`) : null)),

  () => run('stop during playback: player removed, nothing restarts',
    () => {
      world.status = () => statusDone(4);
      world.player = () => 'play';
    },
    ({ speech, elapsed, finished }) => {
      if (elapsed > 400 && !speech._stoppedByTest) {
        speech.stop();
        speech._stoppedByTest = Date.now();
      }
      if (speech._stoppedByTest && Date.now() - speech._stoppedByTest > 1500) {
        if (finished) return 'finished after stop';
        if (world.players.some((p) => !p.removed)) return 'player still active';
        return true;
      }
      return null;
    }),
];

(async () => {
  let failures = 0;
  for (const scenario of scenarios) {
    const result = await scenario();
    if (!result.ok) failures += 1;
    console.log(`${result.ok ? 'PASS' : 'FAIL'}  ${result.name}${result.detail ? '  -> ' + result.detail : ''}`);
  }
  console.log(`\n${scenarios.length - failures}/${scenarios.length} scenarios passed`);
  fs.rmSync(OUT, { recursive: true, force: true });
  process.exit(failures ? 1 : 0);
})();
