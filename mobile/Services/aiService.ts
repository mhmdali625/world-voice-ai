import { File } from 'expo-file-system';
import { fetch } from 'expo/fetch';

import {
  ANALYZE_TIMEOUT_MS,
  BACKEND_URL,
  BackendError,
  authHeaders,
  readErrorDetail,
} from './config';

import { getDeviceId } from './device';
import { speechJobFromPaths } from './ttsService';
import type { SpeechJobRef } from './ttsService';

// ========================================
// SUPPORTED AI ANSWER LANGUAGES
// ========================================

export type AiAnswerLanguage =
  | 'en'
  | 'ar'
  | 'ar-LB'
  | 'fr'
  | 'und';

// ========================================
// AI REQUEST AND RESPONSE TYPES
// ========================================

export type AiAnalysisRequest = {
  photoUri: string;
  question?: string;
  /** Ask the backend to start natural speech immediately. */
  speak?: boolean;
  /** Language detected by speech recognition (english,
   *  arabic, french, mixed), so Gemini answers in the
   *  language the user actually spoke. */
  questionLanguage?: string | null;
  /** Original spoken question (m4a). Gemini hears it, so
   *  code-switched questions are understood completely. */
  questionAudioUri?: string | null;
};

export type AiAnalysisResponse = {
  answer: string;
  answerLanguage: AiAnswerLanguage;
  source: 'mock' | 'backend';
  saved: boolean;
  /** Seconds per backend stage (read_upload, gemini, blur_save, total). */
  serverTimings: Record<string, number>;
  /** Phone-measured round trip in seconds (upload + server + download). */
  roundTripSeconds: number;
  /** Speech already being generated on the server. */
  speechJob: SpeechJobRef | null;
  /** Verbatim question Gemini heard in the attached audio. */
  questionHeard: string;
};

// ========================================
// VALIDATE ANSWER LANGUAGE
// ========================================

function normalizeAnswerLanguage(
  language: unknown
): AiAnswerLanguage {
  switch (language) {
    case 'en':
    case 'ar':
    case 'ar-LB':
    case 'fr':
    case 'und':
      return language;

    default:
      return 'und';
  }
}

// ========================================
// REQUEST GEMINI IMAGE ANALYSIS
// ========================================

export async function requestAiAnalysis(
  request: AiAnalysisRequest,
  signal?: AbortSignal
): Promise<AiAnalysisResponse> {
  if (!request.photoUri) {
    throw new Error('No photo was provided.');
  }

  const question =
    request.question?.trim() ||
    'Describe what is in front of me.';

  const imageFile = new File(request.photoUri);

  if (!imageFile.exists) {
    throw new Error(
      'The captured photo was not found.'
    );
  }

  const formData = new FormData();

  formData.append('image', imageFile);
  formData.append('question', question);
  formData.append('speak', request.speak ? 'true' : 'false');
  formData.append('question_language', request.questionLanguage ?? '');

  if (request.questionAudioUri) {
    const audioFile = new File(request.questionAudioUri);
    if (audioFile.exists && audioFile.size > 0) {
      formData.append('question_audio', audioFile);
    }
  }

  // Timeout + external cancellation.
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal?.addEventListener('abort', abort);
  const timeoutId = setTimeout(abort, ANALYZE_TIMEOUT_MS);

  const startedAt = Date.now();

  try {
    const response = await fetch(
      `${BACKEND_URL}/analyze`,
      {
        method: 'POST',
        body: formData,
        headers: authHeaders(),
        signal: controller.signal,
      }
    );

    if (!response.ok) {
      throw new BackendError(
        response.status,
        `Backend error ${response.status}: ${await readErrorDetail(response)}`
      );
    }

    const data: unknown = await response.json();

    const roundTripSeconds = (Date.now() - startedAt) / 1000;

    if (
      typeof data !== 'object' ||
      data === null ||
      !('answer' in data) ||
      typeof data.answer !== 'string' ||
      !data.answer.trim()
    ) {
      throw new Error(
        'The backend returned an invalid AI answer.'
      );
    }

    const record = data as Record<string, unknown>;

    const serverTimings =
      typeof record.timings === 'object' && record.timings !== null
        ? (record.timings as Record<string, number>)
        : {};

    console.log(
      '[AI] Round trip',
      roundTripSeconds.toFixed(2),
      's | server',
      JSON.stringify(serverTimings),
      '| upload+network ~',
      (roundTripSeconds - (serverTimings.total ?? 0)).toFixed(2),
      's'
    );

    const speech = record.speech as
      | { id: string; audio_path: string; status_path: string }
      | null
      | undefined;

    return {
      answer: (data.answer as string).trim(),
      answerLanguage: normalizeAnswerLanguage(record.answer_language),
      source: 'backend',
      saved: Boolean(record.saved_image),
      serverTimings,
      roundTripSeconds,
      speechJob:
        speech && typeof speech.id === 'string'
          ? speechJobFromPaths(speech)
          : null,
      questionHeard:
        typeof record.question_heard === 'string'
          ? record.question_heard.trim()
          : '',
    };

  } finally {
    clearTimeout(timeoutId);
    signal?.removeEventListener('abort', abort);
  }
}

// ========================================
// LIVE ASSISTANT: ANSWER FROM THE STREAM + MEMORY
// ========================================

export type LiveAnswer = {
  answer: string;
  answerLanguage: AiAnswerLanguage;
  /** Speech-recognition transcript made on the server. */
  question: string;
  questionLanguage: string | null;
  questionHeard: string;
  /** strong | weak | none: how well the camera evidence supports the answer. */
  evidence: 'strong' | 'weak' | 'none';
  memoryFrames: number;
  serverTimings: Record<string, number>;
  roundTripSeconds: number;
  speechJob: SpeechJobRef | null;
  /** Id of this question on the server (to report the first-word latency). */
  qaId: string | null;
};

export async function requestLiveAnswer(
  request: {
    question: string;
    questionLanguage?: string | null;
    questionAudioUri?: string | null;
  },
  signal?: AbortSignal
): Promise<LiveAnswer> {

  const formData = new FormData();
  formData.append('question', request.question);
  formData.append('question_language', request.questionLanguage ?? '');
  formData.append('speak', 'true');
  formData.append('device_id', getDeviceId());

  if (request.questionAudioUri) {
    const audioFile = new File(request.questionAudioUri);
    if (audioFile.exists && audioFile.size > 0) {
      formData.append('question_audio', audioFile);
    }
  }

  const controller = new AbortController();
  const abort = () => controller.abort();
  signal?.addEventListener('abort', abort);
  const timeoutId = setTimeout(abort, ANALYZE_TIMEOUT_MS);
  const startedAt = Date.now();

  try {
    const response = await fetch(`${BACKEND_URL}/ask`, {
      method: 'POST',
      body: formData,
      headers: authHeaders(),
      signal: controller.signal,
    });

    if (!response.ok) {
      throw new BackendError(
        response.status,
        `Backend error ${response.status}: ${await readErrorDetail(response)}`
      );
    }

    const data = (await response.json()) as Record<string, unknown>;

    if (typeof data.answer !== 'string' || !data.answer.trim()) {
      throw new Error('The backend returned an invalid answer.');
    }

    const speech = data.speech as
      | { id: string; audio_path: string; status_path: string }
      | null
      | undefined;

    return {
      answer: data.answer.trim(),
      answerLanguage: normalizeAnswerLanguage(data.answer_language),
      question:
        typeof data.question_received === 'string' ? data.question_received : '',
      questionLanguage:
        typeof data.question_language === 'string' ? data.question_language : null,
      questionHeard:
        typeof data.question_heard === 'string' ? data.question_heard.trim() : '',
      evidence:
        data.evidence === 'strong' || data.evidence === 'weak' ? data.evidence : 'none',
      memoryFrames: Array.isArray(data.memory_frames) ? data.memory_frames.length : 0,
      serverTimings:
        typeof data.timings === 'object' && data.timings !== null
          ? (data.timings as Record<string, number>)
          : {},
      roundTripSeconds: (Date.now() - startedAt) / 1000,
      speechJob:
        speech && typeof speech.id === 'string' ? speechJobFromPaths(speech) : null,
      qaId: typeof data.qa_id === 'string' ? data.qa_id : null,
    };
  } finally {
    clearTimeout(timeoutId);
    signal?.removeEventListener('abort', abort);
  }
}

/**
 * Reports the real end-to-end latency of a live answer: from the moment the
 * user stopped speaking to the first sound of the answer. Shown on the
 * dashboard; failures are ignored (measurement only).
 */
export async function reportFirstWord(qaId: string, seconds: number): Promise<void> {
  try {
    await fetch(`${BACKEND_URL}/ask/${qaId}/first-word`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ first_word_s: Math.round(seconds * 100) / 100 }),
    });
  } catch {
    // Measurement only.
  }
}

// ========================================
// BACKWARD COMPATIBILITY
// ========================================

export async function analyzePhoto(
  photoUri: string
): Promise<string> {
  const result = await requestAiAnalysis({
    photoUri,
  });

  return result.answer;
}
