"""
Motor del Grid para MEXC Futuros
Coloca órdenes límite en niveles calculados con TP integrado.
MEXC usa volumen en contratos, no en USDT directamente.
"""

import logging
import asyncio
from typing import Optional

import config
from state import PairState

log = logging.getLogger("GRID")


class GridEngine:
    def __init__(self, cfg, exchange, state: PairState):
        self.cfg      = cfg
        self.exchange = exchange
        self.state    = state
        self._symbol_info = None

    def is_active(self) -> bool:
        return self.state.grid_center is not None

    def _round_price(self, price: float) -> float:
        return round(price, 4)  # MEXC FET_USDT priceScale=4

    async def _get_symbol_info(self):
        if self._symbol_info is None:
            self._symbol_info = await self.exchange.get_contract_info(self.state.symbol)
        return self._symbol_info

    def _calculate_levels(self, center: float,
                           buy_levels: int = None,
                           sell_levels: int = None) -> list:
        spacing = self.cfg.GRID_SPACING_PCT / 100
        n_buy   = buy_levels  if buy_levels  is not None else self.cfg.GRID_LEVELS
        n_sell  = sell_levels if sell_levels is not None else self.cfg.GRID_LEVELS
        levels  = []
        for i in range(1, n_buy + 1):
            bp = center * (1 - spacing * i)
            levels.append({"price": self._round_price(bp), "side": 1, "distance": i})  # 1=OpenLong
        for i in range(1, n_sell + 1):
            sp = center * (1 + spacing * i)
            levels.append({"price": self._round_price(sp), "side": 2, "distance": i})  # 2=OpenShort
        levels.sort(key=lambda x: x["distance"])
        return levels

    async def _place_orders(self, levels: list):
        info = await self._get_symbol_info()
        if not info:
            log.error("[%s] No se pudo obtener info del contrato", self.state.symbol)
            return

        price    = self.state.current_price or levels[0]["price"]
        vol      = self.exchange.calculate_vol(info, price, self.cfg.ORDER_SIZE_USDT, self.cfg.LEVERAGE)
        tp_pct   = self.cfg.TAKE_PROFIT_PCT / 100
        placed   = 0

        for lvl in levels:
            lp = lvl["price"]
            if lvl["side"] == 1:  # Long — TP arriba
                tp = self._round_price(lp * (1 + tp_pct))
            else:                  # Short — TP abajo
                tp = self._round_price(lp * (1 - tp_pct))

            result = await self.exchange.place_limit_order(
                self.state.symbol, lvl["side"], vol, lp, tp
            )
            if result:
                placed += 1
            await asyncio.sleep(0.5)  # MEXC rate limit: máx 2 órdenes/seg

        log.info("Órdenes colocadas: %d/%d", placed, len(levels))
        log.info("Grid activo | %d niveles colocados", placed)

    async def initialize(self, center_price: float,
                          buy_levels: int = None, sell_levels: int = None):
        b = buy_levels  or self.cfg.GRID_LEVELS
        s = sell_levels or self.cfg.GRID_LEVELS
        log.info("Inicializando grid | Centro: %f | %dB / %dS", center_price, b, s)
        self.state.grid_center  = center_price
        levels = self._calculate_levels(center_price, b, s)
        await self._place_orders(levels)

    async def cancel_all_orders(self):
        await self.exchange.cancel_all_orders(self.state.symbol)

    async def close_all_positions(self):
        await self.exchange.close_all_positions(self.state.symbol)

    async def check_and_recenter(self, price: float):
        if self.state.grid_center is None:
            return
        deviation = abs(price - self.state.grid_center) / self.state.grid_center * 100
        if deviation >= self.cfg.RECENTER_THRESHOLD_PCT:
            log.info("[%s] 🔄 Recentrando grid | Desviación: %.2f%%",
                     self.state.symbol, deviation)
            await self.cancel_all_orders()
            await self.close_all_positions()
            self.state.reset()
