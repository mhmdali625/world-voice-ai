"""
Is this exception a network failure (no internet, connection reset, DNS,
TLS)? The cloud SDKs use different HTTP stacks (httpx, and the Gemini SDK
its own copy "httpcore2"/"httpx2"), so the check is by origin, not by class.
"""

NETWORK_MODULES = {"httpx", "httpcore", "httpx2", "httpcore2", "anyio", "ssl", "socket", "h11", "h2"}


def is_network_error(error: BaseException) -> bool:
    if isinstance(error, (OSError, ConnectionError, TimeoutError)):
        return True
    for cls in type(error).__mro__:
        root = (cls.__module__ or "").split(".")[0]
        if root in NETWORK_MODULES:
            return True
    return False


# ---------------- "probably offline" ----------------
# After a network failure, cloud steps that cannot succeed (cloud speech
# recognition, Groq, other Gemini models, cloud voices) are skipped for a
# while and the local services answer at once. Any cloud success clears it.
import time as _time

OFFLINE_HOLD_S = 30.0
_last_failure = 0.0
_last_success = 0.0


def note_network_failure() -> None:
    global _last_failure
    _last_failure = _time.time()


def note_network_success() -> None:
    global _last_success
    _last_success = _time.time()


def probably_offline() -> bool:
    return _last_failure > _last_success and _time.time() - _last_failure < OFFLINE_HOLD_S

