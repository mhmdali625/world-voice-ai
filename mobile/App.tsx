import { useRef, useState } from 'react';
import { CameraView, useCameraPermissions } from 'expo-camera';
import * as Speech from 'expo-speech';
import { StatusBar } from 'expo-status-bar';

import {
  Alert,
  Image,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';

import VoiceRecorder from './Components/VoiceRecorder';
import { analyzePhoto } from './Services/mockAi';

// Temporary local relay server on your laptop
const RELAY_URL = 'http://192.168.0.109:3001';

export default function App() {
  const [permission, requestPermission] = useCameraPermissions();

  // Camera references
  const cameraRef = useRef<CameraView>(null);
  const photoBase64 = useRef<string | null>(null);

  // Camera states
  const [cameraOpen, setCameraOpen] = useState(false);
  const [cameraReady, setCameraReady] = useState(false);
  const [photoUri, setPhotoUri] = useState<string | null>(null);
  const [capturing, setCapturing] = useState(false);

  // AI states
  const [analyzing, setAnalyzing] = useState(false);
  const [aiAnswer, setAiAnswer] = useState<string | null>(null);

  // Upload state
  const [uploading, setUploading] = useState(false);

  // 1. Capture a photo
  const takePhoto = async () => {
    if (!cameraRef.current || !cameraReady || capturing) {
      return;
    }

    try {
      setCapturing(true);
      photoBase64.current = null;

      const photo = await cameraRef.current.takePictureAsync({
        quality: 0.5,
        base64: true,
      });

      if (photo?.uri && photo.base64) {
        photoBase64.current = photo.base64;
        setPhotoUri(photo.uri);
        setAiAnswer(null);
      } else {
        Alert.alert(
          'Camera error',
          'Could not capture the image data.'
        );
      }
    } catch {
      Alert.alert(
        'Camera error',
        'Could not capture the photo.'
      );
    } finally {
      setCapturing(false);
    }
  };

  // 2. Get a simulated AI response
  const handleAnalyze = async () => {
    if (!photoUri || analyzing || uploading) return;

    try {
      setAnalyzing(true);
      setAiAnswer(null);

      const answer = await analyzePhoto(photoUri);

      setAiAnswer(answer);
    } catch {
      Alert.alert(
        'Error',
        'Could not get the demo response.'
      );
    } finally {
      setAnalyzing(false);
    }
  };

  // 3. Send a captured photo to the local relay server
  const sendPhotoToDashboard = async () => {
    if (uploading || analyzing) return;

    if (!photoUri || !photoBase64.current) {
      Alert.alert(
        'Error',
        'Please capture a photo before sending.'
      );
      return;
    }

    try {
      setUploading(true);

      const response = await fetch(
        `${RELAY_URL}/api/photo`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            imageBase64: photoBase64.current,
          }),
        }
      );

      if (!response.ok) {
        const errorMessage = await response.text();

        throw new Error(
          `Server error ${response.status}: ${errorMessage}`
        );
      }

      Alert.alert(
        'Success!',
        'Photo sent to your laptop successfully.'
      );
    } catch (error) {
      Alert.alert(
        'Upload failed',
        error instanceof Error
          ? error.message
          : 'Could not connect to the relay server.'
      );
    } finally {
      setUploading(false);
    }
  };

  // 4. Read the AI response aloud
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
      Alert.alert(
        'Speech error',
        'Could not speak the response.'
      );
    }
  };

  // 5. Return to the camera
  const retakePhoto = () => {
    if (analyzing || uploading) return;

    void Speech.stop();

    photoBase64.current = null;

    setPhotoUri(null);
    setAiAnswer(null);
    setCameraReady(false);
  };

  // 6. Close camera and reset everything
  const closeCamera = () => {
    if (analyzing || uploading) return;

    void Speech.stop();

    photoBase64.current = null;

    setCameraOpen(false);
    setCameraReady(false);
    setPhotoUri(null);
    setAiAnswer(null);
  };

  // 7. Check camera permission
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
          <Text style={styles.buttonText}>
            Grant Camera Permission
          </Text>
        </Pressable>
      </View>
    );
  }

  // 8. Main application interface
  return (
    <View style={styles.container}>
      <Text style={styles.title}>World Voice AI</Text>

      {!cameraOpen ? (
        // HOME SCREEN
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
            <Text style={styles.buttonText}>
              Open Camera
            </Text>
          </Pressable>

          <VoiceRecorder />
        </View>
      ) : photoUri ? (
        // CAPTURED PHOTO SCREEN
        <ScrollView
          style={styles.scrollArea}
          contentContainerStyle={styles.photoContent}
        >
          <Image
            source={{ uri: photoUri }}
            style={styles.photoPreview}
            resizeMode="contain"
          />

          {/* Simulated AI analysis */}
          <Pressable
            style={[
              styles.button,
              (analyzing || uploading) && styles.disabled,
            ]}
            onPress={handleAnalyze}
            disabled={analyzing || uploading}
            accessibilityRole="button"
          >
            <Text style={styles.buttonText}>
              {analyzing
                ? 'Analyzing...'
                : 'Analyze Photo (Demo)'}
            </Text>
          </Pressable>

          {/* Send photo to laptop */}
          <Pressable
            style={[
              styles.button,
              (uploading || analyzing) && styles.disabled,
            ]}
            onPress={sendPhotoToDashboard}
            disabled={uploading || analyzing}
            accessibilityRole="button"
            accessibilityLabel="Send captured photo to dashboard"
          >
            <Text style={styles.buttonText}>
              {uploading
                ? 'Sending...'
                : 'Send Photo to Dashboard'}
            </Text>
          </Pressable>

          {/* Display AI response */}
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

          {/* Retake photo */}
          <Pressable
            style={styles.buttonSecondary}
            onPress={retakePhoto}
            disabled={analyzing || uploading}
            accessibilityRole="button"
          >
            <Text style={styles.buttonText}>
              Retake Photo
            </Text>
          </Pressable>

          {/* Close camera */}
          <Pressable
            style={styles.buttonSecondary}
            onPress={closeCamera}
            disabled={analyzing || uploading}
            accessibilityRole="button"
          >
            <Text style={styles.buttonText}>
              Close Camera
            </Text>
          </Pressable>
        </ScrollView>
      ) : (
        // LIVE CAMERA SCREEN
        <>
          <CameraView
            ref={cameraRef}
            style={styles.camera}
            facing="back"
            onCameraReady={() => setCameraReady(true)}
            onMountError={() =>
              Alert.alert(
                'Camera error',
                'Camera could not start.'
              )
            }
          />

          <Pressable
            style={[
              styles.button,
              (!cameraReady || capturing) && styles.disabled,
            ]}
            onPress={takePhoto}
            disabled={!cameraReady || capturing}
            accessibilityRole="button"
          >
            <Text style={styles.buttonText}>
              {capturing
                ? 'Capturing...'
                : 'Capture Photo'}
            </Text>
          </Pressable>

          <Pressable
            style={styles.buttonSecondary}
            onPress={closeCamera}
            accessibilityRole="button"
          >
            <Text style={styles.buttonText}>
              Close Camera
            </Text>
          </Pressable>
        </>
      )}

      <Text style={styles.note}>
        Demo mode — photos are sent over local Wi-Fi
        only when you press Send Photo.
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

  scrollArea: {
    flex: 1,
  },

  photoContent: {
    paddingBottom: 20,
  },

  camera: {
    flex: 1,
    borderRadius: 12,
    overflow: 'hidden',
  },

  photoPreview: {
    width: '100%',
    height: 260,
    borderRadius: 12,
    backgroundColor: '#0F172A',
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

  disabled: {
    opacity: 0.5,
  },

  buttonText: {
    color: '#101827',
    fontSize: 17,
    fontWeight: 'bold',
    textAlign: 'center',
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
    fontSize: 12,
  },
});
