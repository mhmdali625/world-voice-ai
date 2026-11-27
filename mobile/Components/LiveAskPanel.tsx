import { useEffect, useRef, useState } from 'react';

import {
  AccessibilityInfo,
  Pressable,
  StyleSheet,
  Text,
  View,
} from 'react-native';

import {
  AudioModule,
  RecordingPresets,
  setAudioModeAsync,
  useAudioRecorder,
} from 'expo-audio';

import type { RecordingOptions } from 'expo-audio';
import { File } from 'expo-file-system';
import { fetch } from 'expo/fetch';

import { reportFirstWord, requestLiveAnswer } from '../Services/aiService';
import { BACKEND_URL, BackendError, authHeaders } from '../Services/config';
import { getDeviceId } from '../Services/device';
import {
  announce,
  isScreenReaderOn,
  playEarcon,
  setFeedbackLanguage,
  stopFeedback,
} from '../Services/feedback';
import { ContinuousSpeech } from '../Services/speechPlayer';
import type { SpeechPhase } from '../Services/speechPlayer';
import { createSpeechJob } from '../Services/ttsService';
import type { SpeechJobRef } from '../Services/ttsService';
import { VoiceActivityDetector } from '../Services/voiceActivity';

// ========================================
// LIVE ASSISTANT PANEL
// ========================================
//
// Used inside the streaming screen. While the phone streams to the
// backend, the user asks by voice; the backend answers from the CURRENT
// VIEW and the VISUAL MEMORY (/ask), and the answer is spoken with the
// continuous natural-voice player.
//
//   Manual mode : tap to ask, tap again when finished.
//   Hands-free  : after each answer the panel listens again by itself and
//                 sends the question when the user stops speaking. The
//                 microphone is never open while an answer or a warning
//                 plays (and opens only after the speaker falls silent).
//
// Also speaks warnings about obstacles the camera STILL shows (bonus),
// in the user's language, never while the user is speaking.

const SPEECH_RECORDING: RecordingOptions = {
  ...RecordingPresets.HIGH_QUALITY,
  sampleRate: 16000,
  numberOfChannels: 1,
  bitRate: 48000,
  isMeteringEnabled: true,
};

const EARCON_SETTLE_MS = 280;
const AFTER_SPEECH_MS = 700;          // speaker tail / echo dies down before listening
const VAD_POLL_MS = 100;
const ALERT_POLL_MS = 3000;

type Props = {
  /** True while the phone is streaming to the backend. */
  streaming: boolean;
  /** Turn hands-free on by itself whenever streaming is live (app auto-start). */
  autoHandsFree?: boolean;
};

type Phase = 'idle' | 'listening' | 'thinking' | 'speaking';

type LiveAlert = {
  id: string;
  hazard: string;
  message: string;
  language: string;
  expires_in: number;
};

const delay = (ms: number) =>
  new Promise<void>(resolve => setTimeout(resolve, ms));

function removeFile(uri: string | null) {
  if (!uri) return;
  try {
    const file = new File(uri);
    if (file.exists) file.delete();
  } catch {
    // Ignore.
  }
}

export default function LiveAskPanel({ streaming, autoHandsFree = false }: Props) {
  const recorder = useAudioRecorder(SPEECH_RECORDING);

  const [phase, setPhaseState] = useState<Phase>('idle');
  const [handsFree, setHandsFreeState] = useState(false);
  const [status, setStatus] = useState('Start streaming, then ask a question.');
  const [question, setQuestion] = useState('');
  const [answer, setAnswer] = useState('');
  const [details, setDetails] = useState('');

  const phaseRef = useRef<Phase>('idle');
  const handsFreeRef = useRef(false);
  const streamingRef = useRef(streaming);
  const sessionRef = useRef<ContinuousSpeech | null>(null);
  const lastJobRef = useRef<SpeechJobRef | null>(null);
  const busyRef = useRef(false);
  const mountedRef = useRef(true);
  const recordingRef = useRef(false);
  const vadRef = useRef<VoiceActivityDetector | null>(null);
  const vadTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const armTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const warningPlayingRef = useRef(false);
  const spokenAlertsRef = useRef<Set<string>>(new Set());
  // The user switched hands-free off: do not switch it back on by itself.
  const userDisabledRef = useRef(false);

  streamingRef.current = streaming;

  const setPhase = (p: Phase) => {
    phaseRef.current = p;
    if (mountedRef.current) setPhaseState(p);
  };

  const say = (message: string) => {
    if (mountedRef.current) setStatus(message);
  };

  // ---------------- microphone ----------------

  const clearVadTimer = () => {
    if (vadTimerRef.current) clearTimeout(vadTimerRef.current);
    vadTimerRef.current = null;
  };

  const clearArmTimer = () => {
    if (armTimerRef.current) clearTimeout(armTimerRef.current);
    armTimerRef.current = null;
  };

  /** Stops the microphone; returns the recording file (or null). */
  const stopRecording = async (): Promise<string | null> => {
    clearVadTimer();
    if (!recordingRef.current) return null;
    recordingRef.current = false;
    try {
      await recorder.stop();
    } catch {
      // Already stopped.
    }
    const uri = recorder.uri;
    await setAudioModeAsync({ playsInSilentMode: true, allowsRecording: false }).catch(() => undefined);
    return uri;
  };

  const startRecording = async (): Promise<boolean> => {
    const permission = await AudioModule.requestRecordingPermissionsAsync();
    if (!permission.granted) {
      say('Microphone permission is required.');
      return false;
    }
    await playEarcon('listen_start');
    await delay(EARCON_SETTLE_MS);
    // Never record our own voice: nothing may play while the mic is open.
    if (sessionRef.current || warningPlayingRef.current) return false;
    await setAudioModeAsync({ playsInSilentMode: true, allowsRecording: true });
    await recorder.prepareToRecordAsync();
    recorder.record();
    recordingRef.current = true;
    return true;
  };

  // ---------------- speech ----------------

  const stopSpeech = () => {
    sessionRef.current?.stop();
    sessionRef.current = null;
    warningPlayingRef.current = false;
    stopFeedback();
  };

  /** Plays an answer or a warning; `onDone` runs when it ends (any way). */
  const play = (
    job: SpeechJobRef,
    options: { onFirstSound?: () => void; onDone?: () => void; isWarning?: boolean } = {}
  ) => {
    stopSpeech();
    if (!options.isWarning) lastJobRef.current = job;
    if (options.isWarning) warningPlayingRef.current = true;
    let firstSound = false;
    const finish = () => {
      if (options.isWarning) warningPlayingRef.current = false;
      options.onDone?.();
    };
    const session = new ContinuousSpeech(job, {
      onPhase: (p: SpeechPhase) => {
        if (sessionRef.current !== session || !mountedRef.current) return;
        if (p === 'playing' && !firstSound) {
          firstSound = true;
          options.onFirstSound?.();
        }
        if ((p === 'playing' || p === 'waiting') && !options.isWarning) setPhase('speaking');
      },
      onFinished: () => {
        if (sessionRef.current !== session) return;
        sessionRef.current = null;
        if (!options.isWarning) setPhase('idle');
        finish();
      },
      onError: () => {
        if (sessionRef.current !== session) return;
        sessionRef.current = null;
        if (!options.isWarning) {
          setPhase('idle');
          say('Voice unavailable. The answer is shown on screen.');
          void announce('voice_unavailable', 'error');
        }
        finish();
      },
    });
    sessionRef.current = session;
    void session.start();
  };

  // ---------------- hands-free listening ----------------

  /** Listens again after a short pause (hands-free mode only). */
  const armHandsFree = (waitMs = AFTER_SPEECH_MS) => {
    clearArmTimer();
    if (!handsFreeRef.current) return;
    armTimerRef.current = setTimeout(() => {
      armTimerRef.current = null;
      void listenHandsFree();
    }, waitMs);
  };

  const listenHandsFree = async () => {
    if (!handsFreeRef.current || !streamingRef.current || !mountedRef.current) return;
    if (busyRef.current || phaseRef.current !== 'idle') return;
    if (sessionRef.current || warningPlayingRef.current) {
      armHandsFree();                 // something is playing: try again after it
      return;
    }
    busyRef.current = true;
    let started = false;
    try {
      started = await startRecording();
    } catch (error) {
      console.warn('[LIVE] Hands-free microphone error:', error);
    } finally {
      busyRef.current = false;
    }
    if (!started) {
      armHandsFree(1500);
      return;
    }
    if (!handsFreeRef.current) {      // switched off while the earcon played
      removeFile(await stopRecording());
      return;
    }
    setPhase('listening');
    say('Listening (hands-free)... speak your question.');
    const vad = new VoiceActivityDetector();
    vadRef.current = vad;

    const tick = () => {
      if (!recordingRef.current || vadRef.current !== vad) return;
      let level: number | undefined;
      try {
        level = recorder.getStatus().metering;
      } catch {
        level = undefined;
      }
      const event = vad.push(level, Date.now());
      if (event === 'speech_end' || event === 'max_length') {
        // The user stopped speaking when the level was last loud.
        void stopAndAsk(vad.lastSpeechAt ?? Date.now());
        return;
      }
      if (event === 'no_speech') {
        // Nothing said: discard silently and keep listening.
        void (async () => {
          vadRef.current = null;
          removeFile(await stopRecording());
          setPhase('idle');
          armHandsFree(100);
        })();
        return;
      }
      vadTimerRef.current = setTimeout(tick, VAD_POLL_MS);
    };
    vadTimerRef.current = setTimeout(tick, VAD_POLL_MS);
  };

  const setHandsFree = (on: boolean, byUser = false) => {
    if (byUser) userDisabledRef.current = !on;
    handsFreeRef.current = on;
    if (mountedRef.current) setHandsFreeState(on);
    if (on) {
      say('Hands-free on: I listen again after each answer.');
      AccessibilityInfo.announceForAccessibility('Hands-free on');
      if (phaseRef.current === 'idle') armHandsFree(200);
    } else {
      clearArmTimer();
      if (phaseRef.current === 'listening') {
        vadRef.current = null;
        void stopRecording().then(removeFile);
        setPhase('idle');
      }
      say('Hands-free off.');
      AccessibilityInfo.announceForAccessibility('Hands-free off. Listening stopped.');
    }
  };

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      handsFreeRef.current = false;
      clearArmTimer();
      clearVadTimer();
      void stopRecording().then(removeFile);
      stopSpeech();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!streaming && handsFreeRef.current) setHandsFree(false);
    // Auto-start: hands-free comes on (again, after a reconnect) unless
    // the user switched it off.
    if (streaming && autoHandsFree && !handsFreeRef.current && !userDisabledRef.current) {
      setHandsFree(true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [streaming, autoHandsFree]);

  // ---------------- ask ----------------

  const startListening = async () => {
    if (busyRef.current || !streaming) {
      if (!streaming) {
        say('Start streaming first.');
        void announce('ask_question_first', 'error');
      }
      return;
    }
    busyRef.current = true;
    try {
      stopSpeech();                       // never record our own voice
      if (await startRecording()) {
        setPhase('listening');
        say('Listening... tap again when you finish.');
      }
    } catch (error) {
      console.warn('[LIVE] Microphone error:', error);
      say('Could not start the microphone.');
      void announce('connection_failed', 'error');
    } finally {
      busyRef.current = false;
    }
  };

  /** `finishedAt`: when the user stopped speaking (ms) - the latency clock starts here. */
  const stopAndAsk = async (finishedAt: number) => {
    if (busyRef.current) return;
    busyRef.current = true;
    vadRef.current = null;
    let audioUri: string | null = null;
    try {
      audioUri = await stopRecording();
      void playEarcon('listen_stop');
      setPhase('thinking');
      say('Thinking...');

      if (!audioUri) throw new Error('No recording');

      // ONE request: the server hears the recording, takes the newest
      // camera frame and the memory, and answers.
      const result = await requestLiveAnswer({
        question: '',
        questionAudioUri: audioUri,
      });

      if (!mountedRef.current) return;
      setFeedbackLanguage(result.answerLanguage);
      setQuestion(result.questionHeard || result.question);
      setAnswer(result.answer);
      const base =
        `Evidence ${result.evidence} | memory frames ${result.memoryFrames} | ` +
        `answer ready ${((Date.now() - finishedAt) / 1000).toFixed(1)} s`;
      setDetails(base);
      say('Answer ready.');

      if (result.speechJob) {
        play(result.speechJob, {
          onFirstSound: () => {
            // Real end-to-end latency: user stopped speaking -> first word heard.
            const seconds = (Date.now() - finishedAt) / 1000;
            if (mountedRef.current) setDetails(`${base} | first word ${seconds.toFixed(1)} s`);
            if (result.qaId) void reportFirstWord(result.qaId, seconds);
          },
          onDone: () => armHandsFree(),
        });
      } else {
        setPhase('idle');
        armHandsFree();
      }
      if (isScreenReaderOn() && !result.speechJob) {
        AccessibilityInfo.announceForAccessibility(result.answer);
      }
    } catch (error) {
      console.warn('[LIVE] Ask failed:', error);
      setPhase('idle');
      if (error instanceof BackendError && error.status === 422) {
        say('I could not understand the question. Please try again.');
        void announce('not_understood', 'error');
      } else if (error instanceof BackendError && error.status === 401) {
        say('The laptop needs the access code. Check Settings > Access code.');
        AccessibilityInfo.announceForAccessibility('The laptop needs the access code. Check the settings.');
      } else if (error instanceof BackendError && error.status === 409) {
        say('The camera is not streaming yet.');
        void announce('ask_question_first', 'error');
      } else if (error instanceof BackendError) {
        say('I could not answer right now. Please try again.');
        void announce('analysis_failed', 'error');
      } else {
        say('Connection problem. Please try again.');
        void announce('connection_failed', 'error');
      }
      armHandsFree(2500);
    } finally {
      removeFile(audioUri);               // the recording is never kept
      busyRef.current = false;
    }
  };

  const onMainButton = () => {
    const current = phaseRef.current;
    if (current === 'listening') {
      if (handsFreeRef.current) {
        setHandsFree(false, true);        // accessible way to stop listening
      } else {
        void stopAndAsk(Date.now());
      }
    } else if (current === 'speaking') {
      stopSpeech();
      setPhase('idle');
      say('Stopped.');
      armHandsFree();
    } else if (current === 'idle') {
      clearArmTimer();
      void startListening();
    }
  };

  const repeat = () => {
    if (lastJobRef.current && phaseRef.current === 'idle' && !recordingRef.current) {
      clearArmTimer();
      play(lastJobRef.current, { onDone: () => armHandsFree() });   // cached on the server: no quota
    }
  };

  // ---------------- warnings without being asked (bonus) ----------------

  useEffect(() => {
    if (!streaming) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const speakWarning = async (alert: LiveAlert) => {
      // Hands-free listening but the user has not started to speak: close
      // the microphone (discard) BEFORE the warning plays, then listen again.
      if (phaseRef.current === 'listening') {
        vadRef.current = null;
        removeFile(await stopRecording());
        setPhase('idle');
      }
      clearArmTimer();
      say(alert.message);
      void playEarcon('error');
      if (isScreenReaderOn()) {
        AccessibilityInfo.announceForAccessibility(alert.message);
        armHandsFree(2500);
        return;
      }
      try {
        const job = await createSpeechJob({
          text: alert.message,
          language: alert.language as 'en' | 'fr' | 'ar' | 'ar-LB',
        });
        play(job, { isWarning: true, onDone: () => armHandsFree() });
      } catch {
        armHandsFree();                   // earcon + on-screen text remain
      }
    };

    const poll = async () => {
      try {
        const response = await fetch(
          `${BACKEND_URL}/memory/alerts?device=${encodeURIComponent(getDeviceId())}`,
          { headers: authHeaders() }
        );
        if (response.ok) {
          const data = (await response.json()) as { alerts: LiveAlert[] };
          // The server returns only warnings valid NOW (not expired, scene
          // still in view). Speak each once, and never while the user is
          // asking, waiting for or hearing an answer.
          const userBusy =
            busyRef.current ||
            phaseRef.current === 'thinking' ||
            phaseRef.current === 'speaking' ||
            sessionRef.current !== null ||
            (phaseRef.current === 'listening' &&
              (!handsFreeRef.current || Boolean(vadRef.current?.speaking)));
          const next = data.alerts.find(
            a => !spokenAlertsRef.current.has(a.id) && a.expires_in > 1
          );
          if (next && !userBusy) {
            spokenAlertsRef.current.add(next.id);
            await speakWarning(next);
          }
        }
      } catch {
        // Network blip: try again on the next poll.
      }
      if (active) timer = setTimeout(() => { void poll(); }, ALERT_POLL_MS);
    };

    void poll();
    return () => {
      active = false;
      if (timer) clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [streaming]);

  // ---------------- UI ----------------

  const mainLabel =
    phase === 'listening'
      ? handsFree ? 'Stop Listening' : 'Stop and Get Answer'
      : phase === 'thinking' ? 'Thinking...'
        : phase === 'speaking' ? 'Stop Speaking'
          : 'Ask a Question';

  return (
    <View style={styles.container}>
      <Text
        style={styles.status}
        accessibilityLiveRegion="polite"
      >
        {status}
      </Text>

      <Pressable
        style={[
          styles.mainButton,
          phase === 'listening' && styles.listening,
          (!streaming || phase === 'thinking') && styles.disabled,
        ]}
        onPress={onMainButton}
        disabled={!streaming || phase === 'thinking'}
        accessibilityRole="button"
        accessibilityLabel={mainLabel}
        accessibilityHint={
          phase === 'idle'
            ? 'Starts listening to your question about what the camera sees'
            : phase === 'listening' && handsFree
              ? 'Stops hands-free listening'
              : undefined
        }
        accessibilityState={{ disabled: !streaming || phase === 'thinking', busy: phase === 'thinking' }}
      >
        <Text style={styles.mainText}>{mainLabel}</Text>
      </Pressable>

      <Pressable
        style={[styles.handsFree, handsFree && styles.handsFreeOn, !streaming && styles.disabled]}
        onPress={() => setHandsFree(!handsFree, true)}
        disabled={!streaming}
        accessibilityRole="switch"
        accessibilityLabel="Hands-free conversation"
        accessibilityHint="When on, I listen again automatically after each answer"
        accessibilityState={{ checked: handsFree, disabled: !streaming }}
      >
        <Text style={styles.handsFreeText}>
          {handsFree ? 'Hands-free: ON (tap to stop)' : 'Hands-free: OFF'}
        </Text>
      </Pressable>

      {answer.length > 0 && (
        <View style={styles.answerBox} accessible={true}>
          {question.length > 0 && (
            <Text style={styles.question}>{question}</Text>
          )}
          <Text style={styles.answer}>{answer}</Text>
          <Text style={styles.details}>{details}</Text>
          <Pressable
            style={[styles.repeat, phase !== 'idle' && styles.disabled]}
            onPress={repeat}
            disabled={phase !== 'idle'}
            accessibilityRole="button"
            accessibilityLabel="Repeat the answer"
          >
            <Text style={styles.repeatText}>Repeat Answer</Text>
          </Pressable>
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { width: '100%', marginTop: 10 },
  status: { color: '#FFFFFF', fontSize: 15, textAlign: 'center', marginBottom: 8 },
  mainButton: {
    backgroundColor: '#5DD6C0', padding: 20, borderRadius: 16,
    alignItems: 'center', minHeight: 72, justifyContent: 'center',
  },
  listening: { backgroundColor: '#F87171' },
  disabled: { opacity: 0.5 },
  mainText: { color: '#101827', fontSize: 20, fontWeight: 'bold' },
  handsFree: {
    backgroundColor: '#334155', padding: 14, borderRadius: 14,
    alignItems: 'center', marginTop: 10, minHeight: 52, justifyContent: 'center',
  },
  handsFreeOn: { backgroundColor: '#0F766E' },
  handsFreeText: { color: '#FFFFFF', fontSize: 17, fontWeight: 'bold' },
  answerBox: { backgroundColor: '#1E293B', borderRadius: 12, padding: 12, marginTop: 10 },
  question: { color: '#94A3B8', fontSize: 14, marginBottom: 6 },
  answer: { color: '#FFFFFF', fontSize: 17, lineHeight: 25 },
  details: { color: '#94A3B8', fontSize: 12, marginTop: 6 },
  repeat: {
    backgroundColor: '#94A3B8', padding: 12, borderRadius: 12,
    alignItems: 'center', marginTop: 8,
  },
  repeatText: { color: '#101827', fontSize: 16, fontWeight: 'bold' },
});
