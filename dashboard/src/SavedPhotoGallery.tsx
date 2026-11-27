
import { useEffect, useState } from 'react';
const BACKEND_URL = 'http://127.0.0.1:8000';

type SavedImage = {
  filename: string;
  saved_at: string;
  url: string;
};

type ImageHistoryResponse = {
  images: SavedImage[];
  count: number;
};

export default function SavedPhotoGallery() {
  const [images, setImages] = useState<SavedImage[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    async function loadImages() {
      try {
        const response = await fetch(
          `${BACKEND_URL}/api/images`,
          { cache: 'no-store' }
        );

        if (!response.ok) {
          throw new Error(
            `Backend returned HTTP ${response.status}`
          );
        }

        const data: ImageHistoryResponse =
          await response.json();

        if (!active) return;

        setImages(data.images);
        setTotal(data.count);
        setError(null);
      } catch (err) {
        if (!active) return;

        setError(
          err instanceof Error
            ? err.message
            : 'Could not load saved photos.'
        );
      } finally {
        if (active) setLoading(false);
      }
    }

    void loadImages();

    // Refresh automatically every 5 seconds.
    const timer = setInterval(() => {
      void loadImages();
    }, 5000);

    return () => {
      active = false;
      clearInterval(timer);
    };
  }, []);

  return (
    <section
      className="world-card"
      style={{ marginTop: 22 }}
    >
      <h2 className="world-panel-title">
        Saved Blurred Photos
      </h2>

      <p className="world-muted">
        Photo history from the FastAPI
        backend: only fully blurred copies
        are stored, and they are deleted
        after 30 minutes.
      </p>

      <p className="world-muted">
        Total saved: <strong>{total}</strong>
      </p>

      {loading && (
        <p className="world-muted">
          Loading photo history...
        </p>
      )}

      {error && (
        <p style={{ color: '#fca5a5' }}>
          Could not connect to FastAPI: {error}
        </p>
      )}

      {!loading && !error && images.length === 0 && (
        <p className="world-muted">
          No saved photos yet.
          Analyze a new photo on Android.
        </p>
      )}

      <div
        style={{
          display: 'grid',
          gridTemplateColumns:
            'repeat(auto-fill, minmax(190px, 1fr))',
          gap: 16,
          marginTop: 18,
        }}
      >
        {images.map((image) => (
          <article
            key={image.filename}
            style={{
              background: '#0b1220',
              border: '1px solid #334155',
              borderRadius: 12,
              padding: 12,
            }}
          >
            <img
              src={`${BACKEND_URL}${image.url}`}
              alt="Saved blurred camera photo"
              loading="lazy"
              style={{
                width: '100%',
                height: 180,
                objectFit: 'contain',
                borderRadius: 8,
              }}
            />

            <p
              className="world-muted"
              style={{ fontSize: 12 }}
            >
              Saved:{' '}
              {new Date(
                image.saved_at
              ).toLocaleString()}
            </p>

            <p
              style={{
                color: '#5dd6c0',
                fontSize: 12,
              }}
            >
              Blurred image
            </p>
          </article>
        ))}
      </div>
    </section>
  );
}
