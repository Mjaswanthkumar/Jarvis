"""``python -m jarvis`` -- start the Jarvis server."""

from __future__ import annotations

import logging

import uvicorn

from jarvis.config import get_settings
from jarvis.logging_setup import configure_logging


def main() -> None:
    settings = get_settings()
    log_path = configure_logging(settings.jarvis_data_dir)
    logging.getLogger(__name__).info("logging to %s", log_path)
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
