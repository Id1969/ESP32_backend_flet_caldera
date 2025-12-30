"""
===========================================================================================
 PROYECTO: Termostato Inteligente WiFi (App Flet - MODO CENTRALIZADO)
===========================================================================================
"""

from __future__ import annotations

import asyncio
import json
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
                self.ui_callback({"type": "disconnected"}) # Aviso interno
                await asyncio.sleep(5)

    async def _connect_once(self):
        print(f"🔌 Conectando a {WEBSOCKET_URL}...")
        async with websockets.connect(WEBSOCKET_URL, ping_interval=None) as ws:
            self.websocket = ws
            print("✅ Conectado")
            
            await self.send_json({"type": "register", "role": "frontend"})
            
            async for message in ws:
                try:
                    data = json.loads(message)
                    if isinstance(data, dict):
                        self.ui_callback(data)
                except json.JSONDecodeError:
                    pass

    async def send_json(self, payload: dict):
        if self.websocket:
            try:
                await self.websocket.send(json.dumps(payload))
            except Exception:
                self.websocket = None

    async def send_config_update(self, mode: str, target_temp: float):
        """Envía la nueva configuración deseada por el usuario"""
        print(f"📤 Enviando Config: {mode} | {target_temp}°C")
        await self.send_json({
            "type": "config_update",
            "mode": mode,         # "AUTO" | "MANUAL"
            "target_temp": target_temp
        })

# ===============================================================================
# 🖥️ INTERFAZ DE TERMOSTATO (VISOR CENTRALIZADO)
# ===============================================================================
def main(page: ft.Page):
    page.title = "Control Caldera Pro"
    page.theme_mode = ft.ThemeMode.DARK
    page.bgcolor = "#1a1a1a"
    page.padding = 30
    page.vertical_alignment = ft.MainAxisAlignment.CENTER
    page.horizontal_alignment = ft.CrossAxisAlignment.CENTER

    # --- Estado Local (Solo para UI, la verdad absoluta viene del Backend) ---
    current_mode = "MANUAL"
    current_target = 21.5
    
    # --- Componentes UI ---
    
    # 1. Indicador de Conexión (Backend)
    status_icon = ft.Icon(ft.Icons.WIFI_OFF, color=ft.Colors.RED, size=30)
    status_text = ft.Text("Desconectado", color=ft.Colors.RED, weight=ft.FontWeight.BOLD)
    
    # 2. Indicadores Dispositivos
    led_relay = ft.Icon(ft.Icons.CIRCLE, color=ft.Colors.GREY, size=15)
    lbl_relay = ft.Text("Relé: ?", color=ft.Colors.GREY)
    
    led_sensor = ft.Icon(ft.Icons.CIRCLE, color=ft.Colors.GREY, size=15)
    lbl_sensor = ft.Text("Sonda: ?", color=ft.Colors.GREY)

    # 3. Temperatura Actual (Grande)
    txt_current_temp = ft.Text("--.-°C", size=70, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE)
    lbl_current_desc = ft.Text("Temperatura Actual", size=16, color=ft.Colors.GREY)

    # 4. Control de Temperatura Objetivo
    txt_target_temp = ft.Text(f"{current_target}°C", size=40, weight=ft.FontWeight.W_500, color=ft.Colors.CYAN)
    
    # FIX: Async Helper para run_task
    async def send_config_helper():
        await ws_client.send_config_update(current_mode, current_target)

    def change_target(e, delta):
        nonlocal current_target
        current_target = round(current_target + delta, 1)
        txt_target_temp.value = f"{current_target}°C"
        # Enviar comando usando el helper async
        page.run_task(send_config_helper)
        page.update()

    btn_minus = ft.IconButton(ft.Icons.REMOVE, on_click=lambda e: change_target(e, -0.5), icon_color=ft.Colors.CYAN, icon_size=40)
    btn_plus = ft.IconButton(ft.Icons.ADD, on_click=lambda e: change_target(e, 0.5), icon_color=ft.Colors.CYAN, icon_size=40)

    # 5. Modo de Operación
    def toggle_mode(e):
        nonlocal current_mode
        current_mode = "AUTO" if e.control.value else "MANUAL"
        page.run_task(send_config_helper)
        page.update()

    sw_mode = ft.Switch(label="Modo Automático", value=False, on_change=toggle_mode, active_color=ft.Colors.GREEN)
    
    # 6. Estado de la Caldera (Visual)
    card_status = ft.Container(
        content=ft.Text("APAGADA", size=20, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE),
        bgcolor=ft.Colors.GREY,
        padding=10,
        border_radius=10,
        alignment=ft.alignment.center
    )

    # --- Lógica de Actualización UI ---
    def update_ui(data):
        nonlocal current_mode, current_target
        t = data.get("type")

        # A) Conexión / Desconexión Backend
        if t == "registered":
            status_icon.name = ft.Icons.WIFI
            status_icon.color = ft.Colors.GREEN
            status_text.value = "Conectado"
            status_text.color = ft.Colors.GREEN
            
            # Reactivar controles de forma segura
            sw_mode.disabled = False
            btn_plus.disabled = False
            btn_minus.disabled = False
            page.update()
        
        elif t == "disconnected":
            status_icon.name = ft.Icons.WIFI_OFF
            status_icon.color = ft.Colors.RED
            status_text.value = "Desconectado"
            status_text.color = ft.Colors.RED
            
            # Desactivar controles para evitar confusión
            sw_mode.disabled = True
            btn_plus.disabled = True
            btn_minus.disabled = True

            # Resetear visuales a estado seguro
            card_status.bgcolor = ft.Colors.GREY
            card_status.content.value = "SIN CONEXIÓN"
            led_relay.color = ft.Colors.RED
            led_sensor.color = ft.Colors.RED
            page.update()
            return

        # B) Estado Completo (Sonda, Relé, Config)
        if t == "full_state_update" or t == "status_update" or t == "sensor_update":
            
            conn = data.get("connection_status", {})
            sys_state = data.get("system_state", {})
            
            # Si es un update parcial directo
            if not sys_state and (t == "status_update"): 
                sys_state = data
            
            # Si es update de sensor directo
            if t == "sensor_update":
                sys_state["current_temp"] = data.get("temperature")
            
            # 1. Conectividad Dispositivos
            if conn:
                r_ok = conn.get("esp32_02") == "connected"
                s_ok = conn.get("esp32_03") == "connected"
                
                led_relay.color = ft.Colors.GREEN if r_ok else ft.Colors.RED
                lbl_relay.value = "Relé: OK" if r_ok else "Relé: OFF"
                
                led_sensor.color = ft.Colors.GREEN if s_ok else ft.Colors.RED
                lbl_sensor.value = "Sonda: OK" if s_ok else "Sonda: OFF"

            # 2. Valores del Sistema
            mode = sys_state.get("mode")
            relay_on = sys_state.get("relay_state") == "ON"
            curr = sys_state.get("current_temp")
            tgt = sys_state.get("target_temp")

            if mode:
                if mode != current_mode:
                    current_mode = mode
                    sw_mode.value = (mode == "AUTO")
            
            if tgt:
                tgt_val = float(tgt)
                if abs(tgt_val - current_target) > 0.1:
                    current_target = tgt_val
                    txt_target_temp.value = f"{current_target}°C"

            if curr is not None:
                txt_current_temp.value = f"{float(curr):.1f}°C"
                txt_current_temp.color = ft.Colors.WHITE
            else:
                txt_current_temp.value = "--.-°C"
                txt_current_temp.color = ft.Colors.GREY

            # 3. Estado Visual Caldera
            if relay_on:
                card_status.bgcolor = ft.Colors.ORANGE_900
                card_status.content.value = "🔥 CALENTANDO"
                card_status.content.color = ft.Colors.ORANGE
                card_status.border = ft.border.all(2, ft.Colors.ORANGE)
            else:
                card_status.bgcolor = ft.Colors.GREY_900
                card_status.content.value = "❄️ EN REPOSO"
                card_status.content.color = ft.Colors.GREY
                card_status.border = None

            page.update()

    ws_client = WebSocketClient(update_ui)

    # --- Layout ---
    page.add(
        ft.Row([status_icon, status_text], alignment=ft.MainAxisAlignment.CENTER),
        ft.Row([
            ft.Row([led_relay, lbl_relay]),
            ft.Container(width=20),
            ft.Row([led_sensor, lbl_sensor])
        ], alignment=ft.MainAxisAlignment.CENTER),
        
        ft.Divider(),
        
        ft.Column([
            txt_current_temp,
            lbl_current_desc
        ], horizontal_alignment=ft.CrossAxisAlignment.CENTER),
        
        ft.Divider(height=40, color=ft.Colors.TRANSPARENT),
        
        ft.Text("TEMPERATURA OBJETIVO", color=ft.Colors.CYAN, size=12),
        ft.Row([
            btn_minus,
            txt_target_temp,
            btn_plus
        ], alignment=ft.MainAxisAlignment.CENTER),
        
        ft.Divider(height=20, color=ft.Colors.TRANSPARENT),
        
        sw_mode,
        
        ft.Divider(height=40, color=ft.Colors.TRANSPARENT),
        
        card_status
    )

    page.run_task(ws_client.connect_forever)

if __name__ == "__main__":
    if PORT == 0:
        ft.app(target=main)
    else:
        ft.app(target=main, port=PORT)
