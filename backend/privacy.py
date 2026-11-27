"""
Face privacy for stored images (course ground rule: no face recognition,
keep only what is needed).

Live Assistant frames are stored for the visual memory, so every face
in them - real people AND faces in portraits, posters or screens - is
made unrecognisable BEFORE the frame is written to disk. Only the face
regions change: signs, text and objects keep their exact pixels, so OCR
and object detection are not affected.

Detector: OpenCV YuNet (face_detection_yunet_2023mar.onnx, MIT licence,
opencv_zoo), run locally. It only finds where faces are; nothing
identifies anyone.

Fail-safe: if detection cannot run, the caller must NOT store the frame
(protect_faces raises FacePrivacyError).

Photo Mode stores fully blurred photos (image_storage.py); both stores
are deleted automatically after 30 minutes.
"""

import os
import threading
import time
from pathlib import Path

from PIL import Image, ImageFilter

MODEL_PATH = Path(os.getenv("WV_FACE_MODEL") or Path(__file__).resolve().parent / "models"
                  / "face_detection_yunet_2023mar.onnx")
# Much lower than YuNet's default 0.9: a missed face is a privacy leak, a
# false alarm only blurs a small patch of wall. Measured: 0.5 also finds a
# man turned away in near-profile (score 0.58) and crowd faces, with 0 false
# alarms on face-free sign/door/wall frames (test_privacy.py).
SCORE_THRESHOLD = float(os.getenv("WV_FACE_SCORE", "0.5"))
PAD = 0.35                 # enlarge each box (hair, ears, chin)
MAX_SIDE = int(os.getenv("WV_FACE_DETECT_SIDE", "768"))   # detection copy (blur applies at full size)
MAX_PASSES = 3             # detect again after blurring until nothing is found


class FacePrivacyError(RuntimeError):
    """Faces could not be checked: the image must not be stored."""


_lock = threading.Lock()
_detector = None
stats = {"frames": 0, "faces": 0, "frames_with_faces": 0, "failures": 0, "last_ms": None}


def _get_detector():
    global _detector
    if _detector is None:
        import cv2  # imported lazily: the server starts even if OpenCV is missing
        if not MODEL_PATH.is_file():
            raise FacePrivacyError(f"face model missing: {MODEL_PATH}")
        _detector = cv2.FaceDetectorYN.create(str(MODEL_PATH), "", (320, 320), SCORE_THRESHOLD, 0.3, 5000)
    return _detector


def available() -> bool:
    try:
        with _lock:
            _get_detector()
        return True
    except Exception:
        return False


def detect_faces(image: Image.Image) -> list[tuple[int, int, int, int, float]]:
    """Face boxes (x, y, w, h, score) in image pixels."""
    import cv2
    import numpy as np

    rgb = image.convert("RGB")
    scale = 1.0
    if max(rgb.size) > MAX_SIDE:
        scale = MAX_SIDE / max(rgb.size)
        rgb = rgb.resize((round(rgb.width * scale), round(rgb.height * scale)))
    bgr = cv2.cvtColor(np.asarray(rgb), cv2.COLOR_RGB2BGR)
    with _lock:                                   # the detector object is not thread-safe
        detector = _get_detector()
        detector.setInputSize((bgr.shape[1], bgr.shape[0]))
        _, faces = detector.detect(bgr)
    boxes = []
    if faces is not None:
        for f in faces:
            x, y, w, h, score = float(f[0]), float(f[1]), float(f[2]), float(f[3]), float(f[-1])
            boxes.append((round(x / scale), round(y / scale), round(w / scale), round(h / scale), score))
    return boxes


def blur_region(image: Image.Image, box: tuple[int, int, int, int]) -> None:
    """Irreversibly anonymise one region in place: pixelate, then blur."""
    x, y, w, h = box
    region = image.crop((x, y, x + w, y + h))
    small = region.resize((max(1, w // 12), max(1, h // 12)), Image.Resampling.BILINEAR)
    region = small.resize((w, h), Image.Resampling.NEAREST).filter(ImageFilter.GaussianBlur(max(2, w // 10)))
    image.paste(region, (x, y))


def protect_faces(image: Image.Image) -> tuple[Image.Image, int]:
    """
    Copy of the image with every detected face anonymised, and the number
    of faces. Raises FacePrivacyError if faces could not be checked.
    """
    started = time.perf_counter()
    out = image.convert("RGB").copy()
    total = 0
    try:
        # Blurring one face changes its surroundings, which can lift a
        # borderline face (e.g. in a crowd) over the threshold: check the
        # protected image again until no face is detectable (max 3 passes).
        for _ in range(MAX_PASSES):
            boxes = detect_faces(out)
            if not boxes:
                break
            total += len(boxes)
            for x, y, w, h, _score in boxes:
                px, py = round(w * PAD), round(h * PAD)
                left, top = max(0, x - px), max(0, y - py)
                right, bottom = min(out.width, x + w + px), min(out.height, y + h + py)
                if right > left and bottom > top:
                    blur_region(out, (left, top, right - left, bottom - top))
        else:
            if detect_faces(out):
                raise FacePrivacyError("faces still detectable after protection")
    except FacePrivacyError:
        stats["failures"] += 1
        raise
    except Exception as error:
        stats["failures"] += 1
        raise FacePrivacyError(f"face detection failed: {type(error).__name__}: {error}") from error
    stats["frames"] += 1
    stats["faces"] += total
    stats["frames_with_faces"] += bool(total)
    stats["last_ms"] = round((time.perf_counter() - started) * 1000, 1)
    return out, total
