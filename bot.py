"""
Bot de Trading MFT300_MEXC V.5 — Multi-par
Grid dinámico para Bitunix con capas de protección
Soporta múltiples pares simultáneos en paralelo
"""

import asyncio
import time
import logging
import signal
import sys
from datetime import datetime, timezone, timedelta, date

from config import Config
from exchange import MEXCClient
from grid import GridEngine
from filters import VolatilityFilter, MaxLossFilter, ScheduledPause, ProtectedZone, TrendFilter, ATRFilter, AsymmetricGrid
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


class MFT300_MEXCMEXCBot:
    def __init__(self):
        self.cfg      = Config()
        self.exchange = MEXCClient(self.cfg)
        self.bot_state = BotState()
        self.notifier = TelegramNotifier(self.cfg)

        # Estado y motores por par
        self.pairs: dict[str, PairState] = {
            s: PairState(symbol=s) for s in self.cfg.SYMBOLS
        }
        self.grids: dict[str, GridEngine] = {
            s: GridEngine(self.cfg, self.exchange, self.pairs[s])
            for s in self.cfg.SYMBOLS
        }

        # Filtros por par
        self.vol_filters0: dict[str, VolatilityFilter] = {
            s: VolatilityFilter(self.cfg.VOL0_WINDOW_SEC, self.cfg.VOL0_THRESHOLD_PCT, f"{s}-30sec")
            for s in self.cfg.SYMBOLS
        }
        self.vol_filters:  dict[str, VolatilityFilter] = {
            s: VolatilityFilter(self.cfg.VOL_WINDOW_SEC,  self.cfg.VOL_THRESHOLD_PCT,  f"{s}-10min")
            for s in self.cfg.SYMBOLS
        }
        self.vol_filters2: dict[str, VolatilityFilter] = {
            s: VolatilityFilter(self.cfg.VOL2_WINDOW_SEC, self.cfg.VOL2_THRESHOLD_PCT, f"{s}-30min")
            for s in self.cfg.SYMBOLS
        }
        self.loss_filter    = MaxLossFilter(self.cfg)
        self.trend_filter   = TrendFilter(self.cfg.TREND_CANDLES, self.cfg.TREND_MIN_PCT)
        self.atr_filter     = ATRFilter(self.cfg)
        self.asym_grid      = AsymmetricGrid(self.cfg)
        self.protected_zone = ProtectedZone(self.cfg)

        self._running = False
        self._paused  = False

        # Registrar callbacks Telegram
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
            log.info("▶️  Bot reanudado por Telegram")
            await self.notifier.send_with_keyboard("▶️ *Bot MFT300_MEXC reanudado*\nOperativa activa.")
        else:
            await self.notifier.send_with_keyboard("ℹ️ El bot ya está activo.")

    async def _cmd_pause(self):
        log.warning("⏸  Pausa de emergencia por Telegram")
        self._paused  = True
        self._running = False
        for symbol, grid in self.grids.items():
            await grid.cancel_all_orders()
            await grid.close_all_positions()
            self.pairs[symbol].reset()
        await self.notifier.send_with_keyboard(
            "⏸ *Pausa de emergencia activada*\n"
            "Órdenes canceladas y posiciones cerradas.\n"
            "Pulsa 🟢 START para reanudar.")

    async def _cmd_stop(self):
        log.warning("🛑 Stop por Telegram")
        await self.stop("Detenido por Telegram")

    async def _cmd_status(self):
        estado = "⏸ Pausado" if self._paused else "🟢 Activo"
        msg    = f"📊 *STATUS — MFT300_MEXC V.5*\n\nEstado: {estado}\n\n"

        for symbol, state in self.pairs.items():
            price  = await self.exchange.get_price(symbol)
            pnl    = await self.exchange.get_position_pnl_pct(symbol)
            orders = await self.exchange.get_open_orders(symbol)
            buys   = sum(1 for o in orders if o.get("side","").upper() == "BUY")
            sells  = sum(1 for o in orders if o.get("side","").upper() == "SELL")

            filtros = []
            if state.paused_volatility:  filtros.append("⚡ Vol 10min")
            if state.paused_volatility2: filtros.append("📉 Vol 30min")
            if state.paused_loss:        filtros.append("🔴 Pérdida máx.")
            if state.paused_daily_loss:  filtros.append("🛑 Límite diario")
            # Mostrar valores actuales de volatilidad
            vf1_pct = self.vol_filters[symbol].last_spike_pct
            vf2_pct = self.vol_filters2[symbol].last_spike_pct

            msg += f"*{symbol}*\n"
            if price: msg += f"  Precio: `{price:.6f}`\n"
            if pnl:   msg += f"  PnL: `{pnl:+.2f}%`\n"
            msg += f"  Órdenes: `{buys}` buy / `{sells}` sell\n"
            msg += f"  Pérdida hoy: `{state.daily_loss:.2f} USDT`\n"
            if filtros: msg += f"  Filtros: {', '.join(filtros)}\n"
            msg += "\n"

        msg += (f"Config: spacing `{self.cfg.GRID_SPACING_PCT}%` | "
                f"TP `{self.cfg.TAKE_PROFIT_PCT}%` | x`{self.cfg.LEVERAGE}`")
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
        trades = [t for s in self.pairs.values()
                  for t in s.trade_history if t["date"] == hoy]
        titulo = f"📈 *Resumen hoy — {datetime.now(TZ_SPAIN).strftime('%d/%m/%Y')}*"
        await self.notifier.send_with_keyboard(self._format_summary(trades, titulo))

    async def _cmd_week(self):
        hoy   = datetime.now(TZ_SPAIN).date()
        lunes = str(hoy - timedelta(days=hoy.weekday()))
        trades = [t for s in self.pairs.values()
                  for t in s.trade_history if t["date"] >= lunes]
        titulo = f"📅 *Resumen semana — desde {lunes[8:]}/{lunes[5:7]}*"
        await self.notifier.send_with_keyboard(self._format_summary(trades, titulo))

    # ── Ciclo principal ────────────────────────────────────────────────────────

    async def start(self):
        symbols_str = ", ".join(self.cfg.SYMBOLS)
        log.info("=" * 60)
        log.info(f"  MFT300_MEXC Bot V.5 — {len(self.cfg.SYMBOLS)} pares")
        log.info(f"  Pares: {symbols_str}")
        log.info("=" * 60)

        # Cancelar órdenes existentes al arrancar
        log.info("🧹 Limpiando órdenes existentes al arrancar...")
        for symbol in self.cfg.SYMBOLS:
            await self.exchange.cancel_all_orders(symbol)
        self.exchange._ignore_trades_before = time.time()
        await asyncio.sleep(2)

        await self.notifier.start_polling()
        await asyncio.sleep(2)
        await self.notifier.send_with_keyboard(
            f"🤖 *MFT300_MEXC Bot V.5 iniciado*\n"
            f"Pares: `{symbols_str}`\n"
            f"Grid: {self.cfg.GRID_LEVELS} niveles | "
            f"Spacing: {self.cfg.GRID_SPACING_PCT}% | "
            f"TP: {self.cfg.TAKE_PROFIT_PCT}%"
        )

        # ── Aviso caducidad API keys ──────────────────────────────────────────
        if self.cfg.API_EXPIRY_DATE:
            try:
                from datetime import date as _date
                expiry    = _date.fromisoformat(self.cfg.API_EXPIRY_DATE)
                days_left = (expiry - _date.today()).days
                if days_left <= 2:
                    await self.notifier.send(
                        f"⚠️ *AVISO — API Keys MEXC*\n"
                        f"Las keys caducan en *{days_left} día(s)* ({self.cfg.API_EXPIRY_DATE})\n"
                        f"Renuévalas en MEXC → API Management."
                    )
            except Exception as e:
                log.error("Error comprobando caducidad API: %s", e)

        self._running = True
        await self._main_loop()

    async def stop(self, reason="Manual"):
        log.warning(f"Deteniendo bot: {reason}")
        self._running = False
        self.notifier.stop_polling()
        for symbol, grid in self.grids.items():
            await grid.cancel_all_orders()
            await grid.close_all_positions()
        await self.notifier.send(f"🛑 *Bot detenido*\nMotivo: {reason}")

    async def _main_loop(self):
        while self._running or self._paused:
            if not self._paused:
                try:
                    await asyncio.gather(*[
                        self._cycle(symbol) for symbol in self.cfg.SYMBOLS
                    ])
                except Exception as e:
                    log.error(f"Error en ciclo principal: {e}", exc_info=True)
            await asyncio.sleep(self.cfg.LOOP_INTERVAL_SEC)

    async def _cycle(self, symbol: str):
        state  = self.pairs[symbol]
        grid   = self.grids[symbol]
        vf0    = self.vol_filters0[symbol]
        vf1    = self.vol_filters[symbol]
        vf2    = self.vol_filters2[symbol]

        price = await self.exchange.get_price(symbol)
        if price is None:
            log.warning(f"[{symbol}] No se pudo obtener precio")
            return

        state.current_price = price

        # ── 1. Pausa programada por horario ───────────────────────────────────────
        if self.cfg.SCHEDULED_PAUSE_ENABLED:
            now_es    = datetime.now(TZ_SPAIN)
            now_str   = now_es.strftime("%H:%M")
            start_str = self.cfg.SCHEDULED_PAUSE_START
            end_str   = self.cfg.SCHEDULED_PAUSE_END
            in_pause  = (start_str <= now_str < end_str) if start_str < end_str else                         (now_str >= start_str or now_str < end_str)

            if in_pause and not state.scheduled_was_active:
                state.scheduled_was_active = True
                state.paused_scheduled     = True
                await grid.cancel_all_orders()
                state.reset()
                log.info(f"[{symbol}] 🌙 Pausa horaria activada ({start_str}-{end_str})")
                await self.notifier.send(
                    f"🌙 *{symbol} — Pausa horaria*\n"
                    f"Horario: `{start_str} - {end_str}` (hora España)\n"
                    f"Órdenes canceladas — posiciones mantenidas."
                )
            elif not in_pause and state.scheduled_was_active:
                state.scheduled_was_active = False
                state.paused_scheduled     = False
                log.info(f"[{symbol}] ☀️  Pausa horaria finalizada — reanudando")
                await self.notifier.send(f"☀️ *{symbol}* — Pausa horaria finalizada. Reanudando grid.")

            if state.paused_scheduled:
                return

        # ── 2. Límite de pérdida diaria ────────────────────────────────────────
        today_str = datetime.now(TZ_SPAIN).strftime("%Y-%m-%d")
        if state.daily_loss_date != today_str:
            state.daily_loss       = 0.0
            state.daily_loss_date  = today_str
            state.paused_daily_loss = False
            log.info(f"[{symbol}] 📅 Nuevo día — contador reseteado")
        if state.paused_daily_loss:
            log.debug(f"[{symbol}] Pausado por límite diario")
            return

        # ── 2d. Filtro volatilidad ultra-rápida (capa 0 — 30 seg) ───────────────
        if state.paused_volatility0:
            if time.time() >= state.volatility0_resume_at:
                state.paused_volatility0 = False
                vf0.reset()
                log.info(f"[{symbol}] ▶️  Fin pausa spike ultra-rápido")
                await self.notifier.send(f"▶️ *{symbol}* — Filtro spike 30s: reanudando")
            else:
                return
        vf0.update(price)
        if vf0.triggered():
            pct = vf0.last_spike_pct
            log.warning(f"[{symbol}] 🔥 Spike ultra-rápido: {pct:.3f}% en 30s")
            await grid.cancel_all_orders()
            state.reset()
            state.paused_volatility0    = True
            state.volatility0_resume_at = time.time() + self.cfg.VOL0_PAUSE_SEC
            vf0.reset()
            await self.notifier.send(
                f"🔥 *{symbol} — Spike detectado*\n"
                f"Movimiento: `{pct:.3f}%` en 30s\n"
                f"Órdenes canceladas — posiciones mantenidas\n"
                f"Pausa: {self.cfg.VOL0_PAUSE_SEC // 60} min"
            )
            return

        # ── 3. Filtro volatilidad capa 2 (30 min) ─────────────────────────────
        if state.paused_volatility2:
            if time.time() >= state.volatility2_resume_at:
                state.paused_volatility2 = False
                vf2.reset()
                log.info(f"[{symbol}] ▶️  Fin pausa volatilidad sostenida")
                await self.notifier.send(f"▶️ *{symbol}* — Filtro volatilidad 30min: reanudando")
            else:
                return
        vf2.update(price)
        if vf2.triggered():
            pct = vf2.last_spike_pct
            log.warning(f"[{symbol}] 📉 Volatilidad sostenida: {pct:.3f}% en 30min")
            await grid.cancel_all_orders()
            state.reset()
            state.paused_volatility2    = True
            state.volatility2_resume_at = time.time() + self.cfg.VOL2_PAUSE_SEC
            vf2.reset()
            await self.notifier.send(
                f"📉 *{symbol} — Volatilidad sostenida*\n"
                f"Movimiento: `{pct:.3f}%` en 30 min\n"
                f"Órdenes canceladas — posiciones mantenidas\n"
                f"Pausa: {self.cfg.VOL2_PAUSE_SEC // 60} min"
            )
            return

        # ── 3b. Filtro de tendencia por velas consecutivas ────────────────────────
        if state.paused_trend:
            if time.time() >= state.trend_resume_at:
                state.paused_trend = False
                log.info(f"[{symbol}] ▶️  Fin pausa por tendencia detectada")
                await self.notifier.send(f"▶️ *{symbol}* — Tendencia finalizada, reanudando grid")
            else:
                remaining = int(state.trend_resume_at - time.time())
                log.debug(f"[{symbol}] Pausa tendencia: {remaining}s restantes")
                return

        # Obtener candles para detectar tendencia
        candles = await self.exchange.get_klines(symbol, self.cfg.TREND_TIMEFRAME, self.cfg.TREND_CANDLES + 5)
        if candles and len(candles) >= self.cfg.TREND_CANDLES + 1:
            # Invertir si vienen en DESC
            if len(candles) >= 2:
                t0 = int(candles[0].get("time", candles[0][0] if isinstance(candles[0], list) else 0))
                t1 = int(candles[1].get("time", candles[1][0] if isinstance(candles[1], list) else 0))
                if t0 > t1:
                    candles = list(reversed(candles))
            # Descartar vela en formación
            closed = candles[:-1]
            closes = [float(c.get("close", c[4] if isinstance(c, list) else 0)) for c in closed]
            trend  = self.trend_filter.detect(closes)
            if trend != "NONE":
                direction = "bajista 📉" if trend == "DOWN" else "alcista 📈"
                log.warning(f"[{symbol}] Tendencia {direction} detectada — avisando")
                await self.notifier.send(
                    f"⚠️ *{symbol} — Tendencia {direction}*\n"
                    f"{self.cfg.TREND_CANDLES} velas consecutivas >= {self.cfg.TREND_MIN_PCT}%\n"
                    f"El grid sigue activo.\nPulsa 🟡 PAUSE si quieres detenerlo."
                )

        # ── 4. Filtro volatilidad capa 1 (10 min) ─────────────────────────────
        if state.paused_volatility:
            if time.time() >= state.volatility_resume_at:
                state.paused_volatility = False
                vf1.reset()
                log.info(f"[{symbol}] ▶️  Fin pausa volatilidad rápida")
                await self.notifier.send(f"▶️ *{symbol}* — Filtro volatilidad 10min: reanudando")
            else:
                return
        vf1.update(price)
        if vf1.triggered():
            pct = vf1.last_spike_pct
            log.warning(f"[{symbol}] ⚡ Volatilidad extrema: {pct:.3f}% en 10min")
            await grid.cancel_all_orders()
            state.reset()
            state.paused_volatility    = True
            state.volatility_resume_at = time.time() + self.cfg.VOL_PAUSE_SEC
            vf1.reset()
            await self.notifier.send(
                f"⚡ *{symbol} — Volatilidad extrema*\n"
                f"Movimiento: `{pct:.3f}%` en 10 min\n"
                f"Órdenes canceladas — posiciones mantenidas\n"
                f"Pausa: {self.cfg.VOL_PAUSE_SEC // 60} min"
            )
            return

        # ── 5. Pausa por pérdida máxima ────────────────────────────────────────
        if state.paused_loss:
            if time.time() >= state.loss_resume_at:
                state.paused_loss = False
                self.exchange._last_closed_order_id = None
                self.exchange._ignore_trades_before = time.time()
                log.info(f"[{symbol}] ▶️  Fin pausa pérdida máxima")
                await self.notifier.send(f"▶️ *{symbol}* — Pausa pérdida finalizada")
            else:
                return

        # ── 5b. Pausa por pérdidas consecutivas ───────────────────────────────
        if state.paused_consecutive:
            if time.time() >= state.consecutive_resume_at:
                state.paused_consecutive = False
                state.consecutive_losses = 0
                self.exchange._last_closed_order_id = None
                self.exchange._ignore_trades_before = time.time()
                log.info(f"[{symbol}] ▶️  Fin pausa por pérdidas consecutivas — grid reiniciado")
                await self.notifier.send(f"▶️ *{symbol}* — Reanudando tras 2 pérdidas seguidas")
            else:
                remaining = int(state.consecutive_resume_at - time.time())
                log.debug(f"[{symbol}] Pausa consecutiva: {remaining}s restantes")
                return


        # ── 6. Detección de cierres (todos los trades nuevos) ────────────────────
        new_trades = await self.exchange.get_new_closed_trades(symbol)
        for trade in new_trades:
            pnl_usdt     = trade["pnl_usdt"]
            pnl_pct      = trade["pnl_pct"]
            fee          = trade["fee"]
            pnl_neto     = pnl_usdt - fee
            margin       = self.cfg.ORDER_SIZE_USDT
            pnl_neto_pct = (pnl_neto / margin * 100) if margin != 0 else 0.0
            emoji        = "✅" if pnl_neto >= 0 else "❌"

            log.info(
                f"[{symbol}] {emoji} Cierre {trade['side']} | "
                f"Precio: {trade['price']:.6f} | "
                f"PnL neto: {pnl_neto:+.4f} USDT ({pnl_neto_pct:+.2f}%) | "
                f"Fee: {fee:.4f} USDT"
            )
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

            # Contador de pérdidas consecutivas
            if pnl_neto < 0:
                state.consecutive_losses += 1
                log.info(f"[{symbol}] ⚠️  Pérdidas consecutivas: {state.consecutive_losses}/2")
                if state.consecutive_losses >= 2:
                    log.warning(f"[{symbol}] ⚠️  2 pérdidas consecutivas — avisando")
                    await self.notifier.send(
                        f"⚠️ *{symbol} — 2 pérdidas consecutivas*\n"
                        f"El grid sigue activo.\n"
                        f"Pulsa 🟡 PAUSE si quieres detenerlo."
                    )
                    state.consecutive_losses = 0
            else:
                state.consecutive_losses = 0

            # Acumular pérdida diaria
            if pnl_neto < 0:
                state.daily_loss += abs(pnl_neto)
                log.info(f"[{symbol}] 📉 Pérdida diaria: {state.daily_loss:.2f} / {self.cfg.DAILY_LOSS_LIMIT:.2f} USDT")
                if state.daily_loss >= self.cfg.DAILY_LOSS_LIMIT:
                    log.warning(f"[{symbol}] 🛑 Límite diario alcanzado")
                    state.paused_daily_loss = True
                    await grid.cancel_all_orders()
                    await grid.close_all_positions()
                    state.reset()
                    await self.notifier.send(
                        f"🛑 *{symbol} — Límite diario alcanzado*\n"
                        f"Pérdida: `{state.daily_loss:.2f} USDT`\n"
                        f"El bot reanudará mañana."
                    )
                    return

            # Filtro pérdida máxima por trade
            if self.loss_filter.triggered(pnl_pct):
                log.warning(f"[{symbol}] 🔴 Filtro pérdida máxima: {pnl_pct:.4f}%")
                await grid.cancel_all_orders()
                await grid.close_all_positions()
                state.reset()
                state.paused_loss    = True
                state.loss_resume_at = time.time() + self.cfg.LOSS_PAUSE_SEC
                await self.notifier.send(
                    f"🔴 *{symbol} — Filtro pérdida máxima*\n"
                    f"PnL: `{pnl_pct:.4f}%`\n"
                    f"Pausa: {self.cfg.LOSS_PAUSE_SEC // 60} min"
                )
                return

        # ── Calcular spacing dinámico por ATR y niveles asimétricos ─────────────
        buy_lvl  = self.cfg.GRID_LEVELS
        sell_lvl = self.cfg.GRID_LEVELS
        atr_candles = await self.exchange.get_klines(symbol, self.cfg.ATR_TIMEFRAME, self.cfg.ATR_PERIOD * 3)
        if atr_candles and len(atr_candles) >= self.cfg.ATR_PERIOD + 1:
            if len(atr_candles) >= 2:
                t0 = int(atr_candles[0].get("time", 0))
                t1 = int(atr_candles[1].get("time", 0))
                if t0 > t1:
                    atr_candles = list(reversed(atr_candles))
            atr_candles = atr_candles[:-1]
            h_list = [float(c.get("high",  0)) for c in atr_candles]
            l_list = [float(c.get("low",   0)) for c in atr_candles]
            c_list = [float(c.get("close", 0)) for c in atr_candles]
            new_spacing, new_tp = self.atr_filter.get_spacing_and_tp(h_list, l_list, c_list)
            if new_spacing != self.cfg.GRID_SPACING_PCT or new_tp != self.cfg.TAKE_PROFIT_PCT:
                log.info(
                    f"[{symbol}] 📐 Spacing dinámico: {self.cfg.GRID_SPACING_PCT}% → {new_spacing}% | "
                    f"TP: {self.cfg.TAKE_PROFIT_PCT}% → {new_tp}%"
                )
                nivel = "🟢 Baja" if new_spacing == self.cfg.SPACING_LOW else ("🔴 Alta" if new_spacing == self.cfg.SPACING_HIGH else "🟡 Normal")
                await self.notifier.send(
                    f"📐 *{symbol} — Spacing ajustado*\n"
                    f"Volatilidad: {nivel}\n"
                    f"Spacing: `{self.cfg.GRID_SPACING_PCT}%` → `{new_spacing}%`\n"
                    f"TP: `{self.cfg.TAKE_PROFIT_PCT}%` → `{new_tp}%`"
                )
            self.cfg.GRID_SPACING_PCT = new_spacing
            self.cfg.TAKE_PROFIT_PCT  = new_tp
            buy_lvl, sell_lvl = self.asym_grid.get_levels(c_list)

            # Detectar cambio de asimetría y reajustar grid
            if (buy_lvl != state.last_buy_lvl or sell_lvl != state.last_sell_lvl) and grid.is_active():
                direction = "alcista 📈" if buy_lvl > sell_lvl else ("bajista 📉" if sell_lvl > buy_lvl else "neutral ↔️")
                log.info(f"[{symbol}] 🔄 Asimetría cambia: {state.last_buy_lvl}B/{state.last_sell_lvl}S → {buy_lvl}B/{sell_lvl}S")
                await grid.cancel_all_orders()
                await grid.initialize(price, buy_lvl, sell_lvl)
                state.last_buy_lvl    = buy_lvl
                state.last_sell_lvl   = sell_lvl
                state.last_cleanup_at = time.time()
                await self.notifier.send(
                    f"🔄 *{symbol} — Grid reajustado*\n"
                    f"Tendencia: {direction}\n"
                    f"Niveles: `{buy_lvl}B / {sell_lvl}S`\n"
                    f"Centro: `{price:.6f}`"
                )
                return
            state.last_buy_lvl  = buy_lvl
            state.last_sell_lvl = sell_lvl

        # ── 7. Limpieza periódica ──────────────────────────────────────────────
        elapsed = time.time() - state.last_cleanup_at
        if elapsed >= self.cfg.CLEANUP_INTERVAL_SEC and grid.is_active():
            log.info(f"[{symbol}] 🧹 Limpieza periódica — {elapsed/3600:.1f}h")
            await grid.cancel_all_orders()
            await grid.initialize(price, buy_lvl, sell_lvl)
            state.last_cleanup_at = time.time()
            await self.notifier.send(f"🧹 *{symbol}* — Grid rehecho en `{price:.6f}`")
            return


        # ── 8. Grid activo ─────────────────────────────────────────────────────
        if not grid.is_active():
            log.info(f"[{symbol}] Iniciando grid en {price:.6f}")
            await grid.initialize(price, buy_lvl, sell_lvl)
            return

        # Zona protegida y Stop Loss por posición
        pnl_pct_pos = await self.exchange.get_position_pnl_pct(symbol)

        # Stop Loss por posición abierta
        if pnl_pct_pos is not None and pnl_pct_pos <= self.cfg.POSITION_SL_PCT:
            log.warning(f"[{symbol}] 🛑 Stop Loss posición | PnL: {pnl_pct_pos:.2f}% <= {self.cfg.POSITION_SL_PCT}%")
            await grid.cancel_all_orders()
            await grid.close_all_positions()
            state.reset()
            await self.notifier.send(
                f"🛑 *{symbol} — Stop Loss activado*\n"
                f"PnL no realizado: `{pnl_pct_pos:.2f}%`\n"
                f"Posición cerrada a mercado.\n"
                f"Grid reiniciándose..."
            )
            return

        # ── Take Profit global por lado ───────────────────────────────────────
        if self.cfg.GLOBAL_TP_PCT > 0 and not state.tp_global_active:
            for side_type, side_name in [(1, "LONG"), (2, "SHORT")]:
                side_pnl = await self.exchange.get_side_pnl_pct(symbol, side_type)
                if side_pnl is not None and side_pnl >= self.cfg.GLOBAL_TP_PCT:
                    state.tp_global_active = True
                    log.info("[%s] 🎯 TP global %s | PnL: %.2f%%", symbol, side_name, side_pnl)
                    await self.exchange.close_side_positions(symbol, side_type)
                    await self.notifier.send(
                        f"🎯 *{symbol} — TP global {side_name}*\n"
                        f"PnL acumulado: `{side_pnl:.2f}%`\n"
                        f"Posiciones {side_name} cerradas."
                    )
                    await asyncio.sleep(5)
                    state.tp_global_active = False

        # Zona protegida
        if pnl_pct_pos is not None and self.protected_zone.is_protected(pnl_pct_pos):
            log.debug(f"[{symbol}] Zona protegida | PnL: {pnl_pct_pos:.2f}%")
            return

        # Grid dinámico
        await grid.check_and_recenter(price)

        pnl_str = f"| PnL: {pnl_pct_pos:.2f}%" if pnl_pct_pos else ""
        log.info(f"[{symbol}] 💹 Precio: {price:.6f} {pnl_str}")


# ─── Entry point ───────────────────────────────────────────────────────────────
async def main():
    bot = MFT300_MEXCMEXCBot()

    def handle_signal(sig, frame):
        asyncio.create_task(bot.stop("Señal del sistema"))

    signal.signal(signal.SIGINT,  handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    await bot.start()


if __name__ == "__main__":
    asyncio.run(main())
