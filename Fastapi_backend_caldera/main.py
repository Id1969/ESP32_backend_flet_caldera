"""
Servidor WebSocket (FastAPI) para el control de CALDERAS mediante ESP32 y Relés.
--------------------------------------------------------------------------------------------

ARQUITECTURA
-----------
    FRONTEND(S) (App Flet)  <── WS/WSS ──>  SERVIDOR FASTAPI  <── WS/WSS ──>  ESP32 (Caldera)

OBJETIVO
--------
- Centralizar la comunicación entre la aplicación del usuario y los nodos ESP32 que activan calderas.
- El servidor enruta las órdenes de encendido y apagado hacia el ESP32 correcto (usando "id").
- Difundir el estado de la caldera ("on" / "off") a todos los dispositivos de control conectados.
- Guardar el último estado conocido para sincronizar la interfaz de usuario nada más abrirla.

PROTOCOLO (JSON)
----------------
1) Registro ESP32: { "type": "register", "role": "esp32", "id": "esp32_01", ... }
2) Registro Frontend: { "type": "register", "role": "frontend" }
3) Comando: { "type":"command", "to":"esp32_01", "device":"relay", "action":"on" }
4) Estado: { "type":"state", "from":"esp32_01", "device":"relay", "state":"on", ... }

NOTAS DE FUNCIONAMIENTO
-----------------------
- El servidor actúa como un túnel bidireccional en tiempo real.
- Incorpora un sistema de "Vigilante" (Keep-Alive) que detecta desconexiones de la caldera para avisar al usuario.
- Diseñado para ser desplegado en plataformas como Render.
"""


from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Set, Tuple

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import uvicorn


# --------------------------------------------------------------------------
# 🕒 Timestamp ISO (UTC) tipo ESP32: 2025-12-22T18:01:33Z
# --------------------------------------------------------------------------
def ts() -> str:
    # Usamos la hora local del sistema para mayor claridad en los logs del usuario
    return datetime.now().strftime("%H:%M:%S")


# --------------------------------------------------------------------------
# 🧩 Helper para identificar conexiones en logs
# --------------------------------------------------------------------------
def peer(ws: WebSocket) -> str:
    try:
        c = ws.client
        if c:
            return f"{c.host}:{c.port}"
    except Exception:
        pass
    return "unknown"


# --------------------------------------------------------------------------
# 🔧 APP FASTAPI + CORS
# --------------------------------------------------------------------------
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],         # En producción puedes restringir dominios
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------
# 🧠 ESTRUCTURAS DE ESTADO EN MEMORIA
# --------------------------------------------------------------------------

esp32_connections: Dict[str, WebSocket] = {}
esp32_meta: Dict[str, Dict[str, Any]] = {}
frontends: Dict[WebSocket, Dict[str, Any]] = {}  # Ahora es un dict para guardar meta-data
state_cache: Dict[Tuple[str, str, int], Dict[str, Any]] = {}


# --------------------------------------------------------------------------
# 🏠 RUTA DE ESTADO (Health Check)
# --------------------------------------------------------------------------
@app.get("/")
async def get_status():
    frontend_ips = [info.get("ip", "unknown") for info in frontends.values()]
    return {
        "status": "online",
        "esp32_conectados": list(esp32_connections.keys()),
        "total_frontends": len(frontends),
        "frontends_active_ips": frontend_ips,
        "cache_estados": len(state_cache),
        "timestamp": ts(),
    }


# --------------------------------------------------------------------------
# 📡 UTILIDADES DE ENVÍO SEGURO Y CACHÉ
# --------------------------------------------------------------------------
async def safe_send_json(ws: WebSocket, payload: Dict[str, Any]) -> bool:
    """Envía JSON y devuelve False si la conexión está muerta."""
    try:
        await ws.send_json(payload)
        return True
    except Exception:
        return False

async def broadcast_to_frontends(payload: Dict[str, Any]) -> None:
    """Difunde a todos los frontends registrados. Si uno falla, se purga."""
    dead = []
    for ws in list(frontends.keys()):
        if not await safe_send_json(ws, payload):
            dead.append(ws)

    for ws in dead:
        if ws in frontends:
            del frontends[ws]
            print(f"{ts()} 🧹 Frontend eliminado (broadcast falló) peer={peer(ws)}")

def cache_state(payload: Dict[str, Any]) -> None:
    """Guarda el último estado conocido por (esp32_id, device, channel)."""
    esp32_id = payload.get("from")
    device = payload.get("device")
    dev_id = payload.get("id")

    if isinstance(esp32_id, str) and isinstance(device, str) and isinstance(dev_id, int):
        state_cache[(esp32_id, device, dev_id)] = payload

def get_cached_state_for_esp32(esp32_id: str) -> Optional[Dict[str, Any]]:
    """Devuelve el estado guardado del relé 0 para un ESP32."""
    key = (esp32_id, "relay", 0)
    return state_cache.get(key)


# --------------------------------------------------------------------------
# ❤️ KEEP-ALIVE (Vigilante Activo)
# --------------------------------------------------------------------------
KEEP_ALIVE_SECONDS = 15  # Cada 15s revisamos salud

async def keep_alive_task() -> None:
    """Tarea continua que barre conexiones muertas para liberar RAM y avisar al front."""
    while True:
        await asyncio.sleep(KEEP_ALIVE_SECONDS)
        now = time.time()
        
        # 1. Vigilar Frontends (Purga por fallo de comunicación o inactividad extrema de 24h)
        dead_fronts = []
        for ws, info in list(frontends.items()):
            # Solo purgamos por inactividad si pasan 24 horas sin señales (por seguridad extrema)
            if now - info.get("last_seen", 0) > 86400:
                dead_fronts.append(ws)
                continue

            # El intento de envío detectará si la pestaña se cerró
            if not await safe_send_json(ws, {"type": "ping"}):
                dead_fronts.append(ws)
        
        for ws in dead_fronts:
            if ws in frontends:
                del frontends[ws]
                print(f"{ts()} 🧹 Limpieza: Frontend 'zombi' eliminado (peer={peer(ws)})")

        # 2. Vigilar ESP32s (Solo purga si el socket se rompe)
        dead_esps = []
        for eid, ws in list(esp32_connections.items()):
            # Solo eliminamos si el envío falla físicamente
            if not await safe_send_json(ws, {"type": "ping"}):
                dead_esps.append(eid)
        
        for eid in dead_esps:
            esp32_connections.pop(eid, None)
            esp32_meta.pop(eid, None)   # También limpiamos sus metadatos
            print(f"{ts()} 🧹 Limpieza: ESP32 'zombi' eliminado ({eid})")
            await broadcast_to_frontends({"type": "esp32_offline", "id": eid})


@app.on_event("startup")
async def startup_event():
    asyncio.create_task(keep_alive_task())
    print(f"{ts()} 🚀 Vigilante Activo: Iniciado (escaneo cada {KEEP_ALIVE_SECONDS}s)")


# --------------------------------------------------------------------------
# 📡 ENDPOINT WEBSOCKET
# --------------------------------------------------------------------------
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    print(f"{ts()} 🔌 Nueva conexión WebSocket peer={peer(ws)}")

    role: Optional[str] = None
    esp32_id: Optional[str] = None

    try:
        # Esperar mensaje de registro inicial
        init_msg = await ws.receive_json()

        if init_msg.get("type") != "register":
            await ws.send_json({"type": "error", "message": "Se esperaba register"})
            await ws.close()
            return

        role = init_msg.get("role")
        
        # --- Lógica de Registro ESP32 ---
        if role == "esp32":
            esp32_id = init_msg.get("id")
            if not esp32_id:
                await ws.close()
                return

            # Simplemente actualizar el registro sin cerrar manualmente la anterior
            # (FastAPI/Uvicorn ya gestionan el cierre de sockets huérfanos)
            esp32_connections[esp32_id] = ws
            esp32_meta[esp32_id] = {
                "mac": init_msg.get("mac"),
                "ip": init_msg.get("ip"),
                "last_seen": time.time(),
            }
            
            print(f"{ts()} ✅ ESP32 registrado: {esp32_id}")
            await safe_send_json(ws, {"type": "registered", "id": esp32_id})
            # Avisar a frontends del nuevo dispositivo (con IP)
            await broadcast_to_frontends({"type": "esp32_online", "id": esp32_id, "ip": init_msg.get("ip")})

        # --- Lógica de Registro Frontend ---
        elif role == "frontend":
            frontends[ws] = {
                "ip": peer(ws),
                "last_seen": time.time()
            }
            print(f"{ts()} ✅ Frontend registrado. Total: {len(frontends)}")
            await safe_send_json(ws, {"type": "registered", "role": "frontend", "ip": peer(ws)})
            # Enviar lista actual como eventos individuales "esp32_online" para que el front sepa IPs
            for eid, meta in esp32_meta.items():
                 await safe_send_json(ws, {"type": "esp32_online", "id": eid, "ip": meta.get("ip")})
            
            # Enviar última telemetría si existe (para que la app muestre dato de inmediato)
            sensor_ws = esp32_connections.get("esp32_03") # O buscar por ID de sensor si cambia
            if sensor_ws and "esp32_03" in esp32_meta:
                last_telem = esp32_meta["esp32_03"].get("last_telemetry")
                if last_telem:
                     await safe_send_json(ws, last_telem)
        
        else:
            await ws.close()
            return

        # --- Bucle de Escucha de Mensajes ---
        while True:
            # Actualizar last_seen en cada mensaje recibido (para el vigilante)
            if role == "frontend" and ws in frontends:
                frontends[ws]["last_seen"] = time.time()
            elif role == "esp32" and esp32_id:
                esp32_meta[esp32_id]["last_seen"] = time.time()

            data = await ws.receive_json()
            m_type = data.get("type")

            # 1. Mensajes desde ESP32
            if role == "esp32":
                if m_type == "state":
                    cache_state(data)
                    device = data.get("device", "unknown")
                    state = data.get("state", "unknown")
                    # Recuperamos quién fue el último que mandó una orden a esta placa
                    triggered_by = esp32_meta.get(esp32_id, {}).get("last_commander", "vía interruptor físico")
                    print(f"{ts()} 📢 [ESTADO] {esp32_id} -> {device}: {state.upper()} (Sincronizado con: {triggered_by})")
                    await broadcast_to_frontends(data)
                
                # El ESP32 ya no envía pings según el nuevo modelo. 
                # Si llegara un ping por error, simplemente lo ignoramos o respondemos pong.

                if m_type == "telemetry":
                    # Mensaje del SENSOR (ESP32_03)
                    temp = data.get("temp")
                    hum = data.get("hum")
                    # Cacheamos la última temperatura conocida para enviarla a nuevos clientes
                    if esp32_id: 
                         esp32_meta[esp32_id]["last_telemetry"] = data
                    
                    print(f"{ts()} 🌡️ [SENSOR] {esp32_id}: {temp}°C | {hum}%")
                    # Retransmitir a TODOS los frontends
                    await broadcast_to_frontends(data)

            # 2. Mensajes desde Frontend
            elif role == "frontend":
                if m_type == "command":
                    target_id = data.get("to")
                    action = data.get("action", "unknown").upper()
                    client_ip = peer(ws)
                    print(f"{ts()} 🎮 [COMANDO] Desde: {client_ip} -> Para: {target_id} -> Acción: {action}")
                    
                    # Guardamos en los metadatos de la placa quién le está mandando la orden
                    if target_id in esp32_meta:
                        esp32_meta[target_id]["last_commander"] = client_ip
                    
                    target_ws = esp32_connections.get(target_id)
                    if target_ws:
                        ok = await safe_send_json(target_ws, data)
                        if not ok:
                            # Purga por fallo: el ESP32 no respondió al envío
                            del esp32_connections[target_id]
                            await broadcast_to_frontends({"type": "esp32_offline", "id": target_id})
                    else:
                        # Si no está en el dict, avisamos al front que está offline
                        await safe_send_json(ws, {"type": "esp32_offline", "id": target_id})

                elif m_type == "get_state":
                    target_id = data.get("to")
                    cached = get_cached_state_for_esp32(target_id)
                    if cached:
                        await safe_send_json(ws, cached)
                    else:
                        # Si no hay cache, se lo pedimos al ESP32 (si existe)
                        tw = esp32_connections.get(target_id)
                        if tw:
                            await safe_send_json(tw, {"type": "get_state"})

    except WebSocketDisconnect:
        print(f"{ts()} 🔌 Desconexión limpia (WebSocketDisconnect) role={role} id={esp32_id}")
    except Exception as e:
        print(f"{ts()} ⚠ Error en bucle WS: {e} role={role} id={esp32_id}")
    finally:
        # Limpieza al desconectar
        if role == "frontend":
            if ws in frontends:
                del frontends[ws]
            print(f"{ts()} 🧹 Frontend desconectado")
        
        if role == "esp32" and esp32_id:
            if esp32_connections.get(esp32_id) == ws:
                del esp32_connections[esp32_id]
                print(f"{ts()} 🧹 ESP32 desconectado: {esp32_id}")
                await broadcast_to_frontends({"type": "esp32_offline", "id": esp32_id})


# --------------------------------------------------------------------------
# 🏁 EJECUCIÓN LOCAL
# --------------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
