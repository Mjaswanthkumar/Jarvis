"""Shared FastAPI dependencies: auth and singletons."""

from __future__ import annotations

import hmac
from functools import lru_cache

from fastapi import Header, HTTPException, status

from jarvis.agent import JarvisAgent
from jarvis.config import Settings, get_settings
from jarvis.llm.factory import get_provider
from jarvis.storage import Store


@lru_cache(maxsize=1)
def get_store() -> Store:
    return Store(get_settings().db_path)


@lru_cache(maxsize=1)
def get_agent() -> JarvisAgent:
    return JarvisAgent(get_provider())


def require_auth(
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
    presented = x_jarvis_token or ""
    if not presented and authorization and authorization.lower().startswith("bearer "):
        presented = authorization[7:].strip()
    if not presented or not hmac.compare_digest(presented, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing Jarvis token",
            headers={"WWW-Authenticate": "Bearer"},
        )
