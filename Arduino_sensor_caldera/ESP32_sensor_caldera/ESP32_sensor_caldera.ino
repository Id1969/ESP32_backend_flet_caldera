/*
==========================================================================================
 PROYECTO: Sonda de Temperatura (ESP32-C3 + BME280) - PROTOCOLO CENTRALIZADO V2
 (DEBUG)
==========================================================================================
*/

#include <Adafruit_BME280.h>
#include <Adafruit_Sensor.h>
#include <Arduino.h>
#include <ArduinoJson.h>
#include <WebSocketsClient.h>
#include <WiFi.h>
#include <Wire.h>
#include <time.h>

#include "config_ESP32.h"

#undef ID_PLACA
#define ID_PLACA "esp32_03"

// -----------------------------------------------------------------------------
// 🌡️ SENSOR BME280
// -----------------------------------------------------------------------------
Adafruit_BME280 bme;
#define I2C_SDA 21
#define I2C_SCL 22

unsigned long lastMeasureTime = 0;
#define MEASURE_INTERVAL_MS 5000

// -----------------------------------------------------------------------------
// 🌐 RED
// -----------------------------------------------------------------------------
WebSocketsClient webSocket;

// -----------------------------------------------------------------------------
// 🛠️ UTILS: WiFi & NTP
// -----------------------------------------------------------------------------
String local_ip_str() {
  IPAddress ip = WiFi.localIP();
  return String(ip[0]) + "." + String(ip[1]) + "." + String(ip[2]) + "." +
         String(ip[3]);
}

bool connect_wifi_one(const char *ssid, const char *pass,
                      uint16_t timeout_ms = 15000) {
  Serial.printf("📶 Conectando a WiFi: %s ...\n", ssid);
  WiFi.begin(ssid, pass);
  unsigned long t0 = millis();
  while (WiFi.status() != WL_CONNECTED && (millis() - t0) < timeout_ms) {
    delay(250);
    Serial.print(".");
  }
  Serial.println();
  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("✅ WiFi OK (%s) — IP: %s\n", ssid, local_ip_str().c_str());
    return true;
  }
  Serial.printf("❌ No fue posible conectar a %s\n", ssid);
  return false;
}

void connect_wifi() {
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.persistent(false);

  if (connect_wifi_one(WIFI1_SSID, WIFI1_PASS))
    return;
  connect_wifi_one(WIFI2_SSID, WIFI2_PASS);
}

void sync_time_once() {
  configTime(GMT_OFFSET_SEC, DAYLIGHT_OFFSET_SEC, "pool.ntp.org",
             "time.nist.gov");
  Serial.print("🕒 Sincronizando hora NTP");
  struct tm tm_info;
  if (getLocalTime(&tm_info, 10000)) {
    Serial.println("\n✅ Hora sincronizada correctamente.");
  } else {
    Serial.println("\n⚠️ No se pudo sincronizar la hora (continuando...)");
  }
}

// -----------------------------------------------------------------------------
// 📡 LÓGICA WEBSOCKET
// -----------------------------------------------------------------------------
void sendRegister() {
  StaticJsonDocument<300> doc;
  doc["type"] = "register";
  doc["role"] = "esp32";
  doc["id"] = ID_PLACA;

  String out;
  serializeJson(doc, out);
  webSocket.sendTXT(out);
  Serial.print("📤 [WS] Registro enviado: ");
  Serial.println(out); // DEBUG
}

void sendSensorUpdate(float temp, float hum) {
  StaticJsonDocument<300> doc;
  doc["type"] = "sensor_update";
  doc["temperature"] = temp;
  doc["humidity"] = hum;
  doc["client_id"] = ID_PLACA;

  String out;
  serializeJson(doc, out);
  webSocket.sendTXT(out);
  Serial.print("📤 [WS] Enviando Medición: ");
  Serial.println(out); // DEBUG
}

void webSocketEvent(WStype_t type, uint8_t *payload, size_t length) {
  switch (type) {
  case WStype_CONNECTED:
    Serial.println("✅ Conectado al Backend");
    sendRegister();
    break;
  case WStype_DISCONNECTED:
    Serial.println("❌ Desconectado");
    break;
  case WStype_TEXT:
    StaticJsonDocument<200> doc;
    DeserializationError error = deserializeJson(doc, payload);
    if (!error) {
      const char *msgType = doc["type"];
      if (msgType && strcmp(msgType, "ping") == 0) {
        Serial.println("🏓 PING recibido del Backend"); // Heartbeat explícito
        webSocket.sendTXT("{\"type\":\"pong\"}");
      }
    }
    break;
  }
}

// -----------------------------------------------------------------------------
// 🚀 SETUP
// -----------------------------------------------------------------------------
void setup() {
  Serial.begin(115200);
  delay(1000);

  // 1. Iniciar Sensor
  Wire.begin(I2C_SDA, I2C_SCL);
  if (!bme.begin(0x76, &Wire)) {
    if (!bme.begin(0x77, &Wire)) {
      Serial.println("❌ ERROR: BME280 no encontrado.");
    }
  }

  // 2. WiFi
  connect_wifi();

  // 3. NTP
  sync_time_once();

  // 4. WebSocket
  Serial.printf("🔗 Conectando al Servidor: %s:%d%s (SSL=%d)\n", WS_HOST,
                WS_PORT, WS_PATH, WS_SECURE);

#if WS_SECURE
  webSocket.beginSSL(WS_HOST, WS_PORT, WS_PATH);
#else
  webSocket.begin(WS_HOST, WS_PORT, WS_PATH);
#endif

  webSocket.onEvent(webSocketEvent);
  webSocket.setReconnectInterval(5000);
  webSocket.enableHeartbeat(15000, 3000, 2);
}

// -----------------------------------------------------------------------------
// 🔁 LOOP
// -----------------------------------------------------------------------------
void loop() {
  webSocket.loop();

  unsigned long now = millis();
  if (now - lastMeasureTime > MEASURE_INTERVAL_MS) {
    lastMeasureTime = now;

    float t = bme.readTemperature();
    float h = bme.readHumidity();

    if (!isnan(t)) {
      Serial.printf("🌡️ Medición Local: %.2f C\n", t); // DEBUG
      sendSensorUpdate(t, h);
    } else {
      Serial.println("⚠ Error leer BME280 (NaN)");
    }
  }
}
