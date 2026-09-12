"""``python -m jarvis`` -- start the Jarvis server."""

from __future__ import annotations

import logging

import uvicorn

from jarvis.config import Settings, get_settings
from jarvis.logging_setup import configure_logging
from jarvis.network import (
    MIN_REMOTE_TOKEN_LENGTH,
    is_loopback,
    pairing_info,
    startup_banner,
)


def check_startup_security(settings: Settings) -> None:
    """Refuse to start in a configuration that would expose the PC unsafely."""
    if not settings.jarvis_auth_token:
        raise SystemExit(
            "JARVIS_AUTH_TOKEN is not set. Copy .env.example to .env and set a "
            "long random token before starting the server."
        )
    if is_loopback(settings.jarvis_host):
        return
    if len(settings.jarvis_auth_token) < MIN_REMOTE_TOKEN_LENGTH:
        raise SystemExit(
            f"JARVIS_HOST={settings.jarvis_host} exposes this PC to the network, "
            f"but JARVIS_AUTH_TOKEN is only {len(settings.jarvis_auth_token)} "
            f"characters. Use at least {MIN_REMOTE_TOKEN_LENGTH}:\n"
            '  python -c "import secrets; print(secrets.token_urlsafe(32))"'
        )


def main() -> None:
    settings = get_settings()
    check_startup_security(settings)

    log_path = configure_logging(settings.jarvis_data_dir)
    logging.getLogger(__name__).info("logging to %s", log_path)

    info = pairing_info(
        settings.jarvis_host, settings.jarvis_port, settings.jarvis_auth_token
    )
    print(startup_banner(info, settings.jarvis_auth_token), flush=True)

    uvicorn.run(
        "jarvis.api.main:app",
        host=settings.jarvis_host,
        port=settings.jarvis_port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
