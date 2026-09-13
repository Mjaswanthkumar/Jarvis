"""Desktop notifications, so an alert reaches the user with nothing open.

A watch that fires at 3am is worthless if it only paints a card into a web page
nobody is looking at. Jarvis runs *on* the machine it is watching, which makes
this easy: raise a native Windows toast and the browser is not involved at all.
No HTTPS, no service worker, no push subscription, no new dependency.

The message is passed through the environment rather than interpolated into the
script, so alert text can never be read as PowerShell.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 10.0

#: PowerShell's own registered AppID. Using it means the toast appears under a
#: known identity without Jarvis having to register one in the Start Menu.
_APP_ID = (
    "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}"
    r"\WindowsPowerShell\v1.0\powershell.exe"
)

_SCRIPT = f"""
$ErrorActionPreference = 'Stop'
try {{
    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
    [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType = WindowsRuntime] | Out-Null
    $template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
    $texts = $template.GetElementsByTagName('text')
    $texts.Item(0).AppendChild($template.CreateTextNode($env:JARVIS_TOAST_TITLE)) | Out-Null
    $texts.Item(1).AppendChild($template.CreateTextNode($env:JARVIS_TOAST_BODY)) | Out-Null
    $toast = [Windows.UI.Notifications.ToastNotification]::new($template)
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{_APP_ID}').Show($toast)
}} catch {{
    Write-Error $_.Exception.Message
}}
"""


def is_supported() -> bool:
    """True when a desktop notification can actually be raised here."""
    return sys.platform == "win32" and shutil.which("powershell") is not None


def send(title: str, body: str, *, enabled: bool = True) -> bool:
    """Raise a desktop notification. Never raises; returns whether it worked."""
    if not enabled or not is_supported():
        return False
    if not body.strip():
        return False

    environment = {
        **os.environ,
        # Never interpolated into the script: the toast reads these back out.
        "JARVIS_TOAST_TITLE": title[:80],
        "JARVIS_TOAST_BODY": body[:250],
    }
    try:
        completed = subprocess.run(  # noqa: S603 - fixed script, no interpolation
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                _SCRIPT,
            ],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            shell=False,
            check=False,
            env=environment,
            cwd=str(Path.home()),
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("desktop notification failed: %s", exc)
        return False

    if completed.returncode != 0 or completed.stderr.strip():
        logger.warning(
            "desktop notification failed: %s",
            (completed.stderr or "unknown error").strip()[:200],
        )
        return False
    return True
