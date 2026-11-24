
import { useEffect, useRef, useState } from 'react';

import {
  AccessibilityInfo,
  Alert,
  Pressable,
  StyleSheet,
  Text,
  View,
} from 'react-native';

import * as Speech from 'expo-speech';

import {
  AudioModule,
  RecordingPresets,
  setAudioModeAsync,
  useAudioRecorder,
  useAudioRecorderState,
  useAudioPlayer,
  useAudioPlayerStatus,
} from 'expo-audio';

import {
  ExpoSpeechRecognitionModule,
  useSpeechRecognitionEvent,
} from 'expo-speech-recognition';

// NEW: Allow the parent component to receive
// the question after speech recognition.
type VoiceRecorderProps = {
  onQuestionRecognized?: (question: string) => void;
};

export default function VoiceRecorder({
  onQuestionRecognized,
}: VoiceRecorderProps) {
  // ========================================
  // AUDIO RECORDING
  // ========================================

  const recorder = useAudioRecorder(
    RecordingPresets.HIGH_QUALITY
  );

  const recorderState = useAudioRecorderState(
    recorder
  );

  const player = useAudioPlayer(null);
  const playerState = useAudioPlayerStatus(player);

  const [recordingUri, setRecordingUri] =
    useState<string | null>(null);

  const [busy, setBusy] = useState(false);

  // ========================================
  // SPEECH-TO-TEXT
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

  // Keep the final result available to the
  // recognition end event without stale state.
  const finalTranscriptRef = useRef('');

  // Only speak automatically when TalkBack
  // was disabled for this recognition session.
  const shouldSpeakResultRef = useRef(false);

  const recognitionFailedRef = useRef(false);

  // ========================================
  // AUTOMATIC SPOKEN FEEDBACK
  // ========================================

  const canSpeakAutomatically = async () => {
    try {
      const screenReaderEnabled =
        await AccessibilityInfo.isScreenReaderEnabled();

      return !screenReaderEnabled;
    } catch {
      return false;
    }
  };

  const speakAndWait = async (message: string) => {
    await new Promise<void>((resolve) => {
      try {
        Speech.speak(message, {
          language: 'en-US',
          rate: 0.9,
          pitch: 1.0,
          onDone: () => resolve(),
          onStopped: () => resolve(),
          onError: () => resolve(),
        });
      } catch (error) {
        console.warn(
          'Voice feedback error:',
          error
        );

        resolve();
      }
    });
  };

  const speakFeedback = async (message: string) => {
    const allowed = await canSpeakAutomatically();

    if (!allowed) {
      return;
    }

    try {
      await Speech.stop();

      Speech.speak(message, {
        language: 'en-US',
        rate: 0.9,
        pitch: 1.0,
      });
    } catch (error) {
      console.warn(
        'Spoken feedback error:',
        error
      );
    }
  };

  // ========================================
  // SPEECH RECOGNITION EVENTS
  // ========================================

  useSpeechRecognitionEvent('start', () => {
    setRecognizing(true);
    setRecognitionError(null);
  });

  useSpeechRecognitionEvent('result', (event) => {
    const recognizedText =
      event.results[0]?.transcript?.trim() ?? '';

    if (!recognizedText) {
      return;
    }

    setTranscript(recognizedText);
    setIsFinalTranscript(event.isFinal);

    if (event.isFinal) {
      finalTranscriptRef.current = recognizedText;

      // NEW:
      // Send the final recognized question
      // to App.tsx through a callback.
      onQuestionRecognized?.(recognizedText);
    }
  });

  useSpeechRecognitionEvent('error', (event) => {
    console.error(
      '[Speech Recognition]',
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

    // Prevent duplicate announcements.
    shouldSpeakResultRef.current = false;

    if (!shouldSpeak || recognitionFailedRef.current) {
      return;
    }

    const recognizedQuestion =
      finalTranscriptRef.current.trim();

    if (recognizedQuestion) {
      void speakFeedback(
        `Question recognized. I heard: ${recognizedQuestion}`
      );
    } else {
      void speakFeedback(
        "I couldn't recognize your question. Please try again."
      );
    }
  });

  // Cleanup when leaving the component.
  useEffect(() => {
    return () => {
      ExpoSpeechRecognitionModule.abort();

      void Speech.stop();
    };
  }, []);

  // ========================================
  // START AUDIO RECORDING
  // ========================================

  const startRecording = async () => {
    if (
      busy ||
      recorderState.isRecording ||
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

      await Speech.stop();

      player.pause();
      setRecordingUri(null);

      await setAudioModeAsync({
        playsInSilentMode: true,
        allowsRecording: true,
      });

      await recorder.prepareToRecordAsync();

      recorder.record();
    } catch (error) {
      console.error(
        'Audio recording error:',
        error
      );

      Alert.alert(
        'Error',
        'Could not start recording.'
      );
    } finally {
      setBusy(false);
    }
  };

  // ========================================
  // STOP AUDIO RECORDING
  // ========================================

  const stopRecording = async () => {
    if (
      busy ||
      !recorderState.isRecording
    ) {
      return;
    }

    setBusy(true);

    try {
      await recorder.stop();

      const uri = recorder.uri;

      if (uri) {
        setRecordingUri(uri);
        player.replace(uri);
      } else {
        Alert.alert(
          'Error',
          'Recording was not saved.'
        );
      }
    } catch (error) {
      console.error(
        'Stop recording error:',
        error
      );

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
  // PLAY AUDIO RECORDING
  // ========================================

  const playRecording = async () => {
    if (
      !recordingUri ||
      !playerState.isLoaded ||
      recognizing ||
      startingRecognition
    ) {
      return;
    }

    try {
      await Speech.stop();

      await player.seekTo(0);
      player.play();
    } catch (error) {
      console.error(
        'Audio playback error:',
        error
      );

      Alert.alert(
        'Error',
        'Could not play the recording.'
      );
    }
  };

  // ========================================
  // START SPEECH-TO-TEXT
  // ========================================

  const startSpeechRecognition = async () => {
    if (
      busy ||
      recorderState.isRecording ||
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
          'Please allow microphone and speech recognition access.'
        );

        return;
      }

      player.pause();

      await Speech.stop();

      await setAudioModeAsync({
        playsInSilentMode: true,
        allowsRecording: false,
      });

      const allowAutomaticVoice =
        await canSpeakAutomatically();

      shouldSpeakResultRef.current =
        allowAutomaticVoice;

      // Speak BEFORE starting the microphone
      // so it doesn't recognize its own voice.
      if (allowAutomaticVoice) {
        await speakAndWait(
          'Listening now. Please ask your question.'
        );
      }

      ExpoSpeechRecognitionModule.start({
        lang: 'en-US',
        interimResults: true,
        continuous: false,
      });
    } catch (error) {
      console.error(
        'Speech recognition start error:',
        error
      );

      shouldSpeakResultRef.current = false;

      setRecognitionError(
        'Could not start speech recognition.'
      );
    } finally {
      setStartingRecognition(false);
    }
  };

  // ========================================
  // STOP SPEECH-TO-TEXT
  // ========================================

  const stopSpeechRecognition = () => {
    try {
      ExpoSpeechRecognitionModule.stop();
    } catch (error) {
      console.error(
        'Speech recognition stop error:',
        error
      );

      setRecognitionError(
        'Could not stop speech recognition.'
      );
    }
  };

  // ========================================
  // BUTTON AVAILABILITY
  // ========================================

  const audioControlsDisabled =
    busy ||
    recognizing ||
    startingRecognition;

  const speechControlsDisabled =
    busy ||
    recorderState.isRecording ||
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
        Audio recordings stay on this device.
        Nothing is uploaded.
      </Text>

      <View style={styles.divider} />

      <Text style={styles.title}>
        Speak Question
      </Text>

      <Text style={styles.description}>
        Ask a question aloud. Android will
        convert your voice into text.
      </Text>

      <Pressable
        style={[
          styles.button,
          recognizing &&
            styles.stopButton,
          speechControlsDisabled &&
            styles.disabled,
        ]}
        onPress={
          recognizing
            ? stopSpeechRecognition
            : startSpeechRecognition
        }
        disabled={speechControlsDisabled}
        accessibilityRole="button"
        accessibilityLabel={
          recognizing
            ? 'Stop Listening'
            : 'Speak Question'
        }
      >
        <Text style={styles.buttonText}>
          {recognizing
            ? 'Stop Listening'
            : startingRecognition
              ? 'Starting...'
              : 'Speak Question'}
        </Text>
      </Pressable>

      <Text style={styles.status}>
        {recognizing
          ? 'Listening to your question...'
          : startingRecognition
            ? 'Preparing microphone...'
            : transcript
              ? 'Speech recognized'
              : 'Ready to recognize speech'}
      </Text>

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

      {recognitionError && (
        <View style={styles.errorBox}>
          <Text style={styles.errorText}>
            {recognitionError}
          </Text>
        </View>
      )}

      <Text style={styles.note}>
        Spoken feedback works automatically
        when TalkBack is off. No question
        is sent to the AI backend yet.
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
  },

  note: {
    color: '#CBD5E1',
    fontSize: 12,
    marginTop: 14,
    textAlign: 'center',
    lineHeight: 18,
  },
});
