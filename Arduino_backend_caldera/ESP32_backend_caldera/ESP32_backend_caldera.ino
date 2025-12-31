/*
==========================================================================================
 PROYECTO: Control MASTER de CALDERA (ESP32) - Lógica Centralizada V2 (FINAL
STABLE)
==========================================================================================
*/

#include <Adafruit_NeoPixel.h>
#include <Arduino.h>
#include <ArduinoJson.h>
#include <EEPROM.h>
#include <WebServer.h>
#include <WebSocketsClient.h>
#include <WiFi.h>
#include <time.h>

#include "config_ESP32.h"

// -----------------------------------------------------------------------------
// 💾 EEPROM
// -----------------------------------------------------------------------------
#define EEPROM_SIZE 96

// -----------------------------------------------------------------------------
// 🔌 HARDWARE
// -----------------------------------------------------------------------------
#define NEOPIXEL_PIN 8
#define NUMPIXELS 1
Adafruit_NeoPixel pixels(NUMPIXELS, NEOPIXEL_PIN, NEO_GRB + NEO_KHZ800);

#define RELE_PIN 4
#define RELAY_ACTIVE_HIGH 1

// -----------------------------------------------------------------------------
// 🌐 RED
// -----------------------------------------------------------------------------
WebSocketsClient webSocket;
WebServer server(80);
bool apMode = false;

// -----------------------------------------------------------------------------
// 🧠 ESTADO DEL SISTEMA (Logic Core)
// -----------------------------------------------------------------------------
enum Mode { MODE_MANUAL, MODE_AUTO };
Mode currentMode = MODE_MANUAL;

float targetTemp = 21.5;
float currentSensorTemp = 0.0;
bool hasSensorData = false;
unsigned long lastSensorUpdate = 0;
// FIX: Reducido a 20s para reacción más rápida ante caída de sonda
const unsigned long SENSOR_TIMEOUT_MS = 20000;

bool relayState = false;

// -----------------------------------------------------------------------------
// 💡 LED & RELÉ
// -----------------------------------------------------------------------------
void setNeoPixelMirror(bool on) {
  pixels.setPixelColor(0, on ? pixels.Color(0, 20, 0) : pixels.Color(5, 0, 0));
  pixels.show();
}

void setRelay(bool on) {
  if (on != relayState) {
    digitalWrite(RELE_PIN, on ? (RELAY_ACTIVE_HIGH ? HIGH : LOW)
                              : (RELAY_ACTIVE_HIGH ? LOW : HIGH));
    relayState = on;
    setNeoPixelMirror(on);
    Serial.printf("🔌 RELÉ CAMBIADO A: %s\n", on ? "ON" : "OFF");

    // Solo intentamos enviar si estamos conectados, para evitar timeouts
    if (webSocket.isConnected()) {
      StaticJsonDocument<200> doc;
      doc["type"] = "status_update";
      doc["mode"] = currentMode == MODE_AUTO ? "AUTO" : "MANUAL";
      doc["relay_state"] = relayState ? "ON" : "OFF";
      doc["target_temp"] = targetTemp;
      String out;
      serializeJson(doc, out);
      webSocket.sendTXT(out);
    }
  }
}

// -----------------------------------------------------------------------------
// 🌡️ LÓGICA DE CONTROL (Termostato)
// -----------------------------------------------------------------------------
void runControlLogic() {
  // 1. FAIL-SAFE: Si no hay conexión con Backend, APAGAR RELÉ INMEDIATAMENTE
  // Esto evita oscilaciones o que funcione con datos viejos si se cae el server
  if (!webSocket.isConnected()) {
    if (relayState) {
      Serial.println("⛔ FAIL-SAFE: Sin conexión al Backend -> Relé OFF");
      setRelay(false);
    }
    return;
  }

  if (currentMode == MODE_MANUAL) {
    setRelay(false);
    return;
  }

  unsigned long now = millis();

  if (now - lastSensorUpdate > SENSOR_TIMEOUT_MS) {
    if (relayState) {
      Serial.println("⚠ ALERTA: Sonda perdida. Apagando relé por seguridad.");
      setRelay(false);
    }
    hasSensorData = false;
    return;
  }

  if (hasSensorData) {
    if (currentSensorTemp < targetTemp) {
      setRelay(true);
    } else {
      setRelay(false);
    }
  }
}

// -----------------------------------------------------------------------------
// 📡 WEBSOCKET HANDLING
// -----------------------------------------------------------------------------
void sendRegister() {
  StaticJsonDocument<300> doc;
  doc["type"] = "register";
  doc["role"] = "esp32";
  doc["id"] = ID_PLACA;
  String out;
  serializeJson(doc, out);
  webSocket.sendTXT(out);
}

void handleMessage(uint8_t *payload) {
  StaticJsonDocument<1024> doc;
  DeserializationError error = deserializeJson(doc, payload);
  if (error) {
    Serial.print("❌ Error JSON: ");
    Serial.println(error.c_str());
    return;
  }

  const char *type = doc["type"];
  if (!type)
    return;

  if (strcmp(type, "sensor_update") == 0) {
    currentSensorTemp = doc["temperature"];
    hasSensorData = true;
    lastSensorUpdate = millis();
    runControlLogic();
    // Log simple sin saturar
    static unsigned long lastLog = 0;
    if (millis() - lastLog > 5000) {
      Serial.printf("📡 Sonda OK Recibida: %.2f C\n", currentSensorTemp);
      lastLog = millis();
    }
  }

  else if (strcmp(type, "config_update") == 0) {
    if (doc.containsKey("mode")) {
      const char *m = doc["mode"];
      if (strcmp(m, "AUTO") == 0)
        currentMode = MODE_AUTO;
      else
        currentMode = MODE_MANUAL;
    }
    if (doc.containsKey("target_temp")) {
      targetTemp = doc["target_temp"];
    }
    Serial.println("⚙ Config recibida");
    runControlLogic();

    StaticJsonDocument<200> sdoc;
    sdoc["type"] = "status_update";
    sdoc["mode"] = currentMode == MODE_AUTO ? "AUTO" : "MANUAL";
    sdoc["relay_state"] = relayState ? "ON" : "OFF";
    sdoc["target_temp"] = targetTemp;
    String out;
    serializeJson(sdoc, out);
    webSocket.sendTXT(out);
  }

  else if (strcmp(type, "ping") == 0) {
    Serial.println("🏓 PING recibido del Backend"); // Heartbeat explícito
    webSocket.sendTXT("{\"type\":\"pong\"}");
  }
}

void webSocketEvent(WStype_t type, uint8_t *payload, size_t length) {
  switch (type) {
  case WStype_CONNECTED:
    Serial.println("✅ Conectado al Backend");
    sendRegister();
    break;

  case WStype_DISCONNECTED:
    Serial.println("❌ Desconectado del Backend");
    setRelay(false);
    break;

  case WStype_TEXT:
    handleMessage(payload);
    break;
  }
}

// -----------------------------------------------------------------------------
// 🚀 SETUP
// -----------------------------------------------------------------------------
void setup() {
  Serial.begin(115200);
  EEPROM.begin(EEPROM_SIZE);

  pinMode(RELE_PIN, OUTPUT);
  digitalWrite(RELE_PIN, RELAY_ACTIVE_HIGH ? LOW : HIGH);

  pixels.begin();
  setNeoPixelMirror(false);

  WiFi.begin(CASA_SSID, CASA_PASS);
  Serial.print("Conectando WiFi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.printf("\n✅ WiFi OK IP: %s\n", WiFi.localIP().toString().c_str());

#if (MODO_PRODUCCION == 1)
  webSocket.beginSSL(WEBSOCKET_HOST, WEBSOCKET_PORT, WEBSOCKET_PATH);
#else
  webSocket.begin(WEBSOCKET_HOST, WEBSOCKET_PORT, WEBSOCKET_PATH);
#endif
  webSocket.onEvent(webSocketEvent);
  webSocket.setReconnectInterval(5000);
  webSocket.enableHeartbeat(15000, 3000, 2);
}

void loop() {
  webSocket.loop();

  static unsigned long lastCheck = 0;
  if (millis() - lastCheck > 2000) {
    lastCheck = millis();
    runControlLogic();
  }

  // DEBUG & HEARTBEAT DE ESTADO (CADA 5 SEGUNDOS)
  static unsigned long lastDebug = 0;
  if (millis() - lastDebug > 5000) {
    lastDebug = millis();

    // 1. Log Local
    Serial.printf("🔍 [ESTADO 5s] Relé: %s | Modo: %s | Sonda: %.2f C | "
                  "Target: %.2f C\n",
                  relayState ? "ON" : "OFF",
                  currentMode == MODE_AUTO ? "AUTO" : "MANUAL",
                  hasSensorData ? currentSensorTemp : -1.0, targetTemp);

    // 2. Enviar al Backend (Sincronización periódica) - SOLO SI CONECTADO
    if (webSocket.isConnected()) {
      StaticJsonDocument<200> doc;
      doc["type"] = "status_update";
      doc["mode"] = currentMode == MODE_AUTO ? "AUTO" : "MANUAL";
      doc["relay_state"] = relayState ? "ON" : "OFF";
      doc["target_temp"] = targetTemp;
      String out;
      serializeJson(doc, out);
      webSocket.sendTXT(out);
    }
  }
}
