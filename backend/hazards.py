"""
Warnings without being asked (bonus): obstacles in the user's path.

A hazard found in an analysed frame becomes a spoken warning only if:
  * it is an OBSTACLE in the walking path (ordinary objects at the side,
    pictures on a wall, signs ... are not hazards);
  * the camera STILL shows that scene: the live view is compared with the
    hazard frame by image fingerprint (dHash) when the alert is created
    and again every time the phone asks for warnings;
  * it is recent: an alert expires after ALERT_TTL_S;
  * the same hazard at the same position was not announced to this
    phone in the last REPEAT_S seconds.

The message is in the user's language (that of their last question),
says where the obstacle is, says so when the position is uncertain, and
never claims that anything is safe.
"""

import time
from typing import Literal

from pydantic import BaseModel, Field

ALERT_TTL_S = 10.0          # a warning older than this is never spoken
MAX_FRAME_AGE_S = 45.0      # older hazard frames are ignored (local analysis takes ~20 s);
                            # freshness itself is guaranteed by the live-view check below
STILL_VISIBLE_BITS = 14     # dHash distance (of 64): live view still shows the hazard scene
REPEAT_S = 45.0             # same hazard + position not repeated within this time


class Hazard(BaseModel):
    label_en: str = Field(description="Short English noun phrase, e.g. 'closed door', 'stairs going down'.")
    label_fr: str = Field(description="The same in French, e.g. 'porte fermée', 'escalier qui descend'.")
    label_ar: str = Field(description="The same in Modern Standard Arabic, e.g. 'باب مغلق', 'درج نازل'.")
    label_lb: str = Field(description="The same in Lebanese Arabic, e.g. 'باب مسكّر', 'درج نازل'.")
    position: Literal["ahead", "left", "right", "unknown"] = Field(description=(
        "Where it is relative to the walking direction; 'unknown' if it cannot be established."))
    in_path: bool = Field(description=(
        "True only if it blocks or is inside the walking path directly ahead or very close "
        "(a closed door ahead, stairs or a step ahead, an object on the floor ahead, a low "
        "obstacle at head height). False for ordinary objects at the side, on a wall or far away."))
    certain: bool = Field(description="False if it is unclear whether it is really an obstacle or where it is.")


HAZARD_FIELD_DESCRIPTION = (
    "Obstacles for a blind pedestrian walking forward, with position and whether they are in the "
    "walking path. Ordinary objects that do not block the way (furniture at the side, a picture or "
    "sign on a wall, a table far away) are NOT hazards. Empty list if there is no obstacle.")

_POSITION = {
    "en": {"ahead": "ahead of you", "left": "on your left", "right": "on your right"},
    "fr": {"ahead": "devant vous", "left": "à votre gauche", "right": "à votre droite"},
    "ar": {"ahead": "أمامك", "left": "على يسارك", "right": "على يمينك"},
    "ar-LB": {"ahead": "قدّامك", "left": "عشمالك", "right": "عيمينك"},
}


def label_for(hazard: dict, language: str) -> str:
    key = {"fr": "label_fr", "ar": "label_ar", "ar-LB": "label_lb"}.get(language, "label_en")
    return (hazard.get(key) or hazard.get("label_en") or "").strip()


def message(hazard: dict, language: str) -> str:
    """The spoken warning; uncertain position -> says so. Never says 'safe'."""
    language = language if language in _POSITION else "en"
    label = label_for(hazard, language)
    known = hazard.get("position") in ("ahead", "left", "right") and hazard.get("certain", True)
    where = _POSITION[language].get(hazard.get("position", ""), "")
    if language == "fr":
        return (f"Attention : {label} {where}." if known else
                f"Attention : {label} possible à proximité. Je ne sais pas exactement où.")
    if language == "ar":
        return (f"انتبه: يوجد {label} {where}." if known else
                f"انتبه: قد يوجد {label} قريبًا، ولست متأكدًا من مكانه.")
    if language == "ar-LB":
        return (f"انتبه: في {label} {where}." if known else
                f"انتبه: في شي {label} قريب، بس مش متأكد وين بالزبط.")
    return (f"Caution: {label} {where}." if known else
            f"Caution: possible {label} nearby. I can't tell exactly where.")


def is_obstacle(hazard: dict) -> bool:
    return bool(hazard.get("in_path")) and bool(hazard.get("label_en", "").strip())


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def still_visible(hazard_hash: int, live_hash: int | None) -> bool:
    return live_hash is not None and hamming(hazard_hash, live_hash) <= STILL_VISIBLE_BITS


class AlertBook:
    """Active warnings per phone: creation rules, expiry, repetition."""

    def __init__(self) -> None:
        self.alerts: list[dict] = []
        self.history: list[dict] = []            # every warning created (dashboard), newest last
        self.last_announced: dict[tuple[str, str, str], float] = {}
        self.stats = {"hazards_seen": 0, "not_obstacle": 0, "stale_frame": 0, "camera_moved": 0,
                      "repeated": 0, "created": 0, "expired": 0, "withdrawn": 0}

    def consider(self, hazard: dict, frame: dict, frame_hash: int, live_hash: int | None,
                 now: float | None = None) -> dict | None:
        """Create an alert from an analysed frame's hazard, or explain why not (stats)."""
        now = now or time.time()
        self.stats["hazards_seen"] += 1
        if not is_obstacle(hazard):
            self.stats["not_obstacle"] += 1
            return None
        if now - frame["captured_at"] > MAX_FRAME_AGE_S:
            self.stats["stale_frame"] += 1
            return None
        if not still_visible(frame_hash, live_hash):
            self.stats["camera_moved"] += 1
            return None
        key = (frame["device"], hazard["label_en"].strip().lower(), hazard.get("position", "unknown"))
        if now - self.last_announced.get(key, 0.0) < REPEAT_S:
            self.stats["repeated"] += 1
            return None
        self.last_announced[key] = now
        alert = {"id": f"{int(now * 1000)}-{len(self.alerts)}", "at": now, "expires_at": now + ALERT_TTL_S,
                 "device": frame["device"], "frame_id": frame["id"], "captured_at": frame["captured_at"],
                 "hazard": hazard["label_en"], "details": hazard, "hash": frame_hash}
        self.alerts = (self.alerts + [alert])[-50:]
        self.history = (self.history + [alert])[-100:]
        self.stats["created"] += 1
        return alert

    def active(self, device: str | None, after: float, live_hash_for, now: float | None = None) -> list[dict]:
        """Unexpired alerts whose scene the camera still shows (re-checked now)."""
        now = now or time.time()
        out = []
        for alert in list(self.alerts):
            if now > alert["expires_at"]:
                self.alerts.remove(alert)
                self.stats["expired"] += 1
                continue
            if alert["at"] <= after or (device is not None and alert["device"] != device):
                continue
            if not still_visible(alert["hash"], live_hash_for(alert["device"])):
                self.alerts.remove(alert)
                self.stats["withdrawn"] += 1
                continue
            out.append(alert)
        return out

    def clear(self, device: str | None = None) -> None:
        self.alerts = [a for a in self.alerts if device is not None and a["device"] != device]
        self.history = [a for a in self.history if device is not None and a["device"] != device]
