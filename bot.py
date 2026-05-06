"""
Bot MFT300 V.5 — MEXC Futuros Perpetuos (USDT-M)
Grid bidireccional dinámico con filtros de protección.
Adaptado de la versión Bitunix para la API de MEXC.
"""

import asyncio
import time
import logging
import signal
import sys
from datetime import datetime, timezone, timedelta, date

from config import *
from exchange import MEXCClient
from grid import GridEngine
from filters import (VolatilityFilter, MaxLossFilter, ProtectedZone,
                     TrendFilter, ATRFilter, AsymmetricGrid)
from notifier import TelegramNotifier
from state import PairState, BotState

TZ_SPAIN = timezone(timedelta(hours=2))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s (ES) [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("MFT300_MEXC")


class MFT300MEXCBot:
    def __init__(self):
        self.cfg       = sys.modules[__name__].__builtins__  # usamos config directamente
        import config as cfg
        self.cfg = cfg

        self.exchange  = MEXCClient()
        self.bot_state = BotState()
        self.notifier  = TelegramNotifier(cfg)

        self.pairs: dict[str, PairState] = {s: PairState(symbol=s) for s in cfg.SYMBOLS}
        self.grids: dict[str, GridEngine] = {
            s: GridEngine(cfg, self.exchange, self.pairs[s]) for s in cfg.SYMBOLS
        }

        self.vol_filters0 = {
            s: VolatilityFilter(cfg.VOL0_WINDOW_SEC, cfg.VOL0_THRESHOLD_PCT, f"{s}-30sec")
            for s in cfg.SYMBOLS
        }
        self.vol_filters = {
            s: VolatilityFilter(cfg.VOL_WINDOW_SEC, cfg.VOL_THRESHOLD_PCT, f"{s}-10min")
            for s in cfg.SYMBOLS
        }
        self.vol_filters2 = {
            s: VolatilityFilter(cfg.VOL2_WINDOW_SEC, cfg.VOL2_THRESHOLD_PCT, f"{s}-30min")
            for s in cfg.SYMBOLS
        }
        self.loss_filter    = MaxLossFilter(cfg)
        self.protected_zone = ProtectedZone(cfg)
        self.trend_filter   = TrendFilter(cfg.TREND_CANDLES, cfg.TREND_MIN_PCT)
        self.atr_filter     = ATRFilter(cfg)
        self.asym_grid      = AsymmetricGrid(cfg)

        self._running = False
        self._paused  = False

        self.notifier.on_start(self._cmd_start)
        self.notifier.on_pause(self._cmd_pause)
        self.notifier.on_stop(self._cmd_stop)
        self.notifier.on_status(self._cmd_status)
        self.notifier.on_today(self._cmd_today)
        self.notifier.on_week(self._cmd_week)

    # ── Comandos Telegram ──────────────────────────────────────────────────────

    async def _cmd_start(self):
        if self._paused:
            self._paused  = False
            self._running = True
            await self.notifier.send_with_keyboard("▶️ *Bot MFT300 MEXC reanudado*")
        else:
            await self.notifier.send_with_keyboard("ℹ️ El bot ya está activo.")

    async def _cmd_pause(self):
        self._paused  = True
        self._running = False
        for symbol, grid in self.grids.items():
            await grid.cancel_all_orders()
            self.pairs[symbol].reset()
        await self.notifier.send_with_keyboard(
            "⏸ *Pausa de emergencia*\nÓrdenes canceladas.\nPulsa 🟢 START para reanudar.")

    async def _cmd_stop(self):
        await self.stop("Detenido por Telegram")

    async def _cmd_status(self):
        estado = "⏸ Pausado" if self._paused else "🟢 Activo"
        balance = await self.exchange.get_balance()
        msg = f"📊 *STATUS — MFT300 MEXC*\n\nEstado: {estado}\n"
        if balance:
            msg += f"Balance: `{balance:.2f} USDT`\n\n"
        for symbol, state in self.pairs.items():
            price = await self.exchange.get_price(symbol)
            msg += f"*{symbol}*\n"
            if price: msg += f"  Precio: `{price:.6f}`\n"
            msg += f"  Pérdida hoy: `{state.daily_loss:.2f} USDT`\n\n"
        await self.notifier.send_with_keyboard(msg)

    def _format_summary(self, trades: list, titulo: str) -> str:
        if not trades:
            return f"{titulo}\n\nSin operaciones registradas."
        ganadoras  = [t for t in trades if t["pnl_usdt"] > 0]
        pnl_bruto  = sum(t["pnl_usdt"] for t in trades)
        fees_total = sum(t["fee"] for t in trades)
        pnl_neto   = pnl_bruto - fees_total
        winrate    = len(ganadoras) / len(trades) * 100 if trades else 0
        lines = [titulo, "━━━━━━━━━━━━━━━━"]
        for t in trades[-20:]:
            emoji = "✅" if t["pnl_usdt"] > 0 else "❌"
            lines.append(f"{emoji} `{t['symbol']}` `{t['side']}` | `{t['pnl_usdt']:+.4f} USDT` (`{t['pnl_pct']:+.2f}%`)")
        lines += [
            "━━━━━━━━━━━━━━━━",
            f"Operaciones: `{len(trades)}` | Ganadoras: `{len(ganadoras)}` (`{winrate:.0f}%`)",
            f"PnL bruto:  `{pnl_bruto:+.4f} USDT`",
            f"Fees:       `-{fees_total:.4f} USDT`",
            f"*PnL neto:  `{pnl_neto:+.4f} USDT`*",
        ]
        return "\n".join(lines)

    async def _cmd_today(self):
        hoy    = datetime.now(TZ_SPAIN).strftime("%Y-%m-%d")
        trades = [t for s in self.pairs.values() for t in s.trade_history if t["date"] == hoy]
        titulo = f"📈 *Resumen hoy — {datetime.now(TZ_SPAIN).strftime('%d/%m/%Y')}*"
        await self.notifier.send_with_keyboard(self._format_summary(trades, titulo))

    async def _cmd_week(self):
        hoy    = datetime.now(TZ_SPAIN).date()
        lunes  = str(hoy - timedelta(days=hoy.weekday()))
        trades = [t for s in self.pairs.values() for t in s.trade_history if t["date"] >= lunes]
        titulo = f"📅 *Resumen semana — desde {lunes[8:]}/{lunes[5:7]}*"
        await self.notifier.send_with_keyboard(self._format_summary(trades, titulo))

    # ── Ciclo principal ────────────────────────────────────────────────────────

    async def start(self):
        symbols_str = ", ".join(self.cfg.SYMBOLS)
        log.info("=" * 60)
        log.info("  MFT300 Bot V.5 — MEXC Futuros")
        log.info("  Pares: %s", symbols_str)
        log.info("=" * 60)

        log.info("🧹 Limpiando órdenes existentes al arrancar...")
        for symbol in self.cfg.SYMBOLS:
            await self.exchange.cancel_all_orders(symbol)
        self.exchange._ignore_trades_before = time.time()
        await asyncio.sleep(2)

        await self.notifier.start_polling()
        await asyncio.sleep(2)

        for symbol in self.cfg.SYMBOLS:
            await self.exchange.set_leverage(symbol, self.cfg.LEVERAGE)

        await self.notifier.send_with_keyboard(
            f"🤖 *MFT300 Bot V.5 — MEXC iniciado*\n"
            f"Pares: `{symbols_str}`\n"
            f"Grid: {self.cfg.GRID_LEVELS} niveles | "
            f"Spacing: {self.cfg.GRID_SPACING_PCT}% | "
            f"TP: {self.cfg.TAKE_PROFIT_PCT}%"
        )

        self._running = True
        await self._main_loop()

    async def stop(self, reason="Manual"):
        log.warning("Deteniendo bot: %s", reason)
        self._running = False
        self.notifier.stop_polling()
        for symbol, grid in self.grids.items():
            await grid.cancel_all_orders()
        await self.notifier.send(f"🛑 *Bot detenido*\nMotivo: {reason}")

    async def _main_loop(self):
        while self._running or self._paused:
            if not self._paused:
                try:
                    await asyncio.gather(*[self._cycle(s) for s in self.cfg.SYMBOLS])
                except Exception as e:
                    log.error("Error en ciclo principal: %s", e, exc_info=True)
            await asyncio.sleep(self.cfg.LOOP_INTERVAL_SEC)

    async def _cycle(self, symbol: str):
        state  = self.pairs[symbol]
        grid   = self.grids[symbol]
        vf0    = self.vol_filters0[symbol]
        vf1    = self.vol_filters[symbol]
        vf2    = self.vol_filters2[symbol]

        price = await self.exchange.get_price(symbol)
        if price is None:
            return
        state.current_price = price

        # ── 1. Pausa programada ────────────────────────────────────────────────
        if self.cfg.SCHEDULED_PAUSE_ENABLED:
            now_es   = datetime.now(TZ_SPAIN)
            now_str  = now_es.strftime("%H:%M")
            start_s  = self.cfg.SCHEDULED_PAUSE_START
            end_s    = self.cfg.SCHEDULED_PAUSE_END
            in_pause = (start_s <= now_str < end_s) if start_s < end_s else \
                       (now_str >= start_s or now_str < end_s)
            if in_pause and not state.scheduled_was_active:
                state.scheduled_was_active = True
                state.paused_scheduled     = True
                await grid.cancel_all_orders()
                state.reset()
                log.info("[%s] 🌙 Pausa horaria activada", symbol)
                await self.notifier.send(f"🌙 *{symbol}* — Pausa horaria activada")
            elif not in_pause and state.scheduled_was_active:
                state.scheduled_was_active = False
                state.paused_scheduled     = False
                await self.notifier.send(f"☀️ *{symbol}* — Pausa horaria finalizada")
            if state.paused_scheduled:
                return

        # ── 2. Límite de pérdida diaria ────────────────────────────────────────
        today_str = datetime.now(TZ_SPAIN).strftime("%Y-%m-%d")
        if state.daily_loss_date != today_str:
            state.daily_loss       = 0.0
            state.daily_loss_date  = today_str
            state.paused_daily_loss = False
            log.info("[%s] 📅 Nuevo día — contador reseteado", symbol)
        if state.paused_daily_loss:
            return

        # ── 2b. Pausa por pérdidas consecutivas ───────────────────────────────
        if state.paused_consecutive:
            if time.time() >= state.consecutive_resume_at:
                state.paused_consecutive = False
                state.consecutive_losses = 0
                self.exchange._ignore_trades_before = time.time()
                log.info("[%s] ▶️  Fin pausa consecutiva", symbol)
                await self.notifier.send(f"▶️ *{symbol}* — Reanudando tras aviso de pérdidas")
            else:
                return

        # ── 3. Filtro spike ultra-rápido (capa 0) ─────────────────────────────
        if state.paused_volatility0:
            if time.time() >= state.volatility0_resume_at:
                state.paused_volatility0 = False
                vf0.reset()
                await self.notifier.send(f"▶️ *{symbol}* — Filtro spike: reanudando")
            else:
                return
        vf0.update(price)
        if vf0.triggered():
            pct = vf0.last_spike_pct
            await grid.cancel_all_orders()
            state.reset()
            state.paused_volatility0    = True
            state.volatility0_resume_at = time.time() + self.cfg.VOL0_PAUSE_SEC
            vf0.reset()
            await self.notifier.send(
                f"🔥 *{symbol} — Spike detectado*\n`{pct:.3f}%` en 30s\n"
                f"Órdenes canceladas — posiciones mantenidas\nPausa: {self.cfg.VOL0_PAUSE_SEC//60} min"
            )
            return

        # ── 4. Filtro volatilidad sostenida (capa 2) ──────────────────────────
        if state.paused_volatility2:
            if time.time() >= state.volatility2_resume_at:
                state.paused_volatility2 = False
                vf2.reset()
                await self.notifier.send(f"▶️ *{symbol}* — Filtro vol 30min: reanudando")
            else:
                return
        vf2.update(price)
        if vf2.triggered():
            pct = vf2.last_spike_pct
            await grid.cancel_all_orders()
            state.reset()
            state.paused_volatility2    = True
            state.volatility2_resume_at = time.time() + self.cfg.VOL2_PAUSE_SEC
            vf2.reset()
            await self.notifier.send(
                f"📉 *{symbol} — Volatilidad sostenida*\n`{pct:.3f}%` en 30 min\n"
                f"Pausa: {self.cfg.VOL2_PAUSE_SEC//60} min"
            )
            return

        # ── 5. Filtro volatilidad rápida (capa 1) ─────────────────────────────
        if state.paused_volatility:
            if time.time() >= state.volatility_resume_at:
                state.paused_volatility = False
                vf1.reset()
                await self.notifier.send(f"▶️ *{symbol}* — Filtro vol 10min: reanudando")
            else:
                return
        vf1.update(price)
        if vf1.triggered():
            pct = vf1.last_spike_pct
            await grid.cancel_all_orders()
            state.reset()
            state.paused_volatility    = True
            state.volatility_resume_at = time.time() + self.cfg.VOL_PAUSE_SEC
            vf1.reset()
            await self.notifier.send(
                f"⚡ *{symbol} — Volatilidad extrema*\n`{pct:.3f}%` en 10 min\n"
                f"Pausa: {self.cfg.VOL_PAUSE_SEC//60} min"
            )
            return

        # ── 6. Filtro tendencia por velas ──────────────────────────────────────
        if state.paused_trend:
            if time.time() >= state.trend_resume_at:
                state.paused_trend = False
                await self.notifier.send(f"▶️ *{symbol}* — Tendencia finalizada, reanudando")
            else:
                return
        candles = await self.exchange.get_klines(symbol, self.cfg.TREND_TIMEFRAME,
                                                  self.cfg.TREND_CANDLES + 5)
        if candles and len(candles) >= self.cfg.TREND_CANDLES + 1:
            closes = [float(c.get("close", 0)) for c in candles[:-1]]
            trend  = self.trend_filter.detect(closes)
            if trend != "NONE":
                direction = "bajista 📉" if trend == "DOWN" else "alcista 📈"
                await grid.cancel_all_orders()
                await grid.close_all_positions()
                state.reset()
                state.paused_trend    = True
                state.trend_resume_at = time.time() + self.cfg.TREND_PAUSE_SEC
                await self.notifier.send(
                    f"🚨 *{symbol} — Tendencia {direction}*\n"
                    f"Pausa: {self.cfg.TREND_PAUSE_SEC//60} min"
                )
                return

        # ── 7. Pausa por pérdida máxima ────────────────────────────────────────
        if state.paused_loss:
            if time.time() >= state.loss_resume_at:
                state.paused_loss = False
                self.exchange._last_closed_order_id = None
                self.exchange._ignore_trades_before = time.time()
                await self.notifier.send(f"▶️ *{symbol}* — Pausa pérdida finalizada")
            else:
                return

        # ── 8. Detección de trades cerrados ───────────────────────────────────
        trade = await self.exchange.get_last_closed_trade(symbol)
        if trade is not None:
            pnl_usdt     = trade["pnl_usdt"]
            fee          = trade["fee"]
            pnl_neto     = pnl_usdt - fee
            pnl_neto_pct = (pnl_neto / self.cfg.ORDER_SIZE_USDT * 100) if self.cfg.ORDER_SIZE_USDT != 0 else 0
            emoji        = "✅" if pnl_neto >= 0 else "❌"

            log.info("[%s] %s Cierre %s | PnL neto: %+.4f USDT (%+.2f%%)",
                     symbol, emoji, trade["side"], pnl_neto, pnl_neto_pct)

            state.trade_history.append({
                "date":     datetime.now(TZ_SPAIN).strftime("%Y-%m-%d"),
                "time":     datetime.now(TZ_SPAIN).strftime("%H:%M"),
                "symbol":   symbol,
                "side":     trade["side"],
                "price":    trade["price"],
                "pnl_usdt": pnl_neto,
                "pnl_pct":  pnl_neto_pct,
                "fee":      fee,
            })
            await self.notifier.send(
                f"{emoji} *Orden cerrada*\n"
                f"Par: `{symbol}` | {trade['side']}\n"
                f"Precio: `{trade['price']:.6f}`\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"PnL bruto: `{pnl_usdt:+.4f} USDT`\n"
                f"Fee:       `-{fee:.4f} USDT`\n"
                f"*PnL neto: `{pnl_neto:+.4f} USDT` (`{pnl_neto_pct:+.2f}%`)*"
            )

            if pnl_neto < 0:
                state.consecutive_losses += 1
                log.info("[%s] ⚠️  Pérdidas consecutivas: %d/2", symbol, state.consecutive_losses)
                if state.consecutive_losses >= 2:
                    await self.notifier.send(
                        f"⚠️ *{symbol} — 2 pérdidas consecutivas*\n"
                        f"El grid sigue activo.\nPulsa 🟡 PAUSE si quieres detenerlo."
                    )
                    state.consecutive_losses = 0
                state.daily_loss += abs(pnl_neto)
                log.info("[%s] 📉 Pérdida diaria: %.2f / %.2f USDT",
                         symbol, state.daily_loss, self.cfg.DAILY_LOSS_LIMIT)
                if state.daily_loss >= self.cfg.DAILY_LOSS_LIMIT:
                    state.paused_daily_loss = True
                    await grid.cancel_all_orders()
                    await grid.close_all_positions()
                    state.reset()
                    await self.notifier.send(
                        f"🛑 *{symbol} — Límite diario alcanzado*\n"
                        f"Pérdida: `{state.daily_loss:.2f} USDT`\nEl bot reanudará mañana."
                    )
                    return
            else:
                state.consecutive_losses = 0

            if self.loss_filter.triggered(trade["pnl_pct"]):
                await grid.cancel_all_orders()
                await grid.close_all_positions()
                state.reset()
                state.paused_loss    = True
                state.loss_resume_at = time.time() + self.cfg.LOSS_PAUSE_SEC
                await self.notifier.send(
                    f"🔴 *{symbol} — Filtro pérdida máxima*\n"
                    f"PnL: `{trade['pnl_pct']:.2f}%`\nPausa: {self.cfg.LOSS_PAUSE_SEC//60} min"
                )
                return

        # ── 9. Calcular spacing y niveles asimétricos ──────────────────────────
        buy_lvl  = self.cfg.GRID_LEVELS
        sell_lvl = self.cfg.GRID_LEVELS
        atr_candles = await self.exchange.get_klines(symbol, self.cfg.ATR_TIMEFRAME,
                                                      self.cfg.ATR_PERIOD * 3)
        if atr_candles and len(atr_candles) >= self.cfg.ATR_PERIOD + 1:
            atr_candles = atr_candles[:-1]
            h_list = [float(c.get("high",  0)) for c in atr_candles]
            l_list = [float(c.get("low",   0)) for c in atr_candles]
            c_list = [float(c.get("close", 0)) for c in atr_candles]
            new_spacing, new_tp = self.atr_filter.get_spacing_and_tp(h_list, l_list, c_list)
            if new_spacing != self.cfg.GRID_SPACING_PCT:
                nivel = "🟢 Baja" if new_spacing == self.cfg.SPACING_LOW else \
                        ("🔴 Alta" if new_spacing == self.cfg.SPACING_HIGH else "🟡 Normal")
                await self.notifier.send(
                    f"📐 *{symbol} — Spacing ajustado*\n"
                    f"Volatilidad: {nivel}\n"
                    f"Spacing: `{self.cfg.GRID_SPACING_PCT}%` → `{new_spacing}%`"
                )
            self.cfg.GRID_SPACING_PCT = new_spacing
            self.cfg.TAKE_PROFIT_PCT  = new_tp
            buy_lvl, sell_lvl = self.asym_grid.get_levels(c_list)

            if (buy_lvl != state.last_buy_lvl or sell_lvl != state.last_sell_lvl) and grid.is_active():
                direction = "alcista 📈" if buy_lvl > sell_lvl else \
                            ("bajista 📉" if sell_lvl > buy_lvl else "neutral ↔️")
                await grid.cancel_all_orders()
                await grid.initialize(price, buy_lvl, sell_lvl)
                state.last_buy_lvl    = buy_lvl
                state.last_sell_lvl   = sell_lvl
                state.last_cleanup_at = time.time()
                await self.notifier.send(
                    f"🔄 *{symbol} — Grid reajustado*\n"
                    f"Tendencia: {direction}\nNiveles: `{buy_lvl}B / {sell_lvl}S`"
                )
                return
            state.last_buy_lvl  = buy_lvl
            state.last_sell_lvl = sell_lvl

        # ── 10. Limpieza periódica ─────────────────────────────────────────────
        elapsed = time.time() - state.last_cleanup_at
        if elapsed >= self.cfg.CLEANUP_INTERVAL_SEC and grid.is_active():
            state.last_cleanup_at = time.time()
            await grid.cancel_all_orders()
            await grid.initialize(price, buy_lvl, sell_lvl)
            await self.notifier.send(f"🧹 *{symbol}* — Grid rehecho en `{price:.6f}`")
            return

        # ── 11. Grid activo ────────────────────────────────────────────────────
        if not grid.is_active():
            log.info("[%s] Iniciando grid en %f", symbol, price)
            await grid.initialize(price, buy_lvl, sell_lvl)
            return

        # Stop Loss por posición
        pnl_pct_pos = await self.exchange.get_position_pnl_pct(symbol)
        if pnl_pct_pos is not None and pnl_pct_pos <= self.cfg.POSITION_SL_PCT:
            log.warning("[%s] 🛑 Stop Loss posición | PnL: %.2f%%", symbol, pnl_pct_pos)
            await grid.cancel_all_orders()
            await grid.close_all_positions()
            state.reset()
            await self.notifier.send(
                f"🛑 *{symbol} — Stop Loss activado*\n"
                f"PnL no realizado: `{pnl_pct_pos:.2f}%`\nPosición cerrada a mercado."
            )
            return

        # Zona protegida
        if pnl_pct_pos is not None and self.protected_zone.is_protected(pnl_pct_pos):
            log.debug("[%s] Zona protegida | PnL: %.2f%%", symbol, pnl_pct_pos)
            return

        await grid.check_and_recenter(price)
        pnl_str = f"| PnL: {pnl_pct_pos:.2f}%" if pnl_pct_pos else ""
        log.info("[%s] 💹 Precio: %f %s", symbol, price, pnl_str)


# ── Entry point ────────────────────────────────────────────────────────────────
async def main():
    bot = MFT300MEXCBot()

    def handle_signal(sig, frame):
        asyncio.create_task(bot.stop("Señal del sistema"))

    signal.signal(signal.SIGINT,  handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    await bot.start()


if __name__ == "__main__":
    asyncio.run(main())
