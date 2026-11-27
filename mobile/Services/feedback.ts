import {
  createAudioPlayer,
  setAudioModeAsync,
} from 'expo-audio';

import type { AudioPlayer } from 'expo-audio';

import { AccessibilityInfo } from 'react-native';

import phrases from '../assets/voice/phrases.json';
import { VOICE_CLIPS } from './voiceClips.generated';

import type { AiAnswerLanguage } from './aiService';


// ========================================
// WHAT THIS MODULE REPLACES
// ========================================
//
// Expo Speech (robotic Android TTS) was used for:
//   "Listening now...", "Question recognized.",
//   "I could not recognize your question.",
//   "Speech recognition failed..." and as an
//   automatic fallback in the middle of answers.
//
// It is replaced by three layers, all non-robotic:
//
// 1. EARCONS  - short tones bundled in the app. Instant,
//               language independent, no network.
// 2. CLIPS    - pre-generated natural Fola recordings of
//               each announcement (generate_voice_clips.py).
// 3. TALKBACK - when the screen reader is on, messages go
//               to TalkBack via announceForAccessibility,
//               so the user's own screen reader speaks them
//               and our audio never talks over it.


export type Earcon =
  | 'listen_start'
  | 'listen_stop'
  | 'processing'
  | 'success'
  | 'error'
  | 'capture';

export type FeedbackKey =
  | 'listening'
  | 'question_received'
  | 'not_understood'
  | 'photo_taken'
  | 'analyzing'
  | 'analysis_failed'
  | 'connection_failed'
  | 'voice_unavailable'
  | 'ask_question_first';

type PhraseTable = Record<string, Record<string, string>>;

const PHRASES = phrases as PhraseTable;

const EARCON_SOURCES: Record<Earcon, number> = {
  listen_start: require('../assets/sounds/listen_start.wav'),
  listen_stop: require('../assets/sounds/listen_stop.wav'),
  processing: require('../assets/sounds/processing.wav'),
  success: require('../assets/sounds/success.wav'),
  error: require('../assets/sounds/error.wav'),
  capture: require('../assets/sounds/capture.wav'),
};


// ========================================
// SCREEN READER STATE
// ========================================

let screenReaderOn = false;

AccessibilityInfo.isScreenReaderEnabled()
  .then((enabled) => {
    screenReaderOn = enabled;
  })
  .catch(() => {});

AccessibilityInfo.addEventListener(
  'screenReaderChanged',
  (enabled) => {
    screenReaderOn = enabled;
  }
);

export function isScreenReaderOn(): boolean {
  return screenReaderOn;
}


// ========================================
// FEEDBACK LANGUAGE
// ========================================
//
// Announcements follow the user's language: the language
// detected from the last question or answer.

let feedbackLanguage: AiAnswerLanguage = 'ar-LB';

export function setFeedbackLanguage(
  language: AiAnswerLanguage | string | null | undefined
): void {

  const value = (language || '').toLowerCase();

  if (value === 'ar-lb' || value === 'ar' || value === 'arabic') {
    feedbackLanguage = 'ar-LB';
  } else if (value === 'en' || value === 'english') {
    feedbackLanguage = 'en';
  } else if (value === 'fr' || value === 'french') {
    feedbackLanguage = 'fr';
  }
}

export function getFeedbackLanguage(): AiAnswerLanguage {
  return feedbackLanguage;
}

export function getPhrase(key: FeedbackKey): string {
  return (
    PHRASES[feedbackLanguage]?.[key] ??
    PHRASES.en[key] ??
    ''
  );
}


// ========================================
// ONE SHARED PLAYER FOR SHORT SOUNDS
// ========================================
//
// A single reused player: starting a new cue stops the
// previous one, so feedback sounds never overlap.

let cuePlayer: AudioPlayer | null = null;

function playSource(source: number): void {

  try {
    if (!cuePlayer) {
      cuePlayer = createAudioPlayer(source);
    } else {
      cuePlayer.pause();
      cuePlayer.replace(source);
    }

    cuePlayer.volume = 1.0;
    cuePlayer.play();

  } catch (error) {
    console.warn('[FEEDBACK] Cue failed:', error);
  }
}

export function stopFeedback(): void {
  try {
    cuePlayer?.pause();
  } catch {
    // Already released.
  }
}

export function releaseFeedback(): void {
  try {
    cuePlayer?.remove();
  } catch {
    // Already released.
  }
  cuePlayer = null;
}

async function ensurePlaybackMode(): Promise<void> {
  try {
    await setAudioModeAsync({
      playsInSilentMode: true,
      allowsRecording: false,
      interruptionMode: 'duckOthers',
      shouldPlayInBackground: false,
    });
  } catch (error) {
    console.warn('[FEEDBACK] Audio mode:', error);
  }
}


// ========================================
// PUBLIC API
// ========================================

/** Play a short tone. Always safe, even with TalkBack. */
export async function playEarcon(earcon: Earcon): Promise<void> {
  await ensurePlaybackMode();
  playSource(EARCON_SOURCES[earcon]);
}

/**
 * Give spoken feedback for a known event.
 *
 * - TalkBack on : earcon + TalkBack announcement.
 * - TalkBack off: natural Fola clip if generated,
 *                 otherwise the earcon alone (the text
 *                 is also shown on screen by the caller).
 */
export async function announce(
  key: FeedbackKey,
  earcon?: Earcon
): Promise<void> {

  const text = getPhrase(key);

  if (screenReaderOn) {

    if (earcon) {
      await playEarcon(earcon);
    }

    if (text) {
      AccessibilityInfo.announceForAccessibility(text);
    }

    return;
  }

  const clip = VOICE_CLIPS[feedbackLanguage]?.[key];

  if (clip) {
    await ensurePlaybackMode();
    playSource(clip);
    return;
  }

  if (earcon) {
    await playEarcon(earcon);
  }
}
