
import { useState } from 'react';
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

export default function VoiceRecorder() {
  const recorder = useAudioRecorder(RecordingPresets.HIGH_QUALITY);
  const recorderState = useAudioRecorderState(recorder);

  const player = useAudioPlayer(null);
  const playerState = useAudioPlayerStatus(player);

  const [recordingUri, setRecordingUri] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Start recording from the microphone
  const startRecording = async () => {
    if (busy || recorderState.isRecording) return;

    setBusy(true);

    try {
      const permission =
        await AudioModule.requestRecordingPermissionsAsync();

      if (!permission.granted) {
        Alert.alert(
          'Permission required',
          'Please allow microphone access to record a question.'
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
    } catch {
      Alert.alert('Error', 'Could not start recording.');
    } finally {
      setBusy(false);
    }
  };

  // Stop and save the recording temporarily
  const stopRecording = async () => {
    if (busy || !recorderState.isRecording) return;

    setBusy(true);

    try {
      await recorder.stop();

      const uri = recorder.uri;

      if (uri) {
        setRecordingUri(uri);
        player.replace(uri);
      } else {
        Alert.alert('Error', 'Recording was not saved.');
      }
    } catch {
      Alert.alert('Error', 'Could not stop recording.');
    } finally {
      await setAudioModeAsync({
        playsInSilentMode: true,
        allowsRecording: false,
      }).catch(() => {});

      setBusy(false);
    }
  };

  // Play the recorded audio through the speaker
  const playRecording = async () => {
    if (!recordingUri || !playerState.isLoaded) return;

    try {
      await player.seekTo(0);
      player.play();
    } catch {
      Alert.alert('Error', 'Could not play the recording.');
    }
  };

  return (
    <View style={styles.container}>
      <Text style={styles.title}>Voice Question</Text>

      <Text style={styles.status}>
        {recorderState.isRecording
          ? `Recording... ${Math.round(
              recorderState.durationMillis / 1000
            )}s`
          : recordingUri
            ? 'Question recorded successfully!'
            : 'Microphone ready'}
      </Text>

      <Pressable
        style={[
          styles.button,
          recorderState.isRecording && styles.stopButton,
          busy && styles.disabled,
        ]}
        onPress={
          recorderState.isRecording ? stopRecording : startRecording
        }
        disabled={busy}
        accessibilityRole="button"
      >
        <Text style={styles.buttonText}>
          {recorderState.isRecording
            ? '⏹ Stop Recording'
            : '🎤 Start Recording'}
        </Text>
      </Pressable>

      {recordingUri && !recorderState.isRecording && (
        <Pressable
          style={[
            styles.playButton,
            (!playerState.isLoaded || busy) && styles.disabled,
          ]}
          onPress={playRecording}
          disabled={!playerState.isLoaded || busy}
          accessibilityRole="button"
        >
          <Text style={styles.buttonText}>
            {playerState.playing ? '🔊 Playing...' : '▶ Play Recording'}
          </Text>
        </Pressable>
      )}

      <Text style={styles.note}>
        Audio stays on this device. Nothing is uploaded.
      </Text>
    </View>
  );
}

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
  button: {
    backgroundColor: '#5DD6C0',
    padding: 16,
    borderRadius: 12,
    alignItems: 'center',
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
  },
  note: {
    color: '#CBD5E1',
    fontSize: 12,
    marginTop: 14,
    textAlign: 'center',
  },
});
