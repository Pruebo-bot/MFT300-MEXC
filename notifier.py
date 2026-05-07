"""
Notificaciones y control por Telegram para el Bot MFT 300 V.5

Botonera:
  /start  → muestra teclado de control
  🟢 START  → reanuda operativa
  🟡 PAUSE  → pausa emergencia (cancela órdenes y cierra posiciones)
  📊 STATUS → informe detallado
  🛑 STOP   → detiene el bot

Seguridad: solo responde al TELEGRAM_CHAT_ID configurado.
"""

import asyncio
import logging
import subprocess
import aiohttp
from typing import Optional, Callable
import config

log = logging.getLogger("notifier")

KEYBOARD = {
    "inline_keyboard": [
        [
            {"text": "🟢 START",   "callback_data": "cmd_start"},
            {"text": "🟡 PAUSE",   "callback_data": "cmd_pause"},
        ],
        [
            {"text": "📊 STATUS",  "callback_data": "cmd_status"},
        ],
        [
            {"text": "📈 HOY",     "callback_data": "cmd_today"},
            {"text": "📅 SEMANA",  "callback_data": "cmd_week"},
        ],
        [
            {"text": "🔄 RESTART", "callback_data": "cmd_restart"},
            {"text": "⛔ STOP SYS","callback_data": "cmd_stopsys"},
        ],
    ]
}


class TelegramNotifier:
    def __init__(self, cfg):
        self.token   = cfg.TELEGRAM_TOKEN
        self.chat_id = cfg.TELEGRAM_CHAT_ID
        self.enabled = bool(self.token and self.chat_id)

        # Callbacks que el bot principal registra
        self._on_start:  Optional[Callable] = None
        self._on_pause:  Optional[Callable] = None
        self._on_stop:   Optional[Callable] = None
        self._on_status: Optional[Callable] = None
        self._on_today:  Optional[Callable] = None
        self._on_week:   Optional[Callable] = None

        self._offset   = 0
        self._polling  = False

        if not self.enabled:
            log.warning("Telegram no configurado. Notificaciones desactivadas.")

    # ── Registro de callbacks ──────────────────────────────────────────────────

    def on_start(self,  fn: Callable): self._on_start  = fn
    def on_pause(self,  fn: Callable): self._on_pause  = fn
    def on_stop(self,   fn: Callable): self._on_stop   = fn
    def on_status(self, fn: Callable): self._on_status = fn
    def on_today(self,  fn: Callable): self._on_today  = fn
    def on_week(self,   fn: Callable): self._on_week   = fn

    # ── Envío de mensajes ──────────────────────────────────────────────────────

    async def send(self, message: str, with_keyboard: bool = False):
        """Envía un mensaje de texto (Markdown). Opcionalmente añade la botonera."""
        if not self.enabled:
            log.info(f"[Telegram] {message}")
            return

        payload = {
            "chat_id":    self.chat_id,
            "text":       message,
            "parse_mode": "Markdown",
        }
        if with_keyboard:
            payload["reply_markup"] = KEYBOARD

        await self._post("sendMessage", payload)

    async def send_with_keyboard(self, message: str):
        await self.send(message, with_keyboard=True)

    # ── Polling de comandos ────────────────────────────────────────────────────

    async def start_polling(self):
        """Arranca el loop de polling en segundo plano."""
        if not self.enabled:
            return
        self._polling = True
        asyncio.create_task(self._poll_loop())
        log.info("Telegram polling iniciado")

    def stop_polling(self):
        self._polling = False

    async def _poll_loop(self):
        while self._polling:
            try:
                await self._process_updates()
            except Exception as e:
                log.error(f"Telegram poll error: {e}")
            await asyncio.sleep(2)

    async def _process_updates(self):
        url  = f"https://api.telegram.org/bot{self.token}/getUpdates"
        params = {"offset": self._offset, "timeout": 10, "limit": 10}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params,
                                       timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    data = await resp.json()
                    if not data.get("ok"):
                        return
                    for update in data.get("result", []):
                        self._offset = update["update_id"] + 1
                        await self._handle_update(update)
        except Exception as e:
            log.debug(f"Poll excepción: {e}")

    async def _handle_update(self, update: dict):
        # Mensaje de texto (ej: /start)
        if "message" in update:
            msg     = update["message"]
            chat_id = str(msg.get("chat", {}).get("id", ""))
            text    = msg.get("text", "").strip()

            if chat_id != str(self.chat_id):
                log.warning(f"Mensaje ignorado de chat_id desconocido: {chat_id}")
                return

            if text == "/start":
                await self.send_with_keyboard(
                    "🤖 *Bot MFT 300 V.5 activo*\n"
                    "Usa los botones para controlar el bot:"
                )

        # Callback de botón inline
        elif "callback_query" in update:
            cb      = update["callback_query"]
            chat_id = str(cb.get("message", {}).get("chat", {}).get("id", ""))
            data    = cb.get("data", "")
            cb_id   = cb.get("id")

            if chat_id != str(self.chat_id):
                return

            # Responder al callback para quitar el "cargando" del botón
            await self._answer_callback(cb_id)

            if data == "cmd_start" and self._on_start:
                await self._on_start()
            elif data == "cmd_pause" and self._on_pause:
                await self._on_pause()
            elif data == "cmd_stop" and self._on_stop:
                await self._on_stop()
            elif data == "cmd_status" and self._on_status:
                await self._on_status()
            elif data == "cmd_today" and self._on_today:
                await self._on_today()
            elif data == "cmd_week" and self._on_week:
                await self._on_week()
            elif data == "cmd_restart":
                await self.send("🔄 Reiniciando servicio del sistema...")
                subprocess.Popen(["systemctl", "restart", "mft300-mexc"])
            elif data == "cmd_stopsys":
                await self.send("⛔ Servicio del sistema detenido.\nUsa `systemctl start mft300-mexc` para reanudar.")

    async def _answer_callback(self, callback_query_id: str):
        """Confirma el tap del botón para que Telegram quite el spinner."""
        await self._post("answerCallbackQuery",
                         {"callback_query_id": callback_query_id})

    # ── HTTP helper ────────────────────────────────────────────────────────────

    async def _post(self, method: str, payload: dict):
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, json=payload,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        log.warning(f"Telegram {method} error {resp.status}: {body}")
        except Exception as e:
            log.error(f"Telegram {method} excepción: {e}")
