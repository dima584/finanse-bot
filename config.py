"""
=============================================================
  CONFIG.PY — ВСЕ НАСТРОЙКИ БОТА В ОДНОМ МЕСТЕ
=============================================================
Здесь хранятся все секретные ключи, параметры и списки активов.
"""

import os
from dotenv import load_dotenv

# Загружаем переменные из файла .env
load_dotenv()

# ── Telegram ──
BOT_TOKEN  = os.getenv("BOT_TOKEN")
ADMIN_IDS  = list(map(int, os.getenv("ADMIN_IDS", "0").split(",")))
SIGNAL_CHANNEL_ID = os.getenv("SIGNAL_CHANNEL_ID", "")

# ── Биржевые API (только для чтения, ключи опциональны) ──
BINANCE_API_KEY    = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")

# ── ЗОЛОТОЙ ПОРТФЕЛЬ (Разделен по результатам бэктестов) ──

# 1. СКАЛЬПИНГ (15m) - Высокая ликвидность + Новые волатильные монеты
SCALP_PAIRS = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", 
    "XRP/USDT", "ADA/USDT", "DOT/USDT", "NEAR/USDT",
    # 🔥 Добавленные турбо-пары (Мемы, L2, новые проекты)
    "PEPE/USDT", "WIF/USDT", "BONK/USDT", "JTO/USDT", 
    "STRK/USDT", "MANTA/USDT", "ALT/USDT", "PIXEL/USDT", "PORTAL/USDT"
]

# 2. СВИНГ-ТРЕЙДИНГ (1h, 4h) - Редкие сигналы, гигантский ROI (85-95% WR)
SWING_PAIRS = [
    "UNI/USDT", "APT/USDT", "SUI/USDT", "DOGE/USDT", 
    "INJ/USDT", "OP/USDT", "LINK/USDT", "ATOM/USDT", 
    "TIA/USDT", "LTC/USDT", "FIL/USDT", "ARB/USDT", 
    "SEI/USDT", "BCH/USDT", "FET/USDT", "AVAX/USDT"
]

# Общий список для ручного обзора рынка (/market)
CRYPTO_PAIRS = SCALP_PAIRS + SWING_PAIRS

FOREX_PAIRS = [
    "EUR/USD", "GBP/USD", "USD/JPY",
    "AUD/USD", "USD/CHF", "NZD/USD"
]

# ── Таймфреймы ──
TIMEFRAMES = ["15m", "1h", "4h", "1d"]

# ── Риск-менеджмент ──
RISK_REWARD_MIN  = 1.2   # Оптимизировано для скальпинга
MAX_RISK_PERCENT = 2.0   # Максимальный риск на сделку (% от депозита)
DEFAULT_LEVERAGE = 1     # Плечо по умолчанию (пользователь выставляет сам на бирже)

# ── Подписки ──
SUBSCRIPTION_PLANS = {
    "basic": {
        "name":            "Basic",
        "price":           19,
        "currency":        "USD",
        "signals_per_day": 5,
        "markets":         ["crypto"],
        "duration_days":   30,
    },
    "pro": {
        "name":            "Pro",
        "price":           49,
        "currency":        "USD",
        "signals_per_day": 20,
        "markets":         ["crypto", "forex"],
        "duration_days":   30,
    },
    "vip": {
        "name":            "VIP",
        "price":           99,
        "currency":        "USD",
        "signals_per_day": 9999,
        "markets":         ["crypto", "forex", "futures"],
        "duration_days":   30,
    },
}

# ── Параметры индикаторов ──
RSI_PERIOD     = 14
RSI_OVERBOUGHT = 70
RSI_OVERSOLD   = 30
MACD_FAST      = 12
MACD_SLOW      = 26
MACD_SIGNAL    = 9
BB_PERIOD      = 20
BB_STD         = 2
EMA_PERIODS    = [9, 21, 50, 200]
ATR_PERIOD     = 14

# ── Score порог (Квантовое ядро) ──
MIN_SIGNAL_SCORE = 5

# ── База данных ──
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///trading_bot.db")