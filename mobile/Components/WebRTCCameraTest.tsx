
import { useEffect, useRef, useState } from 'react';

import {
  mediaDevices,
  MediaStream,
  RTCIceCandidate,
  RTCPeerConnection,
  RTCSessionDescription,
  RTCView,
} from 'react-native-webrtc';

import {
  Pressable,
  StyleSheet,
  Text,
  View,
} from 'react-native';

type Props = {
  onClose: () => void;
};

type CandidateData = {
  candidate: string;
  sdpMid: string | null;
  sdpMLineIndex: number | null;
};

type SignalMessage = {
  type: string;
  peers?: number;
  sdp?: {
    type: 'answer';
    sdp: string;
  };
  candidate?: CandidateData;
  message?: string;
};

// Current laptop Wi-Fi IP.
// Automatic retries require this address to remain valid.
const SIGNAL_URL = 'ws://192.168.0.107:3001/signal';

export default function WebRTCCameraTest({
  onClose,
}: Props) {
  const streamRef = useRef<MediaStream | null>(null);
  const peerRef = useRef<RTCPeerConnection | null>(null);
  const socketRef = useRef<WebSocket | null>(null);

  const mountedRef = useRef(true);
  const cameraRequestRef = useRef(0);

  // True when the user wants streaming to stay connected.
  const shouldConnectRef = useRef(false);

  const offerStartedRef = useRef(false);
  const offerSentRef = useRef(false);

  const localCandidatesRef = useRef<CandidateData[]>([]);
  const remoteCandidatesRef = useRef<CandidateData[]>([]);

  // Automatic reconnection state.
  const retryCountRef = useRef(0);

  const retryTimerRef = useRef<
    ReturnType<typeof setTimeout> | null
  >(null);

  const openTimerRef = useRef<
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

  function clearOpenTimer() {
    if (openTimerRef.current !== null) {
      clearTimeout(openTimerRef.current);
      openTimerRef.current = null;
    }
  }

  function clearPeerTimer() {
    if (peerTimerRef.current !== null) {
      clearTimeout(peerTimerRef.current);
      peerTimerRef.current = null;
    }
  }

  // ==========================================
  // 3. SEND SIGNALING MESSAGE
  // ==========================================

  function sendSignal(message: object) {
    const socket = socketRef.current;

    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify(message));
    }
  }

  // ==========================================
  // 4. CLOSE WEBRTC PEER
  // ==========================================

  // Important:
  // This does NOT close the signaling socket
  // and does NOT stop the Android camera.
  function closePeer() {
    clearPeerTimer();

    const peer = peerRef.current;
    peerRef.current = null;

    if (peer) {
      peer.close();
    }

    offerStartedRef.current = false;
    offerSentRef.current = false;

    localCandidatesRef.current = [];
    remoteCandidatesRef.current = [];
  }

  // ==========================================
  // 5. CLOSE SIGNALING SOCKET
  // ==========================================

  function closeSocket() {
    clearOpenTimer();

    const socket = socketRef.current;
    socketRef.current = null;

    if (socket) {
      socket.onopen = null;
      socket.onmessage = null;
      socket.onerror = null;
      socket.onclose = null;

      try {
        socket.close();
      } catch (error) {
        console.log(
          '[WebRTC] Socket close:',
          error
        );
      }
    }
  }

  // ==========================================
  // 6. AUTOMATIC WEBSOCKET RECONNECTION
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
      startSignaling();
    }, delay);
  }

  // ==========================================
  // 7. RECOVER A STALLED WEBRTC CONNECTION
  // ==========================================

  function recoverConnection() {
    if (!shouldConnectRef.current) {
      return;
    }

    // Force a new signaling session.
    closeSocket();
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
  // 8. STOP EVERYTHING
  // ==========================================

  function stopEverything() {
    cameraRequestRef.current += 1;

    // Disable automatic retries.
    shouldConnectRef.current = false;

    clearRetryTimer();
    closeSocket();
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

      updateStatus('Camera stopped');
    }
  }

  // ==========================================
  // 9. COMPONENT CLEANUP
  // ==========================================

  useEffect(() => {
    mountedRef.current = true;

    return () => {
      mountedRef.current = false;
      cameraRequestRef.current += 1;
      shouldConnectRef.current = false;

      clearRetryTimer();
      closeSocket();
      closePeer();

      streamRef.current?.getTracks().forEach(track => {
        track.stop();
      });

      streamRef.current = null;
    };
  }, []);

  // ==========================================
  // 10. START ANDROID CAMERA
  // ==========================================

  async function startCamera() {
    if (starting || streamRef.current) {
      return;
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

        return;
      }

      streamRef.current = stream;

      setStreamUrl(stream.toURL());

      updateStatus(
        'Camera ready - connect to dashboard'
      );
    } catch (error) {
      updateStatus(
        error instanceof Error
          ? `Camera error: ${error.message}`
          : 'Camera could not start'
      );
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
  // 11. CREATE A WEBRTC PEER CONNECTION
  // ==========================================

  function createPeer(stream: MediaStream) {
    const pc = new RTCPeerConnection({
      iceServers: [
        {
          urls: 'stun:stun.l.google.com:19302',
        },
      ],
    });

    peerRef.current = pc;

    // Send the camera track to the laptop.
    stream.getTracks().forEach(track => {
      pc.addTrack(track, stream);
    });

    // ========================================
    // LOCAL ICE CANDIDATES
    // ========================================

    pc.addEventListener('icecandidate', event => {
      if (
        peerRef.current !== pc ||
        !event.candidate
      ) {
        return;
      }

      const ice = event.candidate;

      const candidate: CandidateData = {
        candidate: ice.candidate,
        sdpMid: ice.sdpMid ?? null,
        sdpMLineIndex: ice.sdpMLineIndex ?? null,
      };

      // Send candidates only after the SDP offer.
      if (!offerSentRef.current) {
        localCandidatesRef.current.push(candidate);
      } else {
        sendSignal({
          type: 'candidate',
          candidate,
        });
      }
    });

    // ========================================
    // WEBRTC CONNECTION STATE
    // ========================================

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

        if (state === 'connected') {
          clearPeerTimer();

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

          watchPeer(pc, 2000);
        }
      }
    );

    return pc;
  }

  // ==========================================
  // 12. CREATE AND SEND WEBRTC OFFER
  // ==========================================

  async function createAndSendOffer() {
    const stream = streamRef.current;
    const socket = socketRef.current;

    if (
      !shouldConnectRef.current ||
      !stream ||
      !socket ||
      socket.readyState !== WebSocket.OPEN ||
      offerStartedRef.current
    ) {
      return;
    }

    offerStartedRef.current = true;

    const pc =
      peerRef.current ?? createPeer(stream);

    try {
      updateStatus(
        'Creating WebRTC offer...'
      );

      const offer = await pc.createOffer();

      await pc.setLocalDescription(offer);

      // Ignore work from a connection that has
      // already been replaced.
      if (
        peerRef.current !== pc ||
        socketRef.current !== socket ||
        !shouldConnectRef.current
      ) {
        return;
      }

      sendSignal({
        type: 'offer',
        sdp: {
          type: offer.type,
          sdp: offer.sdp,
        },
      });

      offerSentRef.current = true;

      // Flush any ICE candidates collected earlier.
      for (const candidate of localCandidatesRef.current) {
        sendSignal({
          type: 'candidate',
          candidate,
        });
      }

      localCandidatesRef.current = [];

      updateStatus(
        'Offer sent - waiting for laptop answer'
      );

      // Recover automatically if negotiation stalls.
      watchPeer(pc, 20000);
    } catch (error) {
      if (peerRef.current !== pc) {
        return;
      }

      console.error(
        '[WebRTC] Offer error:',
        error
      );

      updateStatus(
        'Could not negotiate video - retrying...'
      );

      recoverConnection();
    }
  }

  // ==========================================
  // 13. HANDLE LAPTOP SDP ANSWER
  // ==========================================

  async function handleAnswer(
    answer: {
      type: 'answer';
      sdp: string;
    }
  ) {
    const pc = peerRef.current;

    if (!pc || !shouldConnectRef.current) {
      return;
    }

    await pc.setRemoteDescription(
      new RTCSessionDescription(answer)
    );

    if (peerRef.current !== pc) {
      return;
    }

    const queued = remoteCandidatesRef.current;
    remoteCandidatesRef.current = [];

    for (const candidate of queued) {
      if (peerRef.current !== pc) {
        return;
      }

      try {
        await pc.addIceCandidate(
          new RTCIceCandidate(candidate)
        );
      } catch (error) {
        console.error(
          '[WebRTC] Queued ICE error:',
          error
        );
      }
    }

    if (pc.connectionState !== 'connected') {
      updateStatus(
        'Answer received - connecting video...'
      );
    }
  }

  // ==========================================
  // 14. HANDLE LAPTOP ICE CANDIDATES
  // ==========================================

  async function handleRemoteCandidate(
    candidate: CandidateData
  ) {
    const pc = peerRef.current;

    if (!pc || !pc.remoteDescription) {
      remoteCandidatesRef.current.push(candidate);
      return;
    }

    await pc.addIceCandidate(
      new RTCIceCandidate(candidate)
    );
  }

  // ==========================================
  // 15. CONNECT TO SIGNALING SERVER
  // ==========================================

  function startSignaling() {
    if (
      !mountedRef.current ||
      !shouldConnectRef.current ||
      !streamRef.current ||
      socketRef.current
    ) {
      return;
    }

    updateStatus(
      'Connecting to laptop signaling server...'
    );

    let socket: WebSocket;

    try {
      socket = new WebSocket(SIGNAL_URL);
    } catch (error) {
      console.error(
        '[WebRTC] WebSocket creation failed:',
        error
      );

      scheduleReconnect();
      return;
    }

    socketRef.current = socket;

    // Detect a WebSocket handshake that never finishes.
    clearOpenTimer();

    openTimerRef.current = setTimeout(() => {
      if (
        socketRef.current === socket &&
        socket.readyState !== WebSocket.OPEN
      ) {
        console.log(
          '[WebRTC] WebSocket connection timed out'
        );

        closeSocket();
        closePeer();
        scheduleReconnect();
      }
    }, 10000);

    // ========================================
    // WEBSOCKET CONNECTED
    // ========================================

    socket.onopen = () => {
      if (socketRef.current !== socket) {
        return;
      }

      clearOpenTimer();

      updateStatus(
        'Signaling connected - waiting for receiver'
      );
    };

    // ========================================
    // WEBSOCKET MESSAGE
    // ========================================

    socket.onmessage = async event => {
      if (socketRef.current !== socket) {
        return;
      }

      try {
        const message: SignalMessage = JSON.parse(
          String(event.data)
        );

        switch (message.type) {
          case 'welcome':
            // The relay accepted this connection.
            retryCountRef.current = 0;

            if (message.peers === 2) {
              await createAndSendOffer();
            } else {
              updateStatus(
                'Waiting for laptop receiver'
              );
            }

            break;

          case 'peer-ready':
            // Laptop joined or rejoined.
            // Generate a fresh offer if needed.
            await createAndSendOffer();
            break;

          case 'answer':
            if (message.sdp) {
              await handleAnswer(message.sdp);
            }
            break;

          case 'candidate':
            if (message.candidate) {
              await handleRemoteCandidate(
                message.candidate
              );
            }
            break;

          case 'peer-left':
            // Reset WebRTC, but keep WebSocket open.
            // The next peer-ready starts a new offer.
            closePeer();

            updateStatus(
              'Laptop receiver left - waiting for reconnection'
            );
            break;

          case 'error':
            updateStatus(
              message.message ||
                'Signaling server error'
            );
            break;
        }
      } catch (error) {
        console.error(
          '[WebRTC] Signaling error:',
          error
        );

        updateStatus(
          error instanceof Error
            ? `Signaling error: ${error.message}`
            : 'Could not process signaling message'
        );
      }
    };

    // ========================================
    // WEBSOCKET ERROR
    // ========================================

    socket.onerror = event => {
      if (socketRef.current === socket) {
        console.log(
          '[WebRTC] WebSocket error:',
          event
        );
      }
    };

    // ========================================
    // WEBSOCKET DISCONNECTED
    // ========================================

    socket.onclose = event => {
      if (socketRef.current !== socket) {
        return;
      }

      console.log(
        '[WebRTC] Socket closed:',
        event.code,
        event.reason
      );

      clearOpenTimer();

      socketRef.current = null;

      closePeer();

      // Automatically retry signaling.
      scheduleReconnect();
    };
  }

  // ==========================================
  // 16. CONNECT BUTTON
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

    startSignaling();
  }

  // ==========================================
  // 17. USER INTERFACE
  // ==========================================

  return (
    <View style={styles.container}>
      <Text style={styles.title}>
        WebRTC Live Camera
      </Text>

      <Text style={styles.status}>
        {status}
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

      <Pressable
        style={[
          styles.button,
          (starting || !!streamUrl) && styles.disabled,
        ]}
        onPress={startCamera}
        disabled={starting || !!streamUrl}
        accessibilityRole="button"
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
      >
        <Text style={styles.buttonText}>
          Connect to Dashboard
        </Text>
      </Pressable>

      <Pressable
        style={styles.secondaryButton}
        onPress={stopEverything}
        accessibilityRole="button"
      >
        <Text style={styles.buttonText}>
          Stop Stream & Camera
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
// 18. STYLES
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

  buttonText: {
    color: '#101827',
    fontSize: 16,
    fontWeight: 'bold',
    textAlign: 'center',
  },
});
