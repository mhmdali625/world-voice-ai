import { File, Paths } from 'expo-file-system';
import { fetch } from 'expo/fetch';

import type {
  AiAnswerLanguage,
} from './aiService';

import {
  BACKEND_URL,
  BackendError,
  SPEECH_IDLE_TIMEOUT_MS,
  authHeaders,
} from './config';


// ========================================
// BACKEND CONFIGURATION
// ========================================

const REQUEST_TIMEOUT = 35000;

const MAX_TEXT_LENGTH = 4000;

// A 6-second WAV segment is ~390 KB as Base64.
const MAX_STREAM_LINE_LENGTH = 1500000;


// ========================================
// REQUEST AND RESPONSE TYPES
// ========================================

export type NaturalSpeechRequest = {

  text: string;

  language: AiAnswerLanguage;
};


export type NaturalSpeechResponse = {

  audioUri: string;

  model: string;

  voice: string;

  language: AiAnswerLanguage;
};


// ========================================
// STREAMING RESPONSE TYPES
// ========================================

export type NaturalSpeechSegment =
  NaturalSpeechResponse & {

    index: number;

    duration: number;
  };


export type NaturalSpeechStreamResult = {

  model: string;

  voice: string;

  language: AiAnswerLanguage;

  segments: number;

  duration: number;

  firstSegmentTime: number;

  totalTime: number;
};


// ========================================
// VALIDATE SPEECH TEXT
// ========================================

function validateSpeechText(
  text: string
): string {

  const normalizedText = text.trim();

  if (!normalizedText) {

    throw new Error(
      'Speech text cannot be empty.'
    );
  }

  if (
    normalizedText.length >
    MAX_TEXT_LENGTH
  ) {

    throw new Error(
      'Speech text is too long.'
    );
  }

  return normalizedText;
}


// ========================================
// VALIDATE WAV AUDIO
// ========================================

function isValidWav(
  bytes: Uint8Array
): boolean {

  if (bytes.length <= 44) {
    return false;
  }

  const riffHeader =
    String.fromCharCode(
      ...bytes.slice(0, 4)
    );

  const waveHeader =
    String.fromCharCode(
      ...bytes.slice(8, 12)
    );

  return (
    riffHeader === 'RIFF' &&
    waveHeader === 'WAVE'
  );
}


// ========================================
// CHECK REQUEST CANCELLATION
// ========================================

function checkCancelled(
  signal: AbortSignal
): void {

  if (signal.aborted) {

    throw new Error(
      'Speech request cancelled.'
    );
  }
}


// ========================================
// CREATE TEMPORARY WAV FILE
// ========================================

async function saveTemporaryAudio(
  bytes: Uint8Array,
  signal: AbortSignal,
  prefix: string
): Promise<string> {

  checkCancelled(signal);

  if (!isValidWav(bytes)) {

    throw new Error(
      'Invalid WAV audio received.'
    );
  }

  const filename =
    `${prefix}_${Date.now()}_` +
    `${Math.random()
      .toString(36)
      .slice(2)}.wav`;

  const audioFile = new File(
    Paths.cache,
    filename
  );

  try {

    audioFile.create();

    await audioFile.write(bytes);

    checkCancelled(signal);

    if (!audioFile.exists) {

      throw new Error(
        'Could not save generated audio.'
      );
    }

    return audioFile.uri;

  } catch (error) {

    removeNaturalSpeechAudio(
      audioFile.uri
    );

    throw error;
  }
}


// ========================================
// DECODE BASE64 AUDIO
// ========================================

function decodeBase64Audio(
  encoded: string
): Uint8Array {

  const binary = atob(encoded);

  const bytes =
    new Uint8Array(binary.length);

  for (
    let index = 0;
    index < binary.length;
    index += 1
  ) {

    bytes[index] =
      binary.charCodeAt(index);
  }

  return bytes;
}


// ========================================
// EXISTING COMPLETE AUDIO REQUEST
// ========================================

export async function requestNaturalSpeech(
  request: NaturalSpeechRequest,
  signal?: AbortSignal
): Promise<NaturalSpeechResponse> {

  const text = validateSpeechText(
    request.text
  );

  const controller =
    new AbortController();

  const abortRequest = () => {

    controller.abort();
  };

  if (signal?.aborted) {

    throw new Error(
      'Speech request cancelled.'
    );
  }

  signal?.addEventListener(
    'abort',
    abortRequest
  );

  const timeoutId = setTimeout(
    abortRequest,
    REQUEST_TIMEOUT
  );

  let audioUri: string | null = null;

  try {

    console.log(
      '[NATURAL TTS] Requesting complete audio'
    );

    console.log(
      '[NATURAL TTS] Language:',
      request.language
    );

    const response = await fetch(
      `${BACKEND_URL}/tts`,
      {

        method: 'POST',

        headers: {
          'Content-Type':
            'application/json',

          Accept: 'audio/wav',

          ...authHeaders(),
        },

        body: JSON.stringify({

          text,

          language: request.language,
        }),

        signal: controller.signal,
      }
    );

    if (!response.ok) {

      throw new Error(
        `Natural TTS failed: HTTP ${response.status}`
      );
    }

    const contentType =
      response.headers.get(
        'content-type'
      ) ?? '';

    if (
      !contentType
        .toLowerCase()
        .includes('audio/wav')
    ) {

      throw new Error(
        'Backend returned an invalid audio format.'
      );
    }

    const audioBuffer =
      await response.arrayBuffer();

    const audioBytes =
      new Uint8Array(audioBuffer);

    audioUri = await saveTemporaryAudio(
      audioBytes,
      controller.signal,
      'world_voice'
    );

    checkCancelled(controller.signal);

    console.log(
      '[NATURAL TTS] Audio received'
    );

    console.log(
      '[NATURAL TTS] Audio size:',
      audioBytes.length
    );

    return {

      audioUri,

      model:
        response.headers.get(
          'X-TTS-Model'
        ) ?? 'unknown',

      voice:
        response.headers.get(
          'X-TTS-Voice'
        ) ?? 'unknown',

      language: request.language,
    };

  } catch (error) {

    removeNaturalSpeechAudio(audioUri);

    throw error;

  } finally {

    clearTimeout(timeoutId);

    signal?.removeEventListener(
      'abort',
      abortRequest
    );
  }
}


// ========================================
// NEW STREAMING AUDIO REQUEST
// ========================================

export async function requestStreamingSpeech(

  request: NaturalSpeechRequest,

  onSegment: (
    segment: NaturalSpeechSegment
  ) => void,

  signal?: AbortSignal

): Promise<NaturalSpeechStreamResult> {

  const text = validateSpeechText(
    request.text
  );

  const controller =
    new AbortController();

  const abortRequest = () => {

    controller.abort();
  };

  if (signal?.aborted) {

    throw new Error(
      'Speech request cancelled.'
    );
  }

  signal?.addEventListener(
    'abort',
    abortRequest
  );

  // Idle timeout: restarted every time data arrives.
  let timedOut = false;

  let timeoutId = setTimeout(() => {
    timedOut = true;
    abortRequest();
  }, SPEECH_IDLE_TIMEOUT_MS);

  const resetIdleTimer = () => {
    clearTimeout(timeoutId);
    timeoutId = setTimeout(() => {
      timedOut = true;
      abortRequest();
    }, SPEECH_IDLE_TIMEOUT_MS);
  };

  const startTime = Date.now();

  let model = 'unknown';

  let voice = 'unknown';

  let receivedSegments = 0;

  let totalDuration = 0;

  let firstSegmentTime = 0;

  let streamCompleted = false;

  let streamStarted = false;

  let buffer = '';

  const decoder = new TextDecoder(
    'utf-8'
  );


  // ======================================
  // PROCESS ONE NDJSON MESSAGE
  // ======================================

  const processMessage = async (
    line: string
  ): Promise<void> => {

    checkCancelled(controller.signal);

    const trimmedLine = line.trim();

    if (!trimmedLine) {
      return;
    }

    if (
      trimmedLine.length >
      MAX_STREAM_LINE_LENGTH
    ) {

      throw new Error(
        'Streaming message is too large.'
      );
    }

    if (streamCompleted) {

      throw new Error(
        'Unexpected message after stream completion.'
      );
    }

    const parsed: unknown =
      JSON.parse(trimmedLine);

    if (
      typeof parsed !== 'object' ||
      parsed === null
    ) {

      throw new Error(
        'Invalid streaming message.'
      );
    }

    const message =
      parsed as Record<string, unknown>;


    // ====================================
    // STREAM START
    // ====================================

    if (message.type === 'start') {

      if (streamStarted) {

        throw new Error(
          'Duplicate stream start.'
        );
      }

      streamStarted = true;

      if (
        typeof message.model === 'string'
      ) {

        model = message.model;
      }

      if (
        typeof message.voice === 'string'
      ) {

        voice = message.voice;
      }

      if (
        message.language !==
        request.language
      ) {

        throw new Error(
          'Unexpected streaming language.'
        );
      }

      console.log(
        '[TTS STREAM] Started'
      );

      console.log(
        '[TTS STREAM] Model:',
        model
      );

      console.log(
        '[TTS STREAM] Voice:',
        voice
      );

      return;
    }


    // ====================================
    // STREAMING ERROR
    // ====================================

    if (message.type === 'error') {

      throw new BackendError(
        typeof message.status === 'number'
          ? message.status
          : 502,
        typeof message.message === 'string'
          ? message.message
          : 'Natural speech streaming failed.'
      );
    }

    if (!streamStarted) {

      throw new Error(
        'Audio stream has not started.'
      );
    }


    // ====================================
    // RECEIVE AUDIO SEGMENT
    // ====================================

    if (message.type === 'audio') {

      const index = message.index;

      const duration = message.duration;

      const data = message.data;

      if (
        typeof index !== 'number' ||
        !Number.isInteger(index) ||
        index !== receivedSegments
      ) {

        throw new Error(
          'Invalid audio segment order.'
        );
      }

      if (
        typeof duration !== 'number' ||
        !Number.isFinite(duration) ||
        duration <= 0
      ) {

        throw new Error(
          'Invalid audio segment duration.'
        );
      }

      if (
        typeof data !== 'string' ||
        !data
      ) {

        throw new Error(
          'Invalid audio segment data.'
        );
      }

      const audioBytes =
        decodeBase64Audio(data);

      const audioUri =
        await saveTemporaryAudio(

          audioBytes,

          controller.signal,

          `world_voice_segment_${index}`
        );

      // The playback controller becomes
      // responsible for deleting this file
      // once onSegment returns successfully.

      try {

        checkCancelled(controller.signal);

        if (receivedSegments === 0) {

          firstSegmentTime =
            (Date.now() - startTime) / 1000;

          console.log(

            '[TTS STREAM] First playable segment:',

            `${firstSegmentTime.toFixed(2)} seconds`
          );
        }

        const segment: NaturalSpeechSegment = {

          audioUri,

          model,

          voice,

          language: request.language,

          index,

          duration,
        };

        // Deliver the segment immediately.
        // Do not wait for all audio.

        onSegment(segment);

      } catch (error) {

        removeNaturalSpeechAudio(audioUri);

        throw error;
      }

      receivedSegments += 1;

      totalDuration += duration;

      console.log(

        '[TTS STREAM] Segment received:',

        index,

        'Duration:',

        duration
      );

      return;
    }


    // ====================================
    // STREAM COMPLETED
    // ====================================

    if (message.type === 'complete') {

      if (
        receivedSegments === 0 ||
        message.segments !== receivedSegments
      ) {

        throw new Error(
          'Incomplete audio stream.'
        );
      }

      streamCompleted = true;

      console.log(
        '[TTS STREAM] Generation completed'
      );

      return;
    }

    throw new Error(
      'Unknown streaming message.'
    );
  };


  // ======================================
  // PROCESS RECEIVED TEXT BUFFER
  // ======================================

  const processBuffer =
    async (): Promise<void> => {

      let newlineIndex =
        buffer.indexOf('\n');

      while (newlineIndex !== -1) {

        const line = buffer.slice(
          0,
          newlineIndex
        );

        buffer = buffer.slice(
          newlineIndex + 1
        );

        await processMessage(line);

        newlineIndex =
          buffer.indexOf('\n');
      }

      if (
        buffer.length >
        MAX_STREAM_LINE_LENGTH
      ) {

        throw new Error(
          'Streaming buffer is too large.'
        );
      }
    };


  // ======================================
  // CONNECT TO STREAMING ENDPOINT
  // ======================================

  try {

    console.log(
      '[TTS STREAM] Requesting audio'
    );

    const response = await fetch(
      `${BACKEND_URL}/tts/stream`,
      {

        method: 'POST',

        headers: {

          'Content-Type':
            'application/json',

          Accept:
            'application/x-ndjson',

          ...authHeaders(),
        },

        body: JSON.stringify({

          text,

          language: request.language,
        }),

        signal: controller.signal,
      }
    );

    if (!response.ok) {

      throw new BackendError(
        response.status,
        `Streaming TTS failed: HTTP ${response.status}`
      );
    }

    const contentType =
      response.headers.get(
        'content-type'
      ) ?? '';

    if (
      !contentType
        .toLowerCase()
        .includes('application/x-ndjson')
    ) {

      throw new Error(
        'Invalid streaming response format.'
      );
    }

    model =
      response.headers.get(
        'X-TTS-Model'
      ) ?? 'unknown';

    voice =
      response.headers.get(
        'X-TTS-Voice'
      ) ?? 'unknown';

    if (!response.body) {

      throw new Error(
        'Audio response has no readable stream.'
      );
    }


    // ====================================
    // READ INCOMING AUDIO PROGRESSIVELY
    // ====================================

    const reader =
      response.body.getReader();

    try {

      while (true) {

        checkCancelled(
          controller.signal
        );

        const {
          done,
          value,
        } = await reader.read();

        if (done) {
          break;
        }

        resetIdleTimer();

        if (value) {

          buffer += decoder.decode(
            value,
            {
              stream: true,
            }
          );

          await processBuffer();
        }
      }

      // Flush any remaining decoded text.

      buffer += decoder.decode();

      await processBuffer();

      if (buffer.trim()) {

        await processMessage(buffer);

        buffer = '';
      }

    } finally {

      reader.releaseLock();
    }


    // ====================================
    // VALIDATE STREAM COMPLETION
    // ====================================

    checkCancelled(controller.signal);

    if (
      !streamCompleted ||
      receivedSegments === 0
    ) {

      throw new Error(
        'Audio stream ended unexpectedly.'
      );
    }

    const totalTime =
      (Date.now() - startTime) / 1000;

    console.log(

      '[TTS STREAM] First segment time:',

      `${firstSegmentTime.toFixed(2)} seconds`
    );

    console.log(

      '[TTS STREAM] Total reception time:',

      `${totalTime.toFixed(2)} seconds`
    );

    console.log(

      '[TTS STREAM] Total segments:',

      receivedSegments
    );

    return {

      model,

      voice,

      language: request.language,

      segments: receivedSegments,

      duration: totalDuration,

      firstSegmentTime,

      totalTime,
    };

  } catch (error) {

    controller.abort();

    console.warn(
      '[TTS STREAM] Failed:',
      error
    );

    if (timedOut) {
      throw new BackendError(
        504,
        'Speech stream stopped receiving audio.'
      );
    }

    throw error;

  } finally {

    clearTimeout(timeoutId);

    signal?.removeEventListener(
      'abort',
      abortRequest
    );
  }
}


// ========================================
// CONTINUOUS SPEECH JOBS (single audio stream)
// ========================================
//
// The backend generates the whole answer as ONE audio
// stream. The phone plays it with ONE player: there are
// no segment boundaries where audio can cut out.

export type SpeechJobRef = {
  id: string;
  audioUrl: string;
  statusUrl: string;
};

export type SpeechJobStatus = {
  done: boolean;
  /** Server says audio can play to the end without stalling. */
  readyToStart: boolean;
  model: string | null;
  error: {
    status: number;
    message: string;
    retry_after_s?: number | null;
  } | null;
  audioSeconds: number;
  generationRate: number | null;
  firstAudioSeconds: number | null;
  streamStartedSeconds: number | null;
  totalGenerationSeconds: number | null;
  provider: string;
};

export function speechJobFromPaths(paths: {
  id: string;
  audio_path: string;
  status_path: string;
}): SpeechJobRef {
  return {
    id: paths.id,
    audioUrl: `${BACKEND_URL}${paths.audio_path}`,
    statusUrl: `${BACKEND_URL}${paths.status_path}`,
  };
}

export async function createSpeechJob(
  request: NaturalSpeechRequest
): Promise<SpeechJobRef> {

  const text = validateSpeechText(request.text);

  const response = await fetch(`${BACKEND_URL}/tts/jobs`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(),
    },
    body: JSON.stringify({ text, language: request.language }),
  });

  if (!response.ok) {
    throw new BackendError(
      response.status,
      `Speech job failed: HTTP ${response.status}`
    );
  }

  return speechJobFromPaths(await response.json());
}

export async function getSpeechJobStatus(
  job: SpeechJobRef
): Promise<SpeechJobStatus | null> {

  const response = await fetch(job.statusUrl, {
    headers: authHeaders(),
  });

  if (response.status === 404) {
    return null;
  }

  if (!response.ok) {
    throw new BackendError(
      response.status,
      `Speech status failed: HTTP ${response.status}`
    );
  }

  const data = await response.json();

  return {
    done: Boolean(data.done),
    readyToStart: Boolean(data.ready_to_start),
    model: typeof data.model === 'string' ? data.model : null,
    error: data.error ?? null,
    audioSeconds: Number(data.audio_seconds ?? 0),
    generationRate: data.generation_rate ?? null,
    firstAudioSeconds: data.first_audio_s ?? null,
    streamStartedSeconds: data.stream_started_s ?? null,
    totalGenerationSeconds: data.total_generation_s ?? null,
    provider: String(data.provider ?? 'unknown'),
  };
}


// ========================================
// REMOVE TEMPORARY SPEECH AUDIO
// ========================================

export function removeNaturalSpeechAudio(
  audioUri: string | null
): void {

  if (!audioUri) {
    return;
  }

  try {

    const audioFile =
      new File(audioUri);

    if (audioFile.exists) {

      audioFile.delete();
    }

  } catch (error) {

    console.warn(

      '[NATURAL TTS] Could not remove audio',

      error
    );
  }
}