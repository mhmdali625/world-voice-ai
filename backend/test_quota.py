"""
Quota management and model fallback, with SIMULATED Gemini errors
(no real request, no quota used).

Checks: per-day 429 -> model marked exhausted until Google's retry time
and never called again; per-minute 429 / 503 / timeout / 404 -> next
model at once; background work never uses the daily reserve kept for
questions; the day count survives a restart and resets on a new Pacific
day; Pacific day boundary with daylight saving; no usable model ->
NoCloudVision quickly, without any request; unchanged refresh frames
reuse their analysis (no request).

Usage: .venv\\Scripts\\python test_quota.py
"""

import asyncio
import json
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from google.genai import errors

import config
import quota
import vision

results: list[tuple[bool, str, str]] = []


def check(ok, name: str, detail: str = "") -> None:
    results.append((bool(ok), name, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}  -> {detail}", flush=True)


def api_error(code: int, message: str) -> errors.APIError:
    return errors.APIError(code, {"error": {"code": code, "message": message, "status": "X"}})


DAILY_429 = ("You exceeded your current quota. quotaId: 'GenerateRequestsPerDayPerProjectPerModel-FreeTier', "
             "quotaValue: '500', retryDelay: '3600s'")


class FakeClient:
    """Each model answers according to `behaviour[model]` (a list consumed in order)."""

    def __init__(self, behaviour: dict[str, list]):
        self.behaviour = behaviour
        self.calls: list[str] = []
        outer = self

        class Models:
            async def generate_content(self, model, contents, config):
                outer.calls.append(model)
                action = outer.behaviour[model].pop(0) if outer.behaviour.get(model) else "ok"
                if action == "ok":
                    return type("R", (), {"text": f"answer from {model}"})()
                if action == "slow":
                    await asyncio.sleep(5)
                    return type("R", (), {"text": "late"})()
                raise action

        self.aio = type("Aio", (), {"models": Models()})()


def fresh_budget(tmp: Path) -> quota.GeminiBudget:
    b = quota.GeminiBudget(state_path=tmp / "quota_state.json")
    vision.budget = b
    vision._unavailable_until.clear()
    vision._cooling_until.clear()
    return b


async def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    config.VISION_MODELS = ["model-a", "model-b", "model-c"]

    print("\n[1] Fallback chain", flush=True)
    b = fresh_budget(tmp)
    c = FakeClient({"model-a": [api_error(429, DAILY_429)]})
    r, model = await vision.generate(c, ["q"], None, "user")
    check(model == "model-b" and b.exhausted("model-a"), "daily quota 429 -> model exhausted, next model answers",
          f"calls {c.calls}")
    c2 = FakeClient({})
    await vision.generate(c2, ["q"], None, "user")
    check(c2.calls == ["model-b"], "exhausted model is never called again", f"calls {c2.calls}")
    left = b.exhausted_until["model-a"] - time.time()
    check(3500 < left <= 3600, "exhausted until Google's retryDelay", f"{left:.0f} s")

    b = fresh_budget(tmp / "2")
    for err, label in ((api_error(503, "high demand"), "503 high demand"),
                       (api_error(429, "per minute quotaId: 'GenerateRequestsPerMinute'"), "per-minute 429"),
                       (api_error(404, "no longer available"), "404 model withdrawn")):
        c = FakeClient({"model-a": [err]})
        _, model = await vision.generate(c, ["q"], None, "user")
        check(model == "model-b" and not b.exhausted("model-a"), f"{label} -> next model, not marked exhausted",
              f"calls {c.calls}")
    vision._unavailable_until.clear()

    b = fresh_budget(tmp / "3")
    c = FakeClient({"model-a": ["slow"]})
    t = time.perf_counter()
    _, model = await vision.generate(c, ["q"], None, "user", attempt_timeout=0.5)
    check(model == "model-b" and time.perf_counter() - t < 1.5, "slow model abandoned after the attempt timeout",
          f"{time.perf_counter() - t:.2f} s")
    c = FakeClient({})
    await vision.generate(c, ["q"], None, "user")
    check(c.calls == ["model-b"], "a model that timed out is paused (not retried on every question)",
          f"calls {c.calls}, cooling {sorted(vision._cooling_until)}")

    b = fresh_budget(tmp / "4")
    for m in config.VISION_MODELS:
        b.mark_exhausted(m, 600)
    c = FakeClient({})
    t = time.perf_counter()
    try:
        await vision.generate(c, ["q"], None, "user")
        ok = False
    except vision.NoCloudVision:
        ok = True
    check(ok and not c.calls and time.perf_counter() - t < 0.1,
          "every model exhausted -> NoCloudVision at once, no request sent", f"calls {c.calls}")

    print("\n[2] Daily budget: questions before background work", flush=True)
    b = fresh_budget(tmp / "5")
    b.last_user_at = 0
    limit = quota.daily_limit("model-a")
    b.daily["model-a"] = {"user": 0, "background": limit - quota.USER_DAILY_RESERVE}
    check(not vision.usable("model-a", "background") and vision.usable("model-a", "user"),
          f"background stops when only the reserve ({quota.USER_DAILY_RESERVE}) is left; questions continue",
          f"used {b.used_today('model-a')}/{limit}")
    b.daily["model-a"]["background"] -= 1
    b.calls.clear()
    check(vision.usable("model-a", "background"), "background allowed above the reserve", "")
    async with b.user_request():
        check(not b.background_allowed("model-a"), "background paused while a question is in flight", "")

    print("\n[3] Persistence and the Pacific day", flush=True)
    path = tmp / "6" / "quota_state.json"
    b = quota.GeminiBudget(state_path=path)
    b.record("user", "model-a")
    b.record("background", "model-a")
    b.mark_exhausted("model-b", 900)
    b2 = quota.GeminiBudget(state_path=path)
    check(b2.used_today("model-a") == 2 and b2.exhausted("model-b"), "day count and exhaustion survive a restart",
          json.loads(path.read_text())["daily"])
    data = json.loads(path.read_text())
    data["day"] = "2000-01-01"
    path.write_text(json.dumps(data))
    b3 = quota.GeminiBudget(state_path=path)
    check(b3.used_today("model-a") == 0, "a new Pacific day starts from zero", "")
    winter = datetime(2026, 12, 2, 7, 59, tzinfo=timezone.utc).timestamp()     # 23:59 PST on Dec 1
    summer = datetime(2026, 7, 2, 6, 59, tzinfo=timezone.utc).timestamp()      # 23:59 PDT on Jul 1
    check(quota.pacific_day(winter) == "2026-12-01" and quota.pacific_day(winter + 120) == "2026-12-02"
          and quota.pacific_day(summer) == "2026-07-01" and quota.pacific_day(summer + 120) == "2026-07-02",
          "quota day changes at midnight Pacific (winter and summer time)", "")

    print("\n[4] Unchanged scene: analysis reused, no request", flush=True)
    import memory_service as m
    m.FRAMES_DIR = tmp / "frames"
    svc = m.MemoryService()
    svc.store = m.MemoryStore(tmp / "mem.db")
    vision.budget = quota.GeminiBudget(state_path=None)
    m.budget = vision.budget
    now = time.time()
    base = {"session": "s", "device": "phone", "width": 10, "height": 10, "sharpness": 1.0, "brightness": 1.0,
            "edges": 1.0, "novelty": 1, "enhanced": "", "faces": 0}
    svc.store._insert_frame({**base, "id": "a" * 32, "captured_at": now - 30, "path": "x", "keep_reason": "new view",
                             "dhash": f"{0x0F0F0F0F0F0F0F0F:016x}"})
    svc.store._save_analysis("a" * 32, {"caption": "A door", "text": "ROOM 12", "objects": ["door"],
                                        "hazards": [], "text_languages": ["en"]}, None)
    svc.store._insert_frame({**base, "id": "b" * 32, "captured_at": now, "path": "x", "keep_reason": "periodic refresh",
                             "dhash": f"{0x0F0F0F0F0F0F0F0E:016x}"})
    svc.store._insert_frame({**base, "id": "c" * 32, "captured_at": now, "path": "x", "keep_reason": "periodic refresh",
                             "dhash": f"{0xF0F0F0F0F0F0F0F0:016x}"})
    rows = svc.store._pending(10)
    remaining = await svc._reuse_unchanged(rows)
    reused = svc.store._frame("b" * 32)
    check([r["id"] for r in remaining] == ["c" * 32] and reused["ocr_text"] == "ROOM 12"
          and reused["analysis_source"] == "reused",
          "same scene -> analysis copied (no API); different scene -> still analysed", "")
    hits = svc.store._fts("ROOM", 5, "phone")
    check(len(hits) == 2, "reused analysis is searchable (keyword index)", f"{len(hits)} hits")
    svc.store.db.close()

    passed = sum(ok for ok, _, _ in results)
    print(f"\n{passed}/{len(results)} quota checks passed")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
