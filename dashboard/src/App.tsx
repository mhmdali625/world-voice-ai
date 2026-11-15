
import { useEffect, useState } from 'react';

type RelayPhoto = {
  capturedAt: string;
  dataUrl: string;
};

type PhotoResponse = {
  photo: RelayPhoto | null;
};

type RelayStatus = 'checking' | 'connected' | 'disconnected';

const RELAY_URL = 'http://localhost:3001';

export default function App() {
  const [photo, setPhoto] = useState<RelayPhoto | null>(null);
  const [relayStatus, setRelayStatus] =
    useState<RelayStatus>('checking');
  const [lastChecked, setLastChecked] = useState<string>('');

  // Fetch the most recent photo from the local relay
  useEffect(() => {
    let active = true;

    const fetchLatestPhoto = async () => {
      try {
        const response = await fetch(
          `${RELAY_URL}/api/photo`,
          { cache: 'no-store' }
        );

        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }

        const data: PhotoResponse = await response.json();

        if (!active) return;

        setPhoto(data.photo);
        setRelayStatus('connected');
        setLastChecked(new Date().toLocaleTimeString());
      } catch (error) {
        if (!active) return;

        console.error('Relay connection error:', error);
        setRelayStatus('disconnected');
      }
    };

    // Fetch immediately
    void fetchLatestPhoto();

    // Look for a new photo every 3 seconds
    const interval = setInterval(() => {
      void fetchLatestPhoto();
    }, 3000);

    return () => {
      active = false;
      clearInterval(interval);
    };
  }, []);

  const isConnected = relayStatus === 'connected';

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

        .world-app {
          min-height: 100vh;
          background: #0b1220;
          color: #f8fafc;
        }

        .world-header {
          background: #111c2e;
          border-bottom: 1px solid #263449;
          padding: 24px 32px;
          display: flex;
          justify-content: space-between;
          align-items: center;
          gap: 20px;
          flex-wrap: wrap;
        }

        .world-header h1 {
          margin: 0;
          font-size: 28px;
          color: #5dd6c0;
        }

        .world-subtitle {
          color: #94a3b8;
          margin: 8px 0 0;
          font-size: 14px;
        }

        .world-badge {
          display: inline-block;
          padding: 8px 14px;
          border-radius: 20px;
          font-size: 13px;
          font-weight: 600;
        }

        .badge-green {
          color: #5dd6c0;
          background: #123c38;
        }

        .badge-red {
          color: #fca5a5;
          background: #451f2a;
        }

        .badge-yellow {
          color: #facc15;
          background: #43371c;
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
          grid-template-columns: repeat(3, minmax(0, 1fr));
          gap: 18px;
          margin-bottom: 30px;
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

        .world-grid {
          display: grid;
          grid-template-columns: minmax(0, 1.6fr) minmax(260px, 1fr);
          gap: 22px;
          align-items: start;
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
          text-align: center;
          padding: 32px;
          color: #94a3b8;
        }

        .empty-photo-icon {
          font-size: 44px;
          margin-bottom: 12px;
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
          padding: 16px;
          background: #101c2e;
          border-radius: 12px;
          margin-top: 18px;
          color: #cbd5e1;
          font-size: 13px;
          line-height: 1.7;
        }

        .world-placeholder {
          min-height: 140px;
          border: 1px dashed #334155;
          border-radius: 12px;
          display: flex;
          justify-content: center;
          align-items: center;
          text-align: center;
          padding: 20px;
          color: #94a3b8;
          margin-top: 16px;
        }

        .world-bottom {
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          gap: 22px;
          margin-top: 22px;
        }

        .world-footer {
          color: #64748b;
          text-align: center;
          font-size: 12px;
          margin-top: 30px;
          padding-bottom: 16px;
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
        }
      `}</style>

      {/* Header */}
      <header className="world-header">
        <div>
          <h1>World Voice AI</h1>
          <p className="world-subtitle">
            Visual Assistant — Monitoring Dashboard
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
            ? '● Local Relay Connected'
            : relayStatus === 'checking'
              ? '● Checking Connection'
              : '● Local Relay Offline'}
        </span>
      </header>

      <main className="world-content">
        {/* Overview cards */}
        <h2 className="world-section-title">
          System Overview
        </h2>

        <div className="world-stats">
          <section className="world-card">
            <p className="stat-title">Local Relay Server</p>
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
              Node.js HTTP — Port 3001
            </p>
          </section>

          <section className="world-card">
            <p className="stat-title">Latest Mobile Photo</p>
            <p className="stat-value">
              {photo ? 'Received' : 'Waiting'}
            </p>
            <p className="stat-note">
              {photo
                ? 'A photo is available'
                : 'No photo received yet'}
            </p>
          </section>

          <section className="world-card">
            <p className="stat-title">AI Backend</p>
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

        {/* Main content */}
        <div className="world-grid">
          {/* Latest photo */}
          <section className="world-card">
            <h2 className="world-panel-title">
              Latest Mobile Camera Photo
            </h2>

            <p className="world-muted">
              Images captured using the Expo mobile app
              appear here automatically.
            </p>

            <span
              className={`world-badge ${
                isConnected ? 'badge-green' : 'badge-red'
              }`}
            >
              {isConnected
                ? '● Receiving from Relay'
                : '● Relay Unavailable'}
            </span>

            <div className="photo-container">
              {photo ? (
                <img
                  src={photo.dataUrl}
                  alt="Latest image uploaded by the mobile camera"
                />
              ) : (
                <div className="empty-photo">
                  <div className="empty-photo-icon">📷</div>

                  <h3>No Photo Yet</h3>

                  <p>
                    Open World Voice AI on your phone,
                    capture a photo, and press
                    Send Photo to Dashboard.
                  </p>
                </div>
              )}
            </div>

            {photo && (
              <p className="world-muted">
                Received:{' '}
                {new Date(
                  photo.capturedAt
                ).toLocaleString()}
                {!isConnected &&
                  ' — Last retrieved photo (relay offline)'}
              </p>
            )}

            <p className="world-muted">
              The dashboard checks for new photos
              every 3 seconds.
            </p>
          </section>

          {/* Connection details */}
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
              <span>Communication</span>
              <strong>HTTP REST</strong>
            </div>

            <div className="world-detail">
              <span>Relay Port</span>
              <strong>3001</strong>
            </div>

            <div className="world-detail">
              <span>Connection</span>
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
              <strong>{lastChecked || '—'}</strong>
            </div>

            <div className="world-detail">
              <span>Last Photo</span>
              <strong>
                {photo
                  ? new Date(
                      photo.capturedAt
                    ).toLocaleTimeString()
                  : '—'}
              </strong>
            </div>

            <div className="world-info-box">
              <strong>How it works</strong>
              <p>
                1. Your phone captures a JPEG photo.
              </p>
              <p>
                2. The app sends it to the local
                Node.js relay using HTTP POST.
              </p>
              <p>
                3. This dashboard retrieves the
                latest photo using HTTP GET.
              </p>
              <p>
                4. React updates the image automatically.
              </p>
            </div>
          </section>
        </div>

        {/* Future integrations */}
        <div className="world-bottom">
          <section className="world-card">
            <h2 className="world-panel-title">
              Live Video Streaming
            </h2>

            <span className="world-badge badge-yellow">
              Not Implemented
            </span>

            <div className="world-placeholder">
              <div>
                <div style={{ fontSize: 36 }}>🎥</div>
                <p>WebRTC video will appear here.</p>
                <p>
                  Current functionality: individual photos.
                </p>
              </div>
            </div>
          </section>

          <section className="world-card">
            <h2 className="world-panel-title">
              AI Analysis & Voice Questions
            </h2>

            <span className="world-badge badge-yellow">
              Waiting for Backend
            </span>

            <div className="world-placeholder">
              <div>
                <div style={{ fontSize: 36 }}>🧠</div>
                <p>
                  Object detection, OCR results,
                  and voice questions will appear here.
                </p>
                <p>
                  Real AI processing is not connected yet.
                </p>
              </div>
            </div>
          </section>
        </div>

        <footer className="world-footer">
          World Voice AI — Multimedia Project 2026–2027
          <p>
            Local development only. The relay currently
            has no authentication; use trusted Wi-Fi
            and non-sensitive test photos.
          </p>
        </footer>
      </main>
    </div>
  );
}
