"""Shared FastAPI dependencies: auth, throttling and singletons."""

from __future__ import annotations

import hmac
import logging
import threading
import time
from collections import defaultdict, deque
from functools import lru_cache

from fastapi import Header, HTTPException, Request, status

from jarvis.agent import JarvisAgent
from jarvis.config import Settings, get_settings
from jarvis.llm.factory import get_provider
from jarvis.storage import Store

logger = logging.getLogger(__name__)

#: Once Jarvis is reachable from the network, a shared token is the only thing
#: between a stranger and this PC -- so failed attempts are rate limited.
MAX_FAILURES = 8
FAILURE_WINDOW_SECONDS = 300.0
LOCKOUT_SECONDS = 300.0


class AuthThrottle:
    """Per-client sliding window over failed authentication attempts."""

    def __init__(
        self,
        max_failures: int = MAX_FAILURES,
        window: float = FAILURE_WINDOW_SECONDS,
        lockout: float = LOCKOUT_SECONDS,
    ) -> None:
        self._max_failures = max_failures
        self._window = window
        self._lockout = lockout
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._locked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def locked_for(self, client: str, now: float | None = None) -> float:
        """Seconds remaining in this client's lockout (0 when not locked)."""
        now = time.monotonic() if now is None else now
        with self._lock:
            until = self._locked_until.get(client, 0.0)
        return max(0.0, until - now)

    def record_failure(self, client: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            attempts = self._failures[client]
            attempts.append(now)
            while attempts and now - attempts[0] > self._window:
                attempts.popleft()
            if len(attempts) >= self._max_failures:
                self._locked_until[client] = now + self._lockout
                attempts.clear()
                logger.warning(
                    "locking out %s for %.0fs after repeated auth failures",
                    client,
                    self._lockout,
                )

    def record_success(self, client: str) -> None:
        with self._lock:
            self._failures.pop(client, None)
            self._locked_until.pop(client, None)

    def reset(self) -> None:
        with self._lock:
            self._failures.clear()
            self._locked_until.clear()


THROTTLE = AuthThrottle()


@lru_cache(maxsize=1)
def get_store() -> Store:
    return Store(get_settings().db_path)


@lru_cache(maxsize=1)
def get_agent() -> JarvisAgent:
    return JarvisAgent(get_provider())


def require_auth(
    request: Request,
    authorization: str | None = Header(default=None),
    x_jarvis_token: str | None = Header(default=None),
) -> None:
    """Reject every request that does not carry the shared secret.

    Jarvis controls a real machine, so there is no anonymous mode: a missing
    server-side token fails closed rather than open.
    """
    settings: Settings = get_settings()
    expected = settings.jarvis_auth_token
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="JARVIS_AUTH_TOKEN is not configured on the server",
        )

    client = request.client.host if request.client else "unknown"
    remaining = THROTTLE.locked_for(client)
    if remaining > 0:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many failed attempts; try again later",
            headers={"Retry-After": str(int(remaining) + 1)},
        )

    presented = x_jarvis_token or ""
    if not presented and authorization and authorization.lower().startswith("bearer "):
        presented = authorization[7:].strip()

    if not presented or not hmac.compare_digest(presented, expected):
        THROTTLE.record_failure(client)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing Jarvis token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    THROTTLE.record_success(client)
