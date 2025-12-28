/*
==========================================================================================
 PROYECTO: Sonda de Temperatura (ESP32-C3 + BME280)
==========================================================================================

 DESCRIPCIÓN
 -----------
 - Lee temperatura y humedad cada X segundos.
 - Envía los datos por WebSocket al servidor central (Backend FastAPI).
 - Usado como "Termostato Remoto" en el sistema de caldera.

 HARDWARE
 --------
 - ESP32-C3
 - BME280 (I2C) -> SDA: GPIO21, SCL: GPIO22 (Según solicitud usuario)
   * Nota: En ESP32-C3 los pines I2C por defecto suelen ser 8(SDA) y 9(SCL).
           Se fuerza 21 y 22 en Wire.begin(21, 22).

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

// -----------------------------------------------------------------------------
// 🔧 CONFIGURACIÓN (Se asume un config_ESP32.h compartido, si no, crear copia)
// -----------------------------------------------------------------------------
// Para simplificar, copiaremos el config del otro proyecto o creamos uno nuevo.
// Por ahora, usaremos las mismas constantes.
#include "config_ESP32.h"

// 🛑 IMPORTANTE: Asegúrate de que el ID en config_sensor_ESP32.h (si lo creas)
// sea "esp32_03" OJO: Si usas el mismo archivo que el RELÉ, tendrás conflicto
// de ID. SOLUCIÓN: Definiré aquí el ID localmente sobrescribiendo si es
// necesario, o mejor, asumimos que el usuario creará un config específico. Para
// este código, esperaré que en el config se defina el ID, o lo fuerzo aquí:
#undef ID_PLACA
#define ID_PLACA "esp32_03"

// -----------------------------------------------------------------------------
// 🌡️ SENSOR BME280
// -----------------------------------------------------------------------------
Adafruit_BME280 bme;
// I2C Pines
#define I2C_SDA 21
#define I2C_SCL 22

unsigned long lastMeasureTime = 0;
#define MEASURE_INTERVAL_MS 5000 // Medir cada 5 segundos

// -----------------------------------------------------------------------------
// 🌐 RED
// -----------------------------------------------------------------------------
WebSocketsClient webSocket;

// Credenciales (copiadas por conveniencia si no están en config)
// Se deben sacar de un fichero config común idealmente.

// -----------------------------------------------------------------------------
// ⏱ TIMESTAMP
// -----------------------------------------------------------------------------
String isoTimestampUTC() {
  time_t now = time(nullptr);
  if (now < 1700000000)
    return "1970-01-01T00:00:00Z";
  struct tm tm;
  gmtime_r(&now, &tm);
  char buf[30];
  strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%SZ", &tm);
  return String(buf);
}

void syncTimeNTP() {
  configTime(0, 0, "pool.ntp.org", "time.nist.gov");
  Serial.print("⏳ Sincronizando hora (NTP)...");
  time_t now = time(nullptr);
  int retries = 0;
  while (now < 1700000000 && retries < 20) {
    delay(500);
    Serial.print(".");
    now = time(nullptr);
    retries++;
  }
  Serial.println();
}

// -----------------------------------------------------------------------------
// 📡 WEBSOCKET LOGIC
// -----------------------------------------------------------------------------
void sendRegister() {
  StaticJsonDocument<300> doc;
  doc["type"] = "register";
  doc["role"] = "esp32"; // Rol genérico, el backend lo sabe por el ID
  doc["id"] = ID_PLACA;
  doc["mac"] = WiFi.macAddress();
  doc["ip"] = WiFi.localIP().toString();

  String out;
  serializeJson(doc, out);
  webSocket.sendTXT(out);
  Serial.println("[WS] -> register: " + out);
}

void sendTelemetry(float temp, float hum) {
  StaticJsonDocument<300> doc;
  doc["type"] = "telemetry";
  doc["from"] = ID_PLACA;
  doc["temp"] = temp;
  doc["hum"] = hum;
  doc["ts"] = isoTimestampUTC();

  String out;
  serializeJson(doc, out);
  webSocket.sendTXT(out); // Enviar al backend

  Serial.printf("[Telemetría] T: %.2f C | H: %.2f %%\n", temp, hum);
}

void webSocketEvent(WStype_t type, uint8_t *payload, size_t length) {
  switch (type) {
  case WStype_CONNECTED:
    Serial.println("[WS] ✅ Conectado");
    sendRegister();
    break;
  case WStype_DISCONNECTED:
    Serial.println("[WS] ❌ Desconectado");
    break;
  case WStype_TEXT:
    // Por ahora el sensor no necesita recibir comandos, solo envía.
    // Podríamos procesar 'ping' si fuera necesario.
    break;
  }
}

// -----------------------------------------------------------------------------
// 🚀 SETUP
// -----------------------------------------------------------------------------
void setup() {
  Serial.begin(115200);
  delay(500);

  // 1. Iniciar Sensor
  Serial.println("Iniciando BME280...");
  Wire.begin(I2C_SDA, I2C_SCL);

  // Dirección por defecto suele ser 0x77 o 0x76
  if (!bme.begin(0x76, &Wire)) {
    Serial.println("⚠ Error BME280 no encontrado en 0x76, probando 0x77...");
    if (!bme.begin(0x77, &Wire)) {
      Serial.println(
          "❌ ERROR FATAL: No se encuentra BME280. Revisa cableado (21/22).");
      // No bloqueamos loop para que al menos conecte WiFi y reporte error si
      // quisiéramos
    }
  }

  // 2. WiFi
  WiFi.begin(CASA_SSID, CASA_PASS); // Asume constantes definidas en config
  Serial.print("Conectando WiFi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\n✅ WiFi Conectado");

  // 3. NTP
  syncTimeNTP();

// 4. WebSocket
// Asume constantes de config_ESP32.h
#if (MODO_PRODUCCION == 1)
  webSocket.beginSSL(WEBSOCKET_HOST, WEBSOCKET_PORT, WEBSOCKET_PATH);
#else
  webSocket.begin(WEBSOCKET_HOST, WEBSOCKET_PORT, WEBSOCKET_PATH);
#endif

  webSocket.onEvent(webSocketEvent);
  webSocket.setReconnectInterval(5000);
  webSocket.enableHeartbeat(30000, 6000, 2);
}

// -----------------------------------------------------------------------------
// 🔁 LOOP
// -----------------------------------------------------------------------------
void loop() {
  webSocket.loop();

  // Lectura periódica no bloqueante
  unsigned long now = millis();
  if (now - lastMeasureTime > MEASURE_INTERVAL_MS) {
    lastMeasureTime = now;

    float t = bme.readTemperature();
    float h = bme.readHumidity();

    // Verificación básica de error (NaN)
    if (isnan(t) || isnan(h)) {
      Serial.println("⚠ Error lectura sensor");
    } else {
      sendTelemetry(t, h);
    }
  }
}
