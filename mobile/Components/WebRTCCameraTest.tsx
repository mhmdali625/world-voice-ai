
import { useEffect, useRef, useState } from 'react';

import {
  mediaDevices,
  MediaStream,
  RTCPeerConnection,
  RTCSessionDescription,
  RTCView,
} from 'react-native-webrtc';

import {
  AccessibilityInfo,
  Pressable,
  StyleSheet,
  Text,
  View,
} from 'react-native';

import { BACKEND_URL, authHeaders } from '../Services/config';
import { getDeviceId } from '../Services/device';
import { announce, playEarcon } from '../Services/feedback';

import LiveAskPanel from './LiveAskPanel';

type Props = {
  onClose: () => void;
  /** Start the camera and streaming as soon as the screen opens. */
  autoStart?: boolean;
  /** Also listen hands-free (no button) once streaming is live. */
  autoHandsFree?: boolean;
};

// The phone streams to the BACKEND (backend/stream_service.py),
// which keeps useful frames for the assistant's memory and
// forwards the video to the dashboard. Signalling is one HTTP
// request (offer -> answer); the old WebSocket relay and its
// hardcoded IP are no longer needed.

// Phone and laptop are on the same LAN: no STUN server needed,
// which makes negotiation fast (measured 0.12 s vs ~10 s).
const PEER_CONFIG = { iceServers: [] };

const ICE_GATHER_TIMEOUT_MS = 2000;
const PUBLISH_TIMEOUT_MS = 15000;

export default function WebRTCCameraTest({
  onClose,
  autoStart = false,
  autoHandsFree = false,
}: Props) {
  const streamRef = useRef<MediaStream | null>(null);
  const peerRef = useRef<RTCPeerConnection | null>(null);

  const mountedRef = useRef(true);
  const cameraRequestRef = useRef(0);

  // True when the user wants streaming to stay connected.
  const shouldConnectRef = useRef(false);

  // Increases with every new publish attempt; stale attempts
  // compare against it and stop.
  const attemptRef = useRef(0);

  // Automatic reconnection state.
  const retryCountRef = useRef(0);

  const retryTimerRef = useRef<
    ReturnType<typeof setTimeout> | null
  >(null);

  const peerTimerRef = useRef<
    ReturnType<typeof setTimeout> | null
  >(null);

  const [streamUrl, setStreamUrl] = useState<string | null>(
    null
  );

  const [starting, setStarting] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [status, setStatus] = useState('Camera stopped');

  // True while the video connection to the backend is up.
  const [live, setLive] = useState(false);

  // Spoken connection status: once when connected, once per drop.
  const announcedLiveRef = useRef(false);

  // ==========================================
  // 1. STATUS
  // ==========================================

  function updateStatus(message: string) {
    console.log('[WebRTC]', message);

    if (mountedRef.current) {
      setStatus(message);
    }
  }

  // ==========================================
  // 2. TIMER CLEANUP
  // ==========================================

  function clearRetryTimer() {
    if (retryTimerRef.current !== null) {
      clearTimeout(retryTimerRef.current);
      retryTimerRef.current = null;
    }
  }

  function clearPeerTimer() {
    if (peerTimerRef.current !== null) {
      clearTimeout(peerTimerRef.current);
      peerTimerRef.current = null;
    }
  }

  // ==========================================
  // 3. CLOSE WEBRTC PEER (camera keeps running)
  // ==========================================

  function closePeer() {
    clearPeerTimer();

    const peer = peerRef.current;
    peerRef.current = null;

    if (peer) {
      peer.close();
    }
  }

  // ==========================================
  // 4. AUTOMATIC RECONNECTION
  // ==========================================

  function scheduleReconnect() {
    if (
      !mountedRef.current ||
      !shouldConnectRef.current ||
      !streamRef.current ||
      retryTimerRef.current !== null
    ) {
      return;
    }

    // Exponential backoff:
    // 1s, 2s, 4s, 8s, then maximum 10s.
    const delay = Math.min(
      1000 * Math.pow(
        2,
        Math.min(retryCountRef.current, 4)
      ),
      10000
    );

    retryCountRef.current += 1;

    updateStatus(
      `Connection lost - retrying in ${delay / 1000}s...`
    );

    retryTimerRef.current = setTimeout(() => {
      retryTimerRef.current = null;
      void publish();
    }, delay);
  }

  function recoverConnection() {
    if (!shouldConnectRef.current) {
      return;
    }

    closePeer();
    scheduleReconnect();
  }

  function watchPeer(
    peer: RTCPeerConnection,
    delay: number
  ) {
    clearPeerTimer();

    peerTimerRef.current = setTimeout(() => {
      peerTimerRef.current = null;

      if (
        shouldConnectRef.current &&
        peerRef.current === peer &&
        peer.connectionState !== 'connected'
      ) {
        updateStatus(
          'Video connection stalled - reconnecting...'
        );

        recoverConnection();
      }
    }, delay);
  }

  // ==========================================
  // 5. STOP EVERYTHING
  // ==========================================

  function stopEverything() {
    cameraRequestRef.current += 1;
    attemptRef.current += 1;

    // Disable automatic retries.
    shouldConnectRef.current = false;

    clearRetryTimer();
    closePeer();

    // Release the native camera.
    streamRef.current?.getTracks().forEach(track => {
      track.stop();
    });

    streamRef.current = null;

    if (mountedRef.current) {
      setStreamUrl(null);
      setStarting(false);
      setConnecting(false);
      setLive(false);

      updateStatus('Camera stopped');
    }
  }

  // ==========================================
  // 6. COMPONENT CLEANUP
  // ==========================================

  useEffect(() => {
    mountedRef.current = true;

    return () => {
      mountedRef.current = false;
      cameraRequestRef.current += 1;
      attemptRef.current += 1;
      shouldConnectRef.current = false;

      clearRetryTimer();
      closePeer();

      streamRef.current?.getTracks().forEach(track => {
        track.stop();
      });

      streamRef.current = null;
    };
  }, []);

  // ==========================================
  // 7. START ANDROID CAMERA
  // ==========================================

  async function startCamera(): Promise<boolean> {
    if (starting || streamRef.current) {
      return Boolean(streamRef.current);
    }

    const requestId = ++cameraRequestRef.current;

    setStarting(true);
    updateStatus('Starting Android camera...');

    try {
      const stream = await mediaDevices.getUserMedia({
        audio: false,
        video: {
          facingMode: 'environment',
          frameRate: 15,
        },
      });

      if (
        !mountedRef.current ||
        requestId !== cameraRequestRef.current
      ) {
        stream.getTracks().forEach(track => {
          track.stop();
        });

        return false;
      }

      streamRef.current = stream;

      setStreamUrl(stream.toURL());

      updateStatus(
        'Camera ready - start streaming'
      );
      return true;
    } catch (error) {
      updateStatus(
        error instanceof Error
          ? `Camera error: ${error.message}`
          : 'Camera could not start'
      );
      AccessibilityInfo.announceForAccessibility('The camera could not start.');
      return false;
    } finally {
      if (
        mountedRef.current &&
        requestId === cameraRequestRef.current
      ) {
        setStarting(false);
      }
    }
  }

  // ==========================================
  // 8. CREATE A WEBRTC PEER CONNECTION
  // ==========================================

  function createPeer(stream: MediaStream) {
    const pc = new RTCPeerConnection(PEER_CONFIG);

    peerRef.current = pc;

    // Send the camera track to the backend.
    stream.getTracks().forEach(track => {
      pc.addTrack(track, stream);
    });

    pc.addEventListener(
      'connectionstatechange',
      () => {
        if (peerRef.current !== pc) {
          return;
        }

        const state = pc.connectionState;

        console.log(
          '[WebRTC] Connection state:',
          state
        );

        if (mountedRef.current) {
          setLive(state === 'connected');
        }

        if (state === 'connected') {
          clearPeerTimer();
          retryCountRef.current = 0;
          if (!announcedLiveRef.current) {
            announcedLiveRef.current = true;
            void playEarcon('success');
            AccessibilityInfo.announceForAccessibility(
              'Assistant connected. You can ask your question.'
            );
          }

          updateStatus(
            'Connected - live video streaming!'
          );
        } else if (state === 'disconnected') {
          updateStatus(
            'Video interrupted - checking connection...'
          );

          // Give a temporary interruption time
          // to recover before restarting.
          watchPeer(pc, 6000);
        } else if (state === 'failed') {
          updateStatus(
            'Video connection failed - reconnecting...'
          );
          if (announcedLiveRef.current) {
            announcedLiveRef.current = false;
            void announce('connection_failed', 'error');
          }

          watchPeer(pc, 2000);
        }
      }
    );

    return pc;
  }

  // ==========================================
  // 9. WAIT FOR ICE GATHERING (non-trickle)
  // ==========================================

  function waitForIceGathering(pc: RTCPeerConnection) {
    if (pc.iceGatheringState === 'complete') {
      return Promise.resolve();
    }

    return new Promise<void>(resolve => {
      const timer = setTimeout(resolve, ICE_GATHER_TIMEOUT_MS);

      pc.addEventListener('icegatheringstatechange', () => {
        if (pc.iceGatheringState === 'complete') {
          clearTimeout(timer);
          resolve();
        }
      });
    });
  }

  // ==========================================
  // 10. PUBLISH THE CAMERA TO THE BACKEND
  // ==========================================

  async function publish() {
    const stream = streamRef.current;

    if (!shouldConnectRef.current || !stream) {
      return;
    }

    const attempt = ++attemptRef.current;

    closePeer();

    const pc = createPeer(stream);

    const controller = new AbortController();
    const timeout = setTimeout(
      () => controller.abort(),
      PUBLISH_TIMEOUT_MS
    );

    try {
      updateStatus('Connecting to the assistant server...');

      const offer = await pc.createOffer({});
      await pc.setLocalDescription(offer);
      await waitForIceGathering(pc);

      const local = pc.localDescription;

      if (
        attempt !== attemptRef.current ||
        peerRef.current !== pc ||
        !shouldConnectRef.current ||
        !local
      ) {
        return;
      }

      const response = await fetch(`${BACKEND_URL}/stream/publish`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...authHeaders(),
        },
        body: JSON.stringify({
          sdp: local.sdp,
          type: local.type,
          device_id: getDeviceId(),
        }),
        signal: controller.signal,
      });

      if (response.status === 401) {
        updateStatus('Access code missing or wrong - check Settings, then reopen the assistant');
        AccessibilityInfo.announceForAccessibility('The laptop needs the access code. Check the settings.');
        shouldConnectRef.current = false;        // retrying cannot help until the code is fixed
        setConnecting(false);
        return;
      }

      if (!response.ok) {
        throw new Error(`Server answered HTTP ${response.status}`);
      }

      const answer: { sdp: string; type: 'answer' } =
        await response.json();

      if (attempt !== attemptRef.current || peerRef.current !== pc) {
        return;
      }

      await pc.setRemoteDescription(
        new RTCSessionDescription(answer)
      );

      if (pc.connectionState !== 'connected') {
        updateStatus('Answer received - connecting video...');
      }

      // Recover automatically if the connection never comes up.
      watchPeer(pc, 15000);
    } catch (error) {
      if (attempt !== attemptRef.current) {
        return;
      }

      console.error('[WebRTC] Publish error:', error);

      updateStatus(
        'Could not reach the assistant server - retrying...'
      );

      recoverConnection();
    } finally {
      clearTimeout(timeout);
    }
  }

  // ==========================================
  // 11. CONNECT BUTTON
  // ==========================================

  function connectToDashboard() {
    if (!streamRef.current) {
      updateStatus('Start the camera first');
      return;
    }

    if (shouldConnectRef.current) {
      return;
    }

    shouldConnectRef.current = true;
    retryCountRef.current = 0;

    setConnecting(true);
    clearRetryTimer();

    void publish();
  }

  // ==========================================
  // 12. HANDS-FREE START (no tap needed)
  // ==========================================

  useEffect(() => {
    if (!autoStart) {
      return;
    }
    void (async () => {
      updateStatus('Starting the assistant...');
      if (await startCamera()) {
        connectToDashboard();
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ==========================================
  // 13. USER INTERFACE
  // ==========================================

  return (
    <View style={styles.container}>
      <Text style={styles.title}>
        Live Assistant
      </Text>

      <Text style={styles.status} accessibilityLiveRegion="polite">
        {live ? 'Camera on - streaming to the assistant' : status}
      </Text>

      {streamUrl ? (
        <RTCView
          streamURL={streamUrl}
          style={styles.preview}
          objectFit="contain"
          mirror={false}
        />
      ) : (
        <View style={styles.placeholder}>
          <Text style={styles.status}>
            Camera not started
          </Text>
        </View>
      )}

      <LiveAskPanel streaming={live} autoHandsFree={autoHandsFree} />

      {!live && (<>
      <Pressable
        style={[
          styles.button,
          (starting || !!streamUrl) && styles.disabled,
        ]}
        onPress={() => { void startCamera(); }}
        disabled={starting || !!streamUrl}
        accessibilityRole="button"
        accessibilityLabel="Start camera"
      >
        <Text style={styles.buttonText}>
          {starting
            ? 'Starting...'
            : 'Start WebRTC Camera'}
        </Text>
      </Pressable>

      <Pressable
        style={[
          styles.button,
          (!streamUrl || connecting) && styles.disabled,
        ]}
        onPress={connectToDashboard}
        disabled={!streamUrl || connecting}
        accessibilityRole="button"
        accessibilityLabel="Start streaming to the assistant"
      >
        <Text style={styles.buttonText}>
          Start Streaming
        </Text>
      </Pressable>
      </>)}

      <Pressable
        style={styles.stopButton}
        onPress={() => {
          stopEverything();
          AccessibilityInfo.announceForAccessibility('Camera and microphone stopped.');
        }}
        accessibilityRole="button"
        accessibilityLabel="Stop camera and microphone"
        accessibilityHint="Stops streaming and listening immediately"
      >
        <Text style={styles.stopText}>
          Stop camera and microphone
        </Text>
      </Pressable>

      <Pressable
        style={styles.secondaryButton}
        onPress={() => {
          stopEverything();
          onClose();
        }}
        accessibilityRole="button"
      >
        <Text style={styles.buttonText}>
          Back to Home
        </Text>
      </Pressable>
    </View>
  );
}

// ==========================================
// 13. STYLES
// ==========================================

const styles = StyleSheet.create({
  container: {
    flex: 1,
    width: '100%',
  },

  title: {
    color: '#FFFFFF',
    fontSize: 24,
    fontWeight: 'bold',
    textAlign: 'center',
    marginBottom: 12,
  },

  status: {
    color: '#FFFFFF',
    fontSize: 15,
    textAlign: 'center',
    marginBottom: 12,
  },

  preview: {
    flex: 1,
    width: '100%',
    backgroundColor: '#000000',
  },

  placeholder: {
    flex: 1,
    backgroundColor: '#1E293B',
    justifyContent: 'center',
    alignItems: 'center',
    borderRadius: 12,
  },

  button: {
    backgroundColor: '#5DD6C0',
    padding: 14,
    borderRadius: 12,
    marginTop: 10,
    alignItems: 'center',
  },

  secondaryButton: {
    backgroundColor: '#94A3B8',
    padding: 12,
    borderRadius: 12,
    marginTop: 8,
    alignItems: 'center',
  },

  disabled: {
    opacity: 0.5,
  },

  stopButton: {
    backgroundColor: '#B91C1C',
    padding: 14,
    borderRadius: 12,
    marginTop: 10,
    alignItems: 'center',
    minHeight: 52,
    justifyContent: 'center',
  },

  stopText: {
    color: '#FFFFFF',
    fontSize: 17,
    fontWeight: 'bold',
  },

  buttonText: {
    color: '#101827',
    fontSize: 16,
    fontWeight: 'bold',
    textAlign: 'center',
  },
});
