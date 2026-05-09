"""
Cliente para la API de MEXC Futuros Perpetuos (USDT-M)
Autenticación: HMAC SHA256 estándar
Base URL: https://contract.mexc.com
Símbolos: BTC_USDT, ETH_USDT, FET_USDT...
"""

import hashlib
import hmac
import time
import json
import logging
import aiohttp
from typing import Optional

import config

log = logging.getLogger(__name__)


class MEXCClient:
    def __init__(self):
        self.api_key    = config.MEXC_API_KEY
        self.api_secret = config.MEXC_API_SECRET
        self.base_url   = config.BASE_URL
        self._session: Optional[aiohttp.ClientSession] = None
        self._ignore_trades_before: float = 0.0
        self._last_closed_order_id: Optional[str] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    def _sign(self, to_sign: str) -> str:
        """HMAC SHA256 — firma MEXC: accessKey + timestamp + params."""
        return hmac.new(
            self.api_secret.encode("utf-8"),
            to_sign.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

    def _build_headers(self, timestamp: str, params_str: str) -> dict:
        # Orden correcto: api_key + timestamp + params_string
        to_sign  = self.api_key + timestamp + params_str
        signature = self._sign(to_sign)
        return {
            "ApiKey":       self.api_key,
            "Request-Time": timestamp,
            "Signature":    signature,
            "Content-Type": "application/json",
        }

    async def _get(self, path: str, params: dict = None) -> Optional[dict]:
        try:
            session   = await self._get_session()
            timestamp = str(int(time.time() * 1000))
            # Filtrar params None y ordenar alfabéticamente
            clean_params = {k: v for k, v in (params or {}).items() if v is not None}
            qs = "&".join(f"{k}={v}" for k, v in sorted(clean_params.items()))
            headers = self._build_headers(timestamp, qs)
            url     = f"{self.base_url}{path}"
            async with session.get(url, params=clean_params, headers=headers) as resp:
                data = await resp.json()
                if data.get("success") and data.get("code") == 0:
                    return data.get("data")
                log.warning("GET %s error: %s", path, data)
        except Exception as e:
            log.error("GET %s excepción: %s", path, e)
        return None

    async def _post(self, path: str, body: dict = None) -> Optional[dict]:
        try:
            session   = await self._get_session()
            timestamp = str(int(time.time() * 1000))
            # Filtrar valores None del body
            clean_body = {k: v for k, v in (body or {}).items() if v is not None}
            body_str   = json.dumps(clean_body)
            headers    = self._build_headers(timestamp, body_str)
            url        = f"{self.base_url}{path}"
            async with session.post(url, data=body_str, headers=headers) as resp:
                text = await resp.text()
                try:
                    data = json.loads(text)
                except Exception:
                    log.error("POST %s respuesta no JSON: %s", path, text[:200])
                    return None
                if data.get("success") and data.get("code") == 0:
                    return data.get("data")
                log.warning("POST %s error: %s", path, data)
        except Exception as e:
            log.error("POST %s excepción: %s", path, e)
        return None

    # ── Mercado ────────────────────────────────────────────────────────────────

    async def get_price(self, symbol: str) -> Optional[float]:
        data = await self._get(f"/api/v1/contract/ticker", {"symbol": symbol})
        if data:
            return float(data.get("lastPrice", 0))
        return None

    async def get_klines(self, symbol: str, interval: str, limit: int = 50) -> list:
        """interval: Min1, Min5, Min15, Min30, Min60, Hour4, Day1"""
        data = await self._get(f"/api/v1/contract/kline/{symbol}", {
            "interval": interval,
        })
        if data and isinstance(data, dict):
            times  = data.get("time", [])
            opens  = data.get("open", [])
            highs  = data.get("high", [])
            lows   = data.get("low", [])
            closes = data.get("close", [])
            candles = []
            for i in range(len(closes)):
                candles.append({
                    "time":  times[i] if i < len(times) else 0,
                    "open":  opens[i] if i < len(opens) else 0,
                    "high":  highs[i] if i < len(highs) else 0,
                    "low":   lows[i]  if i < len(lows)  else 0,
                    "close": closes[i],
                })
            return candles[-limit:]
        return []

    async def get_contract_info(self, symbol: str) -> Optional[dict]:
        try:
            session = await self._get_session()
            url     = f"{self.base_url}/api/v1/contract/detail"
            async with session.get(url, params={"symbol": symbol}) as resp:
                data = await resp.json()
                if data.get("success") and data.get("code") == 0:
                    result = data.get("data")
                    if isinstance(result, list):
                        for c in result:
                            if c.get("symbol") == symbol:
                                return c
                    elif isinstance(result, dict):
                        return result
        except Exception as e:
            log.error("get_contract_info error: %s", e)
        # Fallback — devolver info mínima para que el bot pueda operar
        log.warning("[%s] Usando info de contrato por defecto", symbol)
        return {"symbol": symbol, "contractSize": 1, "volScale": 2}

    # ── Cuenta ─────────────────────────────────────────────────────────────────

    async def get_balance(self) -> Optional[float]:
        data = await self._get("/api/v1/private/account/assets")
        if data and isinstance(data, list):
            for asset in data:
                if asset.get("currency") == "USDT":
                    return float(asset.get("availableBalance", 0))
        return None

    async def get_position(self, symbol: str) -> Optional[dict]:
        data = await self._get("/api/v1/private/position/open_positions", {"symbol": symbol})
        if data and isinstance(data, list):
            for pos in data:
                if pos.get("symbol") == symbol and float(pos.get("holdVol", 0)) != 0:
                    return pos
        return None

    async def get_position_pnl_pct(self, symbol: str) -> Optional[float]:
        """Devuelve el PnL% de la primera posición (para SL individual)."""
        try:
            pos = await self.get_position(symbol)
            if not pos:
                return None
            price = await self.get_price(symbol)
            if not price:
                return None
            entry = float(pos.get("openAvgPrice", 0))
            vol   = float(pos.get("holdVol", 0))
            im    = float(pos.get("im", 0))
            side  = int(pos.get("positionType", 1))
            if entry == 0 or vol == 0 or im == 0:
                return None
            contract_size = 10.0  # FET_USDT contractSize = 10
            pnl = (price - entry) * vol * contract_size if side == 1 else (entry - price) * vol * contract_size
            return (pnl / im) * 100
        except Exception as e:
            log.error("get_position_pnl_pct error: %s", e)
        return None

    async def get_side_pnl_pct(self, symbol: str, position_type: int) -> Optional[float]:
        """Calcula el ROE% acumulado de un lado (1=LONG, 2=SHORT) — incluye apalancamiento."""
        try:
            import config as _cfg
            price = await self.get_price(symbol)
            if not price:
                return None
            data = await self._get("/api/v1/private/position/open_positions", {"symbol": symbol})
            if not data or not isinstance(data, list):
                return None
            total_pnl = 0.0
            total_im  = 0.0
            for pos in data:
                if int(pos.get("positionType", 0)) != position_type:
                    continue
                entry    = float(pos.get("openAvgPrice", 0))
                vol      = float(pos.get("holdVol", 0))
                im       = float(pos.get("im", 0))
                leverage = float(pos.get("leverage", _cfg.LEVERAGE))
                if entry == 0 or vol == 0:
                    continue
                contract_size = 10.0  # FET_USDT contractSize = 10
                pnl = (price - entry) * vol * contract_size if position_type == 1 else (entry - price) * vol * contract_size
                total_pnl += pnl
                total_im  += im
            if total_im == 0:
                return None
            return (total_pnl / total_im) * 100
        except Exception as e:
            log.error("get_side_pnl_pct error: %s", e)
        return None

    async def close_side_positions(self, symbol: str, position_type: int) -> bool:
        """Cierra todas las posiciones de un lado (1=LONG, 2=SHORT)."""
        try:
            price = await self.get_price(symbol)
            data = await self._get("/api/v1/private/position/open_positions", {"symbol": symbol})
            if not data or not isinstance(data, list):
                return False
            for pos in data:
                if int(pos.get("positionType", 0)) != position_type:
                    continue
                vol = float(pos.get("holdVol", 0))
                if vol == 0:
                    continue
                close_side = 2 if position_type == 1 else 4  # 2=CloseLong, 4=CloseShort
                await self.place_market_order(symbol, close_side, vol)
            return True
        except Exception as e:
            log.error("close_side_positions error: %s", e)
        return False

    # ── Órdenes ────────────────────────────────────────────────────────────────

    async def set_leverage(self, symbol: str, leverage: int) -> bool:
        """Configura apalancamiento en MEXC — se ignora si no hay posición."""
        result = await self._post("/api/v1/private/position/change_leverage", {
            "symbol":   symbol,
            "leverage": leverage,
        })
        # Error 2009 (no position) es normal al arrancar — no es un error real
        return True

    async def get_open_orders(self, symbol: str) -> list:
        data = await self._get("/api/v1/private/order/list/open_orders/" + symbol)
        if data and isinstance(data, dict):
            return data.get("resultList", [])
        return []

    async def cancel_all_orders(self, symbol: str) -> bool:
        orders = await self.get_open_orders(symbol)
        if not orders:
            return True
        order_ids = [str(o.get("orderId")) for o in orders]
        result = await self._post("/api/v1/private/order/cancel_orders", {
            "symbol":   symbol,
            "orderIds": order_ids,
        })
        log.info("Todas las órdenes canceladas")
        return result is not None

    async def close_all_positions(self, symbol: str) -> bool:
        pos = await self.get_position(symbol)
        if not pos:
            return True
        side   = int(pos.get("positionType", 1))  # 1=LONG, 2=SHORT
        vol    = float(pos.get("holdVol", 0))
        # Para cerrar: LONG → side=3 (close long), SHORT → side=4 (close short)
        close_side = 3 if side == 1 else 4
        result = await self.place_market_order(symbol, close_side, vol)
        log.info("Posiciones cerradas a mercado")
        return result is not None

    async def place_market_order(self, symbol: str, side: int, vol: float,
                                  leverage: int = None) -> Optional[dict]:
        """
        side:
          1 = Abrir LONG
          2 = Abrir SHORT
          3 = Cerrar LONG
          4 = Cerrar SHORT
        vol: cantidad en contratos
        """
        body = {
            "symbol":   symbol,
            "side":     side,
            "type":     5,        # 5 = Market order
            "vol":      vol,
            "openType": 1,        # 1 = aislado
        }
        if leverage:
            body["leverage"] = leverage
        return await self._post("/api/v1/private/order/submit", body)

    async def place_limit_order(self, symbol: str, side: int, vol: float,
                                 price: float, tp_price: float = None) -> Optional[dict]:
        """
        side: 1=OpenLong, 2=OpenShort, 3=CloseLong, 4=CloseShort
        tp_price: precio de take profit adjunto a la orden
        """
        body = {
            "symbol":   symbol,
            "side":     side,
            "type":     1,        # 1 = Limit order
            "vol":      vol,
            "price":    price,
            "openType": 1,        # 1 = aislado
        }
        # Adjuntar TP si se proporciona
        if tp_price is not None:
            body["takeProfitPrice"] = tp_price
            body["takeProfitType"]  = 1
        return await self._post("/api/v1/private/order/submit", body)

    def calculate_vol(self, symbol_info: dict, price: float,
                      margin_usdt: float, leverage: int) -> float:
        """Calcula el volumen en contratos para MEXC."""
        contract_size = float(symbol_info.get("contractSize", 1))
        vol_scale     = int(symbol_info.get("volScale", 0))
        min_vol       = float(symbol_info.get("minVol", 1))
        # vol = (margin * leverage) / (price * contractSize)
        vol = (margin_usdt * leverage) / (price * contract_size)
        vol = round(vol, vol_scale)
        if vol_scale == 0:
            vol = int(vol)
        vol = max(vol, min_vol)
        return vol

    async def get_last_closed_trade(self, symbol: str) -> Optional[dict]:
        """Obtiene el último trade cerrado para detectar PnL."""
        data = await self._get("/api/v1/private/position/list/history_positions", {
            "symbol":   symbol,
            "pageNum":  "1",
            "pageSize": "5",
        })
        if data and isinstance(data, list):
            # MEXC devuelve lista directamente, filtrar las cerradas (state=3)
            closed = [p for p in data if p.get("state") == 3]
            if not closed:
                return None
            # Ordenar por updateTime descendente — la más reciente primero
            closed.sort(key=lambda x: x.get("updateTime", 0), reverse=True)
            latest   = closed[0]
            pos_id   = str(latest.get("positionId"))
            if pos_id == self._last_closed_order_id:
                return None
            close_time = float(latest.get("updateTime", 0)) / 1000
            if close_time < self._ignore_trades_before:
                self._last_closed_order_id = pos_id
                return None
            self._last_closed_order_id = pos_id
            realized  = float(latest.get("realised", 0))
            fee       = abs(float(latest.get("totalFee", 0)))
            side_num  = int(latest.get("positionType", 1))
            side_str  = "LONG" if side_num == 1 else "SHORT"
            avg_price = float(latest.get("closeAvgPrice", 0))
            # Usar im si está disponible, sino ORDER_SIZE_USDT
            im = float(latest.get("im", 0))
            if im == 0:
                im = config.ORDER_SIZE_USDT
            pnl_pct = (realized / im * 100) if im != 0 else 0.0
            return {
                "pnl_usdt": realized,
                "pnl_pct":  pnl_pct,
                "price":    avg_price,
                "fee":      fee,
                "side":     side_str,
            }
        return None
