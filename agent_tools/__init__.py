"""
Agent tools package for RSS Job Hunter: integrations invoked as part of agent
pipelines (e.g. notifications), as opposed to the core fetch/parse/render
utilities in tools/.
"""

from .pushover import PushoverError, is_pushover_configured, send_pushover_notification
from .mqtt import MQTTError, is_mqtt_configured, send_mqtt_notification

__all__ = [
    "PushoverError",
    "is_pushover_configured",
    "send_pushover_notification",
    "MQTTError",
    "is_mqtt_configured",
    "send_mqtt_notification",
]
