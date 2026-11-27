
import { useEffect, useState } from 'react';

type RelayPhoto = {
  capturedAt: string;
  dataUrl: string;
};

export default function LatestPhoto() {
  const [photo, setPhoto] = useState<RelayPhoto | null>(null);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    let active = true;

    const fetchLatestPhoto = async () => {
      try {
        const response = await fetch(
          'http://localhost:3001/api/photo',
          { cache: 'no-store' }
        );

        if (!response.ok) {
          throw new Error('Relay unavailable');
        }

        const result: { photo: RelayPhoto | null } =
          await response.json();

        if (!active) return;

        setPhoto(result.photo);
        setConnected(true);
      } catch {
        if (active) {
          setConnected(false);
        }
      }
    };

    void fetchLatestPhoto();

    // Check for new photos every 3 seconds
    const interval = setInterval(() => {
      void fetchLatestPhoto();
    }, 3000);

    return () => {
      active = false;
      clearInterval(interval);
    };
  }, []);

  return (
    <section
      style={{
        backgroundColor: '#1E293B',
        borderRadius: 14,
        padding: 20,
        marginTop: 20,
        color: 'white',
      }}
    >
      <h2>Latest Mobile Camera Photo</h2>

      <p style={{ color: connected ? '#5DD6C0' : '#FCA5A5' }}>
        {connected
          ? '● Relay Connected'
          : '● Relay Disconnected'}
      </p>

      {photo ? (
        <>
          <img
            src={photo.dataUrl}
            alt="Latest photo captured by the mobile app"
            style={{
              width: '100%',
              maxHeight: 420,
              objectFit: 'contain',
              borderRadius: 12,
              backgroundColor: '#0F172A',
            }}
          />

          <p style={{ color: '#CBD5E1' }}>
            Received:{' '}
            {new Date(photo.capturedAt).toLocaleString()}
          </p>
        </>
      ) : (
        <p style={{ color: '#CBD5E1' }}>
          No photo received yet. Capture and send a photo
          from your mobile app.
        </p>
      )}
    </section>
  );
}
