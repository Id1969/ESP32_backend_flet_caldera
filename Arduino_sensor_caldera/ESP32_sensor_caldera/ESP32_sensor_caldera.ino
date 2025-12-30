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
    // DEBUG RAW
    // Serial.printf("📥 [WS] RAW Recibido: %s\n", (char*)payload);

    StaticJsonDocument<200> doc;
    DeserializationError error = deserializeJson(doc, payload);
    if (!error) {
      const char *msgType = doc["type"];
      if (msgType && strcmp(msgType, "ping") == 0) {
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
  WiFi.begin(CASA_SSID, CASA_PASS);
  Serial.print("Conectando WiFi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.printf("\n✅ WiFi Conectado IP: %s\n",
                WiFi.localIP().toString().c_str());

#if (MODO_PRODUCCION == 1)
  webSocket.beginSSL(WEBSOCKET_HOST, WEBSOCKET_PORT, WEBSOCKET_PATH);
#else
  webSocket.begin(WEBSOCKET_HOST, WEBSOCKET_PORT, WEBSOCKET_PATH);
#endif

  webSocket.onEvent(webSocketEvent);
  webSocket.setReconnectInterval(5000);
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
