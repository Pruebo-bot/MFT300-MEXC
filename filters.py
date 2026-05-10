"""
Filtros de protección del Bot MFT 300 V.5
- ProtectedZone:    evita recentrado en franja sensible de PnL
- MaxLossFilter:    pausa tras pérdida máxima en cierre
- VolatilityFilter: detecta movimientos bruscos en ventana de tiempo
- ScheduledPause:   pausa por fundamentales USA (13:30-17:00 ES)
"""

import time
import logging
from datetime import datetime, time as dtime
from collections import deque
from config import Config

log = logging.getLogger("filters")


class ProtectedZone:
    """
    Evita que el bot recentre el grid cuando el PnL de la posición
    se encuentra en la franja [PROTECTED_ZONE_LOW, PROTECTED_ZONE_HIGH].
    """

    def __init__(self, cfg: Config):
        self.low = cfg.PROTECTED_ZONE_LOW    # p.ej. -4.0
        self.high = cfg.PROTECTED_ZONE_HIGH  # p.ej.  1.0

    def is_protected(self, pnl_pct: float) -> bool:
        """Devuelve True si el PnL está dentro de la zona protegida."""
        in_zone = self.low <= pnl_pct <= self.high
        if in_zone:
            log.debug(f"ProtectedZone: PnL {pnl_pct:.2f}% en franja [{self.low}, {self.high}]")
        return in_zone


class MaxLossFilter:
    """
    Activa pausa si el PnL del último cierre supera el umbral de pérdida máxima.
    """

    def __init__(self, cfg: Config):
        self.threshold = cfg.MAX_LOSS_PCT  # p.ej. -3.0

    def triggered(self, closed_pnl_pct: float) -> bool:
        """
        closed_pnl_pct: PnL% del último trade cerrado (negativo = pérdida).
        Devuelve True si hay que activar la pausa.
        """
        if closed_pnl_pct <= self.threshold:
            log.warning(
                f"MaxLossFilter: PnL cierre {closed_pnl_pct:.4f}% <= umbral {self.threshold}%"
            )
            return True
        return False


class VolatilityFilter:
    """
    Detecta movimientos bruscos de precio en una ventana de tiempo.
    Soporta dos capas: capa 1 (rápida) y capa 2 (sostenida).
    """

    def __init__(self, window_sec: int, threshold_pct: float, name: str = ""):
        self.window_sec    = window_sec
        self.threshold_pct = threshold_pct
        self.name          = name
        self._prices: deque = deque()
        self._triggered    = False
        self.last_spike_pct = 0.0

    def update(self, price: float):
        now = time.time()
        self._prices.append((now, price))
        while self._prices and (now - self._prices[0][0]) > self.window_sec:
            self._prices.popleft()
        if len(self._prices) >= 2:
            prices = [p for _, p in self._prices]
            max_p  = max(prices)
            min_p  = min(prices)
            if min_p > 0:
                spike_pct = ((max_p - min_p) / min_p) * 100
                if spike_pct >= self.threshold_pct:
                    self.last_spike_pct = spike_pct
                    self._triggered     = True
                    log.warning(
                        f"VolatilityFilter [{self.name}]: spike {spike_pct:.3f}% "
                        f"en {self.window_sec//60}min (umbral {self.threshold_pct}%)"
                    )

    def triggered(self) -> bool:
        return self._triggered

    def reset(self):
        self._triggered     = False
        self.last_spike_pct = 0.0
        self._prices.clear()
        log.debug(f"VolatilityFilter [{self.name}]: reiniciado")


class ScheduledPause:
    """
    Pausa programada de lunes a viernes en franja horaria configurada.
    Por defecto: 13:30 – 17:00 hora de España (zona horaria del servidor).
    """

    def __init__(self, cfg: Config):
        start_h, start_m = map(int, cfg.PAUSE_START.split(":"))
        end_h, end_m = map(int, cfg.PAUSE_END.split(":"))
        self.start = dtime(start_h, start_m)
        self.end = dtime(end_h, end_m)
        self.active_days = cfg.PAUSE_DAYS  # [0..4] = lunes a viernes

    def is_active(self, now: datetime) -> bool:
        """Devuelve True si estamos dentro del horario de pausa programada."""
        if now.weekday() not in self.active_days:
            return False
        current_time = now.time()
        in_window = self.start <= current_time <= self.end
        if in_window:
            log.debug(f"ScheduledPause: dentro de franja {self.start}–{self.end}")
        return in_window


class TrendFilter:
    """
    Detecta tendencias sostenidas evaluando velas consecutivas.
    Si N velas seguidas cierran en la misma dirección con movimiento >= min_pct,
    se considera que hay tendencia y el grid debe pausarse.
    """

    def __init__(self, n_candles: int, min_pct: float):
        self.n_candles = n_candles   # Número de velas consecutivas requeridas
        self.min_pct   = min_pct     # % mínimo de movimiento por vela

    def detect(self, closes: list[float]) -> str:
        """
        Analiza las últimas N velas cerradas.
        Retorna:
          "DOWN" — tendencia bajista detectada
          "UP"   — tendencia alcista detectada
          "NONE" — sin tendencia, mercado en rango
        closes: lista de precios de cierre, el último es la vela más reciente
        """
        if len(closes) < self.n_candles + 1:
            return "NONE"

        # Tomar las últimas N velas
        recent = closes[-(self.n_candles + 1):]

        # Comprobar bajista: cada vela cierra más baja que la anterior
        all_down = all(
            recent[i] < recent[i - 1] and
            abs(recent[i] - recent[i - 1]) / recent[i - 1] * 100 >= self.min_pct
            for i in range(1, len(recent))
        )

        # Comprobar alcista: cada vela cierra más alta que la anterior
        all_up = all(
            recent[i] > recent[i - 1] and
            abs(recent[i] - recent[i - 1]) / recent[i - 1] * 100 >= self.min_pct
            for i in range(1, len(recent))
        )

        if all_down:
            log.warning(
                f"TrendFilter: {self.n_candles} velas bajistas consecutivas "
                f"(>={self.min_pct}% cada una)"
            )
            return "DOWN"
        elif all_up:
            log.warning(
                f"TrendFilter: {self.n_candles} velas alcistas consecutivas "
                f"(>={self.min_pct}% cada una)"
            )
            return "UP"

        return "NONE"


class ATRFilter:
    """
    Calcula el ATR (Average True Range) y determina el nivel de volatilidad.
    Retorna el spacing y TP dinámicos según la volatilidad actual.
    """

    def __init__(self, cfg):
        self.cfg    = cfg
        self.period = cfg.ATR_PERIOD

    def calculate_atr(self, highs: list, lows: list, closes: list) -> float:
        """Calcula el ATR de los últimos N periodos."""
        if len(highs) < self.period + 1:
            return 0.0

        true_ranges = []
        for i in range(1, len(highs)):
            high  = highs[i]
            low   = lows[i]
            prev_close = closes[i - 1]
            tr = max(
                high - low,
                abs(high - prev_close),
                abs(low  - prev_close)
            )
            true_ranges.append(tr)

        # ATR = media de los últimos N true ranges
        recent_trs = true_ranges[-self.period:]
        return sum(recent_trs) / len(recent_trs) if recent_trs else 0.0

    def get_spacing_and_tp(self, highs: list, lows: list, closes: list) -> tuple:
        """
        Determina spacing y TP dinámicos según el ATR actual vs promedio.
        Retorna (spacing_pct, tp_pct)
        """
        if not self.cfg.DYNAMIC_SPACING or len(closes) < self.period * 2 + 1:
            return self.cfg.SPACING_NORMAL, self.cfg.SPACING_NORMAL

        # ATR actual (últimas N velas)
        atr_current = self.calculate_atr(highs[-self.period-1:], lows[-self.period-1:], closes[-self.period-1:])

        # ATR promedio (últimas 2N velas como referencia)
        atr_avg = self.calculate_atr(highs[-(self.period*2)-1:], lows[-(self.period*2)-1:], closes[-(self.period*2)-1:])

        if atr_avg == 0:
            return self.cfg.SPACING_NORMAL, self.cfg.SPACING_NORMAL

        ratio = atr_current / atr_avg

        if ratio < self.cfg.ATR_LOW_MULT:
            spacing = self.cfg.SPACING_LOW
            level   = "baja"
        elif ratio > self.cfg.ATR_HIGH_MULT:
            spacing = self.cfg.SPACING_HIGH
            level   = "alta"
        else:
            spacing = self.cfg.SPACING_NORMAL
            level   = "normal"

        log.debug(f"ATR ratio: {ratio:.2f} | Volatilidad: {level} | Spacing: {spacing}%")
        return spacing, spacing  # TP siempre igual al spacing


class AsymmetricGrid:
    """
    Detecta tendencia a corto plazo y calcula niveles asimétricos.
    
    Tendencia alcista → más niveles de compra abajo (captura rebote)
    Tendencia bajista → más niveles de venta arriba (captura rebote)
    Sin tendencia    → grid simétrico
    """

    def __init__(self, cfg):
        self.cfg        = cfg
        self.n_candles  = cfg.ASYM_CANDLES
        self.min_pct    = cfg.ASYM_MIN_PCT
        self.base_levels = cfg.GRID_LEVELS

    def get_levels(self, closes: list[float]) -> tuple[int, int]:
        """
        Retorna (buy_levels, sell_levels) según la tendencia detectada.
        """
        if not self.cfg.ASYMMETRIC_GRID or len(closes) < self.n_candles + 1:
            return self.base_levels, self.base_levels

        recent = closes[-(self.n_candles + 1):]

        # Detectar tendencia alcista
        all_up = all(
            recent[i] > recent[i-1] and
            (recent[i] - recent[i-1]) / recent[i-1] * 100 >= self.min_pct
            for i in range(1, len(recent))
        )

        # Detectar tendencia bajista
        all_down = all(
            recent[i] < recent[i-1] and
            (recent[i-1] - recent[i]) / recent[i-1] * 100 >= self.min_pct
            for i in range(1, len(recent))
        )

        # Calcular magnitud para decidir si es suave o fuerte
        total_move = abs(recent[-1] - recent[0]) / recent[0] * 100
        strong     = total_move >= self.min_pct * self.n_candles * 1.5

        b = self.base_levels  # 5

        if all_up:
            if strong:
                buy_levels, sell_levels = b + 2, b - 2
            else:
                buy_levels, sell_levels = b + 1, b - 1
        elif all_down:
            if strong:
                buy_levels, sell_levels = b - 2, b + 2
            else:
                buy_levels, sell_levels = b - 1, b + 1
        else:
            buy_levels, sell_levels = b, b

        return max(buy_levels, 1), max(sell_levels, 1)
