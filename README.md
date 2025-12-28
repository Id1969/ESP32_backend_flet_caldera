# 📡 Proyecto IoT: Control de Caldera con ESP32, FastAPI y Flet

## 📝 Descripción general

Este proyecto implementa una arquitectura IoT completa diseñada específicamente para el **arranque y parada de una caldera** a través de WiFi. Utiliza un ESP32 conectado a un relé que actúa sobre el termostato o el circuito de encendido de la caldera. La gestión se realiza desde una interfaz gráfica desarrollada en Python con Flet, utilizando un servidor WebSocket basado en FastAPI como intermediario de comunicación en tiempo real.

El sistema permite el control remoto desde cualquier lugar con conexión a internet, proporcionando un control eficiente y bidireccional de la calefacción.

## 🧩 Arquitectura del sistema
```
┌──────────────┐        WebSocket        ┌────────────────────┐        WiFi        ┌───────────────┐
│  Frontend    │ <────────────────────> │  Backend FastAPI   │ <───────────────> │    ESP32      │
│  (Flet UI)   │                        │  Servidor WS       │                  │  + Relé (Caldera)│
└──────────────┘                        └────────────────────┘                  └───────────────┘
```

## ⚙️ Componentes del proyecto

### 🔹 ESP32
- Conectado a la red WiFi del hogar (o punto de acceso móvil).
- Establece una conexión WebSocket segura/persistente con el servidor.
- Recibe comandos de encendido y apagado de la caldera.
- Acciona un **relé** conectado físicamente a los bornes de control de la caldera.
- Envía confirmación del estado actual (ON/OFF) en tiempo real.

### 🔹 Backend – FastAPI + WebSocket
- Punto central de comunicación que gestiona las conexiones.
- Identifica de forma segura los dispositivos (ESP32) y los usuarios (Frontend).
- Reenvía las órdenes de encendido/apagado instantáneamente.
- Mantiene un registro (logs) de quién y cuándo activó la caldera.
- Preparado para despliegue en la nube (Render, VPS, etc.).

### 🔹 Frontend – Flet (Python)
- Aplicación con interfaz moderna y oscura para el usuario.
- Botón principal para encender o apagar la calefacción.
- Indicador visual del estado de la caldera y confirmación del servidor.
- Visualización de la IP de origen y estado de la conexión.

## 🔁 Flujo de funcionamiento
1. El servidor FastAPI se inicia en la nube o localmente.
2. El ESP32 se conecta automáticamente al servidor al encenderse.
3. El usuario abre la App (Flet) y se conecta al mismo servidor.
4. Al pulsar "Encender Caldera", el comando viaja por WebSocket al servidor.
5. El servidor identifica el ESP32 destino y le entrega la orden.
6. El ESP32 activa el relé, cerrando el circuito de la caldera.
7. El ESP32 confirma el cambio de estado y la App actualiza el icono visual.

## 🧪 Hardware Sugerido
- Microcontrolador ESP32 (C6, S3 o estándar).
- Módulo de relé de 5V/3.3V (apropiado para la carga de la caldera).
- Fuente de alimentación estable para el ESP32.

> [!WARNING]
> ### ⚠️ Nota de seguridad:
> La manipulación de sistemas de calefacción y calderas puede implicar voltajes peligrosos. Asegúrese de realizar las conexiones con la caldera apagada y siguiendo las normativas de seguridad eléctrica de su país. Si no tiene experiencia técnica, consulte con un profesional.
