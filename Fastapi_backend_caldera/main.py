"""
Servidor WebSocket (FastAPI) para el control de CALDERAS - Arquitectura Centralizada.
--------------------------------------------------------------------------------------------
ARQUITECTURA NUEVA:
    ESP32_02 (RELÉ)   <── WS ──>  BACKEND  <── WS ──>  FRONTEND (App)
           ^                         ^
           └──────── WS ─────────────┘
             ESP32_03 (SENSOR)

FLUJO DE DATOS:
1. ESP32_03 -> Backend: {"type": "sensor_update", "temperature": 20.5}
   -> Backend reenvía a ESP32_02 (para control) y a Frontends (para UI).

2. Frontend -> Backend: {"type": "config_update", "mode": "AUTO", "target_temp": 22}
   -> Backend reenvía a ESP32_02.

3. ESP32_02 -> Backend: {"type": "status_update", "relay_state": "ON", ...}
   -> Backend actualiza ESTADO GLOBAL y difunde a Frontends.
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
# 🕒 Timestamp
# --------------------------------------------------------------------------
def ts() -> str:
    return datetime.now().strftime("%H:%M:%S")

# --------------------------------------------------------------------------
# 🔧 APP FASTAPI
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

# Conexiones activas
connections: Dict[str, WebSocket] = {}  # "esp32_02", "esp32_03"
frontends: Dict[WebSocket, str] = {}    # ws -> ip

# --------------------------------------------------------------------------
# 📡 UTILIDADES
# --------------------------------------------------------------------------
async def safe_send_json(ws: WebSocket, payload: Dict[str, Any]) -> bool:
    try:
        await ws.send_json(payload)
        return True
    except Exception:
        return False

async def broadcast_state() -> None:
    """Envía el estado completo a todos los frontends conectados."""
    payload = {
        "type": "full_state_update",
        "connection_status": {
            "esp32_02": "connected" if "esp32_02" in connections else "disconnected",
            "esp32_03": "connected" if "esp32_03" in connections else "disconnected"
        },
        "system_state": global_state,
        "timestamp": ts()
    }
    
    dead_fronts = []
    for ws in list(frontends.keys()):
        if not await safe_send_json(ws, payload):
            dead_fronts.append(ws)
    
    for ws in dead_fronts:
        if ws in frontends:
            del frontends[ws]

# --------------------------------------------------------------------------
# ❤️ KEEP-ALIVE & LIMPIEZA
# --------------------------------------------------------------------------
async def keep_alive_task():
    while True:
        await asyncio.sleep(15)
        # 1. Purgar conexiones ESP32 muertas
        dead_esps = []
        for cid, ws in list(connections.items()):
            if not await safe_send_json(ws, {"type": "ping"}):
                dead_esps.append(cid)
        
        for cid in dead_esps:
            print(f"{ts()} 🧹 ESP32 Caído: {cid}")
            # FIX: Verificar si existe antes de borrar para evitar KeyError (Race Condition)
            if cid in connections:
                del connections[cid]
                # FIX: Si se cae el Relé, forzar estado OFF en la verdad global
                if cid == "esp32_02":
                    global_state["relay_state"] = "OFF"
                await broadcast_state() 

        # 2. Purgar Frontends muertos
        dead_fronts = []
        for ws in list(frontends.keys()):
             if not await safe_send_json(ws, {"type": "ping"}):
                dead_fronts.append(ws)
        for ws in dead_fronts:
            if ws in frontends: del frontends[ws]

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(keep_alive_task())
    print(f"{ts()} 🚀 Servidor Iniciado (Modo Centralizado + DEBUG)")

# --------------------------------------------------------------------------
# 🔌 WEBSOCKET ENDPOINT
# --------------------------------------------------------------------------
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    client_id: Optional[str] = None
    role: Optional[str] = None
    
    try:
        # 1. Handshake Inicial
        try:
            init_data = await ws.receive_text()
            init_msg = json.loads(init_data)
        except Exception as e:
            print(f"{ts()} ❌ Error General WS: {e}")
            await ws.close()
            return
            
        role = init_msg.get("role")
        client_id = init_msg.get("id") 

        if role == "esp32" and client_id:
            connections[client_id] = ws
            print(f"{ts()} ✅ ESP32 Conectado: {client_id}")
            await ws.send_json({"type": "registered", "id": client_id})
            await broadcast_state() 

        elif role == "frontend":
            frontends[ws] = ws.client.host if ws.client else "unknown"
            print(f"{ts()} 👤 Frontend Conectado: {frontends[ws]}")
            await ws.send_json({"type": "registered"})
            await broadcast_state() 

        else:
            print(f"⚠️ Rol desconocido o falta ID: {init_msg}")
            await ws.close()
            return

        # 2. Bucle principal
        while True:
            try:
                raw_data = await ws.receive_text()
                # print(f"{ts()} [DEBUG] RAW Loop: {raw_data}") 
                data = json.loads(raw_data)
            except json.JSONDecodeError:
                print(f"{ts()} ❌ JSON roto en bucle")
                continue

            msg_type = data.get("type")

            # --- CASO 1: LLEGA DATO DEL SENSOR (ESP32_03) ---
            if client_id == "esp32_03" and msg_type == "sensor_update":
                temp = data.get("temperature")
                print(f"{ts()} 🌡️ Sonda: {temp}°C")
                global_state["current_temp"] = temp 
                
                if "esp32_02" in connections:
                    await safe_send_json(connections["esp32_02"], data)
                
                await broadcast_state()

            # --- CASO 2: LLEGA STATUS DEL RELÉ (ESP32_02) ---
            elif client_id == "esp32_02" and msg_type == "status_update":
                global_state["mode"] = data.get("mode", global_state["mode"])
                global_state["relay_state"] = data.get("relay_state", "OFF")
                global_state["target_temp"] = data.get("target_temp", global_state["target_temp"])
                global_state["last_update"] = time.time()
                
                # print(f"{ts()} 📢 Relé Update") 
                await broadcast_state()

            # --- CASO 3: COMANDO DESDE FRONTEND ---
            elif role == "frontend" and msg_type == "config_update":
                print(f"{ts()} 🎮 Comando Usuario: {data}")
                if "esp32_02" in connections:
                    await safe_send_json(connections["esp32_02"], data)
                else:
                    print(f"{ts()} ⚠ Comando ignorado: ESP32_02 desconectado")

            # --- CASO 4: PING ---
            elif msg_type == "ping":
                await ws.send_json({"type": "pong"})

    except WebSocketDisconnect:
        print(f"{ts()} 🔌 Desconectado: {client_id or 'Frontend'}")
    except Exception as e:
        print(f"{ts()} ❌ Error General WS: {e}")
    finally:
        if role == "esp32" and client_id in connections:
            del connections[client_id]
            # FIX: Asegurar estado OFF visual si se desconecta
            if client_id == "esp32_02":
                global_state["relay_state"] = "OFF"
            await broadcast_state()
        elif role == "frontend" and ws in frontends:
            del frontends[ws]

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
