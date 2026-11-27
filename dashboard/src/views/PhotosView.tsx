import SavedPhotoGallery from '../SavedPhotoGallery';

// Photo mode (Adam's gallery): fully blurred copies of the photos analysed
// in photo mode, deleted automatically after 30 minutes.

export default function PhotosView() {
  return (
    <section className="card" aria-labelledby="photos-title">
      <h2 id="photos-title">Photo mode</h2>
      <p className="muted" style={{ marginBottom: 14 }}>
        Photos captured in photo mode are analysed once; only a fully blurred copy is stored, for 30 minutes.
      </p>
      <SavedPhotoGallery />
    </section>
  );
}
