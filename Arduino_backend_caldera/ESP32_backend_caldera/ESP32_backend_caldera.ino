/*
==========================================================================================
 PROYECTO: Control remoto de CALDERA con ESP32 (WiFi + Relé)
==========================================================================================

 OBJETIVO
 --------
 - Controlar el encendido/apagado de una caldera mediante un relé (GPIO4).
 - Conexión vía WebSocket (WS/WSS) con servidor FastAPI.
 - Soportar múltiples placas y frontends de control.
 - Protocolo JSON robusto con timestamps ISO 8601.

 ARQUITECTURA
 -----------
    APP MÓVIL/WEB  <── WS/WSS ──>  SERVIDOR FASTAPI  <── WS/WSS ──>  ESP32 +
RELÉ (CALDERA)

 FUNCIONAMIENTO
 --------------
 1) El ESP32 actúa como un interruptor WiFi para la caldera.
 2) El relé se conecta a la entrada de termostato de la caldera.
 3) Recibe comandos "on"/"off" y confirma el estado real tras accionar el pin.
 4) Incluye modo AP (Access Point) para configurar la WiFi si falla la conexión.

==========================================================================================
*/

#include <Arduino.h>
#include <WiFi.h>

#include <WebServer.h>
#include <WebSocketsClient.h>

#include <ArduinoJson.h>
#include <EEPROM.h>

#include <Adafruit_NeoPixel.h>
#include <time.h>

// -----------------------------------------------------------------------------
// 🔧 CONFIGURACIÓN EXTERNA (WiFi, servidor WebSocket, ID placa)
// -----------------------------------------------------------------------------
#include "config_ESP32.h"

// -----------------------------------------------------------------------------
// 💾 EEPROM (para almacenar SSID/pass configurados en modo AP)
// -----------------------------------------------------------------------------
#define EEPROM_SIZE 96

// -----------------------------------------------------------------------------
// 🔌 HARDWARE
// -----------------------------------------------------------------------------

// NeoPixel integrado del ESP32-C6 (en tu proyecto actual está en GPIO 8)
#define NEOPIXEL_PIN 8
#define NUMPIXELS 1
Adafruit_NeoPixel pixels(NUMPIXELS, NEOPIXEL_PIN, NEO_GRB + NEO_KHZ800);

// Relé: pin de control
#define RELE_PIN 4

// Ajusta esto si tu módulo de relé es "activo en LOW"
#define RELAY_ACTIVE_HIGH 1 // 1: HIGH=ON  |  0: LOW=ON

// -----------------------------------------------------------------------------
// 🌐 RED: WebSocket + servidor web AP
// -----------------------------------------------------------------------------
WebSocketsClient webSocket;
WebServer server(80);
bool apMode = false;

// Datos del servidor WebSocket desde config_ESP32.h
const char *websocket_host = WEBSOCKET_HOST;
const uint16_t websocket_port = WEBSOCKET_PORT;
const char *websocket_path = WEBSOCKET_PATH;

// Redes WiFi prioritarias desde config_ESP32.h
const char *casa_ssid = CASA_SSID;
const char *casa_pass = CASA_PASS;
const char *movil_ssid = MOVIL_SSID;
const char *movil_pass = MOVIL_PASS;

// -----------------------------------------------------------------------------
// 🧠 ESTADO (FUENTE DE VERDAD)
// -----------------------------------------------------------------------------
bool relayState = false; // true=ON, false=OFF

// -----------------------------------------------------------------------------
// ⏱ Keep-alive JSON (Render-friendly)
// -----------------------------------------------------------------------------
unsigned long lastPingMs = 0;
const unsigned long PING_INTERVAL_MS = 25000; // ~25s

// -----------------------------------------------------------------------------
// 🕒 NTP / TIMESTAMP
// -----------------------------------------------------------------------------

// Devuelve timestamp ISO 8601 en UTC: "YYYY-MM-DDTHH:MM:SSZ"
String isoTimestampUTC() {
  time_t now = time(nullptr);

  // Si no hay hora válida, devolvemos un valor claro (puedes cambiarlo si
  // quieres)
  if (now < 1700000000) { // ~2023-11-14
    return String("1970-01-01T00:00:00Z");
  }

  struct tm tm;
  gmtime_r(&now, &tm);

  char buf[30];
  strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%SZ", &tm);
  return String(buf);
}

// Sincroniza hora por NTP (UTC). Llamar tras conectar WiFi.
void syncTimeNTP() {
  configTime(0, 0, "pool.ntp.org", "time.nist.gov");

  Serial.print("⏳ Sincronizando hora (NTP)...");
  time_t now = time(nullptr);

  int retries = 0;
  while (now < 1700000000 && retries < 25) { // ~12.5s
    delay(500);
    Serial.print(".");
    now = time(nullptr);
    retries++;
  }
  Serial.println();

  if (now >= 1700000000) {
    Serial.print("✅ Hora sincronizada (UTC): ");
    Serial.println(isoTimestampUTC());
  } else {
    Serial.println("⚠ No se pudo sincronizar hora. Continuo sin NTP.");
  }
}

// -----------------------------------------------------------------------------
// 💾 EEPROM: guardar/cargar credenciales en modo AP
// -----------------------------------------------------------------------------
void saveCredentials(const String &ssid, const String &pass) {
  EEPROM.writeString(0, ssid);
  EEPROM.writeString(32, pass);
  EEPROM.commit();
}

void loadCredentials(String &ssid, String &pass) {
  ssid = EEPROM.readString(0);
  pass = EEPROM.readString(32);
}

// -----------------------------------------------------------------------------
// 💡 LED NeoPixel (ESPEJO del relé)
// -----------------------------------------------------------------------------
void setupNeoPixel() {
  pixels.begin();
  pixels.clear();
  pixels.show();
}

// LED verde suave si ON, apagado si OFF
void setNeoPixelMirror(bool on) {
  if (on) {
    pixels.setPixelColor(0, pixels.Color(0, 20, 0));
  } else {
    pixels.setPixelColor(0, pixels.Color(0, 0, 0));
  }
  pixels.show();
}

// -----------------------------------------------------------------------------
// 🔌 RELÉ: inicialización y control (FUENTE DE VERDAD)
// -----------------------------------------------------------------------------
void setupRelay() {
  pinMode(RELE_PIN, OUTPUT);

  // Estado seguro inicial: OFF
#if RELAY_ACTIVE_HIGH
  digitalWrite(RELE_PIN, LOW);
#else
  digitalWrite(RELE_PIN, HIGH);
#endif

  relayState = false;
  setNeoPixelMirror(false);
}

// Cambia relé + actualiza LED espejo
void setRelay(bool on) {
#if RELAY_ACTIVE_HIGH
  digitalWrite(RELE_PIN, on ? HIGH : LOW);
#else
  digitalWrite(RELE_PIN, on ? LOW : HIGH);
#endif

  relayState = on;
  setNeoPixelMirror(on);

  Serial.println(on ? "🟢 RELÉ ON" : "🔴 RELÉ OFF");
}

// -----------------------------------------------------------------------------
// 📤 Mensajes al servidor (register / state / ping)
// -----------------------------------------------------------------------------

// Envía el registro con id, mac e ip (se manda en cada conexión/reconexión)
void sendRegister() {
  StaticJsonDocument<320> doc;
  doc["type"] = "register";
  doc["role"] = "esp32";
  doc["id"] = ID_PLACA;

  doc["mac"] = WiFi.macAddress();
  doc["ip"] = WiFi.localIP().toString();
  doc["ts"] = isoTimestampUTC();

  String out;
  serializeJson(doc, out);
  webSocket.sendTXT(out);

  Serial.print("[WS] -> register: ");
  Serial.println(out);
}

// Envía el estado actual del relé
void sendState() {
  StaticJsonDocument<300> doc;
  doc["type"] = "state";
  doc["from"] = ID_PLACA;
  doc["device"] = "relay";
  doc["id"] = 0;
  doc["state"] = relayState ? "on" : "off";
  doc["ts"] = isoTimestampUTC();

  String out;
  serializeJson(doc, out);
  webSocket.sendTXT(out);

  Serial.print("[WS] -> state: ");
  Serial.println(out);
}

// -----------------------------------------------------------------------------
// 📥 Mensajes entrantes (command / get_state / registered / pong)
// -----------------------------------------------------------------------------

// Versión robusta: acepta JsonObject
bool isForMe(JsonObject obj) {
  if (!obj.containsKey("to"))
    return true;
  const char *to = obj["to"];
  return (to && strcmp(to, ID_PLACA) == 0);
}

void handleIncomingMessage(const char *message) {
  if (!message)
    return;

  // 0) Filtrado rápido: si no parece JSON, ignoramos
  while (*message == ' ' || *message == '\n' || *message == '\r' ||
         *message == '\t') {
    message++;
  }
  if (*message != '{') {
    Serial.print("[WS] (ignorado) no es JSON: ");
    Serial.println(message);
    return;
  }

  StaticJsonDocument<640> doc;

  // 1) Parse JSON
  DeserializationError err = deserializeJson(doc, message);
  if (err) {
    Serial.print("[WS] ❌ JSON inválido: ");
    Serial.println(err.c_str());
    return;
  }

  // 2) Forzar root a objeto (muy robusto)
  if (!doc.is<JsonObject>()) {
    Serial.println("[WS] ⚠ Root JSON NO es objeto");
    return;
  }
  JsonObject obj = doc.as<JsonObject>();

  // 3) Leer 'type' de forma segura
  if (!obj.containsKey("type")) {
    Serial.println("[WS] ⚠ JSON sin 'type' (claves recibidas):");
    for (JsonPair kv : obj) {
      Serial.print("   - ");
      Serial.println(kv.key().c_str());
    }
    return;
  }

  const char *type = obj["type"].as<const char *>();
  if (!type) {
    Serial.println("[WS] ⚠ 'type' existe pero no es string");
    return;
  }

  if (strcmp(type, "ping") == 0) {
    // El servidor envía este ping para verificar salud.
    // Al recibirlo, el socket se mantiene activo. No requiere respuesta
    // obligatoria.
    return;
  }

  if (strcmp(type, "pong") == 0) {
    Serial.println("[WS] <- pong");
    return;
  }

  if (strcmp(type, "registered") == 0) {
    Serial.print("[WS] <- registered: ");
    serializeJson(obj, Serial);
    Serial.println();
    return;
  }

  // 5) Filtrar por destinatario
  if (!isForMe(obj))
    return;

  // 6) get_state
  if (strcmp(type, "get_state") == 0) {
    Serial.println("[WS] <- get_state (respondiendo con state)");
    sendState();
    return;
  }

  // 7) command  ✅ FIX ROBUSTO AQUÍ
  if (strcmp(type, "command") == 0) {

    if (!obj.containsKey("device") || !obj.containsKey("action")) {
      Serial.println("[WS] ⚠ command incompleto (faltan device/action). Claves "
                     "recibidas:");
      for (JsonPair kv : obj) {
        Serial.print("   - ");
        Serial.println(kv.key().c_str());
      }
      return;
    }

    const char *device = obj["device"].as<const char *>();
    const char *action = obj["action"].as<const char *>();
    int id = obj.containsKey("id") ? obj["id"].as<int>() : 0;

    if (!device || !action) {
      Serial.println("[WS] ⚠ device/action existen pero no son string (o "
                     "vienen null). JSON:");
      serializeJson(obj, Serial);
      Serial.println();
      return;
    }

    if (strcmp(device, "relay") != 0 || id != 0) {
      Serial.println("[WS] ⚠ device/id no soportado aún");
      return;
    }

    Serial.print("[WS] <- command relay id=0 action=");
    Serial.println(action);

    if (strcmp(action, "on") == 0) {
      setRelay(true);
    } else if (strcmp(action, "off") == 0) {
      setRelay(false);
    } else if (strcmp(action, "toggle") == 0) {
      setRelay(!relayState);
    } else if (strcmp(action, "status") == 0) {
      // no cambia nada
    } else {
      Serial.print("[WS] ⚠ Acción desconocida: ");
      Serial.println(action);
      return;
    }

    sendState();
    return;
  }

  // 8) Otros tipos
  Serial.print("[WS] ⚠ type desconocido: ");
  Serial.println(type);
}

// -----------------------------------------------------------------------------
// 🔔 Eventos de WebSocket
// -----------------------------------------------------------------------------
void webSocketEvent(WStype_t type, uint8_t *payload, size_t length) {
  switch (type) {

  case WStype_CONNECTED:
    Serial.println("[WS] ✅ Conectado al servidor");
    sendRegister();
    sendState();
    break;

  case WStype_TEXT: {
    // FIX CRÍTICO: copiamos respetando length (payload no es \0-terminated)
    String msg;
    msg.reserve(length + 1);
    for (size_t i = 0; i < length; i++)
      msg += (char)payload[i];

    Serial.print("[WS] <- RAW (len=");
    Serial.print(length);
    Serial.print("): ");
    Serial.println(msg);

    handleIncomingMessage(msg.c_str());
    break;
  }

  case WStype_DISCONNECTED:
    Serial.println("[WS] ❌ Desconectado (reintentando...)");
    // ⚠️ FAILSAFE: Apagar caldera inmediatamente si se pierde conexión
    setRelay(false);
    break;

  case WStype_ERROR:
    Serial.println("[WS] ⚠ Error WebSocket");
    break;

  // Algunos cores/librerías emiten estos eventos
  case WStype_PING:
    Serial.println("[WS] <- (control) PING frame");
    break;

  case WStype_PONG:
    Serial.println("[WS] <- (control) PONG frame");
    break;

  default:
    break;
  }
}

// -----------------------------------------------------------------------------
// 📡 Modo AP (portal simple para configurar WiFi)
// -----------------------------------------------------------------------------
void startAPMode() {
  apMode = true;

  WiFi.softAP("ESP32_Config", "12345678");
  Serial.println("=== MODO CONFIGURACIÓN ACTIVADO ===");
  Serial.println("WiFi: ESP32_Config (pass: 12345678)");
  Serial.print("URL: http://");
  Serial.println(WiFi.softAPIP());

  server.on("/", HTTP_GET, []() {
    String html = "<h2>Configurar WiFi</h2>";
    html += "<form action='/save'>";
    html += "SSID: <input name='ssid'><br>";
    html += "Password: <input name='pass'><br>";
    html += "<input type='submit' value='Guardar'>";
    html += "</form>";
    server.send(200, "text/html", html);
  });

  server.on("/save", HTTP_GET, []() {
    String ssid = server.arg("ssid");
    String pass = server.arg("pass");

    saveCredentials(ssid, pass);

    server.send(200, "text/html", "Guardado. Reiniciando...");
    delay(1000);
    ESP.restart();
  });

  server.begin();
}

// -----------------------------------------------------------------------------
// 🌐 Conexión WiFi por prioridades
// -----------------------------------------------------------------------------
bool conectarWiFi(const char *ssid, const char *password) {
  Serial.printf("Intentando conectar a %s...\n", ssid);
  WiFi.begin(ssid, password);

  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 8000) {
    delay(500);
    Serial.print(".");
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("\n✅ Conectado a %s — IP: %s\n", ssid,
                  WiFi.localIP().toString().c_str());
    return true;
  }

  Serial.printf("\n❌ Fallo conectando a %s\n", ssid);
  return false;
}

// -----------------------------------------------------------------------------
// 🚀 SETUP
// -----------------------------------------------------------------------------
void setup() {
  Serial.begin(115200);
  delay(200);

  EEPROM.begin(EEPROM_SIZE);

  setupNeoPixel();
  setupRelay(); // OFF seguro

  // Cargar credenciales guardadas
  String savedSsid, savedPass;
  loadCredentials(savedSsid, savedPass);

  // Conectar: Casa -> Móvil -> EEPROM
  if (!conectarWiFi(casa_ssid, casa_pass) &&
      !conectarWiFi(movil_ssid, movil_pass) &&
      !(savedSsid == "" || savedPass == "" ||
        !conectarWiFi(savedSsid.c_str(), savedPass.c_str()))) {
    Serial.println("⚠ No hay conexión. Activando modo AP...");
    startAPMode();
    return;
  }

  // Sincronizar hora NTP (UTC)
  syncTimeNTP();

  // WebSocket WS/WSS
  if (websocket_port == 443) {
    Serial.println("🔐 Conectando con SSL (WSS)...");
    webSocket.beginSSL(websocket_host, websocket_port, websocket_path);
  } else {
    Serial.println("🔓 Conectando sin SSL (WS)...");
    webSocket.begin(websocket_host, websocket_port, websocket_path);
  }

  webSocket.onEvent(webSocketEvent);
  webSocket.setReconnectInterval(5000);

  // 💓 HEARTBEAT (Ping cada 30s, espera pong 6s, 2 fallos = desconectar)
  // Esto mantiene la conexión TCP/WS viva y evita cierres por inactividad de
  // routers/firewalls
  webSocket.enableHeartbeat(30000, 6000, 2);
}

// -----------------------------------------------------------------------------
// 🔁 LOOP
// -----------------------------------------------------------------------------
void loop() {
  if (apMode) {
    server.handleClient();
    return;
  }

  webSocket.loop();
}
