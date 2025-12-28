"""
===========================================================================================
 PROYECTO: Control de CALDERA mediante WebSocket en tiempo real (App Flet)
===========================================================================================

DESCRIPCIÓN
-----------
Este frontend (Flet) permite gestionar el encendido y apagado de una caldera conectada 
a un ESP32 mediante un relé. La aplicación se comunica por WebSockets con un servidor 
FastAPI, permitiendo un control remoto instantáneo desde cualquier lugar.

ARQUITECTURA
------------
        ┌───────────────┐        ┌──────────────────────┐        ┌───────────────┐
        │   APP FLET    │ <----> │  SERVIDOR WEBSOCKET  │ <----> │ ESP32 CALDERA │
        │ (Usuario)     │        │    (FastAPI /ws)     │        │   (+ Relé)    │
        └───────────────┘        └──────────────────────┘        └───────────────┘

MÁS INFORMACIÓN
---------------
(ver README.md en la raíz del proyecto)

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
# 🌐 Cargar configuración desde archivo .env
# ===============================================================================
load_dotenv()

PORT = int(os.environ.get("PORT", 0))
WEBSOCKET_URL = os.getenv("WEBSOCKET_URL")

if not WEBSOCKET_URL:
    raise ValueError("⚠️ ERROR: La variable WEBSOCKET_URL no está definida en .env")

TARGET_DEVICE_ID = "esp32_02"


# ===============================================================================
# 🧠 CLASE CLIENTE WEBSOCKET
# ===============================================================================
class WebSocketClient:
    """
    Cliente WebSocket reutilizable para el frontend.
    """

    def __init__(self, ui_callback: Callable[[dict], None]):
        self.websocket: Optional[websockets.WebSocketClientProtocol] = None
        self.ui_callback = ui_callback
        self._stop = False

    async def connect_forever(self):
        """Mantiene la conexión abierta y reintenta tras pausas de seguridad."""
        while not self._stop:
            try:
                await self._connect_once()
            except Exception as e:
                print(f"{datetime.now()} ❌ Error crítico de conexión: {e}")
            finally:
                self.websocket = None
                self.ui_callback({"type": "server_disconnected"})
                # PAUSA DE SEGURIDAD: Evita el consumo excesivo de CPU en bucles de reconexión
                await asyncio.sleep(5)

    async def _connect_once(self):
        print(f"{datetime.now()} 🔌 Conectando a {WEBSOCKET_URL}...")
        async with websockets.connect(WEBSOCKET_URL, ping_interval=None) as ws:
            self.websocket = ws
            print(f"{datetime.now()} ✅ Conexión establecida")
            
            await self.send_json({"type": "register", "role": "frontend"})
            
            async for message in ws:
                data = json.loads(message)
                if isinstance(data, dict):
                    self.ui_callback(data)

    async def send_json(self, payload: dict):
        if self.websocket is None:
            return
        
        try:
            await self.websocket.send(json.dumps(payload))
        except Exception as e:
            print(f"{datetime.now()} ❌ Error al enviar JSON: {e}")
            self.websocket = None

    async def request_state(self, esp32_id: str):
        if esp32_id:
            await self.send_json({"type": "get_state", "to": esp32_id})

    async def command_relay(self, esp32_id: str, action: str):
        if esp32_id:
            now = datetime.now().strftime("%H:%M:%S")
            print(f"{now} 🕹️  Enviando comando: {action.upper()} -> {esp32_id}")
            await self.send_json({
                "type": "command",
                "to": esp32_id,
                "device": "relay",
                "id": 0,
                "action": action,
            })


# ===============================================================================
# 🖥️ INTERFAZ PRINCIPAL
# ===============================================================================
def main(page: ft.Page):
    page.title = "Control Caldera WiFi"
    page.theme_mode = ft.ThemeMode.DARK
    page.vertical_alignment = ft.MainAxisAlignment.CENTER
    page.padding = 40

    # selected_esp32 ahora es fijo
    selected_esp32 = TARGET_DEVICE_ID
    is_esp_online = False

    # --- Componentes UI ---
    title = ft.Text("Control de Calefacción", size=28, weight=ft.FontWeight.BOLD)
    ws_info = ft.Text(f"Servidor: {WEBSOCKET_URL}", size=12, color=ft.Colors.GREY_400)
    
    status_point = ft.Container(width=12, height=12, border_radius=6, bgcolor=ft.Colors.RED_500)
    status_text = ft.Text("Desconectado del servidor", size=14, italic=True)
    server_status_row = ft.Row([status_point, status_text], alignment=ft.MainAxisAlignment.CENTER)

    client_ip_text = ft.Text("Tu IP: detectando...", size=12, color=ft.Colors.BLUE_200)

    # Eliminado dropdown
    
    esp_status_banner = ft.Container(
        content=ft.Text("CALDERA DESCONECTADA", color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD),
        bgcolor=ft.Colors.RED_700,
        padding=10,
        border_radius=5,
        visible=True, # Visible por defecto hasta que conecte
    )

    bulb_icon = ft.Icon(
        name=ft.Icons.WHATSHOT_OUTLINED, # Icono de fuego/calor
        size=100,
        color=ft.Colors.GREY_700,
    )

    relay_switch = ft.Switch(
        label="Encender Calefacción", 
        value=False, 
        disabled=True,
        on_change=lambda e: page.run_task(on_switch_changed, e)
    )

    # --- Helpers UI ---
    def set_ui_state(online: bool):
        nonlocal is_esp_online
        is_esp_online = online
        relay_switch.disabled = not online
        
        if online:
             esp_status_banner.visible = False
        else:
             esp_status_banner.visible = True
             bulb_icon.color = ft.Colors.GREY_700
             relay_switch.value = False
             
        page.update()

    # Eliminado refresh_dropdown



    # --------------------------------------------------------------------------
    # Callback de mensajes WS
    # --------------------------------------------------------------------------
    def update_status(data: dict):
        nonlocal selected_esp32

        msg_type = data.get("type")

        if msg_type == "registered":
            status_point.bgcolor = ft.Colors.GREEN_500
            status_text.value = "✅ Servidor Online"
            my_ip = data.get("ip", "desconocida")
            client_ip_text.value = f"Tu IP: {my_ip}"
            page.update()
            return

        if msg_type == "server_disconnected":
            status_point.bgcolor = ft.Colors.RED_500
            status_text.value = "❌ Servidor Offline (Reintentando...)"
            set_ui_state(False)
            return

        if msg_type == "esp32_list":
            items = data.get("items", [])
            if TARGET_DEVICE_ID in items:
                page.run_task(ws_client.request_state, TARGET_DEVICE_ID)
            else:
                set_ui_state(False)
            return

        if msg_type == "esp32_online":
            new_id = data.get("id")
            if new_id == TARGET_DEVICE_ID:
                page.run_task(ws_client.request_state, TARGET_DEVICE_ID)
            return

        if msg_type == "esp32_offline":
            off_id = data.get("id")
            if off_id == TARGET_DEVICE_ID:
                set_ui_state(False)
            return

        if msg_type == "state":
            from_id = data.get("from")
            if from_id != selected_esp32:
                return

            st = data.get("state")
            set_ui_state(True)

            now = datetime.now().strftime("%H:%M:%S")
            print(f"{now} 💡 Confirmación recibida: {from_id} es {st.upper()}")

            relay_switch.value = (st == "on")
            bulb_icon.name = ft.Icons.WHATSHOT if st == "on" else ft.Icons.WHATSHOT_OUTLINED
            bulb_icon.color = ft.Colors.ORANGE_500 if st == "on" else ft.Colors.GREY_500
            page.update()
            return

    ws_client = WebSocketClient(update_status)

    # --------------------------------------------------------------------------
    # Eventos UI
    # --------------------------------------------------------------------------
    # Eliminado on_select_esp32

    async def on_switch_changed(e):
        if not selected_esp32 or not is_esp_online:
            relay_switch.value = not e.control.value # Revertir
            page.update()
            return

        action = "on" if e.control.value else "off"
        await ws_client.command_relay(selected_esp32, action)

    # --------------------------------------------------------------------------
    # Layout
    # --------------------------------------------------------------------------
    page.add(
        ft.Column(
            [
                title,
                ws_info,
                client_ip_text,
                ft.Divider(),
                server_status_row,
                ft.Divider(),
                ft.Divider(),
                esp_status_banner,
                # esp32_dropdown eliminado
                ft.Container(bulb_icon, margin=ft.margin.only(top=20, bottom=20)),
                relay_switch,
                ft.Divider(),
            ],
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )
    )

    page.run_task(ws_client.connect_forever)


# ===============================================================================
# 🚀 EJECUCIÓN
# ===============================================================================
if __name__ == "__main__":
    if PORT == 0:
        ft.app(target=main, view=ft.AppView.WEB_BROWSER)
    else:
        ft.app(target=main, port=PORT)
