
import os
import time
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from PIL import (
    Image,
    ImageFilter,
    ImageOps,
    UnidentifiedImageError,
)

# Saved blurred photos will be stored here.
STORAGE_DIR = Path(
    os.getenv("WV_PHOTO_DIR")
    or Path(__file__).resolve().parent / "storage" / "blurred"
)

MAX_IMAGE_PIXELS = 20_000_000

# Blurred photos are deleted automatically after this many minutes
# (same rule as the Live Assistant memory: keep only what is needed).
PHOTO_RETENTION_S = float(os.getenv("WV_PHOTO_RETENTION_MIN", "30")) * 60


def delete_old_photos(max_age_s: float = PHOTO_RETENTION_S) -> int:
    """Delete saved blurred photos older than max_age_s. Returns the count."""
    if not STORAGE_DIR.exists():
        return 0
    cutoff = time.time() - max_age_s
    deleted = 0
    for file in STORAGE_DIR.glob("*.jpg"):
        try:
            if file.stat().st_mtime < cutoff:
                file.unlink()
                deleted += 1
        except OSError:
            pass
    return deleted


def save_blurred_photo(image_bytes: bytes) -> str:
    """
    Save a blurred copy of a photo.

    The original image is not written to disk
    by this function.

    Returns the saved filename.
    """

    try:
        with Image.open(BytesIO(image_bytes)) as original:

            if original.width * original.height > MAX_IMAGE_PIXELS:
                raise ValueError("Image dimensions are too large.")

            # Correct image orientation.
            photo = ImageOps.exif_transpose(original)

            # Remove transparency and prepare JPEG.
            photo = photo.convert("RGB")

            # Reduce resolution before storage.
            photo.thumbnail(
                (1280, 1280),
                Image.Resampling.LANCZOS,
            )

            # Apply strong blur to the whole image.
            blurred = photo.filter(
                ImageFilter.GaussianBlur(radius=30)
            )

            STORAGE_DIR.mkdir(
                parents=True,
                exist_ok=True,
            )

            filename = f"{uuid4().hex}.jpg"
            destination = STORAGE_DIR / filename

            # Save pixels only, without original EXIF metadata.
            blurred.save(
                destination,
                format="JPEG",
                quality=80,
            )

            return filename

    except (UnidentifiedImageError, OSError) as error:
        raise ValueError(
            "Could not process the uploaded image."
        ) from error
