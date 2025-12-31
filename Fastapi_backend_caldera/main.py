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
    expose_headers=["*"]
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

# Estructura de conexiones mejorada: { "id": {"ws": WebSocket, "missed_pings": int, "ip": str} }
connections: Dict[str, Dict[str, Any]] = {} 
# Frontends: { WebSocket: {"ip": str, "missed_pings": int} }
frontends: Dict[WebSocket, Dict[str, Any]] = {} 

MAX_MISSED_PINGS = 3

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
    
    # Intentamos enviar a todos, sin borrar. El keep_alive se encarga de la limpieza.
    for ws, info in frontends.items():
        await safe_send_json(ws, payload)

# --------------------------------------------------------------------------
# ❤️ KEEP-ALIVE & LIMPIEZA
# --------------------------------------------------------------------------
async def keep_alive_task():
    while True:
        await asyncio.sleep(5) # Hacemos check cada 5 segundos
        
        # 1. Gestionar ESP32s
        dead_esps = []
        for cid, info in list(connections.items()):
            ws = info["ws"]
            if not await safe_send_json(ws, {"type": "ping"}):
                info["missed_pings"] += 1
                print(f"{ts()} ⚠️ {cid} Ping fallido ({info['missed_pings']}/{MAX_MISSED_PINGS})")
                if info["missed_pings"] >= MAX_MISSED_PINGS:
                    dead_esps.append(cid)
            else:
                if info["missed_pings"] > 0:
                    print(f"{ts()} ❇️ {cid} Recuperado tras {info['missed_pings']} fallos")
                info["missed_pings"] = 0 # Reset si responde
        
        for cid in dead_esps:
            print(f"{ts()} 💀 ESP32 Muerto por Timeout: {cid}")
            if cid in connections:
                del connections[cid]
                
                # --- LÓGICA FAIL-SAFE CENTRALIZADA ---
                if cid == "esp32_02": # Se murió el Relé
                    global_state["relay_state"] = "OFF"
                    print(f"{ts()} 🛡️ FAIL-SAFE: Relé OFF forzado por desconexión de Relé")
                    
                elif cid == "esp32_03": # Se murió la Sonda
                    global_state["current_temp"] = None # Invalidar temperatura
                    global_state["relay_state"] = "OFF" # Asumir apagado por seguridad
                    print(f"{ts()} 🛡️ FAIL-SAFE: Sonda Caída -> Forzando Relé OFF")
                    
                    # Intentar apagar el Relé físicamente si sigue vivo
                    if "esp32_02" in connections:
                        # Forzamos modo MANUAL para que corte
                        try:
                            cmd_safety = {
                                "type": "config_update", 
                                "mode": "MANUAL", 
                                "target_temp": global_state["target_temp"]
                            }
                            await safe_send_json(connections["esp32_02"]["ws"], cmd_safety)
                            print(f"{ts()} 🛡️ Comando de SEGURIDAD enviado a Relé")
                        except Exception as e:
                            print(f"{ts()} ❌ Error enviando comando fail-safe: {e}")

                await broadcast_state() 

        # 2. Gestionar Frontends
        dead_fronts = []
        for ws, info in list(frontends.items()):
             if not await safe_send_json(ws, {"type": "ping"}):
                info["missed_pings"] += 1
                if info["missed_pings"] >= MAX_MISSED_PINGS:
                    dead_fronts.append(ws)
             else:
                info["missed_pings"] = 0

        for ws in dead_fronts:
            if ws in frontends:
                print(f"{ts()} 💀 Frontend Muerto por Timeout: {frontends[ws]['ip']}")
                del frontends[ws]

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(keep_alive_task())
    print(f"{ts()} 🚀 Servidor Iniciado (Modo Centralizado + Logs Detallados)")

# --------------------------------------------------------------------------
# 🔌 WEBSOCKET ENDPOINT
# --------------------------------------------------------------------------
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    client_ip = ws.client.host if ws.client else "unknown"
    
    client_id: Optional[str] = None
    role: Optional[str] = None
    
    try:
        # 1. Handshake Inicial
        try:
            init_data = await ws.receive_text()
            init_msg = json.loads(init_data)
        except Exception as e:
            print(f"{ts()} ❌ Error Handshake {client_ip}: {e}")
            await ws.close()
            return
            
        role = init_msg.get("role")
        client_id = init_msg.get("id") 

        if role == "esp32" and client_id:
            # Sobuescribir si ya existe, reiniciando contador de pings
            connections[client_id] = {"ws": ws, "missed_pings": 0, "ip": client_ip}
            print(f"{ts()} ✅ ESP32 Conectado: {client_id} desde {client_ip}")
            await ws.send_json({"type": "registered", "id": client_id})
            await broadcast_state() 

        elif role == "frontend":
            frontends[ws] = {"ip": client_ip, "missed_pings": 0}
            print(f"{ts()} 👤 Frontend Conectado desde {client_ip}")
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
                data = json.loads(raw_data)
            except json.JSONDecodeError:
                print(f"{ts()} ❌ JSON roto desde {client_ip}")
                continue
            except Exception:
                break # Salir si hay error de conexión

            msg_type = data.get("type")

            # --- CASO 1: LLEGA DATO DEL SENSOR (ESP32_03) ---
            if client_id == "esp32_03" and msg_type == "sensor_update":
                temp = data.get("temperature")
                # print(f"{ts()} 🌡️ Sonda: {temp}°C") # Comentado para no saturar si es muy frecuente
                global_state["current_temp"] = temp 
                
                if "esp32_02" in connections:
                    await safe_send_json(connections["esp32_02"]["ws"], data)
                
                await broadcast_state()

            # --- CASO 2: LLEGA STATUS DEL RELÉ (ESP32_02) ---
            elif client_id == "esp32_02" and msg_type == "status_update":
                old_mode = global_state["mode"]
                old_relay = global_state["relay_state"]
                
                new_mode = data.get("mode", old_mode)
                new_relay = data.get("relay_state", "OFF")
                
                global_state["mode"] = new_mode
                global_state["relay_state"] = new_relay
                global_state["target_temp"] = data.get("target_temp", global_state["target_temp"])
                global_state["last_update"] = time.time()
                
                # Loggear solo si hubo cambio real (Evento de Sistema)
                if old_relay != new_relay:
                    print(f"{ts()} 📢 SISTEMA: Relé cambió a {new_relay} (Modo: {new_mode})")
                
                await broadcast_state()

            # --- CASO 3: COMANDO DESDE FRONTEND ---
            elif role == "frontend" and msg_type == "config_update":
                mode = data.get("mode")
                target = data.get("target_temp")
                print(f"{ts()} 🎮 CMD Frontend [{client_ip}]: Modo={mode}, Target={target}°C")
                
                if "esp32_02" in connections:
                    await safe_send_json(connections["esp32_02"]["ws"], data)
                else:
                    print(f"{ts()} 🚫 Fallo CMD: ESP32_02 no conectado")

            # --- CASO 4: PING ---
            elif msg_type == "ping":
                await ws.send_json({"type": "pong"})

    except WebSocketDisconnect:
        print(f"{ts()} 🔌 Desconexión socket: {client_id or f'Front({client_ip})'}")
    except Exception as e:
        print(f"{ts()} ❌ Error General WS: {e}")
    finally:
        if role == "esp32" and client_id in connections:
            # Verificar que sea ESTA conexión la que se cierra (evitar race condition si se reconectó rápido)
            if connections[client_id]["ws"] == ws:
                del connections[client_id]
                
                # --- LÓGICA FAIL-SAFE (Cierre Limpio) ---
                if client_id == "esp32_02":
                    global_state["relay_state"] = "OFF"
                    print(f"{ts()} 🛡️ FAIL-SAFE: Relé OFF por DESCONEXIÓN LIMPIA")
                    
                elif client_id == "esp32_03":
                    global_state["current_temp"] = None
                    global_state["relay_state"] = "OFF"
                    print(f"{ts()} 🛡️ FAIL-SAFE: Sonda DESCONECTADA -> Forzando Relé OFF")
                    
                    if "esp32_02" in connections:
                         asyncio.create_task(safe_send_json(connections["esp32_02"]["ws"], {
                             "type": "config_update", "mode": "MANUAL", "target_temp": global_state["target_temp"]
                         }))

                await broadcast_state()
        elif role == "frontend" and ws in frontends:
            del frontends[ws]

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
