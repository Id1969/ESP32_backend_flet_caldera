"""
===========================================================================================
 PROYECTO: Termostato Inteligente WiFi (App Flet - VERSIÓN SIMPLIFICADA)
===========================================================================================
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
import os
from typing import Callable, Optional

import flet as ft
import websockets
from dotenv import load_dotenv

# ===============================================================================
# 🌐 CONFIGURACIÓN
# ===============================================================================
load_dotenv()
PORT = int(os.environ.get("PORT", 0))
WEBSOCKET_URL = os.getenv("WEBSOCKET_URL")

if not WEBSOCKET_URL:
    raise ValueError("⚠️ ERROR: La variable WEBSOCKET_URL no está definida en .env")

# IDs de Dispositivos (Hardcoded)
RELAY_ID = "esp32_02"
SENSOR_ID = "esp32_03"


# ===============================================================================
# 🧠 CLIENTE WEBSOCKET
# ===============================================================================
class WebSocketClient:
    def __init__(self, ui_callback: Callable[[dict], None]):
        self.websocket: Optional[websockets.WebSocketClientProtocol] = None
        self.ui_callback = ui_callback
        self._stop = False

    async def connect_forever(self):
        while not self._stop:
            try:
                await self._connect_once()
            except Exception as e:
                print(f"❌ Error WS: {e}")
            finally:
                self.websocket = None
                self.ui_callback({"type": "server_disconnected"})
                await asyncio.sleep(5)

    async def _connect_once(self):
        print(f"🔌 Conectando a {WEBSOCKET_URL}...")
        async with websockets.connect(WEBSOCKET_URL, ping_interval=None) as ws:
            self.websocket = ws
            print("✅ Conectado")
            
            await self.send_json({"type": "register", "role": "frontend"})
            
            async for message in ws:
                data = json.loads(message)
                if isinstance(data, dict):
                    self.ui_callback(data)

    async def send_json(self, payload: dict):
        if self.websocket:
            try:
                await self.websocket.send(json.dumps(payload))
            except Exception:
                self.websocket = None

    async def request_state(self, esp32_id: str):
        await self.send_json({"type": "get_state", "to": esp32_id})

    async def command_relay(self, action: str):
        print(f"🕹️ Enviando {action.upper()} a {RELAY_ID}")
        await self.send_json({
            "type": "command",
            "to": RELAY_ID,
            "device": "relay",
            "id": 0,
            "action": action,
        })


# ===============================================================================
# 🖥️ INTERFAZ DE TERMOSTATO (SIMPLIFICADA)
# ===============================================================================
def main(page: ft.Page):
    page.title = "Termostato Sencillo"
    page.theme_mode = ft.ThemeMode.DARK
    page.bgcolor = ft.Colors.BLACK
    page.vertical_alignment = ft.MainAxisAlignment.START
    page.padding = 20
    
    # --- Estado Local ---
    target_temp = 21.5
    current_temp = 20.0 
    is_heating = False 
    master_switch_on = False
    last_command = None  # Rastrear último comando enviado para evitar spam 

    # --- Elementos UI (Súper Básicos) ---
    
    status_text = ft.Text("Estado: Desconectado", color=ft.Colors.RED)
    
    lbl_target = ft.Text("Temperatura OBJETIVO:", size=20, color=ft.Colors.GREY)
    txt_target = ft.Text(f"{target_temp:.1f}°C", size=60, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE)
    
    def change_target(e, delta):
        nonlocal target_temp
        target_temp = round(target_temp + delta, 1)
        txt_target.value = f"{target_temp:.1f}°C"
        check_logic()
        page.update()

    btn_plus = ft.ElevatedButton("+", on_click=lambda e: change_target(e, 0.5))
    btn_minus = ft.ElevatedButton("-", on_click=lambda e: change_target(e, -0.5))

    lbl_current = ft.Text("Temperatura ACTUAL (Sonda):", size=20, color=ft.Colors.GREY)
    txt_current = ft.Text(f"{current_temp:.1f}°C", size=40, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE)
    
    lbl_sensor_status = ft.Text("Sonda: Buscando...", color=ft.Colors.GREY, size=14) # Nuevo indicador
    
    lbl_heating = ft.Text("CALDERA APAGADA", size=30, weight=ft.FontWeight.BOLD, color=ft.Colors.GREY)

    def on_master(e):
        nonlocal master_switch_on
        master_switch_on = e.control.value
        check_logic()
        page.update()

    sw_master = ft.Switch(label="Activar Termostato Automático", value=False, on_change=on_master)


    # --- Lógica ---
    def check_logic():
        nonlocal last_command
        need_heat = False
        if master_switch_on:
            if current_temp < target_temp:
                need_heat = True
            elif current_temp >= target_temp:
                need_heat = False
        
        # Actualizar Visual
        if need_heat:
            lbl_heating.value = "🔥 CALDERA ENCENDIDA"
            lbl_heating.color = ft.Colors.ORANGE
        else:
            lbl_heating.value = "❄️ CALDERA APAGADA"
            lbl_heating.color = ft.Colors.GREY

        # ⚡ SOLO mandar comando si cambió el estado deseado
        action = "on" if need_heat else "off"
        if action != last_command:
            last_command = action
            print(f"📤 Estado cambió a {action.upper()} - enviando comando")
            page.run_task(ws_client.command_relay, action)
        else:
            # No enviar comando redundante
            pass

    # --- Callback WS ---
    def update_ui(data):
        nonlocal current_temp, is_heating, master_switch_on
        t = data.get("type")

        if t == "registered":
            status_text.value = "✅ Conectado al Servidor"
            status_text.color = ft.Colors.GREEN
            
            # ✅ Habilitar el switch ahora que hay conexión
            sw_master.disabled = False
            
            page.run_task(ws_client.request_state, RELAY_ID)
            page.update()

        elif t == "server_disconnected":
            # 🔴 DESCONEXIÓN DETECTADA - Resetear todo a modo seguro
            status_text.value = "❌ Desconectado del Servidor"
            status_text.color = ft.Colors.RED
            
            # Forzar apagado visual
            lbl_heating.value = "❄️ CALDERA APAGADA (Sin conexión)"
            lbl_heating.color = ft.Colors.GREY
            
            # Desactivar termostato automático
            master_switch_on = False
            sw_master.value = False
            
            # 🔒 DESHABILITAR el switch mientras no hay servidor
            sw_master.disabled = True
            
            # Marcar sonda como desconectada
            lbl_sensor_status.value = "❌ Sonda Desconectada"
            lbl_sensor_status.color = ft.Colors.RED
            
            is_heating = False
            last_command = None  # Resetear para forzar reenvío al reconectar
            page.update()

        elif t == "telemetry":
            val = data.get("temp")
            if val is not None:
                current_temp = float(val)
                txt_current.value = f"{current_temp:.1f}°C"
                lbl_sensor_status.value = "✅ Sonda Conectada (Recibiendo datos)"
                lbl_sensor_status.color = ft.Colors.GREEN
                check_logic()
                page.update()
        
        elif t == "esp32_offline" and data.get("id") == SENSOR_ID:
            lbl_sensor_status.value = "❌ Sonda Desconectada"
            lbl_sensor_status.color = ft.Colors.RED
            page.update()
            
        elif t == "state" and data.get("from") == RELAY_ID:
            st = data.get("state")
            is_heating = (st == "on")
            # Podríamos actualizar el visual aquí también para confirmar
            page.update()

    ws_client = WebSocketClient(update_ui)

    # --- Layout Simple (Columna sin misterios) ---
    page.add(
        status_text,
        ft.Divider(),
        lbl_target,
        ft.Row([btn_minus, txt_target, btn_plus], alignment=ft.MainAxisAlignment.CENTER),
        ft.Divider(),
        lbl_current,
        txt_current,
        lbl_sensor_status,
        ft.Divider(),
        sw_master,
        ft.Divider(),
        lbl_heating
    )

    page.run_task(ws_client.connect_forever)

if __name__ == "__main__":
    if PORT == 0:
        ft.app(target=main)
    else:
        ft.app(target=main, port=PORT)
