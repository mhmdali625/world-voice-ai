
import { useRef, useState } from 'react';

import {
  CameraView,
  useCameraPermissions,
} from 'expo-camera';

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
import WebRTCCameraTest from './Components/WebRTCCameraTest';

import { analyzePhoto } from './Services/mockAi';

// Laptop Wi-Fi IP.
// Change this if your laptop IP changes.
const RELAY_URL = 'http://192.168.0.105:3001';

export default function App() {
  const [permission, requestPermission] =
    useCameraPermissions();

  // ========================================
  // CAMERA REFERENCES
  // ========================================

  const cameraRef = useRef<CameraView>(null);

  const photoBase64 =
    useRef<string | null>(null);

  // ========================================
  // CAMERA STATE
  // ========================================

  const [cameraOpen, setCameraOpen] =
    useState(false);

  const [cameraReady, setCameraReady] =
    useState(false);

  const [photoUri, setPhotoUri] =
    useState<string | null>(null);

  const [capturing, setCapturing] =
    useState(false);

  // ========================================
  // WEBRTC STATE
  // ========================================

  const [webRtcTestOpen, setWebRtcTestOpen] =
    useState(false);

  // ========================================
  // AI DEMO STATE
  // ========================================

  const [analyzing, setAnalyzing] =
    useState(false);

  const [aiAnswer, setAiAnswer] =
    useState<string | null>(null);

  // ========================================
  // PHOTO UPLOAD STATE
  // ========================================

  const [uploading, setUploading] =
    useState(false);

  // ========================================
  // 1. CAPTURE PHOTO
  // ========================================

  const takePhoto = async () => {
    if (
      !cameraRef.current ||
      !cameraReady ||
      capturing
    ) {
      return;
    }

    try {
      setCapturing(true);
      photoBase64.current = null;

      const photo =
        await cameraRef.current.takePictureAsync({
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
    } catch (error) {
      console.error(
        'Photo capture error:',
        error
      );

      Alert.alert(
        'Camera error',
        'Could not capture the photo.'
      );
    } finally {
      setCapturing(false);
    }
  };

  // ========================================
  // 2. SIMULATED AI ANALYSIS
  // ========================================

  const handleAnalyze = async () => {
    if (
      !photoUri ||
      analyzing ||
      uploading
    ) {
      return;
    }

    try {
      setAnalyzing(true);
      setAiAnswer(null);

      const answer =
        await analyzePhoto(photoUri);

      setAiAnswer(answer);
    } catch (error) {
      console.error(
        'Analysis error:',
        error
      );

      Alert.alert(
        'Error',
        'Could not get the demo response.'
      );
    } finally {
      setAnalyzing(false);
    }
  };

  // ========================================
  // 3. SEND PHOTO TO DASHBOARD
  // ========================================

  const sendPhotoToDashboard = async () => {
    if (uploading || analyzing) {
      return;
    }

    if (
      !photoUri ||
      !photoBase64.current
    ) {
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
        const message =
          await response.text();

        throw new Error(
          `Server error ${response.status}: ${message}`
        );
      }

      Alert.alert(
        'Success!',
        'Photo sent to your laptop successfully.'
      );
    } catch (error) {
      console.error(
        'Photo upload error:',
        error
      );

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

  // ========================================
  // 4. TEXT-TO-SPEECH
  // ========================================

  const speakAnswer = async () => {
    if (!aiAnswer) {
      return;
    }

    try {
      await Speech.stop();

      Speech.speak(aiAnswer, {
        language: 'en-US',
        rate: 0.9,
        pitch: 1.0,
      });
    } catch (error) {
      console.error(
        'Speech error:',
        error
      );

      Alert.alert(
        'Speech error',
        'Could not speak the response.'
      );
    }
  };

  // ========================================
  // 5. RETAKE PHOTO
  // ========================================

  const retakePhoto = () => {
    if (analyzing || uploading) {
      return;
    }

    void Speech.stop();

    photoBase64.current = null;

    setPhotoUri(null);
    setAiAnswer(null);
    setCameraReady(false);
  };

  // ========================================
  // 6. CLOSE CAMERA
  // ========================================

  const closeCamera = () => {
    if (analyzing || uploading) {
      return;
    }

    void Speech.stop();

    photoBase64.current = null;

    setCameraOpen(false);
    setCameraReady(false);
    setPhotoUri(null);
    setAiAnswer(null);
  };

  // ========================================
  // 7. OPEN WEBRTC CAMERA
  // ========================================

  const openWebRtcTest = () => {
    if (cameraOpen) {
      return;
    }

    setWebRtcTestOpen(true);
  };

  // ========================================
  // 8. CAMERA PERMISSIONS
  // ========================================

  if (!permission) {
    return (
      <View style={styles.container}>
        <Text style={styles.text}>
          Checking camera permissions...
        </Text>

        <StatusBar style="light" />
      </View>
    );
  }

  if (!permission.granted) {
    return (
      <View style={styles.container}>
        <Text style={styles.title}>
          World Voice AI
        </Text>

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

        <StatusBar style="light" />
      </View>
    );
  }

  // ========================================
  // 9. MAIN APPLICATION
  // ========================================

  return (
    <View style={styles.container}>

      {webRtcTestOpen ? (

        // ====================================
        // WEBRTC CAMERA SCREEN
        // ====================================

        <>
          <Text style={styles.title}>
            World Voice AI
          </Text>

          <WebRTCCameraTest
            onClose={() =>
              setWebRtcTestOpen(false)
            }
          />
        </>

      ) : !cameraOpen ? (

        // ====================================
        // HOME SCREEN - NOW SCROLLABLE
        // ====================================

        <ScrollView
          style={styles.scrollArea}
          contentContainerStyle={
            styles.homeContent
          }
          showsVerticalScrollIndicator={true}
          keyboardShouldPersistTaps="handled"
        >

          {/* APPLICATION HEADER */}

          <Text style={styles.title}>
            World Voice AI
          </Text>

          <Text style={styles.subtitle}>
            Your intelligent visual assistant
          </Text>

          {/* OPEN CAMERA */}

          <Pressable
            style={styles.button}
            onPress={() => {
              setCameraReady(false);
              setCameraOpen(true);
            }}
            accessibilityRole="button"
            accessibilityLabel="Open Camera"
          >
            <Text style={styles.buttonText}>
              Open Camera
            </Text>
          </Pressable>

          {/* WEBRTC CAMERA */}

          <Pressable
            style={styles.buttonSecondary}
            onPress={openWebRtcTest}
            accessibilityRole="button"
            accessibilityLabel="Test native WebRTC camera"
          >
            <Text style={styles.buttonText}>
              Test WebRTC Camera
            </Text>
          </Pressable>

          {/* AUDIO RECORDING + SPEECH TO TEXT */}

          <VoiceRecorder />

          {/* FOOTER - INSIDE SCROLLVIEW */}

          <Text style={styles.note}>
            Demo mode - photos are sent over
            local Wi-Fi only when you press
            Send Photo.
          </Text>

        </ScrollView>

      ) : photoUri ? (

        // ====================================
        // CAPTURED PHOTO SCREEN
        // ====================================

        <ScrollView
          style={styles.scrollArea}
          contentContainerStyle={
            styles.photoContent
          }
          showsVerticalScrollIndicator={true}
        >

          <Text style={styles.title}>
            World Voice AI
          </Text>

          <Image
            source={{ uri: photoUri }}
            style={styles.photoPreview}
            resizeMode="contain"
          />

          {/* ANALYZE PHOTO */}

          <Pressable
            style={[
              styles.button,
              (analyzing || uploading) &&
                styles.disabled,
            ]}
            onPress={handleAnalyze}
            disabled={
              analyzing || uploading
            }
            accessibilityRole="button"
          >
            <Text style={styles.buttonText}>
              {analyzing
                ? 'Analyzing...'
                : 'Analyze Photo (Demo)'}
            </Text>
          </Pressable>

          {/* UPLOAD PHOTO */}

          <Pressable
            style={[
              styles.button,
              (uploading || analyzing) &&
                styles.disabled,
            ]}
            onPress={sendPhotoToDashboard}
            disabled={
              uploading || analyzing
            }
            accessibilityRole="button"
            accessibilityLabel={
              'Send captured photo to dashboard'
            }
          >
            <Text style={styles.buttonText}>
              {uploading
                ? 'Sending...'
                : 'Send Photo to Dashboard'}
            </Text>
          </Pressable>

          {/* MOCK AI ANSWER */}

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
                accessibilityLabel={
                  'Speak AI response aloud'
                }
              >
                <Text style={styles.buttonText}>
                  Speak Answer
                </Text>
              </Pressable>

            </View>
          )}

          {/* RETAKE PHOTO */}

          <Pressable
            style={[
              styles.buttonSecondary,
              (analyzing || uploading) &&
                styles.disabled,
            ]}
            onPress={retakePhoto}
            disabled={
              analyzing || uploading
            }
            accessibilityRole="button"
          >
            <Text style={styles.buttonText}>
              Retake Photo
            </Text>
          </Pressable>

          {/* CLOSE CAMERA */}

          <Pressable
            style={[
              styles.buttonSecondary,
              (analyzing || uploading) &&
                styles.disabled,
            ]}
            onPress={closeCamera}
            disabled={
              analyzing || uploading
            }
            accessibilityRole="button"
          >
            <Text style={styles.buttonText}>
              Close Camera
            </Text>
          </Pressable>

          <Text style={styles.note}>
            Photos are uploaded only when
            you press Send Photo to Dashboard.
          </Text>

        </ScrollView>

      ) : (

        // ====================================
        // CAMERA PREVIEW SCREEN
        // ====================================

        <>
          <Text style={styles.title}>
            World Voice AI
          </Text>

          <CameraView
            ref={cameraRef}
            style={styles.camera}
            facing="back"
            onCameraReady={() =>
              setCameraReady(true)
            }
            onMountError={() => {
              Alert.alert(
                'Camera error',
                'Camera could not start.'
              );
            }}
          />

          {/* CAPTURE BUTTON */}

          <Pressable
            style={[
              styles.button,
              (!cameraReady || capturing) &&
                styles.disabled,
            ]}
            onPress={takePhoto}
            disabled={
              !cameraReady || capturing
            }
            accessibilityRole="button"
          >
            <Text style={styles.buttonText}>
              {capturing
                ? 'Capturing...'
                : 'Capture Photo'}
            </Text>
          </Pressable>

          {/* CLOSE CAMERA */}

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

      <StatusBar style="light" />
    </View>
  );
}

// ==========================================
// STYLES
// ==========================================

const styles = StyleSheet.create({

  container: {
    flex: 1,
    backgroundColor: '#101827',
    paddingHorizontal: 20,
    paddingTop: 60,
    paddingBottom: 14,
  },

  // Main application title
  title: {
    color: '#FFFFFF',
    fontSize: 30,
    fontWeight: 'bold',
    textAlign: 'center',
    marginBottom: 12,
  },

  subtitle: {
    color: '#CBD5E1',
    textAlign: 'center',
    fontSize: 17,
    lineHeight: 24,
    marginBottom: 20,
  },

  // Shared scrolling area
  scrollArea: {
    flex: 1,
    width: '100%',
  },

  // Home screen:
  // No vertical centering or fixed height.
  // Everything can scroll naturally.
  homeContent: {
    flexGrow: 1,
    paddingTop: 6,
    paddingBottom: 36,
  },

  photoContent: {
    paddingTop: 6,
    paddingBottom: 36,
  },

  // Camera preview
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

  // Main buttons
  button: {
    backgroundColor: '#5DD6C0',
    padding: 16,
    borderRadius: 12,
    marginTop: 15,
    alignItems: 'center',
    justifyContent: 'center',
    minHeight: 55,
  },

  buttonSecondary: {
    backgroundColor: '#94A3B8',
    padding: 14,
    borderRadius: 12,
    marginTop: 10,
    alignItems: 'center',
    justifyContent: 'center',
    minHeight: 52,
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

  // Captured photo response
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
    lineHeight: 24,
  },

  // Footer now belongs to the scrollable
  // content instead of sitting outside it.
  note: {
    color: '#D1D5DB',
    textAlign: 'center',
    marginTop: 22,
    fontSize: 12,
    lineHeight: 19,
  },
});
