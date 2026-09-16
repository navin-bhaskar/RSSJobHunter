"""
MQTT push notification tool, for driving a pager-style display device (e.g. a
LilyGO T-Display) in addition to Pushover.

Only active when MQTT_BROKER_HOST, MQTT_USERNAME, and MQTT_PASSWORD are all
configured in the environment; if any is missing, send_mqtt_notification() is
a silent no-op so the rest of the Find Jobs pipeline is unaffected.
"""

import json
import logging
import os
from typing import Optional

import paho.mqtt.publish as mqtt_publish

logger = logging.getLogger(__name__)

DEFAULT_MQTT_PORT = 8883
DEFAULT_MQTT_TOPIC = "jobhunter/notifications"


class MQTTError(Exception):
    """Raised when an MQTT publish request fails."""


def is_mqtt_configured() -> bool:
    """Returns True only if MQTT_BROKER_HOST, MQTT_USERNAME, and MQTT_PASSWORD are all set."""
    return bool(os.getenv("MQTT_BROKER_HOST")) and bool(os.getenv("MQTT_USERNAME")) and bool(
        os.getenv("MQTT_PASSWORD")
    )


def send_mqtt_notification(
    message: str,
    title: Optional[str] = None,
    url: Optional[str] = None,
    score: Optional[int] = None,
    timeout: int = 10,
) -> bool:
    """
    Publishes a single MQTT notification message if MQTT_BROKER_HOST, MQTT_USERNAME,
    and MQTT_PASSWORD are all configured; otherwise this is a no-op.

    The payload is a JSON object: {"title", "message", "url", "score"}, intended for
    a small display device subscribed to the configured topic to render.

    Args:
        message: The notification body.
        title: Optional notification title.
        url: Optional supplementary URL related to the notification.
        score: Optional numeric score to include in the payload.
        timeout: Broker connection timeout in seconds (used as the keepalive value).

    Returns:
        True if the message was published successfully, False if MQTT is not
        configured or the publish failed. Failures are logged, not raised, so an
        MQTT broker outage never breaks the Find Jobs pipeline.
    """
    host = os.getenv("MQTT_BROKER_HOST")
    username = os.getenv("MQTT_USERNAME")
    password = os.getenv("MQTT_PASSWORD")
    if not host or not username or not password:
        return False

    port = int(os.getenv("MQTT_BROKER_PORT", DEFAULT_MQTT_PORT))
    topic = os.getenv("MQTT_TOPIC", DEFAULT_MQTT_TOPIC)
    use_tls = os.getenv("MQTT_USE_TLS", "true").strip().lower() not in ("false", "0", "no")

    payload = json.dumps({"title": title, "message": message, "url": url, "score": score})

    try:
        mqtt_publish.single(
            topic,
            payload=payload,
            hostname=host,
            port=port,
            auth={"username": username, "password": password},
            tls={} if use_tls else None,
            keepalive=timeout,
        )
        return True
    except Exception as e:
        logger.warning("MQTT notification failed: %s", e)
        return False
