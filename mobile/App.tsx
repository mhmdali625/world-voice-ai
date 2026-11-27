import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from 'react';

import {
  CameraView,
  useCameraPermissions,
} from 'expo-camera';

import { File } from 'expo-file-system';
import { StatusBar } from 'expo-status-bar';

import {
  AccessibilityInfo,
  Alert,
  Image,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';

import AssistantSettings from './Components/AssistantSettings';
import VoiceRecorder from './Components/VoiceRecorder';
import WebRTCCameraTest from './Components/WebRTCCameraTest';

import { requestAiAnalysis } from './Services/aiService';
import type { AiAnswerLanguage } from './Services/aiService';

import { BackendError } from './Services/config';
import { loadSettings } from './Services/settings';

import {
  announce,
  getPhrase,
  isScreenReaderOn,
  playEarcon,
  releaseFeedback,
  setFeedbackLanguage,
  stopFeedback,
} from './Services/feedback';

import type { FeedbackKey } from './Services/feedback';

import { ContinuousSpeech } from './Services/speechPlayer';
import type { SpeechPhase } from './Services/speechPlayer';

import { createSpeechJob } from './Services/ttsService';
import type { SpeechJobRef } from './Services/ttsService';


// ========================================
// SPEECH ARCHITECTURE (summary)
// ========================================
//
// * Answers are spoken ONLY with the natural Fola voice.
//   Expo Speech (robotic Android TTS) has been removed:
//   there is no automatic switch to another voice.
// * The backend starts generating speech the moment the
//   answer exists (inside /analyze) and serves it as ONE
//   continuous WAV stream; ContinuousSpeech plays it with
//   ONE player, so there are no segment boundaries where
//   audio could cut out (see Services/speechPlayer.ts).
// * The answer starts automatically after analysis.
// * "Repeat" replays the same stream from the backend's
//   cache: instant, continuous, no API request.
// * If natural speech fails, the user hears a short
//   natural clip / earcon (or a TalkBack announcement)
//   explaining that the answer is on screen, and can
//   retry with the same voice.

type CachedSpeech = {
  text: string;
  language: AiAnswerLanguage;
  job: SpeechJobRef;
};


// ========================================
// LANGUAGE HELPERS
// ========================================

const getLanguageName = (
  language: AiAnswerLanguage
): string => {
  switch (language) {
    case 'en': return 'English';
    case 'ar': return 'Arabic';
    case 'ar-LB': return 'Lebanese Arabic';
    case 'fr': return 'French';
    default: return 'Undetermined';
  }
};

// Delete a temporary file (photo or question recording).
const removeTemporaryFile = (uri: string | null) => {
  if (!uri) return;
  try {
    const file = new File(uri);
    if (file.exists) file.delete();
  } catch (error) {
    console.warn('[FILES] Cleanup failed:', error);
  }
};

// A backend answer (quota, provider error) means the
// voice is unavailable; anything else is a network error.
const describeFailure = (
  error: unknown
): FeedbackKey =>
  error instanceof BackendError
    ? 'voice_unavailable'
    : 'connection_failed';


// ========================================
// MAIN APPLICATION
// ========================================

export default function App() {

  const [permission, requestPermission] =
    useCameraPermissions();

  const cameraRef = useRef<CameraView>(null);

  const [cameraOpen, setCameraOpen] =
    useState(false);

  const [cameraReady, setCameraReady] =
    useState(false);

  const [photoUri, setPhotoUri] =
    useState<string | null>(null);

  const [capturing, setCapturing] =
    useState(false);

  // Capture resolution: the largest supported size whose
  // long side is <= 2048 px. Full sensor photos (often
  // 4000 x 3000) made the upload ~1 s on Wi-Fi, while
  // Gemini does not need more than ~2048 px to read text.
  const [pictureSize, setPictureSize] =
    useState<string | undefined>(undefined);

  const [settingsOpen, setSettingsOpen] = useState(false);

  // Hands-free auto-start: the Live Assistant opens by itself.
  const [webRtcTestOpen, setWebRtcTestOpen] =
    useState(() => loadSettings().autoStart);

  const [analyzing, setAnalyzing] =
    useState(false);

  const [aiAnswer, setAiAnswer] =
    useState<string | null>(null);

  const [answerLanguage, setAnswerLanguage] =
    useState<AiAnswerLanguage>('und');

  const [photoSaved, setPhotoSaved] =
    useState(false);

  const [recognizedQuestion, setRecognizedQuestion] =
    useState('');

  // Spoken language reported by speech recognition
  // (english / arabic / french / mixed), sent to Gemini.
  const [questionLanguage, setQuestionLanguage] =
    useState<string | null>(null);

  // The recorded question (m4a), sent to Gemini with the
  // photo. Owned here: replaced by the next question and
  // deleted on exit, so recordings never accumulate.
  const questionAudioRef = useRef<string | null>(null);

  const replaceQuestionAudio = (uri: string | null) => {
    const previous = questionAudioRef.current;
    questionAudioRef.current = uri;
    if (previous && previous !== uri) {
      removeTemporaryFile(previous);
    }
  };

  const [speechState, setSpeechState] =
    useState<SpeechPhase>('idle');

  const [speechVoice, setSpeechVoice] =
    useState<string | null>(null);

  const [errorMessage, setErrorMessage] =
    useState<string | null>(null);

  const [timingInfo, setTimingInfo] =
    useState<string | null>(null);

  const sessionRef =
    useRef<ContinuousSpeech | null>(null);

  const cachedSpeechRef =
    useRef<CachedSpeech | null>(null);

  const analyzeControllerRef =
    useRef<AbortController | null>(null);


  // ======================================
  // TEMPORARY PHOTO CLEANUP
  // ======================================

  const removeTemporaryPhoto = (
    uri: string | null
  ) => {

    if (!uri) return;

    try {
      const file = new File(uri);

      if (file.exists) file.delete();

    } catch (error) {
      console.warn('[CAMERA] Cleanup failed:', error);
    }
  };


  // ======================================
  // SPEECH CACHE (Repeat answer)
  // ======================================

  // Audio lives in the backend's job cache (bounded and
  // expiring), so nothing is stored on the phone.
  const clearCachedSpeech = useCallback(() => {
    cachedSpeechRef.current = null;
  }, []);


  // ======================================
  // STOP SPEECH
  // ======================================

  const stopAnswerSpeech = useCallback(
    (clearCache = true) => {

      const session = sessionRef.current;

      sessionRef.current = null;

      session?.stop();

      stopFeedback();

      if (clearCache) {
        clearCachedSpeech();
        setSpeechVoice(null);
      }

      setSpeechState('idle');
    },
    [clearCachedSpeech]
  );

  // Release everything when the app unmounts.
  useEffect(() => {
    return () => {
      sessionRef.current?.stop();
      sessionRef.current = null;
      analyzeControllerRef.current?.abort();
      cachedSpeechRef.current = null;
      releaseFeedback();
      if (questionAudioRef.current) {
        removeTemporaryFile(questionAudioRef.current);
        questionAudioRef.current = null;
      }
    };
  }, []);


  // ======================================
  // SPEAK AN ANSWER (natural voice only)
  // ======================================

  const speakText = useCallback(
    async (
      text: string,
      language: AiAnswerLanguage,
      existingJob: SpeechJobRef | null = null,
      requestedAt: number = Date.now()
    ) => {

      if (!text.trim()) return;

      // Never two voices at once.
      sessionRef.current?.stop();
      sessionRef.current = null;
      stopFeedback();

      setErrorMessage(null);
      setSpeechState('preparing');

      const cached = cachedSpeechRef.current;

      const isReplay =
        cached !== null &&
        cached.text === text &&
        cached.language === language;

      let job: SpeechJobRef | null =
        isReplay && cached ? cached.job : existingJob;

      setSpeechVoice(
        isReplay
          ? 'Natural voice (Fola) | replay from cache'
          : 'Natural voice (Fola) | generating...'
      );

      const handleError = (error: unknown, heardAnything: boolean) => {

        const key = describeFailure(error);

        setSpeechState('idle');

        setSpeechVoice(
          error instanceof BackendError && error.status === 429
            ? `${error.message} The answer is shown on screen.`
            : 'Natural voice unavailable. The answer is shown on screen.'
        );

        setErrorMessage(getPhrase(key));

        console.warn(
          '[SPEECH] Failed',
          heardAnything ? 'after partial playback' : 'before playback',
          error
        );

        void announce(key, 'error');
      };

      try {
        if (!job) {
          job = await createSpeechJob({ text, language });
        }
      } catch (error) {
        handleError(error, false);
        return;
      }

      const createdJob = job;

      const session = new ContinuousSpeech(
        createdJob,
        {
          onPhase: (phase) => {
            if (sessionRef.current === session) {
              setSpeechState(phase);
            }
          },

          onFinished: (metrics) => {

            if (sessionRef.current !== session) return;

            sessionRef.current = null;

            // Keep the job for "Repeat answer".
            cachedSpeechRef.current = { text, language, job: createdJob };

            setSpeechState('idle');

            setSpeechVoice(
              `Natural voice (Fola) | first sound ${
                metrics.firstSoundAt
                  ? ((metrics.firstSoundAt - metrics.requestedAt) / 1000).toFixed(1)
                  : '?'
              } s | interruptions ${metrics.stalls}`
            );
          },

          onError: (error, heardAnything) => {

            if (sessionRef.current !== session) return;

            sessionRef.current = null;

            // A replay of an expired job: drop the cache so
            // the next tap regenerates the answer.
            if (isReplay) {
              cachedSpeechRef.current = null;
            }

            handleError(error, heardAnything);
          },
        },
        requestedAt
      );

      sessionRef.current = session;

      void session.start();
    },
    []
  );


  // ======================================
  // ANALYZE PHOTO (then speak automatically)
  // ======================================

  const analyzePhoto = async (
    uri: string
  ) => {

    if (analyzing) return;

    stopAnswerSpeech(true);

    const controller = new AbortController();
    analyzeControllerRef.current = controller;

    setAnalyzing(true);
    setAiAnswer(null);
    setAnswerLanguage('und');
    setErrorMessage(null);
    setTimingInfo(null);

    void announce('analyzing', 'processing');

    try {

      const result = await requestAiAnalysis(
        {
          photoUri: uri,
          question: recognizedQuestion.trim(),
          // The server starts speech generation the
          // moment the answer exists (no extra round trip).
          speak: true,
          questionLanguage,
          questionAudioUri: questionAudioRef.current,
        },
        controller.signal
      );

      const answerReceivedAt = Date.now();

      if (controller.signal.aborted) return;

      setAiAnswer(result.answer);
      setAnswerLanguage(result.answerLanguage);

      // Show the question exactly as Gemini heard it (keeps
      // every language of a mixed question).
      if (result.questionHeard) {
        setRecognizedQuestion(result.questionHeard);
      }
      setPhotoSaved(result.saved);
      setFeedbackLanguage(result.answerLanguage);

      const timings = result.serverTimings;

      setTimingInfo(
        `Analysis ${result.roundTripSeconds.toFixed(1)} s ` +
        `(Gemini ${(timings.gemini ?? 0).toFixed(1)} s, ` +
        `blur+save ${(timings.blur_save ?? 0).toFixed(1)} s in parallel, ` +
        `network ${Math.max(0, result.roundTripSeconds - (timings.total ?? 0)).toFixed(1)} s)`
      );

      // Automatic spoken answer: no extra tap needed.
      void speakText(
        result.answer,
        result.answerLanguage,
        result.speechJob,
        answerReceivedAt
      );

    } catch (error) {

      if (controller.signal.aborted) return;

      console.error('[AI] Analysis failed:', error);

      const key: FeedbackKey =
        error instanceof BackendError
          ? 'analysis_failed'
          : 'connection_failed';

      setErrorMessage(
        error instanceof BackendError && error.status === 429
          ? `${getPhrase('analysis_failed')} (Gemini quota reached)`
          : getPhrase(key)
      );

      void announce(key, 'error');

    } finally {

      if (analyzeControllerRef.current === controller) {
        analyzeControllerRef.current = null;
        setAnalyzing(false);
      }
    }
  };


  // ======================================
  // CAPTURE PHOTO (auto-analyze if a question exists)
  // ======================================

  const choosePictureSize = async () => {

    if (pictureSize || !cameraRef.current) return;

    try {
      const sizes =
        await cameraRef.current.getAvailablePictureSizesAsync();

      const MAX_LONG_SIDE = 2048;

      const candidates = sizes
        .map((size) => {
          const [w, h] = size.split('x').map(Number);
          return { size, w, h };
        })
        .filter(({ w, h }) =>
          Number.isFinite(w) &&
          Number.isFinite(h) &&
          Math.max(w, h) <= MAX_LONG_SIDE &&
          Math.min(w, h) >= 720
        )
        // Prefer 4:3 (full sensor field of view), then size.
        .sort((a, b) => {
          const ratioA = Math.abs(Math.max(a.w, a.h) / Math.min(a.w, a.h) - 4 / 3) < 0.05 ? 1 : 0;
          const ratioB = Math.abs(Math.max(b.w, b.h) / Math.min(b.w, b.h) - 4 / 3) < 0.05 ? 1 : 0;
          return ratioB - ratioA || b.w * b.h - a.w * a.h;
        });

      if (candidates.length > 0) {
        console.log('[CAMERA] Picture size:', candidates[0].size, 'of', sizes.length);
        setPictureSize(candidates[0].size);
      }

    } catch (error) {
      // Keep the default size; capture still works.
      console.warn('[CAMERA] Picture sizes unavailable:', error);
    }
  };

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

      const photo =
        await cameraRef.current.takePictureAsync({
          quality: 0.5,
          base64: false,
          shutterSound: false,
        });

      if (photo?.uri) {

        void playEarcon('capture');

        console.log(
          '[CAMERA] Photo',
          `${photo.width}x${photo.height}`,
          Math.round((new File(photo.uri).size ?? 0) / 1024),
          'KB'
        );

        stopAnswerSpeech(true);

        setPhotoUri(photo.uri);
        setAiAnswer(null);
        setAnswerLanguage('und');
        setPhotoSaved(false);

        // Blind-user flow: question -> photo -> answer,
        // without having to find the Analyze button.
        void analyzePhoto(photo.uri);

      } else {

        setErrorMessage('Could not capture the photo.');
        void playEarcon('error');
      }

    } catch (error) {

      console.error('[CAMERA] Capture failed:', error);

      setErrorMessage('Could not capture the photo.');
      void playEarcon('error');

    } finally {

      setCapturing(false);
    }
  };

  const handleAnalyze = () => {

    if (!photoUri || analyzing) return;

    void analyzePhoto(photoUri);
  };


  // ======================================
  // SPEAK / STOP / REPEAT
  // ======================================

  const speakAnswer = () => {

    if (!aiAnswer) return;

    if (sessionRef.current) {

      stopAnswerSpeech(false);
      setSpeechVoice('Speech stopped');

      if (isScreenReaderOn()) {
        AccessibilityInfo.announceForAccessibility('Stopped');
      }

      return;
    }

    speakText(aiAnswer, answerLanguage);
  };

  const getSpeechButtonLabel = () => {

    switch (speechState) {

      case 'preparing':
        return 'Preparing Voice... Tap to Cancel';

      case 'waiting':
        return 'Receiving Audio... Tap to Stop';

      case 'playing':
        return 'Stop Speaking';

      default:
        return cachedSpeechRef.current
          ? 'Repeat Answer'
          : 'Speak Answer';
    }
  };


  // ======================================
  // RETAKE AND CLOSE
  // ======================================

  const cancelAnalysis = () => {
    analyzeControllerRef.current?.abort();
    analyzeControllerRef.current = null;
    setAnalyzing(false);
  };

  const retakePhoto = () => {

    cancelAnalysis();
    stopAnswerSpeech(true);

    removeTemporaryPhoto(photoUri);

    setPhotoUri(null);
    setAiAnswer(null);
    setAnswerLanguage('und');
    setPhotoSaved(false);
    setCameraReady(false);
    setErrorMessage(null);
    setTimingInfo(null);
  };

  const closeCamera = () => {

    if (capturing) return;

    cancelAnalysis();
    stopAnswerSpeech(true);

    removeTemporaryPhoto(photoUri);

    setCameraOpen(false);
    setCameraReady(false);
    setPhotoUri(null);
    setAiAnswer(null);
    setAnswerLanguage('und');
    setPhotoSaved(false);
    setErrorMessage(null);
    setTimingInfo(null);
  };

  const openWebRtcTest = () => {

    if (!cameraOpen) {
      stopAnswerSpeech(true);
      setWebRtcTestOpen(true);
    }
  };


  // ======================================
  // CAMERA PERMISSIONS
  // ======================================

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
          accessibilityLabel="Grant Camera Permission"
        >
          <Text style={styles.buttonText}>
            Grant Camera Permission
          </Text>
        </Pressable>

        <StatusBar style="light" />

      </View>
    );
  }


  // ======================================
  // MAIN INTERFACE
  // ======================================

  return (

    <View style={styles.container}>

      {webRtcTestOpen ? (

        <>

          <Text style={styles.title}>
            World Voice AI
          </Text>

          <WebRTCCameraTest
            onClose={() => setWebRtcTestOpen(false)}
            autoStart={true}
            autoHandsFree={loadSettings().autoStart}
          />

        </>

      ) : !cameraOpen ? (

        <ScrollView
          style={styles.scrollArea}
          contentContainerStyle={styles.homeContent}
          showsVerticalScrollIndicator={true}
          keyboardShouldPersistTaps="handled"
        >

          <Text style={styles.title}>
            World Voice AI
          </Text>

          <Text style={styles.subtitle}>
            Your intelligent visual assistant
          </Text>

          <Pressable
            style={[styles.button, styles.primaryAction]}
            onPress={openWebRtcTest}
            accessibilityRole="button"
            accessibilityLabel="Live Assistant"
            accessibilityHint="Starts the camera and listens to your questions about your surroundings"
          >
            <Text style={[styles.buttonText, styles.primaryActionText]}>
              Live Assistant
            </Text>
            <Text style={styles.primaryActionNote}>
              Camera, questions and warnings, hands-free
            </Text>
          </Pressable>

          <Pressable
            style={styles.buttonSecondary}
            onPress={() => {
              setCameraReady(false);
              setCameraOpen(true);
            }}
            accessibilityRole="button"
            accessibilityLabel="Photo mode"
            accessibilityHint="Take one photo and ask a question about it"
          >
            <Text style={styles.buttonText}>
              Photo mode
            </Text>
          </Pressable>

          <VoiceRecorder
            onQuestionRecognized={(question, language) => {
              setRecognizedQuestion(question);
              setQuestionLanguage(language ?? null);
              // A new question invalidates the previous recording.
              replaceQuestionAudio(null);
            }}
            onQuestionAudio={(uri) => replaceQuestionAudio(uri)}
            onBeforeListening={() =>
              stopAnswerSpeech(false)
            }
          />

          {recognizedQuestion.length > 0 && (

            <View style={styles.questionBox}>

              <Text style={styles.questionTitle}>
                Current Question
              </Text>

              <Text style={styles.questionText}>
                {recognizedQuestion}
              </Text>

            </View>
          )}

          <Pressable
            style={styles.settingsToggle}
            onPress={() => setSettingsOpen(open => !open)}
            accessibilityRole="button"
            accessibilityLabel="Settings"
            accessibilityState={{ expanded: settingsOpen }}
          >
            <Text style={styles.settingsToggleText}>
              {settingsOpen ? 'Hide settings' : 'Settings'}
            </Text>
          </Pressable>

          {settingsOpen && <AssistantSettings />}

          <Text style={styles.note}>
            Ask a question, then open the camera
            and capture a photo. The photo is
            analyzed automatically and the answer
            is spoken in a natural voice, while a
            blurred copy is saved to the backend
            for your dashboard.
          </Text>

        </ScrollView>

      ) : photoUri ? (

        <ScrollView
          style={styles.scrollArea}
          contentContainerStyle={styles.photoContent}
          showsVerticalScrollIndicator={true}
        >

          <Text style={styles.title}>
            World Voice AI
          </Text>

          <Image
            source={{ uri: photoUri }}
            style={styles.photoPreview}
            resizeMode="contain"
            accessible={true}
            accessibilityLabel="Captured camera photo"
          />

          {recognizedQuestion.length > 0 && (

            <View style={styles.questionBox}>

              <Text style={styles.questionTitle}>
                Your Voice Question
              </Text>

              <Text style={styles.questionText}>
                {recognizedQuestion}
              </Text>

            </View>
          )}

          <Pressable
            style={[
              styles.button,
              (analyzing || (photoSaved && aiAnswer !== null)) &&
                styles.disabled,
            ]}
            onPress={handleAnalyze}
            disabled={analyzing || (photoSaved && aiAnswer !== null)}
            accessibilityRole="button"
            accessibilityLabel={
              analyzing
                ? 'Analyzing photo'
                : photoSaved && aiAnswer !== null
                  ? 'Photo analyzed and saved'
                  : 'Analyze and save photo'
            }
            accessibilityState={{
              disabled: analyzing || (photoSaved && aiAnswer !== null),
              busy: analyzing,
            }}
          >
            <Text style={styles.buttonText}>
              {analyzing
                ? 'Analyzing and Saving...'
                : photoSaved && aiAnswer !== null
                  ? 'Analyzed and Saved'
                  : aiAnswer === null && errorMessage
                    ? 'Try Again'
                    : 'Analyze & Save Photo'}
            </Text>
          </Pressable>

          {errorMessage !== null && (

            <View
              style={styles.errorBox}
              accessible={true}
              accessibilityRole="alert"
            >
              <Text style={styles.errorText}>
                {errorMessage}
              </Text>
            </View>
          )}

          {photoSaved && (

            <View
              style={styles.savedBox}
              accessible={true}
            >

              <Text style={styles.savedTitle}>
                Photo Saved
              </Text>

              <Text style={styles.savedText}>
                A blurred copy was saved
                permanently on the FastAPI
                backend and is available in
                your dashboard's Saved
                Blurred Photos section.
              </Text>

            </View>
          )}

          {aiAnswer && (

            <View style={styles.answerBox}>

              <Text style={styles.answerTitle}>
                AI Response
              </Text>

              <View style={styles.languageBox}>

                <Text style={styles.languageLabel}>
                  Answer Language
                </Text>

                <Text style={styles.languageValue}>
                  {getLanguageName(answerLanguage)}
                </Text>

              </View>

              <Text style={styles.answerText}>
                {aiAnswer}
              </Text>

              <Pressable
                style={[
                  styles.button,
                  speechState !== 'idle' &&
                    styles.stopButton,
                ]}
                onPress={speakAnswer}
                accessibilityRole="button"
                accessibilityLabel={
                  speechState === 'preparing'
                    ? 'Cancel voice generation'
                    : speechState === 'playing' ||
                        speechState === 'waiting'
                      ? 'Stop speaking'
                      : cachedSpeechRef.current
                        ? 'Repeat the answer aloud'
                        : 'Speak AI response aloud'
                }
              >
                <Text style={styles.buttonText}>
                  {getSpeechButtonLabel()}
                </Text>
              </Pressable>

              {speechVoice !== null && (

                <View style={styles.voiceInfoBox}>

                  <Text style={styles.voiceInfoTitle}>
                    TTS Voice
                  </Text>

                  <Text
                    selectable={true}
                    style={styles.voiceInfoText}
                  >
                    {speechVoice}
                  </Text>

                  {timingInfo !== null && (
                    <Text
                      selectable={true}
                      style={styles.voiceInfoText}
                    >
                      {timingInfo}
                    </Text>
                  )}

                </View>
              )}

            </View>
          )}

          <Pressable
            style={styles.buttonSecondary}
            onPress={retakePhoto}
            accessibilityRole="button"
            accessibilityLabel={
              analyzing
                ? 'Cancel and retake photo'
                : 'Retake Photo'
            }
          >
            <Text style={styles.buttonText}>
              Retake Photo
            </Text>
          </Pressable>

          <Pressable
            style={styles.buttonSecondary}
            onPress={closeCamera}
            accessibilityRole="button"
            accessibilityLabel="Close Camera"
          >
            <Text style={styles.buttonText}>
              Close Camera
            </Text>
          </Pressable>

          <Text style={styles.note}>
            Photos are sent to FastAPI only
            when you select Analyze &amp; Save
            Photo. The saved dashboard image
            is blurred, not the original.
            Natural speech sends the AI answer
            to Gemini for audio generation.
          </Text>

        </ScrollView>

      ) : (

        <>

          <Text style={styles.title}>
            World Voice AI
          </Text>

          <CameraView
            ref={cameraRef}
            style={styles.camera}
            facing="back"
            pictureSize={pictureSize}
            onCameraReady={() => {
              setCameraReady(true);
              void choosePictureSize();
            }}
            onMountError={() => {
              Alert.alert(
                'Camera error',
                'Camera could not start.'
              );
            }}
          />

          <Pressable
            style={[
              styles.button,
              (!cameraReady || capturing) &&
                styles.disabled,
            ]}
            onPress={takePhoto}
            disabled={!cameraReady || capturing}
            accessibilityRole="button"
            accessibilityLabel="Capture Photo"
          >
            <Text style={styles.buttonText}>
              {capturing
                ? 'Capturing...'
                : 'Capture Photo'}
            </Text>
          </Pressable>

          <Pressable
            style={[
              styles.buttonSecondary,
              capturing && styles.disabled,
            ]}
            onPress={closeCamera}
            disabled={capturing}
            accessibilityRole="button"
            accessibilityLabel="Close Camera"
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


// ========================================
// STYLES
// ========================================

const styles = StyleSheet.create({

  container: {
    flex: 1,
    backgroundColor: '#101827',
    paddingHorizontal: 20,
    paddingTop: 60,
    paddingBottom: 14,
  },

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

  scrollArea: {
    flex: 1,
    width: '100%',
  },

  homeContent: {
    flexGrow: 1,
    paddingTop: 6,
    paddingBottom: 36,
  },

  photoContent: {
    paddingTop: 6,
    paddingBottom: 36,
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
    justifyContent: 'center',
    minHeight: 55,
  },

  stopButton: {
    backgroundColor: '#FBBF24',
  },

  primaryAction: {
    minHeight: 96,
    paddingVertical: 22,
  },

  primaryActionText: {
    fontSize: 24,
  },

  primaryActionNote: {
    color: '#101827',
    fontSize: 14,
    marginTop: 4,
    textAlign: 'center',
  },

  settingsToggle: {
    marginTop: 16,
    paddingVertical: 14,
    paddingHorizontal: 18,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: '#475569',
    alignItems: 'center',
    width: '100%',
    minHeight: 52,
  },

  settingsToggleText: {
    color: '#FFFFFF',
    fontSize: 17,
    fontWeight: 'bold',
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

  questionBox: {
    backgroundColor: '#1E293B',
    borderRadius: 12,
    padding: 15,
    marginTop: 15,
    borderWidth: 1,
    borderColor: '#5DD6C0',
  },

  questionTitle: {
    color: '#5DD6C0',
    fontSize: 17,
    fontWeight: 'bold',
    marginBottom: 8,
  },

  questionText: {
    color: '#FFFFFF',
    fontSize: 16,
    lineHeight: 24,
  },

  savedBox: {
    backgroundColor: '#123C38',
    borderRadius: 12,
    padding: 15,
    marginTop: 15,
    borderWidth: 1,
    borderColor: '#5DD6C0',
  },

  savedTitle: {
    color: '#5DD6C0',
    fontSize: 17,
    fontWeight: 'bold',
    marginBottom: 8,
  },

  savedText: {
    color: '#FFFFFF',
    fontSize: 14,
    lineHeight: 22,
  },

  languageBox: {
    backgroundColor: '#0F172A',
    padding: 12,
    borderRadius: 8,
    marginBottom: 12,
    borderWidth: 1,
    borderColor: '#334155',
  },

  languageLabel: {
    color: '#CBD5E1',
    fontSize: 13,
    marginBottom: 4,
  },

  languageValue: {
    color: '#5DD6C0',
    fontSize: 16,
    fontWeight: 'bold',
  },

  errorBox: {
    backgroundColor: '#451F2A',
    borderRadius: 12,
    padding: 15,
    marginTop: 15,
  },

  errorText: {
    color: '#FCA5A5',
    fontSize: 16,
    lineHeight: 24,
  },

  voiceInfoBox: {
    backgroundColor: '#0F172A',
    borderRadius: 8,
    padding: 12,
    marginTop: 12,
  },

  voiceInfoTitle: {
    color: '#5DD6C0',
    fontSize: 14,
    fontWeight: 'bold',
    marginBottom: 6,
  },

  voiceInfoText: {
    color: '#CBD5E1',
    fontSize: 12,
    lineHeight: 19,
  },

  note: {
    color: '#D1D5DB',
    textAlign: 'center',
    marginTop: 22,
    fontSize: 12,
    lineHeight: 19,
  },

});