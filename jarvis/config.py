"""Runtime configuration, loaded from the environment / .env file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings. Every value is overridable via environment."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")
    gemini_model: str = Field(default="gemini-2.0-flash", alias="GEMINI_MODEL")

    jarvis_auth_token: str = Field(default="", alias="JARVIS_AUTH_TOKEN")
    jarvis_host: str = Field(default="127.0.0.1", alias="JARVIS_HOST")
    jarvis_port: int = Field(default=8010, alias="JARVIS_PORT")

    #: Filesystem roots Jarvis is allowed to inspect. Empty -> user home only.
    jarvis_allowed_roots: list[Path] = Field(
        default_factory=list, alias="JARVIS_ALLOWED_ROOTS"
    )

    #: Where the SQLite conversation/audit database lives.
    jarvis_data_dir: Path = Field(
        default_factory=lambda: Path.home() / ".jarvis", alias="JARVIS_DATA_DIR"
    )

    @field_validator("jarvis_allowed_roots", mode="before")
    @classmethod
    def _split_roots(cls, value: object) -> object:
        if isinstance(value, str):
            return [Path(p.strip()) for p in value.split(",") if p.strip()]
        return value

    @property
    def allowed_roots(self) -> list[Path]:
        """Resolved roots Jarvis may touch; defaults to the user's home dir."""
        roots = self.jarvis_allowed_roots or [Path.home()]
        return [r.expanduser().resolve() for r in roots]

    @property
    def db_path(self) -> Path:
        self.jarvis_data_dir.mkdir(parents=True, exist_ok=True)
        return self.jarvis_data_dir / "jarvis.db"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
