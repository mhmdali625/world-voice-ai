import { File } from 'expo-file-system';
import { fetch } from 'expo/fetch';

import {
  BACKEND_URL,
  BackendError,
  TRANSCRIBE_TIMEOUT_MS,
  authHeaders,
  readErrorDetail,
} from './config';

// Whisper Large V3 on Groq, reached through the main
// FastAPI backend (port 8000). The separate port-8002
// service is no longer required.

const MAX_AUDIO_SIZE = 6 * 1024 * 1024;

export type TranscriptionResult = {
  transcript: string;
  /** Whisper's detected language name, e.g. "arabic". */
  detectedLanguage: string | null;
  roundTripSeconds: number;
};

export async function transcribeAudioDetailed(
  audioUri: string
): Promise<TranscriptionResult> {
  if (!audioUri) {
    throw new Error('No audio recording was provided.');
  }

  const audioFile = new File(audioUri);

  if (!audioFile.exists) {
    throw new Error('Audio recording was not found.');
  }

  if (audioFile.size === 0 || audioFile.size > MAX_AUDIO_SIZE) {
    throw new Error('Audio recording is empty or exceeds 6 MB.');
  }

  const formData = new FormData();
  formData.append('audio', audioFile);

  const controller = new AbortController();
  const timeoutId = setTimeout(
    () => controller.abort(),
    TRANSCRIBE_TIMEOUT_MS
  );

  const startedAt = Date.now();

  try {
    const response = await fetch(
      `${BACKEND_URL}/transcribe`,
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
        `Whisper error ${response.status}: ${await readErrorDetail(response)}`
      );
    }

    const result = (await response.json()) as {
      transcript?: unknown;
      detected_language?: unknown;
    };

    if (
      typeof result.transcript !== 'string' ||
      !result.transcript.trim()
    ) {
      throw new BackendError(422, 'Whisper returned no recognized words.');
    }

    const roundTripSeconds = (Date.now() - startedAt) / 1000;

    console.log(
      '[ASR] Round trip',
      roundTripSeconds.toFixed(2),
      's | size',
      Math.round(audioFile.size / 1024),
      'KB | language',
      result.detected_language
    );

    return {
      transcript: result.transcript.trim(),
      detectedLanguage:
        typeof result.detected_language === 'string'
          ? result.detected_language
          : null,
      roundTripSeconds,
    };

  } finally {
    clearTimeout(timeoutId);
  }
}

/** Backward-compatible helper returning only the text. */
export async function transcribeAudio(
  audioUri: string
): Promise<string> {
  return (await transcribeAudioDetailed(audioUri)).transcript;
}
