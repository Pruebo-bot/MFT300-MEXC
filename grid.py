"""
Motor del Grid para MEXC Futuros.
Adapta el grid de Bitunix para MEXC:
- Volumen en contratos (no en USDT directamente)
- side=1 OpenLong, side=3 OpenShort
- TP adjunto a cada orden
"""

import logging
import asyncio
from typing import Optional

log = logging.getLogger("grid")


class GridEngine:
    def __init__(self, cfg, exchange, state):
        self.cfg      = cfg
        self.exchange = exchange
        self.state    = state
        self._active  = False

    def is_active(self) -> bool:
        return self._active and self.state.grid_center is not None

    def _round_price(self, price: float) -> float:
        return round(price, 4)

    def _calculate_qty(self, price: float) -> float:
        """Volumen en contratos = (margen * leverage) / (precio * contractSize)."""
        cs  = self.cfg.CONTRACT_SIZE
        vol = (self.cfg.ORDER_SIZE_USDT * self.cfg.LEVERAGE) / (price * cs)
        return max(1, int(vol))  # MEXC usa enteros para FET_USDT

    def _calculate_levels(self, center: float, buy_levels: int, sell_levels: int) -> list:
        spacing = self.cfg.GRID_SPACING_PCT / 100
        levels  = []
        for i in range(1, buy_levels + 1):
            bp = center * (1 - spacing * i)
            levels.append({"price": self._round_price(bp), "side": 1})  # 1=OpenLong
        for i in range(1, sell_levels + 1):
            sp = center * (1 + spacing * i)
            levels.append({"price": self._round_price(sp), "side": 3})  # 3=OpenShort
        return levels

    async def initialize(self, center_price: float, buy_levels: int = None, sell_levels: int = None):
        b = buy_levels  or self.cfg.GRID_LEVELS
        s = sell_levels or self.cfg.GRID_LEVELS
        log.info("Inicializando grid | Centro: %f | %dB / %dS", center_price, b, s)
        self.state.grid_center = center_price
        levels = self._calculate_levels(center_price, b, s)
        tp_pct = self.cfg.TAKE_PROFIT_PCT / 100
        qty    = self._calculate_qty(center_price)
        placed = 0
        for lvl in levels:
            lp = lvl["price"]
            tp = self._round_price(lp * (1 + tp_pct)) if lvl["side"] == 1 else self._round_price(lp * (1 - tp_pct))
            ok = await self.exchange.place_limit_order(
                self.state.symbol, lvl["side"], lp, qty, tp
            )
            if ok:
                placed += 1
            await asyncio.sleep(3.0)  # Rate limit MEXC
        log.info("Órdenes colocadas: %d/%d", placed, len(levels))
        log.info("Grid activo | %d niveles colocados", placed)
        self._active = placed > 0

    async def cancel_all_orders(self):
        await self.exchange.cancel_all_orders(self.state.symbol)
        self._active = False

    async def close_all_positions(self):
        await self.exchange.close_all_positions(self.state.symbol)

    async def check_and_recenter(self, price: float):
        if self.state.grid_center is None:
            return
        deviation = abs(price - self.state.grid_center) / self.state.grid_center * 100
        if deviation >= self.cfg.RECENTER_THRESHOLD_PCT:
            log.info("[%s] 🔄 Recentrando | Desviación: %.2f%%", self.state.symbol, deviation)
            await self.cancel_all_orders()
            await self.close_all_positions()
            self.state.reset()
