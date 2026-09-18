# LilyGO T-Display Pager

Firmware for the LilyGO T-Display (ESP32, 240x135 ST7789) that turns the
device into a pager: it subscribes to MQTT and renders incoming job-match
notifications on the onboard screen. The MQTT contract is already live on
the Python side (`agent_tools/mqtt.py`):

- **Topic:** `MQTT_TOPIC` env var, default `jobhunter/notifications`
- **Payload:** JSON `{"title": str | null, "message": str | null, "url": str | null, "score": int | null}`
- **Broker:** any standard MQTT broker with username/password auth — host, port,
  and TLS are all configurable (`MQTT_BROKER_HOST` / `MQTT_BROKER_PORT` /
  `MQTT_USE_TLS` on the Python side, matched by the `MQTT_*` defines in
  `secrets.h` on the firmware side). Options that work well for this project:
  - **[Eclipse Mosquitto](https://mosquitto.org/)** — self-hosted (e.g. on a
    Raspberry Pi or small VPS), free, full control.
  - **[HiveMQ Cloud](https://www.hivemq.com/mqtt-cloud-broker/)** — free
    tier, TLS-only (port 8883).
  - **[EMQX Cloud](https://www.emqx.com/en/cloud)** — free tier, TLS-only
    (port 8883).
  - **[Bevywise CrystalMQ Cloud](https://crystalmq.bevywise.com/)** — free
    tier, plain MQTT on port 1883 (no TLS); this is what the reference
    firmware config in `secrets.h.example` uses.

## What it does

The `notification_display/` sketch shows an idle status screen (WiFi/MQTT
indicators + breathing "waiting" dot), then an animated slide-in card with
the job title, a color-coded score badge matching the score bands from
`gui/main_window.py`'s `SCORE_COLORS` (>=80 green, 65-79 amber, 50-64
orange, <50 red), and the payload's `message` field (company name, set in
`pipeline.py`) below a divider line.

## TFT_eSPI setup

`TFT_eSPI` needs to be told which display driver/pinout to use for the
T-Display, via a compile-time config rather than a sketch-level setting:

1. Install the `TFT_eSPI` library (Bodmer) via Library Manager.
2. Open `<Arduino libraries folder>/TFT_eSPI/User_Setup_Select.h`.
3. Comment out the default `#include <User_Setup.h>` line.
4. Uncomment `#include <User_Setups/Setup25_TTGO_T_Display.h>` (or the
   closest match for your board revision — check the comments in that file).
5. Re-open/re-compile the sketch.

Also install `ArduinoJson` (v7+) and `PubSubClient` via Library Manager.
