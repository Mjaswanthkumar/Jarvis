from __future__ import annotations

import pytest

from jarvis.__main__ import check_startup_security
from jarvis.config import Settings
from jarvis.network import (
    MIN_REMOTE_TOKEN_LENGTH,
    is_loopback,
    pairing_info,
    qr_ascii,
    startup_banner,
)

STRONG_TOKEN = "x" * MIN_REMOTE_TOKEN_LENGTH


def _settings(**overrides: object) -> Settings:
    defaults = {
        "JARVIS_AUTH_TOKEN": STRONG_TOKEN,
        "JARVIS_HOST": "127.0.0.1",
        "JARVIS_PORT": 8010,
    }
    return Settings(**{**defaults, **overrides})  # type: ignore[arg-type]


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_loopback_hosts_are_recognised(host: str) -> None:
    assert is_loopback(host)


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.20"])
def test_network_hosts_are_not_loopback(host: str) -> None:
    assert not is_loopback(host)


def test_loopback_binding_offers_no_pairing_url() -> None:
    info = pairing_info("127.0.0.1", 8010, STRONG_TOKEN)
    assert info.lan_url is None
    assert info.pairing_url is None
    assert info.reachable_url == "http://127.0.0.1:8010"


def test_network_binding_builds_a_pairing_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("jarvis.network.lan_ip", lambda: "192.168.1.20")
    info = pairing_info("0.0.0.0", 8010, "secret-token")

    assert info.lan_url == "http://192.168.1.20:8010"
    # The token rides in the fragment, which browsers never send to a server.
    assert info.pairing_url == "http://192.168.1.20:8010/#t=secret-token"
    assert "#" in info.pairing_url


def test_pairing_falls_back_when_there_is_no_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("jarvis.network.lan_ip", lambda: None)
    info = pairing_info("0.0.0.0", 8010, STRONG_TOKEN)
    assert info.lan_url is None


def test_banner_shows_the_token_only_for_local_use() -> None:
    local = startup_banner(pairing_info("127.0.0.1", 8010, "tok"), "tok")
    assert "tok" in local
    assert "JARVIS_HOST=0.0.0.0" in local


def test_banner_shows_a_qr_code_for_remote_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("jarvis.network.lan_ip", lambda: "192.168.1.20")
    banner = startup_banner(pairing_info("0.0.0.0", 8010, STRONG_TOKEN), STRONG_TOKEN)
    assert "192.168.1.20:8010" in banner
    assert "Scan to pair" in banner


def test_qr_renders_as_terminal_text() -> None:
    art = qr_ascii("http://192.168.1.20:8010/#t=abc")
    assert art.count("\n") > 10


def test_startup_requires_a_token() -> None:
    with pytest.raises(SystemExit, match="JARVIS_AUTH_TOKEN is not set"):
        check_startup_security(_settings(JARVIS_AUTH_TOKEN=""))


def test_a_weak_token_is_fine_on_loopback() -> None:
    check_startup_security(_settings(JARVIS_AUTH_TOKEN="short"))


def test_a_weak_token_blocks_network_exposure() -> None:
    """Binding to the network with a guessable token must not be possible."""
    with pytest.raises(SystemExit, match="at least"):
        check_startup_security(
            _settings(JARVIS_AUTH_TOKEN="short", JARVIS_HOST="0.0.0.0")
        )


def test_a_strong_token_allows_network_exposure() -> None:
    check_startup_security(_settings(JARVIS_HOST="0.0.0.0"))
