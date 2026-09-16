/*
  Job notification pager display for LilyGO T-Display (ESP32, 240x135 ST7789).

  Idle screen shows WiFi/MQTT status and a breathing "waiting" dot. When a
  job-match notification arrives over MQTT, a card slides in from the right
  with the job title and a color-coded score badge (matching the score bands
  used in gui/main_window.py: >=80 green, 65-79 amber, 50-64 orange, <50 red),
  the badge does a small bounce, then the card holds for 10s before returning
  to idle. A new notification arriving mid-animation/hold interrupts and
  restarts immediately with the new job.

  The idle screen also tracks a running count of good matches (every MQTT
  notification is already a match that cleared MATCH_SCORE_THRESHOLD on the
  Python side, so each one just increments the count). Pressing the GPIO35
  button resets the count to 0 and hides it from the idle screen until a
  fresh notification arrives.

  An active-low buzzer on GPIO27 (driven via tone()/noTone()) gives a short
  beep whenever a new notification arrives. GPIO37 cannot be used for this:
  it's one of the ESP32's input-only pins (34-39) with no output driver.

  Libraries required (Arduino Library Manager):
    - TFT_eSPI (Bodmer)      - see ../README.md for the required User_Setup
    - ArduinoJson (v7+)
    - PubSubClient (Nick O'Leary)

  Setup: copy secrets.h.example to secrets.h in this folder and fill in your
  WiFi credentials (MQTT_* already match the verified broker).
*/

#include <math.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <TFT_eSPI.h>
#include <ArduinoJson.h>
#include "secrets.h"

// ---------- Display ----------
TFT_eSPI tft = TFT_eSPI();
TFT_eSprite sprite = TFT_eSprite(&tft);


const int SCREEN_W = 240;
const int SCREEN_H = 135;
const int CARD_X = 8;

// ---------- Reset button ----------
const int RESET_BUTTON_PIN = 35;
const unsigned long BUTTON_DEBOUNCE_MS = 50;
int lastButtonReading = HIGH;
int stableButtonState = HIGH;
unsigned long lastButtonChangeMs = 0;

int goodMatchCount = 0;

// ---------- Buzzer (active-low: tone()/noTone() drive it, LOW = sounding) ----------
const int BUZZER_PIN = 27;
const int BUZZER_TONE_HZ = 2000;
const unsigned long BUZZER_BEEP_MS = 150;
unsigned long buzzerOffAtMs = 0;

// ---------- Network ----------
WiFiClient wifiClient;
PubSubClient mqttClient(wifiClient);

// ---------- Animation timing ----------
const unsigned long SLIDE_DURATION_MS = 350;
const unsigned long BOUNCE_DURATION_MS = 250;
const unsigned long HOLD_DURATION_MS = 5000;
const unsigned long FRAME_INTERVAL_MS = 20;

enum DisplayState { STATE_IDLE, STATE_SLIDE_IN, STATE_BOUNCE, STATE_HOLD };
DisplayState state = STATE_IDLE;
unsigned long stateStartMs = 0;
unsigned long lastFrameMs = 0;

struct JobNotification {
  String title = "";
  int score = -1;
};
JobNotification currentJob;

// ---------- Color helpers (bands match gui/main_window.py SCORE_COLORS) ----------

void scoreColors(int score, uint16_t &bg, uint16_t &fg) {
  if (score >= 80)      { bg = sprite.color565(0x2e, 0x7d, 0x32); fg = TFT_WHITE; }
  else if (score >= 65) { bg = sprite.color565(0xf9, 0xa8, 0x25); fg = sprite.color565(0x1a, 0x1a, 0x1a); }
  else if (score >= 50) { bg = sprite.color565(0xef, 0x6c, 0x00); fg = TFT_WHITE; }
  else                  { bg = sprite.color565(0xc6, 0x28, 0x28); fg = TFT_WHITE; }
}

uint16_t lerpColor565(uint16_t c1, uint16_t c2, float t) {
  if (t <= 0) return c1;
  if (t >= 1) return c2;
  uint8_t r1 = (c1 >> 11) & 0x1F, g1 = (c1 >> 5) & 0x3F, b1 = c1 & 0x1F;
  uint8_t r2 = (c2 >> 11) & 0x1F, g2 = (c2 >> 5) & 0x3F, b2 = c2 & 0x1F;
  uint8_t r = r1 + (uint8_t)((r2 - r1) * t);
  uint8_t g = g1 + (uint8_t)((g2 - g1) * t);
  uint8_t b = b1 + (uint8_t)((b2 - b1) * t);
  return (r << 11) | (g << 5) | b;
}

// ---------- Easing ----------

float easeOutCubic(float t) {
  float f = t - 1.0f;
  return f * f * f + 1.0f;
}

// Single overshoot bounce: rises to ~1.15x scale then settles to 1.0x.
float bounceScale(float t) {
  if (t < 0.6f) {
    return 1.0f + 0.15f * easeOutCubic(t / 0.6f);
  }
  return 1.15f - 0.15f * easeOutCubic((t - 0.6f) / 0.4f);
}

// ---------- Text wrapping (greedy word-wrap to 2 lines, ellipsis on overflow) ----------

void wrapTitle(const String &title, String &line1, String &line2, int maxWidth) {
  String remaining = title;
  remaining.trim();

  int fitEnd = 0;
  while (fitEnd < (int)remaining.length() &&
         sprite.textWidth(remaining.substring(0, fitEnd + 1)) <= maxWidth) {
    fitEnd++;
  }
  if (fitEnd >= (int)remaining.length()) {
    line1 = remaining;
    line2 = "";
    return;
  }

  int splitAt = remaining.lastIndexOf(' ', fitEnd);
  if (splitAt <= 0) splitAt = fitEnd;
  line1 = remaining.substring(0, splitAt);
  line1.trim();

  String rest = remaining.substring(splitAt);
  rest.trim();

  int restFitEnd = 0;
  while (restFitEnd < (int)rest.length() &&
         sprite.textWidth(rest.substring(0, restFitEnd + 1) + "...") <= maxWidth) {
    restFitEnd++;
  }
  if (restFitEnd >= (int)rest.length()) {
    line2 = rest;
  } else {
    line2 = rest.substring(0, restFitEnd);
    line2.trim();
    line2 += "...";
  }
}

// ---------- Rendering ----------

void drawIdleScreen() {
  sprite.fillSprite(TFT_BLACK);

  sprite.setTextDatum(TL_DATUM);
  sprite.setTextColor(TFT_WHITE, TFT_BLACK);
  sprite.setTextFont(2);
  sprite.drawString("Job Hunter", 8, 8);

  bool wifiConnected = (WiFi.status() == WL_CONNECTED);
  bool mqttConnected = mqttClient.connected();
  int dotY = 34;
  sprite.setTextFont(1);
  sprite.fillCircle(14, dotY, 4, wifiConnected ? TFT_GREEN : TFT_RED);
  sprite.drawString("WiFi", 24, dotY - 4);
  sprite.fillCircle(74, dotY, 4, mqttConnected ? TFT_GREEN : TFT_RED);
  sprite.drawString("MQTT", 84, dotY - 4);

  if (goodMatchCount > 0) {
    sprite.setTextDatum(MC_DATUM);
    sprite.setTextColor(TFT_CYAN, TFT_BLACK);
    sprite.setTextFont(7);
    sprite.drawString(String(goodMatchCount), SCREEN_W / 2, 78);

    sprite.setTextFont(1);
    sprite.setTextColor(TFT_WHITE, TFT_BLACK);
    sprite.drawString(goodMatchCount == 1 ? "good match" : "good matches", SCREEN_W / 2, SCREEN_H - 8);
    sprite.setTextDatum(TL_DATUM);
  } else {
    float phase = (millis() % 3000) / 3000.0f;
    float pulse = (sinf(phase * 2.0f * PI) + 1.0f) / 2.0f;
    int radius = 3 + (int)(pulse * 4);
    uint16_t dotColor = lerpColor565(TFT_DARKGREY, TFT_CYAN, pulse);
    sprite.fillCircle(SCREEN_W / 2, SCREEN_H - 24, radius, dotColor);

    sprite.setTextDatum(MC_DATUM);
    sprite.drawString("waiting for jobs...", SCREEN_W / 2, SCREEN_H - 8);
    sprite.setTextDatum(TL_DATUM);
  }

  sprite.pushSprite(0, 0);
}

void drawNotificationFrame(int cardX, float badgeScale) {
  sprite.fillSprite(TFT_BLACK);

  int cardW = SCREEN_W - 16;
  int cardH = SCREEN_H - 16;
  int cardY = 8;
  uint16_t cardBg = sprite.color565(0x20, 0x20, 0x20);

  sprite.fillRoundRect(cardX, cardY, cardW, cardH, 10, cardBg);
  sprite.drawRoundRect(cardX, cardY, cardW, cardH, 10, TFT_DARKGREY);

  uint16_t badgeBg, badgeFg;
  scoreColors(currentJob.score, badgeBg, badgeFg);
  int badgeCx = cardX + cardW - 28;
  int badgeCy = cardY + 28;
  int radius = (int)(22 * badgeScale);
  sprite.fillCircle(badgeCx, badgeCy, radius, badgeBg);
  sprite.setTextDatum(MC_DATUM);
  sprite.setTextColor(badgeFg, badgeBg);
  sprite.setTextFont(4);
  sprite.drawString(currentJob.score >= 0 ? String(currentJob.score) : "--", badgeCx, badgeCy);

  int textMaxWidth = cardW - 70;
  String line1, line2;
  sprite.setTextFont(2);
  wrapTitle(currentJob.title, line1, line2, textMaxWidth);
  sprite.setTextDatum(TL_DATUM);
  sprite.setTextColor(TFT_WHITE, cardBg);
  sprite.drawString(line1, cardX + 12, cardY + 16);
  sprite.drawString(line2, cardX + 12, cardY + 38);

  sprite.pushSprite(0, 0);
}

void updateDisplay() {
  unsigned long now = millis();
  if (now - lastFrameMs < FRAME_INTERVAL_MS) return;
  lastFrameMs = now;

  unsigned long elapsed = now - stateStartMs;

  switch (state) {
    case STATE_IDLE:
      drawIdleScreen();
      break;

    case STATE_SLIDE_IN: {
      float t = min(1.0f, (float)elapsed / (float)SLIDE_DURATION_MS);
      float eased = easeOutCubic(t);
      int cardX = CARD_X + (int)((1.0f - eased) * (SCREEN_W - CARD_X));
      drawNotificationFrame(cardX, 1.0f);
      if (t >= 1.0f) {
        state = STATE_BOUNCE;
        stateStartMs = now;
      }
      break;
    }

    case STATE_BOUNCE: {
      float t = min(1.0f, (float)elapsed / (float)BOUNCE_DURATION_MS);
      drawNotificationFrame(CARD_X, bounceScale(t));
      if (t >= 1.0f) {
        state = STATE_HOLD;
        stateStartMs = now;
      }
      break;
    }

    case STATE_HOLD:
      drawNotificationFrame(CARD_X, 1.0f);
      if (elapsed >= HOLD_DURATION_MS) {
        state = STATE_IDLE;
        stateStartMs = now;
      }
      break;
  }
}

// ---------- Networking ----------

void connectWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;
  Serial.printf("Connecting to WiFi SSID '%s'...\n", WIFI_SSID);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 15000) {
    delay(500);
    Serial.print(".");
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("\nWiFi connected, IP: %s\n", WiFi.localIP().toString().c_str());
  } else {
    Serial.println("\nWiFi connect timed out, will retry.");
  }
}

void onMqttMessage(char* topic, byte* payload, unsigned int length) {
  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, payload, length);
  if (err) {
    Serial.printf("[MQTT] JSON parse failed: %s\n", err.c_str());
    return;
  }

  currentJob.title = String((const char*)(doc["title"] | "Untitled job"));
  currentJob.score = doc["score"] | -1;
  goodMatchCount++;

  Serial.printf("[MQTT] Notification: '%s' score=%d (good match count: %d)\n",
                currentJob.title.c_str(), currentJob.score, goodMatchCount);

  state = STATE_SLIDE_IN;
  stateStartMs = millis();

  tone(BUZZER_PIN, BUZZER_TONE_HZ);
  buzzerOffAtMs = millis() + BUZZER_BEEP_MS;
}

void connectMqtt() {
  if (mqttClient.connected()) return;
  mqttClient.setServer(MQTT_BROKER_HOST, MQTT_BROKER_PORT);
  mqttClient.setCallback(onMqttMessage);

  String clientId = "tdisplay-notif-" + String((uint32_t)ESP.getEfuseMac(), HEX);
  Serial.printf("Connecting to MQTT broker %s:%d as '%s'...\n", MQTT_BROKER_HOST, MQTT_BROKER_PORT, clientId.c_str());

  if (mqttClient.connect(clientId.c_str(), MQTT_USERNAME, MQTT_PASSWORD)) {
    Serial.println("MQTT connected.");
    mqttClient.subscribe(MQTT_TOPIC);
    Serial.printf("Subscribed to '%s'\n", MQTT_TOPIC);
  } else {
    Serial.printf("MQTT connect failed, rc=%d. Will retry.\n", mqttClient.state());
  }
}

// ---------- Reset button ----------

void checkResetButton() {
  int reading = digitalRead(RESET_BUTTON_PIN);
  unsigned long now = millis();

  if (reading != lastButtonReading) {
    lastButtonChangeMs = now;
    lastButtonReading = reading;
  }

  if (now - lastButtonChangeMs >= BUTTON_DEBOUNCE_MS && reading != stableButtonState) {
    stableButtonState = reading;
    if (stableButtonState == LOW) {
      goodMatchCount = 0;
      Serial.println("[Button] Good match count reset.");
    }
  }
}

// ---------- Buzzer ----------

void checkBuzzer() {
  if (buzzerOffAtMs != 0 && millis() >= buzzerOffAtMs) {
    noTone(BUZZER_PIN);
    buzzerOffAtMs = 0;
  }
}

// ---------- Arduino entry points ----------

void setup() {
  Serial.begin(115200);
  delay(300);

  pinMode(RESET_BUTTON_PIN, INPUT);
  pinMode(BUZZER_PIN, OUTPUT);
  digitalWrite(BUZZER_PIN, HIGH); // active-low: HIGH = silent

  // Default PubSubClient buffer (256 bytes) is too small for the topic +
  // JSON payload (title/message/url can push well past that); packets over
  // the limit are silently dropped rather than erroring.
  mqttClient.setBufferSize(1024);

  tft.init();
  tft.setRotation(1);
  sprite.createSprite(SCREEN_W, SCREEN_H);

  drawIdleScreen();

  connectWiFi();
  connectMqtt();

  stateStartMs = millis();
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    connectWiFi();
  }
  if (!mqttClient.connected()) {
    connectMqtt();
  }
  mqttClient.loop();

  checkResetButton();
  checkBuzzer();
  updateDisplay();
}
