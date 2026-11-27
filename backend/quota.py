"""
Gemini quota governor: per-minute AND per-day, per model.

Measured on the free tier (Google's own 429 messages):
  * 15 requests per minute per vision model;
  * 500 requests per DAY per model (quota id
    GenerateRequestsPerDayPerProjectPerModel-FreeTier), per project,
    reset at midnight Pacific time.

User questions (/ask, /analyze) and background frame analysis share
these budgets, so:
  * every request is recorded per model, in a 60 s window and per day
    (the day count is saved in storage/quota_state.json, so a restart
    does not forget it);
  * user requests are never blocked here (the user is waiting);
  * background analysis runs only when no question is in flight or was
    asked in the last 3 s, at least RESERVE requests of the minute remain,
    no 429 back-off is active, AND the day still has more than the daily
    reserve for questions (WV_USER_DAILY_RESERVE) left on that model;
  * a per-day 429 marks the model EXHAUSTED until Google's retry time, so
    no further request is wasted on it; other models are tried instead.
"""

import json
import os
import time
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

PER_MINUTE = 15
RESERVE_FOR_USER = 6          # background may use at most 9 / minute
QUIET_AFTER_USER_S = 3.0

DAILY_LIMIT = int(os.getenv("WV_GEMINI_RPD", "500"))                 # per model, measured
_PRESENTATION = os.getenv("WV_PRESENTATION_MODE", "0").strip() in {"1", "true", "yes"}
USER_DAILY_RESERVE = int(os.getenv("WV_USER_DAILY_RESERVE", "350" if _PRESENTATION else "250"))  # never used by background work
STATE_PATH = Path(os.getenv("WV_QUOTA_STATE") or Path(__file__).resolve().parent / "storage" / "quota_state.json")


def pacific_day(now: float | None = None) -> str:
    """Date in US Pacific time (Google's quota day), without a tz database."""
    utc = datetime.fromtimestamp(now or time.time(), tz=timezone.utc)
    year = utc.year
    # DST: second Sunday of March 10:00 UTC -> first Sunday of November 09:00 UTC.
    march = datetime(year, 3, 8, 10, tzinfo=timezone.utc)
    dst_start = march + timedelta(days=(6 - march.weekday()) % 7)
    november = datetime(year, 11, 1, 9, tzinfo=timezone.utc)
    dst_end = november + timedelta(days=(6 - november.weekday()) % 7)
    offset = -7 if dst_start <= utc < dst_end else -8
    return (utc + timedelta(hours=offset)).date().isoformat()


# Measured daily limits of other providers (response headers).
KNOWN_DAILY_LIMITS = {"groq:qwen/qwen3.8-27b": 1000}


def daily_limit(model: str) -> int:
    key = "WV_GEMINI_RPD_" + "".join(ch if ch.isalnum() else "_" for ch in model.upper())
    return int(os.getenv(key, str(KNOWN_DAILY_LIMITS.get(model, DAILY_LIMIT))))


class GeminiBudget:
    def __init__(self, per_minute: int = PER_MINUTE, reserve: int = RESERVE_FOR_USER,
                 state_path: Path | None = STATE_PATH) -> None:
        self.per_minute = per_minute
        self.reserve = reserve
        self.calls: deque[tuple[float, str, str]] = deque()      # (monotonic, kind, model)
        self.user_inflight = 0
        self.last_user_at = 0.0
        self.blocked_until = 0.0
        self.stats = {"user": 0, "background": 0, "background_deferred": 0, "rate_limited": 0,
                      "daily_exhausted": 0, "fallbacks": 0, "reused_analysis": 0}
        self.state_path = state_path
        self.day = pacific_day()
        self.daily: dict[str, dict[str, int]] = {}                # model -> {"user": n, "background": n}
        self.exhausted_until: dict[str, float] = {}               # model -> unix time
        self._load()

    # ---------------- persistence ----------------

    def _load(self) -> None:
        if not self.state_path or not self.state_path.is_file():
            return
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            if data.get("day") == self.day:
                self.daily = data.get("daily", {})
            self.exhausted_until = {m: t for m, t in data.get("exhausted_until", {}).items() if t > time.time()}
        except (OSError, ValueError):
            pass

    def _save(self) -> None:
        if not self.state_path:
            return
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps({"day": self.day, "daily": self.daily,
                                                   "exhausted_until": self.exhausted_until}), encoding="utf-8")
        except OSError:
            pass

    def _roll_day(self) -> None:
        today = pacific_day()
        if today != self.day:
            self.day, self.daily = today, {}
            self.exhausted_until = {m: t for m, t in self.exhausted_until.items() if t > time.time()}

    # ---------------- accounting ----------------

    def _prune(self, now: float) -> None:
        while self.calls and now - self.calls[0][0] > 60:
            self.calls.popleft()

    def used_last_minute(self, model: str | None = None) -> int:
        self._prune(time.monotonic())
        return sum(1 for _, _, m in self.calls if model is None or m == model)

    def used_today(self, model: str) -> int:
        self._roll_day()
        counts = self.daily.get(model, {})
        return counts.get("user", 0) + counts.get("background", 0)

    def remaining_today(self, model: str) -> int:
        return max(0, daily_limit(model) - self.used_today(model))

    def record(self, kind: str, model: str = "default") -> None:
        now = time.monotonic()
        self._prune(now)
        self._roll_day()
        self.calls.append((now, kind, model))
        self.stats[kind] = self.stats.get(kind, 0) + 1
        counts = self.daily.setdefault(model, {"user": 0, "background": 0})
        counts[kind] = counts.get(kind, 0) + 1
        self._save()

    # ---------------- decisions ----------------

    def exhausted(self, model: str) -> bool:
        until = self.exhausted_until.get(model)
        return bool(until and until > time.time())

    def mark_exhausted(self, model: str, retry_after_s: float | None) -> None:
        """A per-day 429: no request to this model until Google's retry time."""
        self.exhausted_until[model] = time.time() + (retry_after_s or 3600.0)
        self.stats["daily_exhausted"] += 1
        self._save()

    def background_allowed(self, model: str | None = None) -> bool:
        now = time.monotonic()
        self._prune(now)
        allowed = (
            now >= self.blocked_until
            and self.user_inflight == 0
            and now - self.last_user_at >= QUIET_AFTER_USER_S
            and self.used_last_minute(model) < self.per_minute - self.reserve
            and (model is None or (not self.exhausted(model)
                                   and self.remaining_today(model) > USER_DAILY_RESERVE))
        )
        if not allowed:
            self.stats["background_deferred"] += 1
        return allowed

    def rate_limited(self, seconds: float = 30.0) -> None:
        """Back off background work after a per-minute 429."""
        self.blocked_until = max(self.blocked_until, time.monotonic() + seconds)
        self.stats["rate_limited"] += 1

    @asynccontextmanager
    async def user_request(self):
        self.user_inflight += 1
        self.last_user_at = time.monotonic()
        try:
            yield
        finally:
            self.user_inflight -= 1
            self.last_user_at = time.monotonic()

    def status(self) -> dict:
        self._roll_day()
        models = sorted(set(self.daily) | set(self.exhausted_until))
        return {"used_last_minute": self.used_last_minute(), "per_minute": self.per_minute,
                "reserved_for_user": self.reserve, "user_inflight": self.user_inflight,
                "background_paused_s": round(max(0.0, self.blocked_until - time.monotonic()), 1),
                "day": self.day, "user_daily_reserve": USER_DAILY_RESERVE,
                "models": {m: {"used_today": self.used_today(m), "daily_limit": daily_limit(m),
                               "exhausted": self.exhausted(m),
                               "available_again_in_s": round(max(0.0, self.exhausted_until.get(m, 0) - time.time()))}
                           for m in models},
                **self.stats}


budget = GeminiBudget()
