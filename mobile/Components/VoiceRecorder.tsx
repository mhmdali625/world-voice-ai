
import { useEffect, useState } from 'react';

import {
  Alert,
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
  useAudioRecorderState,
  useAudioPlayer,
  useAudioPlayerStatus,
} from 'expo-audio';

import {
  ExpoSpeechRecognitionModule,
  useSpeechRecognitionEvent,
} from 'expo-speech-recognition';

export default function VoiceRecorder() {
  // ========================================
  // EXISTING AUDIO RECORDER
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
  // NEW SPEECH-TO-TEXT STATE
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

  // ========================================
  // SPEECH RECOGNITION EVENTS
  // ========================================

  useSpeechRecognitionEvent('start', () => {
    setRecognizing(true);
    setRecognitionError(null);
  });

  useSpeechRecognitionEvent('end', () => {
    setRecognizing(false);
  });

  useSpeechRecognitionEvent('result', event => {
    const text =
      event.results[0]?.transcript ?? '';

    if (text.trim()) {
      setTranscript(text);
      setIsFinalTranscript(event.isFinal);
    }
  });

  useSpeechRecognitionEvent('error', event => {
    console.error(
      '[Speech Recognition]',
      event.error,
      event.message
    );

    setRecognizing(false);

    if (event.error !== 'aborted') {
      setRecognitionError(
        event.message || event.error
      );
    }
  });

  useEffect(() => {
    return () => {
      // Do not leave Android speech recognition
      // listening when this screen closes.
      ExpoSpeechRecognitionModule.abort();
    };
  }, []);

  // ========================================
  // 1. START AUDIO RECORDING
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
  // 2. STOP AUDIO RECORDING
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
  // 3. PLAY RECORDED AUDIO
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
  // 4. START SPEECH-TO-TEXT
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

    try {
      // Request microphone and speech
      // recognition permissions.
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

      // Avoid simultaneous microphone
      // usage with the audio player.
      player.pause();

      await setAudioModeAsync({
        playsInSilentMode: true,
        allowsRecording: false,
      });

      // Start Android speech recognition.
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

      setRecognitionError(
        'Could not start speech recognition.'
      );
    } finally {
      setStartingRecognition(false);
    }
  };

  // ========================================
  // 5. STOP SPEECH-TO-TEXT
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
      {/* EXISTING AUDIO RECORDER */}

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
              (
                !playerState.isLoaded ||
                audioControlsDisabled
              ) &&
                styles.disabled,
            ]}
            onPress={playRecording}
            disabled={
              !playerState.isLoaded ||
              audioControlsDisabled
            }
            accessibilityRole="button"
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

      {/* SPEECH-TO-TEXT SECTION */}

      <View style={styles.divider} />

      <Text style={styles.title}>
        Speak Question
      </Text>

      <Text style={styles.description}>
        Ask a question aloud and see the words
        recognized by Android.
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
            ? 'Stop speech recognition'
            : 'Start speech recognition'
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
            ? 'Starting speech recognition...'
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
        Speech recognition uses the Android
        recognition service. Depending on the
        device, internet access may be required.
        No question is sent to our AI backend yet.
      </Text>
    </View>
  );
}

// ==========================================
// STYLES
// ==========================================

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
