"""
Estado interno del Bot MFT300 V.5 — MEXC
Cada par tiene su propio estado independiente.
"""

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PairState:
    """Estado independiente para cada par."""
    symbol: str

    # Grid
    grid_center: Optional[float] = None

    # Pausas
    paused_scheduled:     bool  = False
    scheduled_was_active: bool  = False
    last_buy_lvl:         int   = 5
    last_sell_lvl:        int   = 5
    paused_volatility0:   bool  = False
    volatility0_resume_at: float = 0.0
    paused_volatility:    bool  = False
    paused_volatility2:   bool  = False
    paused_loss:          bool  = False
    paused_consecutive:   bool  = False
    paused_trend:         bool  = False
    trend_resume_at:      float = 0.0
    consecutive_resume_at: float = 0.0
    consecutive_losses:   int   = 0
    paused_daily_loss:    bool  = False

    # Timestamps
    volatility_resume_at:  float = 0.0
    volatility2_resume_at: float = 0.0
    loss_resume_at:        float = 0.0
    last_cleanup_at:       float = field(default_factory=time.time)

    # Pérdida diaria
    daily_loss:      float = 0.0
    daily_loss_date: str   = ""

    # Historial de trades
    trade_history: list = field(default_factory=list)

    # Precio actual
    current_price: float = 0.0

    # MEXC específico
    last_trend:        str   = "NONE"   # Última tendencia detectada (evita bucle de avisos)
    tp_cooldown_until: float = 0.0      # Cooldown del TP global

    def reset(self):
        """Limpia el estado del grid (mantiene estadísticas)."""
        self.grid_center = None

    def is_any_pause_active(self) -> bool:
        return (self.paused_scheduled or self.paused_volatility or
                self.paused_volatility2 or self.paused_volatility0 or self.paused_loss or
                self.paused_daily_loss)


@dataclass
class BotState:
    """Estado global del bot."""
    session_start: float = field(default_factory=time.time)
