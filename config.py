import os

# ── API MEXC Futuros ───────────────────────────────────────────────────────────
MEXC_API_KEY    = os.getenv("MEXC_API_KEY", "")
MEXC_API_SECRET = os.getenv("MEXC_API_SECRET", "")
BASE_URL        = os.getenv("MEXC_BASE_URL", "https://api.mexc.com")

# ── Telegram ───────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Par (formato MEXC: BTC_USDT) ───────────────────────────────────────────────
SYMBOLS: list = [s.strip() for s in os.getenv("SYMBOL", "FET_USDT").split(",")]

# ── Grid ───────────────────────────────────────────────────────────────────────
GRID_LEVELS:           int   = int(os.getenv("GRID_LEVELS", "5"))
GRID_SPACING_PCT:      float = float(os.getenv("GRID_SPACING_PCT", "0.5"))
TAKE_PROFIT_PCT:       float = float(os.getenv("TAKE_PROFIT_PCT", "0.5"))
RECENTER_THRESHOLD_PCT:float = float(os.getenv("RECENTER_THRESHOLD_PCT", "1.5"))
ORDER_SIZE_USDT:       float = float(os.getenv("ORDER_SIZE_USDT", "25.0"))
LEVERAGE:              int   = int(os.getenv("LEVERAGE", "10"))

# ── Spacing dinámico por ATR ───────────────────────────────────────────────────
DYNAMIC_SPACING: bool  = os.getenv("DYNAMIC_SPACING", "true").lower() == "true"
ATR_PERIOD:      int   = int(os.getenv("ATR_PERIOD", "14"))
ATR_TIMEFRAME:   str   = os.getenv("ATR_TIMEFRAME", "Min15")   # MEXC: Min1, Min5, Min15, Min30, Min60
SPACING_LOW:     float = float(os.getenv("SPACING_LOW", "0.40"))
SPACING_NORMAL:  float = float(os.getenv("SPACING_NORMAL", "0.50"))
SPACING_HIGH:    float = float(os.getenv("SPACING_HIGH", "0.65"))
ATR_LOW_MULT:    float = float(os.getenv("ATR_LOW_MULT", "0.8"))
ATR_HIGH_MULT:   float = float(os.getenv("ATR_HIGH_MULT", "1.4"))

# ── Grid asimétrico ────────────────────────────────────────────────────────────
ASYMMETRIC_GRID: bool  = os.getenv("ASYMMETRIC_GRID", "true").lower() == "true"
ASYM_CANDLES:    int   = int(os.getenv("ASYM_CANDLES", "2"))
ASYM_MIN_PCT:    float = float(os.getenv("ASYM_MIN_PCT", "0.3"))

# ── Filtros de protección ──────────────────────────────────────────────────────
# Volatilidad capa 0 (spike)
VOL0_WINDOW_SEC:    int   = int(os.getenv("VOL0_WINDOW_SEC", "30"))
VOL0_THRESHOLD_PCT: float = float(os.getenv("VOL0_THRESHOLD_PCT", "0.8"))
VOL0_PAUSE_SEC:     int   = int(os.getenv("VOL0_PAUSE_SEC", "600"))

# Volatilidad capa 1 (rápida)
VOL_WINDOW_SEC:    int   = int(os.getenv("VOL_WINDOW_SEC", "600"))
VOL_THRESHOLD_PCT: float = float(os.getenv("VOL_THRESHOLD_PCT", "1.5"))
VOL_PAUSE_SEC:     int   = int(os.getenv("VOL_PAUSE_SEC", "420"))

# Volatilidad capa 2 (sostenida)
VOL2_WINDOW_SEC:    int   = int(os.getenv("VOL2_WINDOW_SEC", "1800"))
VOL2_THRESHOLD_PCT: float = float(os.getenv("VOL2_THRESHOLD_PCT", "1.5"))
VOL2_PAUSE_SEC:     int   = int(os.getenv("VOL2_PAUSE_SEC", "1800"))

# Tendencia por velas consecutivas
TREND_CANDLES:   int   = int(os.getenv("TREND_CANDLES", "4"))
TREND_MIN_PCT:   float = float(os.getenv("TREND_MIN_PCT", "0.4"))
TREND_PAUSE_SEC: int   = int(os.getenv("TREND_PAUSE_SEC", "900"))
TREND_TIMEFRAME: str   = os.getenv("TREND_TIMEFRAME", "Min15")

# Pérdida máxima por trade
MAX_LOSS_PCT:   float = float(os.getenv("MAX_LOSS_PCT", "-40.0"))
LOSS_PAUSE_SEC: int   = int(os.getenv("LOSS_PAUSE_SEC", "2700"))

# Pérdida diaria
DAILY_LOSS_LIMIT: float = float(os.getenv("DAILY_LOSS_LIMIT", "25.0"))

# Stop Loss por posición abierta
POSITION_SL_PCT: float = float(os.getenv("POSITION_SL_PCT", "-40.0"))

# Pérdidas consecutivas
CONSECUTIVE_LOSS_PAUSE_SEC: int = int(os.getenv("CONSECUTIVE_LOSS_PAUSE_SEC", "900"))

# ── Pausa programada ───────────────────────────────────────────────────────────
SCHEDULED_PAUSE_ENABLED: bool = os.getenv("SCHEDULED_PAUSE_ENABLED", "false").lower() == "true"
SCHEDULED_PAUSE_START:   str  = os.getenv("SCHEDULED_PAUSE_START", "01:00")
SCHEDULED_PAUSE_END:     str  = os.getenv("SCHEDULED_PAUSE_END",   "08:00")

# ── Limpieza periódica ─────────────────────────────────────────────────────────
CLEANUP_INTERVAL_SEC: int = int(os.getenv("CLEANUP_INTERVAL_SEC", "1800"))

# ── API Keys caducidad ────────────────────────────────────────────────────────
API_EXPIRY_DATE: str = os.getenv("API_EXPIRY_DATE", "")  # Formato: YYYY-MM-DD

# ── Loop ───────────────────────────────────────────────────────────────────────
LOOP_INTERVAL_SEC: float = float(os.getenv("LOOP_INTERVAL_SEC", "2"))

# ── Zona protegida ─────────────────────────────────────────────────────────────
PROTECTED_ZONE_LOW:  float = float(os.getenv("PROTECTED_ZONE_LOW",  "-4.0"))
PROTECTED_ZONE_HIGH: float = float(os.getenv("PROTECTED_ZONE_HIGH", "1.0"))
