"""Runtime configuration, loaded from the environment / .env file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings. Every value is overridable via environment."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")
    gemini_model: str = Field(default="gemini-3.5-flash-lite", alias="GEMINI_MODEL")

    jarvis_auth_token: str = Field(default="", alias="JARVIS_AUTH_TOKEN")
    jarvis_host: str = Field(default="127.0.0.1", alias="JARVIS_HOST")
    jarvis_port: int = Field(default=8010, alias="JARVIS_PORT")

    #: Comma-separated filesystem roots Jarvis may inspect. Empty -> home only.
    jarvis_allowed_roots: str = Field(default="", alias="JARVIS_ALLOWED_ROOTS")

    #: Optional TLS. Browsers only allow microphone access on a secure origin,
    #: so voice input from a phone needs these set.
    jarvis_tls_certfile: str = Field(default="", alias="JARVIS_TLS_CERTFILE")
    jarvis_tls_keyfile: str = Field(default="", alias="JARVIS_TLS_KEYFILE")

    #: Tools to disable entirely, by name (comma-separated).
    jarvis_blocked_tools: str = Field(default="", alias="JARVIS_BLOCKED_TOOLS")

    #: CONFIRM_REQUIRED tools pre-approved by configuration (comma-separated).
    jarvis_auto_approve_tools: str = Field(
        default="", alias="JARVIS_AUTO_APPROVE_TOOLS"
    )

    #: Raise a native desktop notification when a watch fires, so an alert
    #: reaches the user with no page open.
    jarvis_desktop_notifications: bool = Field(
        default=True, alias="JARVIS_DESKTOP_NOTIFICATIONS"
    )

    #: How reading untrusted content affects later actions in the same turn:
    #: "strict"     - any untrusted content escalates LOW_RISK to CONFIRM_REQUIRED
    #: "suspicious" - only content that trips the injection detector escalates
    #: "off"        - no escalation (not recommended)
    jarvis_taint_mode: str = Field(default="strict", alias="JARVIS_TAINT_MODE")

    #: When true, only READ_ONLY tools may run -- nothing can change the machine.
    jarvis_read_only_mode: bool = Field(default=False, alias="JARVIS_READ_ONLY_MODE")

    #: Where the SQLite conversation/audit database lives.
    jarvis_data_dir: Path = Field(
        default_factory=lambda: Path.home() / ".jarvis", alias="JARVIS_DATA_DIR"
    )

    @property
    def tls_enabled(self) -> bool:
        return bool(self.jarvis_tls_certfile and self.jarvis_tls_keyfile)

    @property
    def scheme(self) -> str:
        return "https" if self.tls_enabled else "http"

    @property
    def allowed_roots(self) -> list[Path]:
        """Resolved roots Jarvis may touch; defaults to the user's home dir."""
        roots = [
            Path(part.strip())
            for part in self.jarvis_allowed_roots.split(",")
            if part.strip()
        ] or [Path.home()]
        return [root.expanduser().resolve() for root in roots]

    @property
    def db_path(self) -> Path:
        self.jarvis_data_dir.mkdir(parents=True, exist_ok=True)
        return self.jarvis_data_dir / "jarvis.db"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
