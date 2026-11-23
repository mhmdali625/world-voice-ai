
import { useEffect, useRef, useState } from 'react';

type RelayPhoto = {
  capturedAt: string;
  dataUrl: string;
};

type PhotoResponse = {
  photo: RelayPhoto | null;
};

type RelayStatus =
  | 'checking'
  | 'connected'
  | 'disconnected';

type VideoStatus =
  | 'connecting'
  | 'waiting'
  | 'negotiating'
  | 'streaming'
  | 'offline';

type SignalMessage = {
  type: string;
  peers?: number;
  sdp?: RTCSessionDescriptionInit;
  candidate?: RTCIceCandidateInit;
};

// The dashboard and relay run on the same laptop.
const RELAY_URL = 'http://127.0.0.1:3001';

const SIGNAL_URL = 'ws://127.0.0.1:3001/signal';

// ==========================================
// 1. LIVE WEBRTC CAMERA RECEIVER
// ==========================================

function LiveCameraReceiver() {
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);

  const [enabled, setEnabled] = useState(true);

  const [videoStatus, setVideoStatus] =
    useState<VideoStatus>('connecting');

  const [videoMessage, setVideoMessage] = useState(
    'Connecting to signaling server...'
  );

  useEffect(() => {
    if (!enabled) {
      return;
    }

    // ========================================
    // VALIDATE DOM ELEMENTS
    // ========================================

    const videoNode = videoRef.current;
    const canvasNode = canvasRef.current;

    if (!videoNode || !canvasNode) {
      return;
    }

    // These references are now guaranteed
    // to be non-null inside our callbacks.
    const video: HTMLVideoElement = videoNode;
    const canvas: HTMLCanvasElement = canvasNode;

    const context = canvas.getContext('2d');

    if (!context) {
      setVideoStatus('offline');
      setVideoMessage(
        'Canvas is unavailable in this browser'
      );
      return;
    }

    const ctx: CanvasRenderingContext2D = context;

    // ========================================
    // CONNECTION STATE
    // ========================================

    let active = true;

    let socket: WebSocket | null = null;
    let peer: RTCPeerConnection | null = null;

    let pendingCandidates: RTCIceCandidateInit[] = [];

    let retryCount = 0;

    let reconnectTimer:
      ReturnType<typeof setTimeout> | null = null;

    let videoTimer:
      ReturnType<typeof setTimeout> | null = null;

    let animationFrame = 0;
    let hasVideoFrame = false;

    // ========================================
    // UPDATE STATUS
    // ========================================

    function updateStatus(
      next: VideoStatus,
      message: string
    ) {
      if (!active) {
        return;
      }

      setVideoStatus(next);
      setVideoMessage(message);

      console.log('[Dashboard WebRTC]', message);
    }

    // ========================================
    // CLEAR TIMERS
    // ========================================

    function clearReconnectTimer() {
      if (reconnectTimer !== null) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
    }

    function clearVideoTimer() {
      if (videoTimer !== null) {
        clearTimeout(videoTimer);
        videoTimer = null;
      }
    }

    // ========================================
    // SEND SIGNALING
    // ========================================

    function sendSignal(message: object) {
      if (socket?.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify(message));
      }
    }

    // ========================================
    // CLOSE WEBRTC PEER
    // ========================================

    function closePeer(
      preserveCandidates = false
    ) {
      clearVideoTimer();

      const previousPeer = peer;
      peer = null;

      if (previousPeer) {
        previousPeer.onicecandidate = null;
        previousPeer.ontrack = null;
        previousPeer.onconnectionstatechange = null;

        previousPeer.close();
      }

      video.pause();
      video.srcObject = null;
      video.onloadedmetadata = null;

      hasVideoFrame = false;

      if (!preserveCandidates) {
        pendingCandidates = [];
      }
    }

    // ========================================
    // AUTOMATIC SIGNALING RECONNECTION
    // ========================================

    function scheduleReconnect() {
      if (
        !active ||
        reconnectTimer !== null
      ) {
        return;
      }

      const delay = Math.min(
        1000 * 2 ** Math.min(retryCount, 4),
        10000
      );

      retryCount += 1;

      updateStatus(
        'offline',
        `Signaling disconnected. Retrying in ${delay / 1000}s...`
      );

      reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        connectSignaling();
      }, delay);
    }

    // ========================================
    // VIDEO CONNECTION RECOVERY
    // ========================================

    function scheduleVideoRecovery(
      connection: RTCPeerConnection,
      delay: number
    ) {
      clearVideoTimer();

      videoTimer = setTimeout(() => {
        videoTimer = null;

        if (
          !active ||
          peer !== connection ||
          connection.connectionState === 'connected'
        ) {
          return;
        }

        updateStatus(
          'connecting',
          'Video interrupted. Reconnecting...'
        );

        // Closing signaling tells Android
        // that the receiver has disconnected.
        // A new signaling session allows
        // a fresh WebRTC negotiation.
        if (
          socket?.readyState === WebSocket.OPEN
        ) {
          socket.close();
        } else {
          closePeer();
          scheduleReconnect();
        }
      }, delay);
    }

    // ========================================
    // CANVAS VIDEO RENDERER
    // ========================================

    function drawVideo() {
      if (!active) {
        return;
      }

      const width = canvas.clientWidth;
      const height = canvas.clientHeight;

      if (width > 0 && height > 0) {
        const ratio = Math.min(
          window.devicePixelRatio || 1,
          2
        );

        const pixelWidth =
          Math.round(width * ratio);

        const pixelHeight =
          Math.round(height * ratio);

        if (
          canvas.width !== pixelWidth ||
          canvas.height !== pixelHeight
        ) {
          canvas.width = pixelWidth;
          canvas.height = pixelHeight;
        }

        ctx.setTransform(
          ratio,
          0,
          0,
          ratio,
          0,
          0
        );

        ctx.fillStyle = '#000000';

        ctx.fillRect(
          0,
          0,
          width,
          height
        );

        if (
          video.srcObject &&
          video.readyState >= 2 &&
          video.videoWidth > 0 &&
          video.videoHeight > 0
        ) {
          // Preserve portrait aspect ratio.
          const scale = Math.min(
            width / video.videoWidth,
            height / video.videoHeight
          );

          const drawWidth =
            video.videoWidth * scale;

          const drawHeight =
            video.videoHeight * scale;

          const x =
            (width - drawWidth) / 2;

          const y =
            (height - drawHeight) / 2;

          try {
            ctx.drawImage(
              video,
              x,
              y,
              drawWidth,
              drawHeight
            );

            if (!hasVideoFrame) {
              hasVideoFrame = true;

              updateStatus(
                'streaming',
                'Live Android camera streaming'
              );

              console.log(
                '[Dashboard WebRTC] Resolution:',
                video.videoWidth,
                video.videoHeight
              );
            }
          } catch (error) {
            console.warn(
              '[Dashboard WebRTC] Frame not ready:',
              error
            );
          }
        }
      }

      animationFrame =
        requestAnimationFrame(drawVideo);
    }

    // ========================================
    // CREATE WEBRTC PEER
    // ========================================

    function createPeerConnection() {
      // Preserve ICE candidates that may
      // have arrived before the offer.
      closePeer(true);

      const connection =
        new RTCPeerConnection({
          iceServers: [
            {
              urls:
                'stun:stun.l.google.com:19302',
            },
          ],
        });

      peer = connection;

      // Send laptop ICE candidates.
      connection.onicecandidate = event => {
        if (
          peer === connection &&
          event.candidate
        ) {
          sendSignal({
            type: 'candidate',
            candidate: event.candidate.toJSON(),
          });
        }
      };

      // Receive Android video stream.
      connection.ontrack = event => {
        if (peer !== connection) {
          return;
        }

        const remoteStream =
          event.streams[0] ||
          new MediaStream([event.track]);

        video.srcObject = remoteStream;

        const startPlayback = () => {
          void video.play().catch(error => {
            console.error(
              '[Dashboard WebRTC] Playback error:',
              error
            );

            updateStatus(
              'offline',
              'Video playback blocked. Check browser Console.'
            );
          });
        };

        video.onloadedmetadata =
          startPlayback;

        startPlayback();
      };

      // Monitor connection status.
      connection.onconnectionstatechange = () => {
        if (peer !== connection) {
          return;
        }

        const state = connection.connectionState;

        console.log(
          '[Dashboard WebRTC] Connection state:',
          state
        );

        if (state === 'connected') {
          clearVideoTimer();

          if (!hasVideoFrame) {
            updateStatus(
              'negotiating',
              'Connected. Starting camera video...'
            );
          }
        } else if (state === 'disconnected') {
          updateStatus(
            'connecting',
            'Video interrupted. Checking connection...'
          );

          scheduleVideoRecovery(
            connection,
            7000
          );
        } else if (state === 'failed') {
          updateStatus(
            'offline',
            'WebRTC failed. Reconnecting...'
          );

          scheduleVideoRecovery(
            connection,
            1500
          );
        }
      };

      return connection;
    }

    // ========================================
    // HANDLE ANDROID SDP OFFER
    // ========================================

    async function handleOffer(
      offer: RTCSessionDescriptionInit
    ) {
      updateStatus(
        'negotiating',
        'Phone detected. Negotiating WebRTC...'
      );

      const connection =
        createPeerConnection();

      await connection.setRemoteDescription(
        offer
      );

      if (
        !active ||
        peer !== connection
      ) {
        return;
      }

      // Apply candidates received
      // before the SDP offer.
      const queued = pendingCandidates;
      pendingCandidates = [];

      for (const candidate of queued) {
        if (peer !== connection) {
          return;
        }

        try {
          await connection.addIceCandidate(
            candidate
          );
        } catch (error) {
          console.error(
            '[Dashboard WebRTC] Queued ICE error:',
            error
          );
        }
      }

      const answer =
        await connection.createAnswer();

      await connection.setLocalDescription(
        answer
      );

      if (
        !active ||
        peer !== connection
      ) {
        return;
      }

      sendSignal({
        type: 'answer',
        sdp: connection.localDescription,
      });

      updateStatus(
        'negotiating',
        'Answer sent. Waiting for camera frames...'
      );
    }

    // ========================================
    // HANDLE ANDROID ICE CANDIDATES
    // ========================================

    async function handleCandidate(
      candidate: RTCIceCandidateInit
    ) {
      const connection = peer;

      if (
        !connection ||
        !connection.remoteDescription
      ) {
        pendingCandidates.push(candidate);
        return;
      }

      try {
        await connection.addIceCandidate(
          candidate
        );
      } catch (error) {
        console.error(
          '[Dashboard WebRTC] ICE candidate error:',
          error
        );
      }
    }

    // ========================================
    // CONNECT SIGNALING WEBSOCKET
    // ========================================

    function connectSignaling() {
      if (!active) {
        return;
      }

      updateStatus(
        'connecting',
        'Connecting to signaling server...'
      );

      let connection: WebSocket;

      try {
        connection = new WebSocket(
          SIGNAL_URL
        );
      } catch (error) {
        console.error(
          '[Dashboard WebRTC] WebSocket creation failed:',
          error
        );

        scheduleReconnect();
        return;
      }

      socket = connection;

      // WebSocket opened.
      connection.onopen = () => {
        if (
          !active ||
          socket !== connection
        ) {
          return;
        }

        retryCount = 0;

        updateStatus(
          'waiting',
          'Waiting for Android phone'
        );
      };

      // Receive signaling messages.
      connection.onmessage = event => {
        if (
          !active ||
          socket !== connection
        ) {
          return;
        }

        let message: SignalMessage;

        try {
          message = JSON.parse(
            String(event.data)
          ) as SignalMessage;
        } catch (error) {
          console.error(
            '[Dashboard WebRTC] Invalid signaling JSON:',
            error
          );

          return;
        }

        switch (message.type) {
          case 'welcome':
            if (message.peers === 2) {
              updateStatus(
                'waiting',
                'Phone detected. Waiting for offer...'
              );
            }
            break;

          case 'peer-ready':
            updateStatus(
              'waiting',
              'Phone connected. Waiting for video offer...'
            );
            break;

          case 'offer':
            if (message.sdp?.type === 'offer') {
              void handleOffer(
                message.sdp
              ).catch(error => {
                console.error(
                  '[Dashboard WebRTC] Offer error:',
                  error
                );

                updateStatus(
                  'offline',
                  'Could not negotiate video'
                );
              });
            }
            break;

          case 'candidate':
            if (message.candidate) {
              void handleCandidate(
                message.candidate
              );
            }
            break;

          case 'peer-left':
            closePeer();

            updateStatus(
              'waiting',
              'Android disconnected. Waiting for phone...'
            );
            break;

          default:
            console.log(
              '[Dashboard WebRTC] Ignoring:',
              message.type
            );
        }
      };

      // WebSocket error.
      connection.onerror = () => {
        if (socket === connection) {
          console.warn(
            '[Dashboard WebRTC] WebSocket error'
          );
        }
      };

      // WebSocket disconnected.
      connection.onclose = event => {
        if (
          !active ||
          socket !== connection
        ) {
          return;
        }

        console.log(
          '[Dashboard WebRTC] Socket closed:',
          event.code,
          event.reason
        );

        socket = null;

        closePeer();
        scheduleReconnect();
      };
    }

    // ========================================
    // START THE RECEIVER
    // ========================================

    animationFrame =
      requestAnimationFrame(drawVideo);

    connectSignaling();

    // ========================================
    // CLEAN UP WHEN DISCONNECTED
    // ========================================

    return () => {
      active = false;

      clearReconnectTimer();
      clearVideoTimer();

      cancelAnimationFrame(
        animationFrame
      );

      closePeer();

      if (socket) {
        socket.onopen = null;
        socket.onmessage = null;
        socket.onerror = null;
        socket.onclose = null;

        socket.close();
        socket = null;
      }
    };
  }, [enabled]);

  const isStreaming =
    enabled && videoStatus === 'streaming';

  return (
    <section className="world-card video-panel">
      <div className="panel-heading">
        <div>
          <h2 className="world-panel-title">
            Live Android Camera
          </h2>

          <p className="world-muted">
            Live video from the phone using WebRTC.
          </p>
        </div>

        <span
          className={`world-badge ${
            isStreaming
              ? 'badge-green'
              : enabled
                ? 'badge-yellow'
                : 'badge-red'
          }`}
        >
          {isStreaming
            ? 'Live'
            : enabled
              ? 'Connecting'
              : 'Disconnected'}
        </span>
      </div>

      <div className="live-video-container">
        <video
          ref={videoRef}
          className="remote-video"
          playsInline
          muted
        />

        <canvas
          ref={canvasRef}
          className="live-canvas"
        />

        {!isStreaming && (
          <div className="video-overlay">
            <p>
              {enabled
                ? videoMessage
                : 'Live video disconnected'}
            </p>
          </div>
        )}
      </div>

      <div className="video-controls">
        <p className="world-muted video-message">
          {enabled
            ? videoMessage
            : 'Receiver stopped'}
        </p>

        <button
          type="button"
          className={`world-button ${
            enabled ? 'secondary' : ''
          }`}
          onClick={() =>
            setEnabled(current => !current)
          }
        >
          {enabled
            ? 'Disconnect'
            : 'Reconnect'}
        </button>
      </div>

      <p className="world-muted">
        On Android, open WebRTC Live Camera,
        start the camera, then connect to
        the dashboard.
      </p>
    </section>
  );
}

// ==========================================
// 2. MAIN DASHBOARD
// ==========================================

export default function App() {
  const [photo, setPhoto] =
    useState<RelayPhoto | null>(null);

  const [relayStatus, setRelayStatus] =
    useState<RelayStatus>('checking');

  const [lastChecked, setLastChecked] =
    useState('');

  // ========================================
  // POLL LATEST MOBILE PHOTO
  // ========================================

  useEffect(() => {
    let active = true;

    async function fetchLatestPhoto() {
      try {
        const response = await fetch(
          `${RELAY_URL}/api/photo`,
          { cache: 'no-store' }
        );

        if (!response.ok) {
          throw new Error(
            `HTTP ${response.status}`
          );
        }

        const data: PhotoResponse =
          await response.json();

        if (!active) {
          return;
        }

        setPhoto(data.photo);
        setRelayStatus('connected');

        setLastChecked(
          new Date().toLocaleTimeString()
        );
      } catch (error) {
        if (!active) {
          return;
        }

        console.error(
          '[Dashboard] Relay unavailable:',
          error
        );

        setRelayStatus('disconnected');
      }
    }

    void fetchLatestPhoto();

    const interval = setInterval(() => {
      void fetchLatestPhoto();
    }, 3000);

    return () => {
      active = false;
      clearInterval(interval);
    };
  }, []);

  const isConnected =
    relayStatus === 'connected';

  return (
    <div className="world-app">
      <style>{`
        * {
          box-sizing: border-box;
        }

        body {
          margin: 0;
          background: #0b1220;
          color: #f8fafc;
          font-family: Inter, Arial, sans-serif;
        }

        button {
          font: inherit;
        }

        .world-app {
          min-height: 100vh;
          background: #0b1220;
          color: #f8fafc;
        }

        .world-header {
          padding: 24px 32px;
          background: #111c2e;
          border-bottom: 1px solid #263449;
          display: flex;
          justify-content: space-between;
          align-items: center;
          gap: 20px;
          flex-wrap: wrap;
        }

        .world-header h1 {
          margin: 0;
          color: #5dd6c0;
          font-size: 28px;
        }

        .world-subtitle {
          color: #94a3b8;
          margin: 8px 0 0;
          font-size: 14px;
        }

        .world-content {
          padding: 30px;
          max-width: 1500px;
          margin: 0 auto;
        }

        .world-section-title {
          font-size: 21px;
          margin: 0 0 18px;
        }

        .world-stats {
          display: grid;
          grid-template-columns:
            repeat(3, minmax(0, 1fr));
          gap: 18px;
          margin-bottom: 25px;
        }

        .world-card {
          background: #162236;
          border: 1px solid #2a3a50;
          border-radius: 16px;
          padding: 22px;
          min-width: 0;
        }

        .stat-title {
          color: #94a3b8;
          font-size: 14px;
          margin: 0 0 12px;
        }

        .stat-value {
          font-size: 23px;
          font-weight: 700;
          margin: 0 0 8px;
          overflow-wrap: anywhere;
        }

        .stat-note {
          color: #94a3b8;
          font-size: 12px;
          margin: 0;
        }

        .world-panel-title {
          font-size: 19px;
          margin: 0 0 12px;
        }

        .world-muted {
          color: #94a3b8;
          font-size: 14px;
          line-height: 1.6;
        }

        .world-badge {
          padding: 8px 14px;
          border-radius: 20px;
          font-size: 13px;
          font-weight: 600;
          white-space: nowrap;
        }

        .badge-green {
          color: #5dd6c0;
          background: #123c38;
        }

        .badge-yellow {
          color: #facc15;
          background: #43371c;
        }

        .badge-red {
          color: #fca5a5;
          background: #451f2a;
        }

        .world-grid,
        .world-bottom {
          display: grid;
          grid-template-columns:
            minmax(0, 1.5fr) minmax(300px, 1fr);
          gap: 22px;
          align-items: start;
        }

        .world-bottom {
          margin-top: 22px;
        }

        .panel-heading {
          display: flex;
          align-items: flex-start;
          justify-content: space-between;
          gap: 16px;
        }

        .live-video-container {
          position: relative;
          width: 100%;
          height: clamp(300px, 42vw, 500px);
          background: #000;
          border: 1px solid #334155;
          border-radius: 12px;
          overflow: hidden;
          margin-top: 14px;
        }

        .remote-video {
          position: absolute;
          inset: 0;
          width: 100%;
          height: 100%;
          opacity: 0.001;
          z-index: 1;
          pointer-events: none;
        }

        .live-canvas {
          position: absolute;
          inset: 0;
          width: 100%;
          height: 100%;
          z-index: 2;
          display: block;
          background: #000;
        }

        .video-overlay {
          position: absolute;
          inset: 0;
          z-index: 3;
          display: flex;
          align-items: center;
          justify-content: center;
          padding: 25px;
          text-align: center;
          background: rgba(11,18,32,0.84);
          color: #cbd5e1;
          pointer-events: none;
        }

        .video-controls {
          display: flex;
          justify-content: space-between;
          align-items: center;
          gap: 14px;
          flex-wrap: wrap;
          margin-top: 16px;
        }

        .video-message {
          margin: 0;
          flex: 1;
        }

        .world-button {
          border: 0;
          border-radius: 10px;
          background: #5dd6c0;
          color: #101827;
          padding: 11px 18px;
          font-weight: bold;
          cursor: pointer;
        }

        .world-button.secondary {
          background: #94a3b8;
        }

        .world-button:hover {
          filter: brightness(1.08);
        }

        .photo-container {
          width: 100%;
          min-height: 300px;
          background: #0b1220;
          border: 1px dashed #334155;
          border-radius: 12px;
          display: flex;
          align-items: center;
          justify-content: center;
          overflow: hidden;
          margin-top: 18px;
        }

        .photo-container img {
          width: 100%;
          max-height: 490px;
          object-fit: contain;
          display: block;
        }

        .empty-photo {
          padding: 32px;
          text-align: center;
          color: #94a3b8;
        }

        .world-detail {
          display: flex;
          justify-content: space-between;
          gap: 12px;
          padding: 14px 0;
          border-bottom: 1px solid #2a3a50;
          font-size: 14px;
        }

        .world-detail span:first-child {
          color: #94a3b8;
        }

        .world-detail strong {
          text-align: right;
          overflow-wrap: anywhere;
        }

        .world-info-box {
          background: #101c2e;
          border-radius: 12px;
          padding: 16px;
          margin-top: 18px;
          color: #cbd5e1;
          font-size: 13px;
          line-height: 1.7;
        }

        .world-placeholder {
          min-height: 180px;
          border: 1px dashed #334155;
          border-radius: 12px;
          display: flex;
          align-items: center;
          justify-content: center;
          text-align: center;
          padding: 20px;
          color: #94a3b8;
          margin-top: 16px;
        }

        .world-footer {
          margin-top: 30px;
          padding-bottom: 16px;
          text-align: center;
          color: #64748b;
          font-size: 12px;
        }

        @media (max-width: 850px) {
          .world-stats,
          .world-grid,
          .world-bottom {
            grid-template-columns: 1fr;
          }

          .world-content {
            padding: 16px;
          }

          .world-header {
            padding: 20px;
          }

          .world-card {
            padding: 16px;
          }

          .live-video-container {
            height: 380px;
          }
        }
      `}</style>

      {/* Dashboard header */}
      <header className="world-header">
        <div>
          <h1>World Voice AI</h1>

          <p className="world-subtitle">
            Visual Assistant - Monitoring Dashboard
          </p>
        </div>

        <span
          className={`world-badge ${
            isConnected
              ? 'badge-green'
              : relayStatus === 'checking'
                ? 'badge-yellow'
                : 'badge-red'
          }`}
        >
          {isConnected
            ? 'Local Relay Connected'
            : relayStatus === 'checking'
              ? 'Checking Connection'
              : 'Local Relay Offline'}
        </span>
      </header>

      <main className="world-content">
        <h2 className="world-section-title">
          System Overview
        </h2>

        {/* System statistics */}
        <div className="world-stats">
          <section className="world-card">
            <p className="stat-title">
              Local Relay Server
            </p>

            <p
              className="stat-value"
              style={{
                color: isConnected
                  ? '#5dd6c0'
                  : '#fca5a5',
              }}
            >
              {isConnected
                ? 'Connected'
                : relayStatus === 'checking'
                  ? 'Checking...'
                  : 'Disconnected'}
            </p>

            <p className="stat-note">
              Node.js HTTP + WebSocket - Port 3001
            </p>
          </section>

          <section className="world-card">
            <p className="stat-title">
              Latest Mobile Photo
            </p>

            <p className="stat-value">
              {photo ? 'Received' : 'Waiting'}
            </p>

            <p className="stat-note">
              {photo
                ? 'Photo available from Android'
                : 'No photo received yet'}
            </p>
          </section>

          <section className="world-card">
            <p className="stat-title">
              AI Backend
            </p>

            <p
              className="stat-value"
              style={{ color: '#facc15' }}
            >
              Not Connected
            </p>

            <p className="stat-note">
              Awaiting FastAPI integration
            </p>
          </section>
        </div>

        {/* Live camera and connection details */}
        <div className="world-grid">
          <LiveCameraReceiver />

          <section className="world-card">
            <h2 className="world-panel-title">
              Connection Details
            </h2>

            <div className="world-detail">
              <span>Mobile Application</span>
              <strong>React Native + Expo</strong>
            </div>

            <div className="world-detail">
              <span>Dashboard</span>
              <strong>React + Vite</strong>
            </div>

            <div className="world-detail">
              <span>Photos</span>
              <strong>HTTP REST</strong>
            </div>

            <div className="world-detail">
              <span>Live Video</span>
              <strong>WebRTC</strong>
            </div>

            <div className="world-detail">
              <span>Signaling</span>
              <strong>WebSocket</strong>
            </div>

            <div className="world-detail">
              <span>Relay Port</span>
              <strong>3001</strong>
            </div>

            <div className="world-detail">
              <span>Relay Status</span>
              <strong>
                {relayStatus === 'connected'
                  ? 'Online'
                  : relayStatus === 'checking'
                    ? 'Checking'
                    : 'Offline'}
              </strong>
            </div>

            <div className="world-detail">
              <span>Last Successful Check</span>
              <strong>
                {lastChecked || '-'}
              </strong>
            </div>

            <div className="world-info-box">
              <strong>
                How live streaming works
              </strong>

              <p>
                1. Android captures video using
                react-native-webrtc.
              </p>

              <p>
                2. The WebSocket relay exchanges
                offers, answers, and ICE candidates.
              </p>

              <p>
                3. WebRTC carries the camera
                stream to the browser.
              </p>

              <p>
                4. Canvas displays the decoded
                video frames.
              </p>
            </div>
          </section>
        </div>

        {/* Photo and AI panels */}
        <div className="world-bottom">
          <section className="world-card">
            <h2 className="world-panel-title">
              Latest Mobile Camera Photo
            </h2>

            <p className="world-muted">
              Photos captured using the Expo
              mobile application.
            </p>

            <span
              className={`world-badge ${
                isConnected
                  ? 'badge-green'
                  : 'badge-red'
              }`}
            >
              {isConnected
                ? 'Relay Online'
                : 'Relay Offline'}
            </span>

            <div className="photo-container">
              {photo ? (
                <img
                  src={photo.dataUrl}
                  alt="Latest image uploaded from the Android camera"
                />
              ) : (
                <div className="empty-photo">
                  <h3>No Photo Yet</h3>

                  <p>
                    Capture a photo on your phone
                    and send it to the dashboard.
                  </p>
                </div>
              )}
            </div>

            {photo && (
              <p className="world-muted">
                Captured:{' '}
                {new Date(
                  photo.capturedAt
                ).toLocaleString()}
              </p>
            )}

            <p className="world-muted">
              This dashboard checks for new
              photos every 3 seconds.
            </p>
          </section>

          <section className="world-card">
            <h2 className="world-panel-title">
              AI Analysis &amp; Voice Questions
            </h2>

            <span className="world-badge badge-yellow">
              Waiting for Backend
            </span>

            <div className="world-placeholder">
              <div>
                <h3>
                  AI Integration Coming Next
                </h3>

                <p>
                  Object detection, OCR, and
                  voice questions will appear here.
                </p>

                <p>
                  The FastAPI AI backend is not
                  connected yet.
                </p>
              </div>
            </div>

            <div className="world-info-box">
              This section is reserved for AI
              integration after our mobile and
              dashboard features are complete.
            </div>
          </section>
        </div>

        <footer className="world-footer">
          World Voice AI - Multimedia Project 2026-2027

          <p>
            Local development only.
            Use trusted Wi-Fi and non-sensitive
            test photos.
          </p>
        </footer>
      </main>
    </div>
  );
}
