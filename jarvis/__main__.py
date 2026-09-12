"""``python -m jarvis`` -- start the Jarvis server."""

from __future__ import annotations

import logging

import uvicorn

from jarvis.config import get_settings


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    settings = get_settings()
    if not settings.jarvis_auth_token:
        raise SystemExit(
            "JARVIS_AUTH_TOKEN is not set. Copy .env.example to .env and set a "
            "long random token before starting the server."
        )
    uvicorn.run(
        "jarvis.api.main:app",
        host=settings.jarvis_host,
        port=settings.jarvis_port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
