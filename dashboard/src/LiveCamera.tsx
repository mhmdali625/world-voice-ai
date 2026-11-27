
import { useEffect, useRef, useState } from 'react';

// ==========================================
// TYPES
// ==========================================

type VideoStatus =
  | 'connecting'
  | 'waiting'
  | 'negotiating'
  | 'streaming'
  | 'offline';

// ==========================================
// SERVER ADDRESSES
// ==========================================

// Both servers run on the laptop.
import { BACKEND_URL } from './api';

// ==========================================
// 1. LIVE WEBRTC CAMERA RECEIVER
// ==========================================

// Live WebRTC receiver (Adam's component, moved here unchanged except the
// shared BACKEND_URL import).
export default function LiveCameraReceiver({ device }: { device: string | null }) {
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

    const videoNode = videoRef.current;
    const canvasNode = canvasRef.current;

    if (!videoNode || !canvasNode) {
      return;
    }

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

    // The phone now streams to the BACKEND, which forwards the
    // video to this dashboard (see backend/stream_service.py).
    // This loop watches /stream/status and (re)connects whenever
    // a new live session appears.

    let active = true;
    let peer: RTCPeerConnection | null = null;
    let currentSession: string | null = null;
    let connecting = false;
    let viewedDevice: string | null = device;
    let pollTimer: ReturnType<typeof setTimeout> | null = null;
    let animationFrame = 0;
    let hasVideoFrame = false;

    function updateStatus(
      next: VideoStatus,
      message: string
    ) {
      if (!active) {
        return;
      }

      setVideoStatus(next);
      setVideoMessage(message);
    }

    function closePeer() {
      const previousPeer = peer;
      peer = null;

      if (previousPeer) {
        previousPeer.ontrack = null;
        previousPeer.onconnectionstatechange = null;
        previousPeer.close();
      }

      video.pause();
      video.srcObject = null;
      video.onloadedmetadata = null;

      hasVideoFrame = false;
    }

    // ======================================
    // RENDER LIVE CAMERA FRAMES (unchanged)
    // ======================================

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

        const pixelWidth = Math.round(width * ratio);
        const pixelHeight = Math.round(height * ratio);

        if (
          canvas.width !== pixelWidth ||
          canvas.height !== pixelHeight
        ) {
          canvas.width = pixelWidth;
          canvas.height = pixelHeight;
        }

        ctx.setTransform(ratio, 0, 0, ratio, 0, 0);

        ctx.fillStyle = '#000000';
        ctx.fillRect(0, 0, width, height);

        if (
          video.srcObject &&
          video.readyState >= 2 &&
          video.videoWidth > 0 &&
          video.videoHeight > 0
        ) {
          const scale = Math.min(
            width / video.videoWidth,
            height / video.videoHeight
          );

          const drawWidth = video.videoWidth * scale;
          const drawHeight = video.videoHeight * scale;
          const x = (width - drawWidth) / 2;
          const y = (height - drawHeight) / 2;

          try {
            ctx.drawImage(video, x, y, drawWidth, drawHeight);

            if (!hasVideoFrame) {
              hasVideoFrame = true;

              updateStatus(
                'streaming',
                'Live Android camera streaming'
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

      animationFrame = requestAnimationFrame(drawVideo);
    }

    // ======================================
    // CONNECT TO THE BACKEND STREAM
    // ======================================

    function waitForIceGathering(connection: RTCPeerConnection) {
      if (connection.iceGatheringState === 'complete') {
        return Promise.resolve();
      }

      return new Promise<void>(resolve => {
        const timer = setTimeout(resolve, 2000);

        connection.addEventListener('icegatheringstatechange', () => {
          if (connection.iceGatheringState === 'complete') {
            clearTimeout(timer);
            resolve();
          }
        });
      });
    }

    async function connect(session: string) {
      connecting = true;
      closePeer();

      updateStatus(
        'negotiating',
        'Live stream found. Connecting...'
      );

      // Same LAN as the backend: no STUN needed.
      const connection = new RTCPeerConnection({ iceServers: [] });
      peer = connection;

      connection.addTransceiver('video', { direction: 'recvonly' });

      connection.ontrack = event => {
        if (peer !== connection) {
          return;
        }

        video.srcObject =
          event.streams[0] || new MediaStream([event.track]);

        const startPlayback = () => {
          void video.play().catch(error => {
            console.error('[Dashboard WebRTC] Playback error:', error);

            updateStatus(
              'offline',
              'Video playback blocked. Check browser Console.'
            );
          });
        };

        video.onloadedmetadata = startPlayback;
        startPlayback();
      };

      connection.onconnectionstatechange = () => {
        if (peer !== connection) {
          return;
        }

        const state = connection.connectionState;

        if (state === 'failed' || state === 'closed') {
          // Next status poll reconnects.
          currentSession = null;

          updateStatus(
            'connecting',
            'Video interrupted. Reconnecting...'
          );
        }
      };

      try {
        const offer = await connection.createOffer();
        await connection.setLocalDescription(offer);
        await waitForIceGathering(connection);

        if (!active || peer !== connection || !connection.localDescription) {
          return;
        }

        const response = await fetch(`${BACKEND_URL}/stream/view`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            sdp: connection.localDescription.sdp,
            type: connection.localDescription.type,
            device_id: viewedDevice,
          }),
        });

        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }

        const answer = await response.json();

        if (!active || peer !== connection) {
          return;
        }

        await connection.setRemoteDescription({
          type: answer.type,
          sdp: answer.sdp,
        });

        currentSession = session;
      } catch (error) {
        console.error('[Dashboard WebRTC] Connect error:', error);

        if (peer === connection) {
          closePeer();
        }

        currentSession = null;

        updateStatus(
          'offline',
          'Could not connect to the live stream. Retrying...'
        );
      } finally {
        connecting = false;
      }
    }

    // ======================================
    // WATCH THE BACKEND STREAM STATUS
    // ======================================

    async function pollStatus() {
      try {
        const query = device
          ? `?device=${encodeURIComponent(device)}`
          : '';

        const response = await fetch(`${BACKEND_URL}/stream/status${query}`, {
          cache: 'no-store',
        });

        const status: {
          live: boolean;
          stalled: boolean;
          session_id: string | null;
          device: string;
        } = await response.json();

        if (!active) {
          return;
        }

        // The phone shown (the selected one, or the live one).
        viewedDevice = status.device;

        if (!status.live) {
          if (currentSession) {
            closePeer();
            currentSession = null;
          }

          updateStatus(
            'waiting',
            status.stalled
              ? 'Phone stream stalled. Waiting for video...'
              : 'Waiting for the phone to start streaming'
          );
        } else if (
          status.session_id &&
          status.session_id !== currentSession &&
          !connecting
        ) {
          await connect(status.session_id);
        }
      } catch {
        updateStatus(
          'offline',
          'Backend unreachable. Retrying...'
        );
      }

      if (active) {
        pollTimer = setTimeout(() => {
          void pollStatus();
        }, 2000);
      }
    }

    updateStatus('connecting', 'Checking the live stream...');

    animationFrame = requestAnimationFrame(drawVideo);
    void pollStatus();

    return () => {
      active = false;

      if (pollTimer !== null) {
        clearTimeout(pollTimer);
      }

      cancelAnimationFrame(animationFrame);
      closePeer();
    };
  }, [enabled, device]);

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
          role="img"
          aria-label={
            isStreaming
              ? 'Live video from the phone camera'
              : 'Live video not available'
          }
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
        <p className="world-muted video-message" aria-live="polite">
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
        On the phone, open Live Assistant and
        tap Start Streaming. The video appears
        here automatically.
      </p>
    </section>
  );
}
