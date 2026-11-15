
import * as Speech from 'expo-speech';
import VoiceRecorder from './Components/VoiceRecorder';
import { useRef, useState } from 'react';
import { CameraView, useCameraPermissions } from 'expo-camera';
import { StatusBar } from 'expo-status-bar';
import {
  Alert,
  Image,
  Pressable,
  StyleSheet,
  Text,
  View,
} from 'react-native';

import { analyzePhoto } from './Services/mockAi';

export default function App() {
  const [permission, requestPermission] = useCameraPermissions();
  const cameraRef = useRef<CameraView>(null);

  const [cameraOpen, setCameraOpen] = useState(false);
  const [cameraReady, setCameraReady] = useState(false);
  const [photoUri, setPhotoUri] = useState<string | null>(null);
  const [capturing, setCapturing] = useState(false);
  const [analyzing, setAnalyzing] = useState(false);
  const [aiAnswer, setAiAnswer] = useState<string | null>(null);

  // Capture an image using the phone camera
  const takePhoto = async () => {
    if (!cameraRef.current || !cameraReady || capturing) {
      return;
    }

    try {
      setCapturing(true);

      const photo = await cameraRef.current.takePictureAsync({
        quality: 0.7,
      });

      if (photo?.uri) {
        setPhotoUri(photo.uri);
        setAiAnswer(null);
      }
    } catch {
      Alert.alert('Camera error', 'Could not capture the photo.');
    } finally {
      setCapturing(false);
    }
  };

  // Get a simulated AI response
  const handleAnalyze = async () => {
    if (!photoUri || analyzing) return;

    try {
      setAnalyzing(true);
      setAiAnswer(null);

      const answer = await analyzePhoto(photoUri);

      setAiAnswer(answer);
    } catch {
      Alert.alert('Error', 'Could not get the demo response.');
    } finally {
      setAnalyzing(false);
    }
  };

  // Read the AI response aloud
  const speakAnswer = async () => {
    if (!aiAnswer) return;

    try {
      await Speech.stop();

      Speech.speak(aiAnswer, {
        language: 'en-US',
        rate: 0.9,
        pitch: 1.0,
      });
    } catch {
      Alert.alert('Speech error', 'Could not speak the response.');
    }
  };

  // Return to the camera to capture another photo
  const retakePhoto = () => {
    if (analyzing) return;

    void Speech.stop();
    setPhotoUri(null);
    setAiAnswer(null);
    setCameraReady(false);
  };

  // Close the camera and reset the screen
  const closeCamera = () => {
    if (analyzing) return;

    void Speech.stop();
    setCameraOpen(false);
    setCameraReady(false);
    setPhotoUri(null);
    setAiAnswer(null);
  };

  if (!permission) {
    return (
      <View style={styles.container}>
        <Text style={styles.text}>
          Checking camera permissions...
        </Text>
      </View>
    );
  }

  if (!permission.granted) {
    return (
      <View style={styles.container}>
        <Text style={styles.title}>World Voice AI</Text>

        <Text style={styles.text}>
          Camera permission is required.
        </Text>

        <Pressable
          style={styles.button}
          onPress={requestPermission}
          accessibilityRole="button"
        >
          <Text style={styles.buttonText}>Grant Permission</Text>
        </Pressable>
      </View>
    );
  }

  return (
    <View style={styles.container}>
      <Text style={styles.title}>World Voice AI</Text>

      {!cameraOpen ? (
        <View style={styles.center}>
          <Text style={styles.text}>
            Your intelligent visual assistant
          </Text>

          <Pressable
            style={styles.button}
            onPress={() => {
              setCameraReady(false);
              setCameraOpen(true);
            }}
            accessibilityRole="button"
          >
            <Text style={styles.buttonText}>Open Camera</Text>
          </Pressable>
          <VoiceRecorder />
        </View>
      ) : photoUri ? (
        <>
          <Image
            source={{ uri: photoUri }}
            style={styles.camera}
            resizeMode="contain"
          />

          <Pressable
            style={styles.button}
            onPress={handleAnalyze}
            disabled={analyzing}
            accessibilityRole="button"
            accessibilityLabel="Analyze captured photo demo"
          >
            <Text style={styles.buttonText}>
              {analyzing ? 'Analyzing...' : 'Analyze Photo (Demo)'}
            </Text>
          </Pressable>

          {aiAnswer && (
            <View style={styles.answerBox}>
              <Text style={styles.answerTitle}>
                Simulated AI Response
              </Text>

              <Text style={styles.answerText}>
                {aiAnswer}
              </Text>

              <Pressable
                style={styles.button}
                onPress={speakAnswer}
                accessibilityRole="button"
                accessibilityLabel="Speak AI response aloud"
              >
                <Text style={styles.buttonText}>
                  🔊 Speak Answer
                </Text>
              </Pressable>
            </View>
          )}

          <Pressable
            style={styles.buttonSecondary}
            onPress={retakePhoto}
            disabled={analyzing}
            accessibilityRole="button"
          >
            <Text style={styles.buttonText}>Retake Photo</Text>
          </Pressable>

          <Pressable
            style={styles.buttonSecondary}
            onPress={closeCamera}
            disabled={analyzing}
            accessibilityRole="button"
          >
            <Text style={styles.buttonText}>Close Camera</Text>
          </Pressable>
        </>
      ) : (
        <>
          <CameraView
            ref={cameraRef}
            style={styles.camera}
            facing="back"
            onCameraReady={() => setCameraReady(true)}
            onMountError={() =>
              Alert.alert('Camera error', 'Camera could not start.')
            }
          />

          <Pressable
            style={styles.button}
            onPress={takePhoto}
            disabled={!cameraReady || capturing}
            accessibilityRole="button"
          >
            <Text style={styles.buttonText}>
              {capturing ? 'Capturing...' : 'Capture Photo'}
            </Text>
          </Pressable>

          <Pressable
            style={styles.buttonSecondary}
            onPress={closeCamera}
            accessibilityRole="button"
          >
            <Text style={styles.buttonText}>Close Camera</Text>
          </Pressable>
        </>
      )}

      <Text style={styles.note}>
        Demo mode - no photos are uploaded.
      </Text>

      <StatusBar style="light" />
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#101827',
    padding: 20,
    paddingTop: 60,
  },
  title: {
    color: '#FFFFFF',
    fontSize: 30,
    fontWeight: 'bold',
    textAlign: 'center',
    marginBottom: 20,
  },
  center: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
  },
  camera: {
    flex: 1,
    minHeight: 120,
    borderRadius: 12,
    overflow: 'hidden',
  },
  button: {
    backgroundColor: '#5DD6C0',
    padding: 16,
    borderRadius: 12,
    marginTop: 15,
    alignItems: 'center',
  },
  buttonSecondary: {
    backgroundColor: '#94A3B8',
    padding: 14,
    borderRadius: 12,
    marginTop: 10,
    alignItems: 'center',
  },
  buttonText: {
    color: '#101827',
    fontSize: 17,
    fontWeight: 'bold',
  },
  text: {
    color: '#FFFFFF',
    textAlign: 'center',
    fontSize: 18,
    marginVertical: 12,
  },
  answerBox: {
    backgroundColor: '#1E293B',
    borderRadius: 12,
    padding: 15,
    marginTop: 15,
  },
  answerTitle: {
    color: '#5DD6C0',
    fontSize: 17,
    fontWeight: 'bold',
    marginBottom: 8,
  },
  answerText: {
    color: '#FFFFFF',
    fontSize: 16,
  },
  note: {
    color: '#D1D5DB',
    textAlign: 'center',
    marginTop: 12,
    fontSize: 13,
  },
});
