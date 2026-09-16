"""
Pushover push notification tool (https://pushover.net/).

Only active when both PUSHOVER_API_TOKEN and PUSHOVER_USER_KEY are configured in
the environment; if either is missing, send_pushover_notification() is a silent
no-op so the rest of the Find Jobs pipeline is unaffected.
"""

import logging
import os
from typing import Optional

import requests

logger = logging.getLogger(__name__)

PUSHOVER_API_URL = "https://api.pushover.net/1/messages.json"


class PushoverError(Exception):
    """Raised when a Pushover notification request fails."""


def is_pushover_configured() -> bool:
    """Returns True only if both PUSHOVER_API_TOKEN and PUSHOVER_USER_KEY are set."""
    return bool(os.getenv("PUSHOVER_API_TOKEN")) and bool(os.getenv("PUSHOVER_USER_KEY"))


def send_pushover_notification(
    message: str,
    title: Optional[str] = None,
    url: Optional[str] = None,
    url_title: Optional[str] = None,
    timeout: int = 10,
) -> bool:
    """
    Sends a Pushover push notification if PUSHOVER_API_TOKEN and PUSHOVER_USER_KEY
    are both configured; otherwise this is a no-op.

    Args:
        message: The notification body (required by the Pushover API).
        title: Optional notification title.
        url: Optional supplementary URL shown with the notification.
        url_title: Optional display text for the URL.
        timeout: Request timeout in seconds.

    Returns:
        True if the notification was sent successfully, False if Pushover is not
        configured or the request failed. Failures are logged, not raised, so a
        Pushover outage never breaks the Find Jobs pipeline.
    """
    api_token = os.getenv("PUSHOVER_API_TOKEN")
    user_key = os.getenv("PUSHOVER_USER_KEY")
    if not api_token or not user_key:
        return False

    payload = {"token": api_token, "user": user_key, "message": message}
    if title:
        payload["title"] = title
    if url:
        payload["url"] = url
    if url_title:
        payload["url_title"] = url_title

    try:
        response = requests.post(PUSHOVER_API_URL, data=payload, timeout=timeout)
        response.raise_for_status()
        return True
    except requests.RequestException as e:
        logger.warning("Pushover notification failed: %s", e)
        return False
