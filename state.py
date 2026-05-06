import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PairState:
    symbol: str

    # Grid
    grid_center:    Optional[float] = None
    last_buy_lvl:   int   = 5
    last_sell_lvl:  int   = 5

    # Pausas
    paused_scheduled:    bool  = False
    scheduled_was_active: bool = False
    paused_volatility0:  bool  = False
    volatility0_resume_at: float = 0.0
    paused_volatility:   bool  = False
    volatility_resume_at: float = 0.0
    paused_volatility2:  bool  = False
    volatility2_resume_at: float = 0.0
    paused_trend:        bool  = False
    trend_resume_at:     float = 0.0
    paused_loss:         bool  = False
    loss_resume_at:      float = 0.0
    paused_consecutive:  bool  = False
    consecutive_resume_at: float = 0.0
    paused_daily_loss:   bool  = False

    # Contadores
    consecutive_losses: int   = 0
    daily_loss:         float = 0.0
    daily_loss_date:    str   = ""
    last_cleanup_at:    float = field(default_factory=time.time)

    # Historial
    trade_history: list = field(default_factory=list)
    current_price: float = 0.0

    def reset(self):
        self.grid_center = None

    def is_any_pause_active(self) -> bool:
        return (self.paused_scheduled or self.paused_volatility0 or
                self.paused_volatility or self.paused_volatility2 or
                self.paused_trend or self.paused_loss or
                self.paused_consecutive or self.paused_daily_loss)


@dataclass
class BotState:
    session_start: float = field(default_factory=time.time)
