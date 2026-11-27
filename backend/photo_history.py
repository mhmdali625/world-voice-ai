
import re
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from image_storage import STORAGE_DIR

router = APIRouter(
    prefix="/api/images",
    tags=["Saved Blurred Images"],
)

# Only accept filenames created by our storage system.
VALID_FILENAME = re.compile(r"^[0-9a-f]{32}\.jpg$")


@router.get("")
def list_saved_images():
    if not STORAGE_DIR.exists():
        return {"images": [], "count": 0}

    files = sorted(
        (
            file
            for file in STORAGE_DIR.glob("*.jpg")
            if VALID_FILENAME.fullmatch(file.name)
        ),
        key=lambda file: file.stat().st_mtime,
        reverse=True,
    )

    images = []

    for file in files[:100]:
        saved_at = datetime.fromtimestamp(
            file.stat().st_mtime,
            tz=timezone.utc,
        ).isoformat()

        images.append({
            "filename": file.name,
            "saved_at": saved_at,
            "url": f"/api/images/{file.name}",
        })

    return {
        "images": images,
        "count": len(files),
    }


@router.get("/{filename}")
def get_saved_image(filename: str):
    if not VALID_FILENAME.fullmatch(filename):
        raise HTTPException(
            status_code=404,
            detail="Image not found.",
        )

    path = STORAGE_DIR / filename

    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail="Image not found.",
        )

    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
