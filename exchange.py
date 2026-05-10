"""
Cliente para la API de MEXC Futuros Perpetuos (USDT-M)
Firma: HMAC SHA256 — api_key + timestamp + params_str
Base URL: https://api.mexc.com
Símbolos: FET_USDT, BTC_USDT...
"""

import hashlib
import hmac
import time
import json
import logging
import aiohttp
from typing import Optional

log = logging.getLogger("exchange")


class MEXCClient:
    def __init__(self, cfg):
        self.cfg        = cfg
        self.api_key    = cfg.API_KEY
        self.api_secret = cfg.API_SECRET
        self.base_url   = cfg.BASE_URL
        self._session: Optional[aiohttp.ClientSession] = None
        self._last_closed_pos_id: Optional[str] = None
        self._ignore_trades_before: float = 0.0

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    def _sign(self, to_sign: str) -> str:
        return hmac.new(
            self.api_secret.encode("utf-8"),
            to_sign.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

    def _build_headers(self, timestamp: str, params_str: str) -> dict:
        to_sign   = self.api_key + timestamp + params_str
        signature = self._sign(to_sign)
        return {
            "ApiKey":       self.api_key,
            "Request-Time": timestamp,
            "Signature":    signature,
            "Content-Type": "application/json",
        }

    async def _get(self, path: str, params: dict = None) -> Optional[object]:
        try:
            session   = await self._get_session()
            timestamp = str(int(time.time() * 1000))
            clean     = {k: str(v) for k, v in (params or {}).items() if v is not None}
            qs        = "&".join(f"{k}={v}" for k, v in sorted(clean.items()))
            headers   = self._build_headers(timestamp, qs)
            url       = f"{self.base_url}{path}"
            async with session.get(url, params=clean, headers=headers) as resp:
                data = await resp.json()
                if data.get("success") and data.get("code") == 0:
                    return data.get("data")
                log.warning("GET %s → %s", path, data)
        except Exception as e:
            log.error("GET %s excepción: %s", path, e)
        return None

    async def _post(self, path: str, body: dict = None) -> Optional[object]:
        try:
            session   = await self._get_session()
            timestamp = str(int(time.time() * 1000))
            clean     = {k: v for k, v in (body or {}).items() if v is not None}
            body_str  = json.dumps(clean)
            headers   = self._build_headers(timestamp, body_str)
            url       = f"{self.base_url}{path}"
            async with session.post(url, data=body_str, headers=headers) as resp:
                text = await resp.text()
                try:
                    data = json.loads(text)
                except Exception:
                    log.error("POST %s respuesta no JSON: %s", path, text[:200])
                    return None
                if data.get("success") and data.get("code") == 0:
                    return data.get("data")
                log.warning("POST %s → %s", path, data)
        except Exception as e:
            log.error("POST %s excepción: %s", path, e)
        return None

    # ── Precio ────────────────────────────────────────────────────────────────

    async def get_price(self, symbol: str) -> Optional[float]:
        try:
            session = await self._get_session()
            url     = f"{self.base_url}/api/v1/contract/ticker"
            async with session.get(url, params={"symbol": symbol}) as resp:
                data = await resp.json()
                if data.get("success") and data.get("code") == 0:
                    return float(data["data"].get("lastPrice", 0))
        except Exception as e:
            log.error("get_price: %s", e)
        return None

    # ── Klines ────────────────────────────────────────────────────────────────

    async def get_klines(self, symbol: str, interval: str, limit: int = 50) -> list:
        """interval: Min1, Min5, Min15, Min30, Min60, Hour4, Day1"""
        try:
            session = await self._get_session()
            url     = f"{self.base_url}/api/v1/contract/kline/{symbol}"
            async with session.get(url, params={"interval": interval}) as resp:
                data = await resp.json()
                if data.get("success") and data.get("code") == 0:
                    d      = data["data"]
                    times  = d.get("time", [])
                    opens  = d.get("open", [])
                    highs  = d.get("high", [])
                    lows   = d.get("low", [])
                    closes = d.get("close", [])
                    candles = [
                        {"time": times[i] if i < len(times) else 0,
                         "open":  opens[i]  if i < len(opens)  else 0,
                         "high":  highs[i]  if i < len(highs)  else 0,
                         "low":   lows[i]   if i < len(lows)   else 0,
                         "close": closes[i]}
                        for i in range(len(closes))
                    ]
                    return candles[-limit:]
        except Exception as e:
            log.error("get_klines: %s", e)
        return []

    # ── Órdenes ───────────────────────────────────────────────────────────────

    async def get_open_orders(self, symbol: str) -> list:
        timestamp = str(int(time.time() * 1000))
        to_sign   = self.api_key + timestamp
        sign      = self._sign(to_sign)
        headers   = {"ApiKey": self.api_key, "Request-Time": timestamp, "Signature": sign}
        try:
            session = await self._get_session()
            url     = f"{self.base_url}/api/v1/private/order/list/open_orders/{symbol}"
            async with session.get(url, headers=headers) as resp:
                data = await resp.json()
                if data.get("success") and data.get("code") == 0:
                    result = data.get("data", [])
                    return result if isinstance(result, list) else result.get("resultList", [])
        except Exception as e:
            log.error("get_open_orders: %s", e)
        return []

    async def cancel_all_orders(self, symbol: str) -> bool:
        result = await self._post("/api/v1/private/order/cancel_all", {"symbol": symbol})
        log.info("Todas las órdenes canceladas")
        return True

    async def place_limit_order(
        self,
        symbol: str,
        side: int,
        price: float,
        qty: float,
        take_profit: Optional[float] = None,
    ) -> bool:
        """
        side: 1=OpenLong, 3=OpenShort
        """
        body = {
            "symbol":   symbol,
            "side":     side,
            "type":     1,
            "vol":      qty,
            "price":    round(price, 4),
            "openType": 1,
            "leverage": self.cfg.LEVERAGE,
        }
        if take_profit is not None:
            body["takeProfitPrice"] = round(take_profit, 4)
            body["takeProfitType"]  = 1
        result = await self._post("/api/v1/private/order/submit", body)
        if result is None:
            log.warning("place_limit_order falló: side=%s price=%s", side, price)
        return result is not None

    async def close_all_positions(self, symbol: str) -> bool:
        result = await self._post("/api/v1/private/order/cancel_all", {"symbol": symbol})
        # También cerrar posiciones abiertas con market order
        positions = await self._get_positions(symbol)
        for pos in positions:
            vol  = float(pos.get("holdVol", 0))
            side = int(pos.get("positionType", 1))
            if vol > 0:
                close_side = 2 if side == 1 else 4
                await self._post("/api/v1/private/order/submit", {
                    "symbol":   symbol,
                    "side":     close_side,
                    "type":     5,
                    "vol":      vol,
                    "openType": 1,
                })
        log.info("Posiciones cerradas")
        return True

    # ── Posiciones ────────────────────────────────────────────────────────────

    async def _get_positions(self, symbol: str) -> list:
        timestamp = str(int(time.time() * 1000))
        params    = {"symbol": symbol}
        qs        = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        to_sign   = self.api_key + timestamp + qs
        sign      = self._sign(to_sign)
        headers   = {"ApiKey": self.api_key, "Request-Time": timestamp, "Signature": sign}
        try:
            session = await self._get_session()
            url     = f"{self.base_url}/api/v1/private/position/open_positions"
            async with session.get(url, params=params, headers=headers) as resp:
                data = await resp.json()
                if data.get("success") and data.get("code") == 0:
                    return data.get("data", [])
        except Exception as e:
            log.error("_get_positions: %s", e)
        return []

    async def get_position(self, symbol: str) -> Optional[dict]:
        positions = await self._get_positions(symbol)
        for pos in positions:
            if float(pos.get("holdVol", 0)) > 0:
                return pos
        return None

    async def get_position_pnl_pct(self, symbol: str) -> Optional[float]:
        """PnL% real sobre el margen (coincide con lo que muestra MEXC)."""
        try:
            price = await self.get_price(symbol)
            if not price:
                return None
            positions = await self._get_positions(symbol)
            if not positions:
                return None
            total_pnl = 0.0
            total_im  = 0.0
            for pos in positions:
                entry = float(pos.get("openAvgPrice", 0))
                vol   = float(pos.get("holdVol", 0))
                im    = float(pos.get("im", 0))
                side  = int(pos.get("positionType", 1))
                if entry == 0 or vol == 0:
                    continue
                cs  = self.cfg.CONTRACT_SIZE
                pnl = (price - entry) * vol * cs if side == 1 else (entry - price) * vol * cs
                total_pnl += pnl
                total_im  += im
            if total_im == 0:
                return None
            return (total_pnl / total_im) * 100
        except Exception as e:
            log.error("get_position_pnl_pct: %s", e)
        return None

    async def get_side_pnl_pct(self, symbol: str, position_type: int) -> Optional[float]:
        """PnL% real de un lado (1=LONG, 2=SHORT) sobre su margen."""
        try:
            price = await self.get_price(symbol)
            if not price:
                return None
            positions = await self._get_positions(symbol)
            total_pnl = 0.0
            total_im  = 0.0
            for pos in positions:
                if int(pos.get("positionType", 0)) != position_type:
                    continue
                entry = float(pos.get("openAvgPrice", 0))
                vol   = float(pos.get("holdVol", 0))
                im    = float(pos.get("im", 0))
                if entry == 0 or vol == 0:
                    continue
                cs  = self.cfg.CONTRACT_SIZE
                pnl = (price - entry) * vol * cs if position_type == 1 else (entry - price) * vol * cs
                total_pnl += pnl
                total_im  += im
            if total_im == 0:
                return None
            return (total_pnl / total_im) * 100
        except Exception as e:
            log.error("get_side_pnl_pct: %s", e)
        return None

    async def close_side_positions(self, symbol: str, position_type: int) -> bool:
        """Cierra todas las posiciones de un lado (1=LONG, 2=SHORT) a mercado."""
        positions = await self._get_positions(symbol)
        for pos in positions:
            if int(pos.get("positionType", 0)) != position_type:
                continue
            vol = float(pos.get("holdVol", 0))
            if vol == 0:
                continue
            close_side = 2 if position_type == 1 else 4
            await self._post("/api/v1/private/order/submit", {
                "symbol":   symbol,
                "side":     close_side,
                "type":     5,
                "vol":      vol,
                "openType": 1,
            })
        return True

    async def set_leverage(self, symbol: str, leverage: int) -> bool:
        return True  # Se pasa en cada orden

    # ── Historial de trades ───────────────────────────────────────────────────

    async def get_new_closed_trades(self, symbol: str) -> list:
        """Obtiene posiciones cerradas nuevas desde el último procesado."""
        timestamp = str(int(time.time() * 1000))
        params    = {"symbol": symbol, "pageNum": "1", "pageSize": "10"}
        qs        = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        to_sign   = self.api_key + timestamp + qs
        sign      = self._sign(to_sign)
        headers   = {"ApiKey": self.api_key, "Request-Time": timestamp, "Signature": sign}
        try:
            session = await self._get_session()
            url     = f"{self.base_url}/api/v1/private/position/list/history_positions"
            async with session.get(url, params=params, headers=headers) as resp:
                data = await resp.json()
                if not (data.get("success") and data.get("code") == 0):
                    return []
                positions = data.get("data", [])
                if not positions:
                    return []

                new_trades = []
                for pos in positions:
                    if pos.get("state") != 3:
                        continue
                    pos_id     = str(pos.get("positionId"))
                    close_time = float(pos.get("updateTime", 0)) / 1000

                    if pos_id == self._last_closed_pos_id:
                        break
                    if close_time < self._ignore_trades_before:
                        continue

                    realised = float(pos.get("realised", 0))
                    fee      = abs(float(pos.get("totalFee", 0)))
                    side_num = int(pos.get("positionType", 1))
                    price    = float(pos.get("closeAvgPrice", 0))
                    im       = float(pos.get("im", self.cfg.ORDER_SIZE_USDT))
                    if im == 0:
                        im = self.cfg.ORDER_SIZE_USDT
                    pnl_pct = (realised / im * 100) if im != 0 else 0.0

                    new_trades.append({
                        "pnl_usdt": realised,
                        "pnl_pct":  pnl_pct,
                        "price":    price,
                        "fee":      fee,
                        "side":     "BUY" if side_num == 1 else "SELL",
                        "trade_id": pos_id,
                    })

                if positions:
                    self._last_closed_pos_id = str(positions[0].get("positionId"))

                return new_trades
        except Exception as e:
            log.error("get_new_closed_trades: %s", e)
        return []

    async def get_last_closed_trade(self, symbol: str) -> Optional[dict]:
        trades = await self.get_new_closed_trades(symbol)
        return trades[0] if trades else None
