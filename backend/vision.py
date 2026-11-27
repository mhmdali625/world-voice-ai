"""
One entry point for every Gemini vision request (live answers, photo
mode, background frame analysis): picks the model, keeps the quota
books, and falls back when a model cannot answer.

    WV_VISION_MODELS = gemini-3.5-flash-lite,gemini-3.1-flash-lite,gemini-3.5-flash

Order of the chain = order of preference. For each request:
  * skip a model marked EXHAUSTED (per-day 429 until Google's retry time)
    or UNAVAILABLE (404: model withdrawn);
  * background work skips a model whose day is down to the reserve for
    questions (quota.USER_DAILY_RESERVE);
  * on a per-day 429 -> mark exhausted, try the next model at once;
    on a per-minute 429, 5xx "high demand" or timeout -> try the next model;
  * if no model can answer -> NoCloudVision (callers then use the local
    model or say clearly that the service is unavailable).
"""

import asyncio
import re
import time

import httpx

from google.genai import errors

import config
from neterrors import is_network_error, note_network_failure, note_network_success
from quota import budget

_unavailable_until: dict[str, float] = {}
# A model that timed out or answered 5xx for a waiting user is skipped for a
# while instead of costing every question another 12 s (measured today:
# gemini-3.1-flash-lite and gemini-3.5-flash timing out / 503 again and again).
COOL_DOWN_S = 600.0
_cooling_until: dict[str, float] = {}
last_model: dict[str, str] = {}               # kind -> model that answered last
events: list[dict] = []                       # recent fallbacks, for the dashboard


class NoCloudVision(RuntimeError):
    """No Gemini model could answer (quota, outage or timeout)."""


def chain() -> list[str]:
    return list(config.VISION_MODELS)


def usable(model: str, kind: str) -> bool:
    if _unavailable_until.get(model, 0) > time.time() or budget.exhausted(model):
        return False
    if _cooling_until.get(model, 0) > time.time():
        return False
    if kind == "background":
        return budget.remaining_today(model) > 0 and budget.background_allowed(model)
    return True


def _retry_seconds(error: errors.APIError) -> float | None:
    text = str(error)
    m = re.search(r"retryDelay['\"]?:\s*['\"](\d+(?:\.\d+)?)s", text)
    return float(m.group(1)) if m else None


def _note(kind: str, model: str, reason: str) -> None:
    events.append({"at": time.time(), "kind": kind, "model": model, "reason": reason})
    del events[:-30]
    print(f"[VISION] {kind}: {model} skipped ({reason})", flush=True)


async def generate(client, contents, generation_config, kind: str = "user",
                   attempt_timeout: float | None = None, models: list[str] | None = None):
    """
    Returns (response, model). Raises NoCloudVision when no model can answer.
    `kind`: "user" (a person is waiting) or "background".
    `models`: only these models of the chain (default: the whole chain).
    """
    tried = []
    for model in (models if models is not None else chain()):
        if not usable(model, kind):
            continue
        tried.append(model)
        budget.record(kind, model)
        try:
            call = client.aio.models.generate_content(model=model, contents=contents, config=generation_config)
            response = await (asyncio.wait_for(call, attempt_timeout) if attempt_timeout else call)
            if len(tried) > 1:
                budget.stats["fallbacks"] += 1
            last_model[kind] = model
            note_network_success()
            return response, model
        except asyncio.TimeoutError:
            _cooling_until[model] = time.time() + COOL_DOWN_S
            _note(kind, model, f"no answer within {attempt_timeout:.0f} s; paused {COOL_DOWN_S / 60:.0f} min")
        except errors.APIError as error:
            text = str(error)
            if error.code == 429 and "PerDay" in text:
                budget.mark_exhausted(model, _retry_seconds(error))
                _note(kind, model, "daily quota exhausted")
            elif error.code == 429:
                budget.rate_limited(30)
                _note(kind, model, "per-minute limit")
            elif error.code == 404:
                _unavailable_until[model] = time.time() + 24 * 3600
                _note(kind, model, "model not available")
            elif error.code in (500, 502, 503, 504):
                if model != chain()[0]:       # the main model keeps its hedged retries
                    _cooling_until[model] = time.time() + COOL_DOWN_S
                _note(kind, model, f"HTTP {error.code} (temporary)")
            else:
                raise
        except Exception as error:  # noqa: BLE001
            if not is_network_error(error):
                raise
            # No internet / connection reset: not the model's fault. Skip it briefly
            # (30 s, so it is tried again soon after the connection returns).
            _cooling_until[model] = time.time() + 30.0
            _note(kind, model, f"network: {type(error).__name__}")
            note_network_failure()
    raise NoCloudVision(f"no Gemini model could answer (tried: {', '.join(tried) or 'none usable'})")


def answer_chain() -> list[dict]:
    """The order a waiting user's question is answered in (assistant_service)."""
    import groq_vision
    import local_ai
    rows = [{"name": chain()[0], "provider": "gemini", "usable": usable(chain()[0], "user")}]
    rows.append({"name": groq_vision.BUDGET_NAME, "provider": "groq", "usable": groq_vision.usable()})
    rows += [{"name": m, "provider": "gemini", "usable": usable(m, "user")} for m in chain()[1:]]
    local = local_ai._health.get(local_ai.VLM_URL)                       # cached /health of the local model
    rows.append({"name": "local (Qwen, offline)", "provider": "local", "usable": bool(local and local[1])})
    return rows


def status() -> dict:
    return {"chain": chain(), "answer_chain": answer_chain(),
            "usable_for_questions": [m for m in chain() if usable(m, "user")],
            "cooling": {m: round(t - time.time()) for m, t in _cooling_until.items() if t > time.time()},
            "last_model": dict(last_model), "recent_fallbacks": events[-8:]}
