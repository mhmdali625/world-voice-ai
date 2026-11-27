
import { useEffect, useRef, useState } from 'react';

import {
  Alert,
  Pressable,
  StyleSheet,
  Text,
  View,
} from 'react-native';

import { File } from 'expo-file-system';

import {
  AudioModule,
  RecordingPresets,
  setAudioModeAsync,
  useAudioRecorder,
  useAudioRecorderState,
  useAudioPlayer,
  useAudioPlayerStatus,
} from 'expo-audio';

import type { RecordingOptions } from 'expo-audio';

import {
  ExpoSpeechRecognitionModule,
  useSpeechRecognitionEvent,
} from 'expo-speech-recognition';

import { transcribeAudioDetailed } from '../Services/speechService';

import { BackendError } from '../Services/config';

import {
  announce,
  playEarcon,
  setFeedbackLanguage,
  stopFeedback,
} from '../Services/feedback';

type VoiceRecorderProps = {
  onQuestionRecognized?: (
    question: string,
    detectedLanguage?: string | null
  ) => void;
  /** Called before the microphone opens, so the app can
   *  stop any answer audio (prevents the microphone from
   *  recording the app's own voice). */
  onBeforeListening?: () => void;
  /** Receives the question recording (m4a). When provided,
   *  the parent owns the file and must delete it; it is sent
   *  to Gemini with the photo so mixed-language questions are
   *  understood from the original audio. */
  onQuestionAudio?: (audioUri: string) => void;
};

// ========================================
// SPEECH RECORDING FORMAT
// ========================================
//
// Whisper converts every input to 16 kHz mono before
// recognising it. Recording 44.1 kHz stereo at 128 kbps
// (HIGH_QUALITY) only made the upload ~3x larger and
// slower without improving accuracy. 16 kHz mono AAC at
// 48 kbps keeps full speech bandwidth (up to 8 kHz).

const SPEECH_RECORDING: RecordingOptions = {
  ...RecordingPresets.HIGH_QUALITY,
  sampleRate: 16000,
  numberOfChannels: 1,
  bitRate: 48000,
};

// Length of the "listening" tone, so it finishes
// before the microphone opens and is not recorded.
const EARCON_SETTLE_MS = 280;

const delay = (ms: number) =>
  new Promise<void>((resolve) => setTimeout(resolve, ms));

export default function VoiceRecorder({
  onQuestionRecognized,
  onBeforeListening,
  onQuestionAudio,
}: VoiceRecorderProps) {
  // ========================================
  // LOCAL AUDIO RECORDER
  // ========================================

  const recorder = useAudioRecorder(
    RecordingPresets.HIGH_QUALITY
  );

  const recorderState = useAudioRecorderState(recorder);

  const player = useAudioPlayer(null);
  const playerState = useAudioPlayerStatus(player);

  const [recordingUri, setRecordingUri] =
    useState<string | null>(null);

  const [busy, setBusy] = useState(false);

  // ========================================
  // WHISPER AUDIO RECORDER
  // ========================================

  const whisperRecorder = useAudioRecorder(
    SPEECH_RECORDING
  );

  const [whisperRecording, setWhisperRecording] =
    useState(false);

  const [whisperStarting, setWhisperStarting] =
    useState(false);

  const [transcribing, setTranscribing] =
    useState(false);

  // ========================================
  // SPEECH RECOGNITION STATE
  // ========================================

  const [recognizing, setRecognizing] =
    useState(false);

  const [startingRecognition, setStartingRecognition] =
    useState(false);

  const [transcript, setTranscript] =
    useState('');

  const [isFinalTranscript, setIsFinalTranscript] =
    useState(false);

  const [recognitionError, setRecognitionError] =
    useState<string | null>(null);

  const finalTranscriptRef = useRef('');
  const shouldSpeakResultRef = useRef(false);
  const recognitionFailedRef = useRef(false);

  // ========================================
  // ACCESSIBLE FEEDBACK
  // ========================================
  //
  // Expo Speech was removed. Feedback now uses earcons,
  // natural Fola clips, and TalkBack announcements when
  // the screen reader is on (see Services/feedback.ts).

  // Plays the "listening" tone, then waits until it has
  // finished so it is not captured by the microphone.
  const signalListening = async () => {
    stopFeedback();
    await playEarcon('listen_start');
    await delay(EARCON_SETTLE_MS);
  };

  // ========================================
  // ANDROID SPEECH RECOGNITION EVENTS
  // ========================================

  useSpeechRecognitionEvent('start', () => {
    setRecognizing(true);
    setRecognitionError(null);
  });

  useSpeechRecognitionEvent('result', (event) => {
    const text =
      event.results[0]?.transcript?.trim() ?? '';

    if (!text) {
      return;
    }

    setTranscript(text);
    setIsFinalTranscript(event.isFinal);

    if (event.isFinal) {
      finalTranscriptRef.current = text;
      onQuestionRecognized?.(text);
    }
  });

  useSpeechRecognitionEvent('error', (event) => {
    console.error(
      '[Android Speech Recognition]',
      event.error,
      event.message
    );

    setRecognizing(false);
    recognitionFailedRef.current = true;

    if (event.error !== 'aborted') {
      setRecognitionError(
        event.message || event.error
      );
    }
  });

  useSpeechRecognitionEvent('end', () => {
    setRecognizing(false);

    const shouldSpeak =
      shouldSpeakResultRef.current;

    shouldSpeakResultRef.current = false;

    if (
      !shouldSpeak ||
      recognitionFailedRef.current
    ) {
      return;
    }

    if (finalTranscriptRef.current.trim()) {
      void announce('question_received', 'success');
    } else {
      void announce('not_understood', 'error');
    }
  });

  useEffect(() => {
    return () => {
      ExpoSpeechRecognitionModule.abort();
      stopFeedback();
    };
  }, []);

  // ========================================
  // START LOCAL AUDIO RECORDING
  // ========================================

  const startRecording = async () => {
    if (
      busy ||
      recorderState.isRecording ||
      whisperRecording ||
      whisperStarting ||
      transcribing ||
      recognizing ||
      startingRecognition
    ) {
      return;
    }

    setBusy(true);

    try {
      const permission =
        await AudioModule.requestRecordingPermissionsAsync();

      if (!permission.granted) {
        Alert.alert(
          'Permission required',
          'Please allow microphone access.'
        );
        return;
      }

      stopFeedback();
      onBeforeListening?.();
      player.pause();
      setRecordingUri(null);

      await setAudioModeAsync({
        playsInSilentMode: true,
        allowsRecording: true,
      });

      await recorder.prepareToRecordAsync();
      recorder.record();
    } catch (error) {
      console.error('Recording error:', error);

      Alert.alert(
        'Error',
        'Could not start recording.'
      );
    } finally {
      setBusy(false);
    }
  };

  // ========================================
  // STOP LOCAL AUDIO RECORDING
  // ========================================

  const stopRecording = async () => {
    if (busy || !recorderState.isRecording) {
      return;
    }

    setBusy(true);

    try {
      await recorder.stop();

      const uri = recorder.uri;

      if (!uri) {
        throw new Error('Recording was not saved.');
      }

      setRecordingUri(uri);
      player.replace(uri);
    } catch (error) {
      console.error('Stop recording error:', error);

      Alert.alert(
        'Error',
        'Could not stop recording.'
      );
    } finally {
      await setAudioModeAsync({
        playsInSilentMode: true,
        allowsRecording: false,
      }).catch(() => {});

      setBusy(false);
    }
  };

  // ========================================
  // PLAY LOCAL RECORDING
  // ========================================

  const playRecording = async () => {
    if (
      !recordingUri ||
      !playerState.isLoaded ||
      busy ||
      recognizing ||
      whisperRecording ||
      whisperStarting ||
      transcribing
    ) {
      return;
    }

    try {
      stopFeedback();
      await player.seekTo(0);
      player.play();
    } catch (error) {
      console.error('Playback error:', error);

      Alert.alert(
        'Error',
        'Could not play the recording.'
      );
    }
  };

  // ========================================
  // WHISPER: START LISTENING
  // ========================================

  const startWhisperRecognition = async () => {
    if (
      busy ||
      recorderState.isRecording ||
      whisperRecording ||
      whisperStarting ||
      transcribing ||
      recognizing ||
      startingRecognition
    ) {
      return;
    }

    setWhisperStarting(true);
    setTranscript('');
    setIsFinalTranscript(false);
    setRecognitionError(null);

    try {
      const permission =
        await AudioModule.requestRecordingPermissionsAsync();

      if (!permission.granted) {
        Alert.alert(
          'Microphone permission',
          'Please allow microphone access.'
        );
        return;
      }

      player.pause();

      // Stop any answer audio so the microphone does
      // not record the app's own voice.
      onBeforeListening?.();

      // A 0.25 s tone replaces the ~2 s robotic
      // "Listening now..." sentence.
      await signalListening();

      await setAudioModeAsync({
        playsInSilentMode: true,
        allowsRecording: true,
      });

      await whisperRecorder.prepareToRecordAsync();
      whisperRecorder.record();

      setWhisperRecording(true);
    } catch (error) {
      console.error(
        'Whisper recording start error:',
        error
      );

      setRecognitionError(
        'Could not start the microphone.'
      );

      await setAudioModeAsync({
        playsInSilentMode: true,
        allowsRecording: false,
      }).catch(() => {});
    } finally {
      setWhisperStarting(false);
    }
  };

  // ========================================
  // WHISPER: STOP AND TRANSCRIBE
  // ========================================

  const stopWhisperRecognition = async () => {
    if (!whisperRecording || transcribing) {
      return;
    }

    setWhisperRecording(false);
    setTranscribing(true);

    let temporaryAudioUri: string | null = null;

    try {
      await whisperRecorder.stop();

      temporaryAudioUri = whisperRecorder.uri;

      await setAudioModeAsync({
        playsInSilentMode: true,
        allowsRecording: false,
      });

      if (!temporaryAudioUri) {
        throw new Error(
          'The microphone produced no recording.'
        );
      }

      void playEarcon('listen_stop');

      // FastAPI forwards this recording
      // to Whisper Large V3 through Groq.
      const result =
        await transcribeAudioDetailed(temporaryAudioUri);

      const recognizedQuestion = result.transcript;

      setTranscript(recognizedQuestion);
      setIsFinalTranscript(true);
      setRecognitionError(null);

      // Feedback and later answers follow the language
      // the user actually spoke.
      setFeedbackLanguage(result.detectedLanguage);

      // Send the question to the main App.
      // Gemini Vision will use this question
      // when analyzing the captured photo.
      onQuestionRecognized?.(
        recognizedQuestion,
        result.detectedLanguage
      );

      // Hand the recording to the parent (it is sent to
      // Gemini with the photo); the parent deletes it later.
      if (onQuestionAudio && temporaryAudioUri) {
        onQuestionAudio(temporaryAudioUri);
        temporaryAudioUri = null;
      }

      void announce('question_received', 'success');
    } catch (error) {
      console.error(
        'Whisper transcription error:',
        error
      );

      setRecognitionError(
        error instanceof Error
          ? error.message
          : 'Whisper could not recognize the question.'
      );

      const notUnderstood =
        error instanceof BackendError &&
        error.status === 422;

      void announce(
        notUnderstood
          ? 'not_understood'
          : 'connection_failed',
        'error'
      );
    } finally {
      // Delete the temporary audio recording.
      if (temporaryAudioUri) {
        try {
          const file = new File(temporaryAudioUri);

          if (file.exists) {
            file.delete();
          }
        } catch (error) {
          console.warn(
            'Could not remove temporary audio:',
            error
          );
        }
      }

      await setAudioModeAsync({
        playsInSilentMode: true,
        allowsRecording: false,
      }).catch(() => {});

      setTranscribing(false);
    }
  };

  // ========================================
  // ANDROID RECOGNITION FALLBACK
  // ========================================

  const startAndroidRecognition = async () => {
    if (
      busy ||
      recorderState.isRecording ||
      whisperRecording ||
      whisperStarting ||
      transcribing ||
      recognizing ||
      startingRecognition
    ) {
      return;
    }

    setStartingRecognition(true);
    setTranscript('');
    setIsFinalTranscript(false);
    setRecognitionError(null);

    finalTranscriptRef.current = '';
    recognitionFailedRef.current = false;
    shouldSpeakResultRef.current = false;

    try {
      const permission =
        await ExpoSpeechRecognitionModule
          .requestPermissionsAsync();

      if (!permission.granted) {
        Alert.alert(
          'Permission required',
          'Please allow speech recognition.'
        );
        return;
      }

      player.pause();
      onBeforeListening?.();

      await setAudioModeAsync({
        playsInSilentMode: true,
        allowsRecording: false,
      });

      shouldSpeakResultRef.current = true;

      await signalListening();

      ExpoSpeechRecognitionModule.start({
        lang: 'en-US',
        interimResults: true,
        continuous: false,
      });
    } catch (error) {
      console.error(
        'Android speech recognition error:',
        error
      );

      setRecognitionError(
        'Could not start Android speech recognition.'
      );
    } finally {
      setStartingRecognition(false);
    }
  };

  const stopAndroidRecognition = () => {
    try {
      ExpoSpeechRecognitionModule.stop();
    } catch (error) {
      console.error(
        'Android recognition stop error:',
        error
      );

      setRecognitionError(
        'Could not stop recognition.'
      );
    }
  };

  // ========================================
  // BUTTON AVAILABILITY
  // ========================================

  const audioControlsDisabled =
    busy ||
    recognizing ||
    startingRecognition ||
    whisperRecording ||
    whisperStarting ||
    transcribing;

  const whisperButtonDisabled =
    busy ||
    recorderState.isRecording ||
    recognizing ||
    startingRecognition ||
    whisperStarting ||
    transcribing;

  const androidButtonDisabled =
    busy ||
    recorderState.isRecording ||
    whisperRecording ||
    whisperStarting ||
    transcribing ||
    startingRecognition;

  // ========================================
  // USER INTERFACE
  // ========================================

  return (
    <View style={styles.container}>
      <Text style={styles.title}>
        Voice Recording
      </Text>

      <Text style={styles.status}>
        {recorderState.isRecording
          ? `Recording... ${Math.round(
              recorderState.durationMillis / 1000
            )}s`
          : recordingUri
            ? 'Audio recorded successfully!'
            : 'Microphone ready'}
      </Text>

      {/* LOCAL RECORDING */}

      <Pressable
        style={[
          styles.button,
          recorderState.isRecording &&
            styles.stopButton,
          audioControlsDisabled &&
            styles.disabled,
        ]}
        onPress={
          recorderState.isRecording
            ? stopRecording
            : startRecording
        }
        disabled={audioControlsDisabled}
        accessibilityRole="button"
        accessibilityLabel={
          recorderState.isRecording
            ? 'Stop Recording'
            : 'Start Recording'
        }
      >
        <Text style={styles.buttonText}>
          {recorderState.isRecording
            ? 'Stop Recording'
            : 'Start Recording'}
        </Text>
      </Pressable>

      {recordingUri &&
        !recorderState.isRecording && (
          <Pressable
            style={[
              styles.playButton,
              (!playerState.isLoaded ||
                audioControlsDisabled) &&
                styles.disabled,
            ]}
            onPress={playRecording}
            disabled={
              !playerState.isLoaded ||
              audioControlsDisabled
            }
            accessibilityRole="button"
            accessibilityLabel="Play Recording"
          >
            <Text style={styles.buttonText}>
              {playerState.playing
                ? 'Playing...'
                : 'Play Recording'}
            </Text>
          </Pressable>
        )}

      <Text style={styles.note}>
        These manual recordings stay on
        your device and are not uploaded.
      </Text>

      <View style={styles.divider} />

      {/* WHISPER SPEECH RECOGNITION */}

      <Text style={styles.title}>
        Speak Question
      </Text>

      <Text style={styles.description}>
        Whisper can recognize English,
        Arabic, French, and mixed speech.
        Press Speak Question, speak,
        then press Stop and Transcribe.
      </Text>

      <Pressable
        style={[
          styles.button,
          whisperRecording &&
            styles.stopButton,
          whisperButtonDisabled &&
            styles.disabled,
        ]}
        onPress={
          whisperRecording
            ? stopWhisperRecognition
            : startWhisperRecognition
        }
        disabled={whisperButtonDisabled}
        accessibilityRole="button"
        accessibilityLabel={
          whisperRecording
            ? 'Stop and transcribe question'
            : 'Speak Question with Whisper'
        }
      >
        <Text style={styles.buttonText}>
          {whisperRecording
            ? 'Stop & Transcribe'
            : whisperStarting
              ? 'Preparing Microphone...'
              : transcribing
                ? 'Whisper is Transcribing...'
                : 'Speak Question (Whisper)'}
        </Text>
      </Pressable>

      <Text style={styles.status}>
        {whisperRecording
          ? 'Listening to your question...'
          : transcribing
            ? 'Sending audio to Groq Whisper...'
            : 'Whisper microphone ready'}
      </Text>

      {/* ANDROID FALLBACK */}

      <Text style={styles.description}>
        If Whisper is unavailable, you can
        still use Android speech recognition
        in English.
      </Text>

      <Pressable
        style={[
          styles.playButton,
          recognizing &&
            styles.stopButton,
          androidButtonDisabled &&
            styles.disabled,
        ]}
        onPress={
          recognizing
            ? stopAndroidRecognition
            : startAndroidRecognition
        }
        disabled={androidButtonDisabled}
        accessibilityRole="button"
        accessibilityLabel={
          recognizing
            ? 'Stop Android Listening'
            : 'Use Android Speech Recognition'
        }
      >
        <Text style={styles.buttonText}>
          {recognizing
            ? 'Stop Android Listening'
            : startingRecognition
              ? 'Starting Android Recognition...'
              : 'Android Recognition (Fallback)'}
        </Text>
      </Pressable>

      {/* RECOGNIZED QUESTION */}

      {transcript.trim().length > 0 && (
        <View style={styles.transcriptBox}>
          <Text style={styles.transcriptTitle}>
            Recognized Question
          </Text>

          <Text style={styles.transcriptText}>
            {transcript}
          </Text>

          <Text style={styles.note}>
            {isFinalTranscript
              ? 'Final transcription'
              : 'Listening for final result...'}
          </Text>
        </View>
      )}

      {/* ERRORS */}

      {recognitionError && (
        <View style={styles.errorBox}>
          <Text style={styles.errorText}>
            {recognitionError}
          </Text>
        </View>
      )}

      <Text style={styles.note}>
        Whisper speech recognition sends
        your temporary voice recording
        through FastAPI to Groq for
        transcription. The app deletes
        the temporary recording afterward.
        With TalkBack on, status messages are
        announced by TalkBack itself.
      </Text>
    </View>
  );
}

// ========================================
// STYLES
// ========================================

const styles = StyleSheet.create({
  container: {
    width: '100%',
    backgroundColor: '#1E293B',
    borderRadius: 12,
    padding: 16,
    marginTop: 24,
  },

  title: {
    color: '#5DD6C0',
    fontSize: 20,
    fontWeight: 'bold',
    textAlign: 'center',
  },

  status: {
    color: '#FFFFFF',
    fontSize: 16,
    textAlign: 'center',
    marginVertical: 15,
  },

  description: {
    color: '#CBD5E1',
    fontSize: 14,
    textAlign: 'center',
    marginTop: 12,
    lineHeight: 21,
  },

  button: {
    backgroundColor: '#5DD6C0',
    padding: 16,
    borderRadius: 12,
    alignItems: 'center',
    marginTop: 10,
    minHeight: 54,
  },

  stopButton: {
    backgroundColor: '#F87171',
  },

  playButton: {
    backgroundColor: '#94A3B8',
    padding: 16,
    borderRadius: 12,
    alignItems: 'center',
    marginTop: 12,
    minHeight: 54,
  },

  disabled: {
    opacity: 0.5,
  },

  buttonText: {
    color: '#101827',
    fontSize: 17,
    fontWeight: 'bold',
    textAlign: 'center',
  },

  divider: {
    height: 1,
    backgroundColor: '#475569',
    marginVertical: 24,
  },

  transcriptBox: {
    backgroundColor: '#0F172A',
    borderRadius: 12,
    padding: 16,
    marginTop: 12,
  },

  transcriptTitle: {
    color: '#5DD6C0',
    fontSize: 16,
    fontWeight: 'bold',
    marginBottom: 8,
  },

  transcriptText: {
    color: '#FFFFFF',
    fontSize: 17,
    lineHeight: 26,
  },

  errorBox: {
    backgroundColor: '#451F2A',
    borderRadius: 10,
    padding: 12,
    marginTop: 12,
  },

  errorText: {
    color: '#FCA5A5',
    fontSize: 14,
    lineHeight: 21,
  },

  note: {
    color: '#CBD5E1',
    fontSize: 12,
    marginTop: 14,
    textAlign: 'center',
    lineHeight: 18,
  },
});
