from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("JARVIS_AUTH_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "")


@pytest.fixture()
def store(tmp_path: Path):
    from jarvis.storage import Store

    instance = Store(tmp_path / "test.db")
    yield instance
    instance.close()
