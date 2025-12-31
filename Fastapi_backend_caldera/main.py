"""
Servidor WebSocket (FastAPI) para el control de CALDERAS - Arquitectura Centralizada.
--------------------------------------------------------------------------------------------
ARQUITECTURA NUEVA (PRODUCCIÓN):
    ESP32_02 (RELÉ)   <── WS ──>  BACKEND  <── WS ──>  FRONTEND (Flet)
           ^                         ^
           └──────── WS ─────────────┘
             ESP32_03 (SENSOR)

Compatible con:
- ESP32 con enableHeartbeat (Cliente PING -> Servidor PONG) (y viceversa)
- Logs optimizados para Render.
"""

from __future__ import annotations
import asyncio
import time
import json 
from datetime import datetime
from typing import Any, Dict, Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

# --------------------------------------------------------------------------
# 🔧 APP FASTAPI & CONFIG
# --------------------------------------------------------------------------
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --------------------------------------------------------------------------
# 🧠 ESTADO GLOBAL DEL SISTEMA
# --------------------------------------------------------------------------
global_state = {
    "mode": "MANUAL",        
    "relay_state": "OFF",    
    "current_temp": None,    
    "target_temp": 21.5,     
    "last_update": 0
}

# Estructura de conexiones: { "esp32_02":WebSocket, "esp32_03":WebSocket }
esp32_clients: Dict[str, WebSocket] = {}
# Frontends: Lista de WebSockets
front_clients: list[WebSocket] = []

# --------------------------------------------------------------------------
# 📝 LOGGING UNIFICADO
# --------------------------------------------------------------------------
def ts() -> str:
    return datetime.now().strftime("%H:%M:%S")

def log_event(origen: str, accion: str, mensaje: str = ""):
    print(f"{ts()} [{origen}] {accion} | {mensaje}")

# --------------------------------------------------------------------------
# 📡 UTILIDADES DE ENVÍO
# --------------------------------------------------------------------------
async def safe_send_json(ws: WebSocket, payload: Dict[str, Any]) -> bool:
    try:
        await ws.send_json(payload)
        return True
    except Exception:
        return False

async def broadcast_state() -> None:
    """Envía el estado completo a todos los frontends."""
    payload = {
        "type": "full_state_update",
        "connection_status": {
            "esp32_02": "connected" if "esp32_02" in esp32_clients else "disconnected",
            "esp32_03": "connected" if "esp32_03" in esp32_clients else "disconnected"
        },
        "system_state": global_state,
        "timestamp": ts()
    }
    
    # Broadcast a frontends
    to_remove = []
    for ws in front_clients:
        if not await safe_send_json(ws, payload):
            to_remove.append(ws)
    
    for ws in to_remove:
        if ws in front_clients:
            front_clients.remove(ws)

# --------------------------------------------------------------------------
# ❤️ KEEP-ALIVE & LIMPIEZA DE ZOMBIS
# --------------------------------------------------------------------------
async def keep_alive_task():
    """
    Tarea de fondo para pings periódicos y limpieza.
    - Los ESP32 ahora tienen heartbeat propio, pero el servidor también verifica.
    """
    while True:
        await asyncio.sleep(15) # Check cada 15s (menos agresivo que antes)
        
        # 1. Ping a ESP32s
        dead_esps = []
        for pid, ws in list(esp32_clients.items()):
            if not await safe_send_json(ws, {"type": "ping"}):
                dead_esps.append(pid)
        
        # 2. Limpieza y Fail-Safe
        for pid in dead_esps:
            log_event("SISTEMA", "Limpieza Zombie", f"Eliminando {pid} por timeout")
            await handle_disconnect(pid)

        # 3. Ping a Frontends
        dead_fronts = []
        for ws in front_clients:
            if not await safe_send_json(ws, {"type": "ping"}):
                dead_fronts.append(ws)
        
        for ws in dead_fronts:
            if ws in front_clients:
                front_clients.remove(ws)

async def handle_disconnect(client_id: str):
    """Maneja la desconexión de un ESP32 con lógica de seguridad."""
    if client_id in esp32_clients:
        del esp32_clients[client_id]
    
    msg_extra = ""
    # --- LÓGICA FAIL-SAFE ---
    if client_id == "esp32_02": # Se fue el Relé
        global_state["relay_state"] = "OFF"
        msg_extra = "🛡️ FAIL-SAFE: Relé OFF (Desconexión)"
        
    elif client_id == "esp32_03": # Se fue la Sonda
        global_state["current_temp"] = None
        global_state["relay_state"] = "OFF"
        msg_extra = "🛡️ FAIL-SAFE: Sonda OFF -> Forzando Relé OFF"
        
        # Intentar apagar físicamente el relé si sigue vivo
        if "esp32_02" in esp32_clients:
            try:
                cmd = {"type": "config_update", "mode": "MANUAL", "target_temp": global_state["target_temp"]}
                await safe_send_json(esp32_clients["esp32_02"], cmd)
                log_event("FAIL-SAFE", "Comando de emergencia enviado a Relé")
            except Exception:
                pass

    log_event("CONEXIÓN", "Desconexión detectada", f"{client_id} {msg_extra}")
    await broadcast_state()

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(keep_alive_task())
    log_event("SISTEMA", "Inicio Servidor", "Modo Centralizado Listo (Render Optimized)")

# --------------------------------------------------------------------------
# 🔌 WEBSOCKET ENDPOINT
# --------------------------------------------------------------------------
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    client_ip = ws.client.host if ws.client else "unknown"
    
    role = None
    client_id = None
    
    try:
        while True:
            # Esperar JSON
            data = await ws.receive_json()
            msg_type = data.get("type", "")

            # --- FASE 1: REGISTRO/IDENTIFICACIÓN ---
            if not role: 
                # Admitimos "register" (ESP32) o infiere por primer mensaje
                if msg_type == "register":
                    role = data.get("role") # "esp32"
                    client_id = data.get("id")
                    
                    if role == "esp32" and client_id:
                        esp32_clients[client_id] = ws
                        log_event("CONEXIÓN", "ESP32 Registrado", f"ID: {client_id} | IP: {client_ip}")
                        await ws.send_json({"type": "registered", "id": client_id})
                        await broadcast_state()
                    else:
                        # Frontend suele mandar register o simplemente conectar
                        # Si no es esp32, asumimos frontend si type="register" o connect
                        role = "frontend"
                        if ws not in front_clients:
                            front_clients.append(ws)
                        log_event("CONEXIÓN", "Frontend Registrado", f"IP: {client_ip}")
                        await ws.send_json({"type": "registered"})
                        await broadcast_state()
                else:
                     # Si manda algo distinto a register de entrada, asumimos frontend o error
                     role = "frontend" # Fallback permissive
                     if ws not in front_clients:
                        front_clients.append(ws)
                     log_event("CONEXIÓN", "Frontend Auto-Detectado", f"IP: {client_ip} (msg: {msg_type})")

                # Continuar al siguiente loop ya identificado
                continue

            # --- FASE 2: OPERACIÓN NORMAL ---
            
            # > PING/PONG (Heartbeat de librería Client o Server)
            if msg_type == "ping":
                await ws.send_json({"type": "pong"})
                continue
            if msg_type == "pong":
                # Heartbeat ack, no action needed
                continue

            # > MENSAJES DE ESP32
            if role == "esp32":
                if msg_type == "sensor_update": # ESP32_03
                    temp = data.get("temperature")
                    global_state["current_temp"] = temp
                    # Reenviar a relé si existe
                    if "esp32_02" in esp32_clients:
                        await safe_send_json(esp32_clients["esp32_02"], data)
                    await broadcast_state()
                
                elif msg_type == "status_update": # ESP32_02
                    global_state["mode"] = data.get("mode", global_state["mode"])
                    new_relay = data.get("relay_state", "OFF")
                    
                    if global_state["relay_state"] != new_relay:
                        log_event("SISTEMA", "Cambio Estado Relé", f"{new_relay} (Modo: {global_state['mode']})")
                    
                    global_state["relay_state"] = new_relay
                    global_state["target_temp"] = data.get("target_temp", global_state["target_temp"])
                    await broadcast_state()

            # > MENSAJES DE FRONTEND
            elif role == "frontend":
                if msg_type == "config_update":
                    log_event("COMANDO", "Config Update", f"Modo={data.get('mode')}, T={data.get('target_temp')}")
                    # Reenviar a ESP32_02
                    if "esp32_02" in esp32_clients:
                        await safe_send_json(esp32_clients["esp32_02"], data)
                    else:
                         log_event("ERROR", "No se pudo enviar comando", "ESP32_02 desconectado")

    except WebSocketDisconnect:
        # Usar la misma lógica de limpieza
        if role == "esp32" and client_id:
            await handle_disconnect(client_id)
        elif role == "frontend":
             if ws in front_clients:
                 front_clients.remove(ws)
             log_event("CONEXIÓN", "Frontend Desconectado", client_ip)
    except Exception as e:
        log_event("ERROR", "Excepción WS", str(e))
        # Intentar limpieza agresiva si hay error
        if role == "esp32" and client_id:
            await handle_disconnect(client_id)

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
