"""Logging configuration: console plus a rotating file in the data directory."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_MAX_BYTES = 2 * 1024 * 1024
_BACKUPS = 3


def configure_logging(data_dir: Path, level: int = logging.INFO) -> Path:
    """Attach console + rotating file handlers once, and return the log path."""
    data_dir.mkdir(parents=True, exist_ok=True)
    log_path = data_dir / "jarvis.log"

    root = logging.getLogger()
    root.setLevel(level)
    if any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        return log_path

    formatter = logging.Formatter(_FORMAT)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        log_path, maxBytes=_MAX_BYTES, backupCount=_BACKUPS, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # uvicorn installs its own handlers; let records propagate to ours too.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).propagate = True

    return log_path
