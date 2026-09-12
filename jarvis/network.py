"""LAN discovery and terminal pairing for phone access."""

from __future__ import annotations

import io
import socket
from dataclasses import dataclass

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

#: Below this, a token is too weak to expose beyond loopback.
MIN_REMOTE_TOKEN_LENGTH = 24


def is_loopback(host: str) -> bool:
    return host in LOOPBACK_HOSTS


def lan_ip() -> str | None:
    """This machine's address on the local network, without sending traffic."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("10.255.255.255", 1))
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()


@dataclass(slots=True, frozen=True)
class PairingInfo:
    """Everything a phone needs to reach this Jarvis."""

    local_url: str
    lan_url: str | None
    pairing_url: str | None

    @property
    def reachable_url(self) -> str:
        return self.lan_url or self.local_url


def pairing_info(host: str, port: int, token: str) -> PairingInfo:
    """Build the URLs shown at startup.

    The pairing URL carries the token in the *fragment*, which browsers never
    send to the server and which the client strips from the address bar as soon
    as it has stored it.
    """
    local_url = f"http://127.0.0.1:{port}"
    if is_loopback(host):
        return PairingInfo(local_url=local_url, lan_url=None, pairing_url=None)

    address = lan_ip()
    if address is None:
        return PairingInfo(local_url=local_url, lan_url=None, pairing_url=None)

    lan_url = f"http://{address}:{port}"
    return PairingInfo(
        local_url=local_url, lan_url=lan_url, pairing_url=f"{lan_url}/#t={token}"
    )


def qr_ascii(data: str) -> str:
    """Render a QR code as terminal text, or an empty string if unavailable."""
    try:
        import segno
    except ImportError:  # pragma: no cover - optional convenience
        return ""
    buffer = io.StringIO()
    segno.make(data, error="m").terminal(out=buffer, border=2)
    return buffer.getvalue()


def startup_banner(info: PairingInfo, token: str) -> str:
    """The block printed when Jarvis starts."""
    lines = ["", "  JARVIS is running", "", f"  This PC     {info.local_url}"]
    if info.lan_url:
        lines += [
            f"  Phone/LAN   {info.lan_url}",
            "",
            "  Scan to pair a phone (the token travels in the URL fragment,",
            "  so it is never sent to the server):",
            "",
            qr_ascii(info.pairing_url or info.lan_url),
        ]
    else:
        lines += [
            "",
            f"  Access token  {token}",
            "",
            "  Bound to loopback only. Set JARVIS_HOST=0.0.0.0 to reach it",
            "  from your phone on the same network.",
        ]
    lines.append("")
    return "\n".join(lines)
