# LilyGO T-Display Pager

Firmware for the LilyGO T-Display (ESP32, 240x135 ST7789) that turns the
device into a pager: it subscribes to MQTT and renders incoming job-match
notifications on the onboard screen. The MQTT contract is already live on
the Python side (`agent_tools/mqtt.py`):

- **Topic:** `MQTT_TOPIC` env var, default `jobhunter/notifications`
- **Payload:** JSON `{"title": str | null, "message": str | null, "url": str | null, "score": int | null}`
- **Broker:** Bevywise CrystalMQ Cloud (`crystalmq.bevywise.com`), plain MQTT on port 1883, username/password auth

## Projects

- **`notification_display/`** — the pager firmware: idle status screen
  (WiFi/MQTT indicators + breathing "waiting" dot), and an animated
  slide-in card with a color-coded score badge when a notification arrives,
  matching the score bands from `gui/main_window.py`'s `SCORE_COLORS`
  (>=80 green, 65-79 amber, 50-64 orange, <50 red).

## TFT_eSPI setup (required for `notification_display/`)

`TFT_eSPI` needs to be told which display driver/pinout to use for the
T-Display, via a compile-time config rather than a sketch-level setting:

1. Install the `TFT_eSPI` library (Bodmer) via Library Manager.
2. Open `<Arduino libraries folder>/TFT_eSPI/User_Setup_Select.h`.
3. Comment out the default `#include <User_Setup.h>` line.
4. Uncomment `#include <User_Setups/Setup25_TTGO_T_Display.h>` (or the
   closest match for your board revision — check the comments in that file).
5. Re-open/re-compile the sketch.

Also install `ArduinoJson` (v7+) and `PubSubClient` via Library Manager.
