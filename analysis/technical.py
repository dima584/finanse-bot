"""
analysis/technical.py — АНАЛИТИЧЕСКОЕ ЯДРО v3
Источники улучшений:
  - Базовые индикаторы (RSI, MACD, EMA, BB, ATR, Stoch) — наш бэктест
  - DXY макро-фильтр + стакан + imbalance — Gemini
  - Time-of-Day фильтр + Volatility фильтр — Gemini (доработаны)
  - MTFA обязательный — наш бэктест
  - Исправлен баг confidence < 60 (ломал логику при score=5)
  - Порог score >= 5 (бэктест: 77-95% WR)
"""

import logging
import pandas as pd
import numpy as np
import aiohttp
import asyncio
from datetime import datetime, timezone
from typing import Optional
from database import save_ml_snapshot
  # Убедись, что путь импорта правильный для твоего проекта
logger = logging.getLogger(__name__)

try:
    import yfinance as yf
    HAS_YFINANCE = True
except ImportError:
    HAS_YFINANCE = False

from config import (
    RSI_PERIOD, RSI_OVERBOUGHT, RSI_OVERSOLD,
    MACD_FAST, MACD_SLOW, MACD_SIGNAL,
    BB_PERIOD, BB_STD, ATR_PERIOD
)

# ── Ключевые параметры (не менять без нового бэктеста) ──
MIN_SCORE       = 3     # Минимальный score для сигнала
MIN_CONFIDENCE  = 55  # Минимальная уверенность % (не 60 — это был баг)
MIN_RR          = 1.2   # Минимальный Risk:Reward

# ── Time-of-Day фильтр ──
# Азиатская сессия даёт больше ложных сигналов на крипте
ASIAN_SESSION_START = 22  # UTC час начала "тихой" зоны
ASIAN_SESSION_END   = 7   # UTC час конца "тихой" зоны
ASIAN_MIN_SCORE     = 6   # Повышенный порог ночью (вместо полного отключения)

# ── Volatility фильтр ──
# ATR слишком маленький = боковик, комиссии съедят прибыль
# ATR слишком большой = рынок "бешеный", стопы сносит
ATR_MIN_RATIO = 0.0001   # ATR / цена < 0.03% — рынок мёртвый
ATR_MAX_RATIO = 0.08     # ATR / цена > 8% — слишком волатильно


import asyncio
import ccxt.async_support as ccxt
import numpy as np
import time

class NezolaLiquidityScoreV2:
    """
    Институциональная версия калькулятора микроструктуры рынка (NLS V2).
    Использует ATR для стакана и временные окна для CVD.
    """
    def __init__(self, symbol: str, limit_depth: int = 500, trades_limit: int = 1000):
        self.symbol = symbol
        self.limit_depth = limit_depth
        self.trades_limit = trades_limit
        self.exchange = ccxt.binance({'options': {'defaultType': 'future'}})

    async def get_market_data(self):
        """Параллельный сбор свечей (для ATR), стакана и сделок"""
        tasks = [
            self.exchange.fetch_ohlcv(self.symbol, timeframe='1m', limit=15),
            self.exchange.fetch_order_book(self.symbol, limit=self.limit_depth),
            self.exchange.fetch_trades(self.symbol, limit=self.trades_limit)
        ]
        return await asyncio.gather(*tasks)

    def calc_atr(self, ohlcv) -> float:
        """Быстрый расчет 14-периодного ATR для определения зоны волатильности"""
        if len(ohlcv) < 2:
            return 0.0
        
        tr_list = []
        for i in range(1, len(ohlcv)):
            high, low, prev_close = ohlcv[i][2], ohlcv[i][3], ohlcv[i-1][4]
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            tr_list.append(tr)
        return np.mean(tr_list)

    def calc_smart_skewness(self, orderbook, current_price, atr) -> float:
        """Асимметрия стакана строго внутри зоны текущей волатильности"""
        bids = np.array(orderbook['bids'])
        asks = np.array(orderbook['asks'])
        
        if len(bids) == 0 or len(asks) == 0 or atr == 0:
            return 0.0

        # Ограничиваем обзор стакана до 2-х ATR (только то, что реально влияет на цену)
        action_zone = atr * 2.0
        valid_bids = bids[bids[:, 0] >= (current_price - action_zone)]
        valid_asks = asks[asks[:, 0] <= (current_price + action_zone)]

        if len(valid_bids) == 0 or len(valid_asks) == 0:
            return 0.0

        # Вес ордера: чем ближе к текущей цене, тем выше значимость (экспоненциальное затухание)
        bid_distances = np.abs(valid_bids[:, 0] - current_price) / current_price
        ask_distances = np.abs(valid_asks[:, 0] - current_price) / current_price
        
        # Предотвращение деления на ноль
        bid_distances = np.where(bid_distances == 0, 0.0001, bid_distances)
        ask_distances = np.where(ask_distances == 0, 0.0001, ask_distances)

        bid_weights = np.exp(-bid_distances * 100) 
        ask_weights = np.exp(-ask_distances * 100)

        weighted_bid_vol = np.sum(valid_bids[:, 1] * bid_weights)
        weighted_ask_vol = np.sum(valid_asks[:, 1] * ask_weights)

        total_vol = weighted_bid_vol + weighted_ask_vol
        if total_vol == 0:
            return 0.0

        return (weighted_bid_vol - weighted_ask_vol) / total_vol

    def calc_time_cvd(self, trades) -> tuple[float, float]:
        """CVD по временным окнам и детектор китовых ударов"""
        if not trades:
            return 0.0, 0.0

        now_ms = trades[-1]['timestamp']
        window_ms = 30 * 1000  # 30 секунд

        recent_trades = [t for t in trades if t['timestamp'] >= now_ms - window_ms]
        past_trades = [t for t in trades if now_ms - (window_ms * 2) <= t['timestamp'] < now_ms - window_ms]

        def get_metrics(trade_batch):
            if not trade_batch: return 0.0, 0.0
            buy_vol = sum(t['amount'] for t in trade_batch if t['side'] == 'buy')
            sell_vol = sum(t['amount'] for t in trade_batch if t['side'] == 'sell')
            delta = buy_vol - sell_vol
            
            # Поиск китовых ударов (95 перцентиль)
            amounts = [t['amount'] for t in trade_batch]
            whale_threshold = np.percentile(amounts, 95) if amounts else 0
            whale_buys = sum(t['amount'] for t in trade_batch if t['side'] == 'buy' and t['amount'] >= whale_threshold)
            whale_sells = sum(t['amount'] for t in trade_batch if t['side'] == 'sell' and t['amount'] >= whale_threshold)
            whale_delta = whale_buys - whale_sells
            
            return delta, whale_delta

        recent_delta, recent_whale = get_metrics(recent_trades)
        past_delta, _ = get_metrics(past_trades)

        acceleration = recent_delta - past_delta
        
        # Нормализация
        max_vol = max(sum(t['amount'] for t in trades), 1)
        norm_accel = np.tanh(acceleration / max_vol * 10) # Коэффициент чувствительности
        norm_whale = np.tanh(recent_whale / max_vol * 10)

        return norm_accel, norm_whale

    async def calculate(self) -> dict:
        try:
            ohlcv, orderbook, trades = await self.get_market_data()
            
            current_price = trades[-1]['price'] if trades else ohlcv[-1][4]
            atr = self.calc_atr(ohlcv)
            
            skewness = self.calc_smart_skewness(orderbook, current_price, atr)
            cvd_accel, whale_impact = self.calc_time_cvd(trades)
            
            # Математика NLS V2: Стакан (30%), Ускорение толпы (40%), Удары китов (30%)
            nls_score = (skewness * 30) + (cvd_accel * 40) + (whale_impact * 30)
            
            return {
                "nls_score": round(nls_score, 2),
                "skewness": round(skewness * 100, 2),
                "cvd_accel": round(cvd_accel * 100, 2),
                "whale_impact": round(whale_impact * 100, 2),
                "atr_zone": round(atr, 4)
            }
        except Exception as e:
            return {"nls_score": 0.0, "error": str(e)}
        finally:
            await self.exchange.close()

async def run_test():
    symbols_to_test = ["BTC/USDT", "SOL/USDT", "WIF/USDT"]
    
    print("🔬 Запуск квантового анализатора NLS V2 (ATR + Time CVD)...\n")
    for sym in symbols_to_test:
        analyzer = NezolaLiquidityScoreV2(sym)
        result = await analyzer.calculate()
        
        if "error" in result:
            print(f"[{sym}] Ошибка: {result['error']}")
            continue
            
        score = result['nls_score']
        trend = "🟢 LONG" if score > 20 else "🔴 SHORT" if score < -20 else "⚪ NEUTRAL"
        
        print(f"[{sym}] | NLS V2 Индекс: {score} ({trend})")
        print(f" ├ Давление стакана (в зоне волатильности): {result['skewness']}%")
        print(f" ├ Ускорение рыночных ордеров: {result['cvd_accel']}%")
        print(f" ├ Удары крупных игроков (китов): {result['whale_impact']}%")
        print(f" └─────────────────────────────────────────\n")

if __name__ == "__main__":
    asyncio.run(run_test())


# ════════════════════════════════════════════
#  ПОЛУЧЕНИЕ ДАННЫХ
# ════════════════════════════════════════════

async def fetch_ohlcv(symbol: str, timeframe: str = "1h", limit: int = 200) -> Optional[pd.DataFrame]:
    clean = symbol.replace("/", "")
    tf_map = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d"}
    url    = "https://api.binance.com/api/v3/klines"
    params = {"symbol": clean, "interval": tf_map.get(timeframe, "1h"), "limit": limit}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status != 200:
                    return None
                data = await r.json()
        if not data or len(data) < 50:
            return None
        df = pd.DataFrame(data, columns=[
            "timestamp","open","high","low","close","volume",
            "close_time","quote_volume","trades","taker_buy_base","taker_buy_quote","ignore"
        ])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df.set_index("timestamp", inplace=True)
        for col in ["open","high","low","close","volume","taker_buy_base"]:
            df[col] = pd.to_numeric(df[col])
        return df
    except Exception as e:
        print(f"fetch_ohlcv {symbol} {timeframe}: {e}")
        return None


# ════════════════════════════════════════════
#  КВАНТОВЫЕ ФИЛЬТРЫ
# ════════════════════════════════════════════

async def check_btc_market_bias() -> str:
    """
    Определяет глобальное состояние рынка по BTC/USDT.
    Возвращает: LONG_ONLY, SHORT_ONLY, или NEUTRAL
    """
    df_btc = await fetch_ohlcv("BTC/USDT", "1h", limit=250)
    if df_btc is None:
        return "NEUTRAL"
        
    close = df_btc["close"]
    ema50 = calculate_ema(close, 50)
    ema200 = calculate_ema(close, 200)
    
    p = float(close.iloc[-1])
    e50 = float(ema50.iloc[-1])
    e200 = float(ema200.iloc[-1])
    
    # Жесткий тренд: Цена и Быстрая скользящая выше Медленной
    if p > e200 and e50 > e200:
        return "LONG_ONLY"
    elif p < e200 and e50 < e200:
        return "SHORT_ONLY"
        
    return "NEUTRAL"

async def check_dxy_trend() -> int:
    """
    DXY (Индекс Доллара) — макро-фильтр.
    Крипта обратно коррелирует с долларом:
      DXY растёт → крипта падает → блокируем LONG
      DXY падает → крипта растёт → блокируем SHORT
    Возвращает: 1 (доллар силён), -1 (доллар слаб), 0 (флэт/нет данных)
    """
    if not HAS_YFINANCE:
        return 0

    def _fetch():
        try:
            # Заменили старый тикер на рабочий фьючерс
            ticker = yf.Ticker("DX=F")
            hist = ticker.history(period="5d", interval="1d")
            if hist.empty or len(hist) < 2:
                return 0
            
            today = hist["Close"].iloc[-1]
            yest  = hist["Close"].iloc[-2]
            change = (today - yest) / yest
            
            if change > 0.002:    # +0.2% = сильный рост доллара
                return 1
            elif change < -0.002: # -0.2% = сильное падение доллара
                return -1
            return 0
        except Exception as e:
            logger.debug(f"Ошибка получения DXY: {e}")
            return 0

    try:
        # Жесткий таймаут 3 секунды. Не позволим Yahoo Finance вешать бота!
        return await asyncio.wait_for(asyncio.to_thread(_fetch), timeout=3.0)
    except asyncio.TimeoutError:
        logger.warning("Таймаут DXY (Yahoo Finance тупит). Возвращаем нейтральный тренд.")
        return 0
    except Exception:
        return 0

async def fetch_funding_rate(symbol: str) -> float:
    """
    Получает текущую ставку финансирования (Funding Rate) с Binance Futures.
    Возвращает значение в процентах (например, 0.01%).
    """
    clean = symbol.replace("/", "")
    url = f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={clean}"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=5)) as r:
                if r.status != 200:
                    return 0.0
                data = await r.json()
                # Превращаем десятичную дробь в проценты (0.0001 -> 0.01%)
                return float(data.get("lastFundingRate", 0.0)) * 100
    except Exception:
        return 0.0

async def analyze_imbalance(symbol: str) -> int:
    """
    Псевдо-Footprint: анализ дельты объёма за последние 15 минут.
    Taker buy volume = агрессивные покупки по рынку.
    Если покупок значительно больше чем продаж — бычий дисбаланс.
    Возвращает: 2 (бычий), -2 (медвежий), 0 (нейтрально)
    """
    df = await fetch_ohlcv(symbol, "1m", limit=15)
    if df is None:
        return 0
    buy_vol   = df["taker_buy_base"].sum()
    total_vol = df["volume"].sum()
    if total_vol <= 0:
        return 0
    sell_vol  = total_vol - buy_vol
    delta_pct = (buy_vol - sell_vol) / total_vol * 100

    if delta_pct > 15:    # Перевес покупок > 15%
        return 2
    elif delta_pct < -15: # Перевес продаж > 15%
        return -2
    return 0


async def find_liquidity_walls(symbol: str, price: float, atr: float) -> dict:
    """
    Анализ стакана (Level 2, 100 уровней).
    Ищет крупнейшую стену покупателей (поддержка) и продавцов (сопротивление)
    в радиусе 2×ATR от текущей цены.
    """
    clean = symbol.replace("/", "")
    url   = f"https://api.binance.com/api/v3/depth?symbol={clean}&limit=100"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=5)) as r:
                if r.status != 200:
                    return {}
                data = await r.json()

        support    = None
        resistance = None

        # Стена поддержки — самый большой ордер среди покупателей
        bids = sorted(data.get("bids", []), key=lambda x: float(x[1]), reverse=True)
        for bid in bids[:10]:  # Смотрим топ-10
            bp = float(bid[0])
            if 0 < price - bp <= atr * 2:
                support = bp
                break

        # Стена сопротивления — самый большой ордер среди продавцов
        asks = sorted(data.get("asks", []), key=lambda x: float(x[1]), reverse=True)
        for ask in asks[:10]:
            ap = float(ask[0])
            if 0 < ap - price <= atr * 2:
                resistance = ap
                break

        return {"support": support, "resistance": resistance}
    except Exception:
        return {}


def check_time_of_day() -> tuple:
    """
    Time-of-Day фильтр.
    Возвращает (is_asian_session, min_score_required).
    Ночью (22:00-07:00 UTC) повышаем порог — больше ложных сигналов.
    """
    hour = datetime.now(timezone.utc).hour
    is_asian = (hour >= ASIAN_SESSION_START) or (hour < ASIAN_SESSION_END)
    required_score = ASIAN_MIN_SCORE if is_asian else MIN_SCORE
    return is_asian, required_score


def check_volatility(atr: float, price: float) -> tuple:
    """
    Volatility фильтр.
    Возвращает (is_valid, reason).
    Слишком низкий ATR = боковик (комиссии съедят).
    Слишком высокий ATR = хаос (стопы сносит).
    """
    if price <= 0:
        return False, "invalid price"
    ratio = atr / price
    if ratio < ATR_MIN_RATIO:
        return False, f"ATR слишком мал ({ratio:.4%}) — боковик"
    if ratio > ATR_MAX_RATIO:
        return False, f"ATR слишком велик ({ratio:.4%}) — хаос"
    return True, ""


# ════════════════════════════════════════════
#  ИНДИКАТОРЫ
# ════════════════════════════════════════════

def calculate_price_action(open_p: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series) -> pd.DataFrame:
    """Вычисляет анатомию свечи. Исправлено деление на ноль и обработка NaN."""
    body = (close - open_p).abs()
    upper_wick = high - pd.concat([open_p, close], axis=1).max(axis=1)
    lower_wick = pd.concat([open_p, close], axis=1).min(axis=1) - low
    
    # Защита от нулевого размера свечи (доджи)
    range_size = (high - low).replace(0, 1e-10)
    
    df_pa = pd.DataFrame(index=close.index)
    df_pa['body_ratio'] = (body / range_size).fillna(0)
    df_pa['upper_ratio'] = (upper_wick / range_size).fillna(0)
    df_pa['lower_ratio'] = (lower_wick / range_size).fillna(0)
    return df_pa

def calculate_vwap(df: pd.DataFrame) -> pd.Series:
    """Вычисляет VWAP (Volume Weighted Average Price)"""
    typical_price = (df['high'] + df['low'] + df['close']) / 3
    # Для крипты (24/7) можно считать кумулятивный VWAP за окно загруженных данных
    cum_vol = df['volume'].cumsum()
    cum_vol_price = (typical_price * df['volume']).cumsum()
    return cum_vol_price / cum_vol

def calculate_rsi(close: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    delta    = close.diff()
    gain     = delta.where(delta > 0, 0.0)
    loss     = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs       = avg_gain / avg_loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))

def calculate_macd(close: pd.Series) -> tuple:
    fast = close.ewm(span=MACD_FAST, adjust=False).mean()
    slow = close.ewm(span=MACD_SLOW, adjust=False).mean()
    ml   = fast - slow
    sl   = ml.ewm(span=MACD_SIGNAL, adjust=False).mean()
    return ml, sl, ml - sl

def calculate_bollinger_bands(close: pd.Series) -> tuple:
    mid = close.rolling(BB_PERIOD).mean()
    std = close.rolling(BB_PERIOD).std()
    return mid + BB_STD * std, mid, mid - BB_STD * std

def calculate_ema(close: pd.Series, period: int) -> pd.Series:
    return close.ewm(span=period, adjust=False).mean()

def calculate_atr(high, low, close, period: int = ATR_PERIOD) -> pd.Series:
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs()
    ], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()

def calculate_stochastic(high, low, close, k=14, d=3) -> tuple:
    lo = low.rolling(k).min()
    hi = high.rolling(k).max()
    k_line = 100 * (close - lo) / (hi - lo + 1e-10)
    return k_line, k_line.rolling(d).mean()

def calculate_adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Вычисляет индикатор силы тренда ADX. Исправлено распространение NaN."""
    up = high.diff()
    down = low.shift(1) - low
    
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)
    
    # ВАЖНО: заполняем первый NaN нулями, чтобы ewm не вернул None на весь массив
    tr = tr.fillna(0)
    plus_dm = pd.Series(plus_dm, index=high.index).fillna(0)
    minus_dm = pd.Series(minus_dm, index=high.index).fillna(0)
    
    atr = tr.ewm(alpha=1/period, adjust=False).mean()
    
    # Добавляем 1e-10 к ATR, чтобы избежать ZeroDivision
    plus_di = 100 * (plus_dm.ewm(alpha=1/period, adjust=False).mean() / (atr + 1e-10))
    minus_di = 100 * (minus_dm.ewm(alpha=1/period, adjust=False).mean() / (atr + 1e-10))
    
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-10)
    adx = dx.ewm(alpha=1/period, adjust=False).mean()
    
    # Финальная страховка от пропусков
    return adx.fillna(0)

def calculate_volume_signal(volume: pd.Series, period: int = 20) -> dict:
    """Оценка всплеска объемов. Добавлен min_periods=1."""
    # min_periods=1 спасает от NaN, если истории меньше, чем period
    avg = volume.rolling(period, min_periods=1).mean()
    cur = float(volume.iloc[-1])
    avg_v = float(avg.iloc[-1])
    
    if np.isnan(avg_v) or avg_v == 0:
        avg_v = 1.0
        
    ratio = cur / avg_v
    return {"ratio": round(ratio, 2), "is_high": ratio > 1.3}


# ════════════════════════════════════════════
#  СКОРИНГ
# ════════════════════════════════════════════

def _score_dataframe(df: pd.DataFrame, symbol: str, timeframe: str, btc_trend: int = 0) -> Optional[dict]:
    """
    Считает score по всем индикаторам.
    Используется и для основного таймфрейма и для MTFA-проверки.
    """
    if len(df) < 55:
        return None

    close, high, low, volume = df["close"], df["high"], df["low"], df["volume"]

    rsi                      = calculate_rsi(close)
    macd_line, sig, hist     = calculate_macd(close)
    bb_upper, bb_mid, bb_low = calculate_bollinger_bands(close)
    ema9   = calculate_ema(close, 9)
    ema21  = calculate_ema(close, 21)
    ema50  = calculate_ema(close, 50)
    ema200 = calculate_ema(close, 200)
    atr    = calculate_atr(high, low, close)
    sk, sd = calculate_stochastic(high, low, close)  # Stochastic — Gemini его убрал, мы возвращаем
    vol    = calculate_volume_signal(volume)
    adx_line = calculate_adx(high, low, close)

    p     = float(close.iloc[-1])
    r     = float(rsi.iloc[-1])
    h     = float(hist.iloc[-1])
    ph    = float(hist.iloc[-2])
    atr_v = float(atr.iloc[-1])
    e9    = float(ema9.iloc[-1])
    e21   = float(ema21.iloc[-1])
    e50   = float(ema50.iloc[-1])
    e200  = float(ema200.iloc[-1])
    bbu   = float(bb_upper.iloc[-1])
    bbl   = float(bb_low.iloc[-1])
    sk_v  = float(sk.iloc[-1])
    psk_v = float(sk.iloc[-2])
    ml    = float(macd_line.iloc[-1])
    sl_v  = float(sig.iloc[-1])
    adx_v = 0.0 if np.isnan(float(adx_line.iloc[-1])) else float(adx_line.iloc[-1])
    padx_v = float(adx_line.iloc[-2])

    # ── НОВЫЙ БЛОК 1: ОПРЕДЕЛЕНИЕ МАКРОТРЕНДА ──
    # Тренд бычий: Быстрая выше медленной, цена выше локального дна (EMA50) + есть сила (ADX > 25)
    is_strong_bull = (e9 > e21) and (p > e50) and (adx_v >= 25)
    
    # Тренд медвежий: Быстрая ниже медленной, цена ниже локального потолка + есть сила
    is_strong_bear = (e9 < e21) and (p < e50) and (adx_v >= 25)
    # ───────────────────────────────────────────

    score   = 0
    reasons = []

    # RSI
    if r < 25:
        if is_strong_bear:
            score -= 2; reasons.append(f"📉 RSI={r:.0f} перепродан, но тренд медвежий (игнорируем откуп)")
        else:
            score += 3; reasons.append(f"RSI={r:.0f} сильно перепродан")
    elif r < 30:
        if not is_strong_bear: score += 2; reasons.append(f"RSI={r:.0f} перепродан")
    elif r < 45:
        if not is_strong_bear: score += 1; reasons.append(f"RSI={r:.0f} слабость")
    elif r > 75:
        if is_strong_bull:
            score += 2; reasons.append(f"🔥 RSI={r:.0f} перекуплен, но тренд бычий (едем дальше)")
        else:
            score -= 3; reasons.append(f"RSI={r:.0f} сильно перекуплен")
    elif r > 70:
        if not is_strong_bull: score -= 2; reasons.append(f"RSI={r:.0f} перекуплен")
    elif r > 55:
        if not is_strong_bull: score -= 1; reasons.append(f"RSI={r:.0f} сила")

    # Stochastic
    if sk_v < 20 and sk_v > psk_v:
        if not is_strong_bear: score += 2; reasons.append(f"Stoch={sk_v:.0f} разворот вверх")
    elif sk_v < 20:
        if not is_strong_bear: score += 1; reasons.append(f"Stoch={sk_v:.0f} перепродан")
    elif sk_v > 80 and sk_v < psk_v:
        if not is_strong_bull: score -= 2; reasons.append(f"Stoch={sk_v:.0f} разворот вниз")
    elif sk_v > 80:
        if is_strong_bull:
            score += 1; reasons.append(f"🔥 Stoch={sk_v:.0f} перекуплен (тренд бычий)")
        else:
            score -= 1; reasons.append(f"Stoch={sk_v:.0f} перекуплен")
    # ──────────────────────────────────────────

    # Дивергенции RSI (Ищем расхождение на последних 30 свечах)
    try:
        recent_price_min = min(low.iloc[-15:-1])
        past_price_min   = min(low.iloc[-30:-15])
        recent_rsi_min   = min(rsi.iloc[-15:-1])
        past_rsi_min     = min(rsi.iloc[-30:-15])

        # Бычья дивергенция (цена падает, RSI растет)
        if recent_price_min < past_price_min and recent_rsi_min > past_rsi_min and r < 40:
            score += 3
            reasons.append("🐂 Бычья дивергенция (Скрытая сила покупателя)")

        recent_price_max = max(high.iloc[-15:-1])
        past_price_max   = max(high.iloc[-30:-15])
        recent_rsi_max   = max(rsi.iloc[-15:-1])
        past_rsi_max     = max(rsi.iloc[-30:-15])

        # Медвежья дивергенция (цена растет, RSI падает)
        if recent_price_max > past_price_max and recent_rsi_max < past_rsi_max and r > 60:
            score -= 3
            reasons.append("🐻 Медвежья дивергенция (Скрытая слабость покупателя)")
    except Exception:
        pass # Защита на случай, если история свечей слишком короткая

    # MACD
    if h > 0 and ph <= 0:    score += 3; reasons.append("MACD бычье пересечение")
    elif h < 0 and ph >= 0:  score -= 3; reasons.append("MACD медвежье пересечение")
    elif h > 0 and h > ph:   score += 1; reasons.append("MACD гистограмма растёт")
    elif h < 0 and h < ph:   score -= 1; reasons.append("MACD гистограмма падает")
    elif h > 0: score += 1
    else:        score -= 1

    # EMA тренд
    if p > e50 and e50 > e200:   score += 2; reasons.append("EMA восходящий тренд")
    elif p < e50 and e50 < e200: score -= 2; reasons.append("EMA нисходящий тренд")
    if e9 > e21 and p > e21:     score += 1; reasons.append("EMA9>EMA21")
    elif e9 < e21 and p < e21:   score -= 1; reasons.append("EMA9<EMA21")

    # Bollinger
    if p < bbl:          score += 2; reasons.append("BB цена ниже нижней полосы")
    elif p < bbl * 1.005: score += 1; reasons.append("BB цена у нижней полосы")
    elif p > bbu:         score -= 2; reasons.append("BB цена выше верхней полосы")
    elif p > bbu * 0.995: score -= 1; reasons.append("BB цена у верхней полосы")

    # Объём
    if vol["is_high"]:
        if score > 0:   score += 1; reasons.append(f"Объём x{vol['ratio']:.1f} подтверждает")
        elif score < 0: score -= 1; reasons.append(f"Объём x{vol['ratio']:.1f} подтверждает")

    # ── ОБНОВЛЕННЫЙ БЛОК: ADX (Ловим зарождение тренда) ──
    # Момент пробоя: ADX пересекает 20 снизу вверх
    if adx_v >= 20 and padx_v < 20:
        # Проверяем, совпадает ли это с пересечением MACD
        if score > 0 and h > 0 and ph <= 0:
            score += 4; reasons.append("🚀 Идеальный LONG пробой (ADX пробил 20 + MACD кросс)")
        elif score < 0 and h < 0 and ph >= 0:
            score -= 4; reasons.append("🚀 Идеальный SHORT пробой (ADX пробил 20 + MACD кросс)")
        else:
            if score > 0: score += 2
            elif score < 0: score -= 2
            reasons.append(f"🔥 Начало импульса (ADX пробил 20: {adx_v:.1f})")
            
    # Если мы глубоко во флэте
    elif adx_v < 20:
        if score > 0: score -= 2
        elif score < 0: score += 2
        reasons.append(f"⚠️ ADX={adx_v:.0f} (Флэт: трендовые сигналы опасны)")
        
    # Если тренд уже идет вовсю (стандартное поведение)
    elif adx_v >= 25:
        if score > 0: score += 1
        elif score < 0: score -= 1
        reasons.append(f"ADX={adx_v:.0f} (Сильный направленный тренд)")
    # ─────────────────────────────────────────────────────


    vwap_line = calculate_vwap(df)
    pa_data = calculate_price_action(df['open'], high, low, close)
    
    vwap_v = float(vwap_line.iloc[-1])
    pa_body = float(pa_data['body_ratio'].iloc[-1])
    pa_upper = float(pa_data['upper_ratio'].iloc[-1])
    pa_lower = float(pa_data['lower_ratio'].iloc[-1])
    
    # Дистанция до EMA200 в процентах
    dist_ema200 = (p - e200) / e200 * 100
    
    # Относительный ATR в процентах
    atr_pct = (atr_v / p) * 100

    # ── НОВЫЕ ПРАВИЛА СКОРИНГА ──

    # 1. Мягкий фильтр VWAP
    if p > vwap_v: 
        score += 2; reasons.append("📈 Цена выше VWAP (+2)")
    elif p < vwap_v: 
        score -= 2; reasons.append("📉 Цена ниже VWAP (-2)")

    # 2. Штраф за истощение тренда (Сильный тренд + Экстремальный RSI)
    if adx_v > 45:
        if r > 75:
            score -= 5; reasons.append("⚠️ Истощение бычьего тренда (ADX>45 + RSI>75)")
        elif r < 25:
            score += 5; reasons.append("⚠️ Истощение медвежьего тренда (ADX>45 + RSI<25)")

    # 3. Защита от перетянутой резинки (Distance to EMA200)
    if dist_ema200 > 15:
        score -= 3; reasons.append(f"⚠️ Цена слишком оторвалась от EMA200 (+{dist_ema200:.1f}%)")
    elif dist_ema200 < -15:
        score += 3; reasons.append(f"⚠️ Цена слишком провалилась от EMA200 ({dist_ema200:.1f}%)")

    # 4. Price Action фильтр (Пин-бары)
    if pa_upper > 0.6:  # Верхняя тень занимает больше 60% свечи
        score -= 2; reasons.append("🐻 Давление продавцов (Огромная верхняя тень)")
    if pa_lower > 0.6:  # Нижняя тень занимает больше 60% свечи
        score += 2; reasons.append("🐂 Откуп (Огромная нижняя тень)")

    # ── НОВЫЙ БЛОК: ЗАЩИТА ОТ ВХОДА НА ИСТОЩЕНИИ (Натянутая резинка) ──
    # Вычисляем отрыв цены от быстрой EMA9 в процентах
    dist_ema9_pct = (p - e9) / e9 * 100
    
    # Максимально допустимый отрыв (2.0%). Если цена улетела дальше, входить поздно.
    MAX_EMA9_DIST = 2.0
    
    # Если бот хочет в лонг, но цена УЖЕ высоко над EMA9 -> блокируем
    if score > 0 and dist_ema9_pct > MAX_EMA9_DIST:
        score -= 2
        reasons.append(f"⚠️ Вход на хаях EMA9 (-2, отрыв {dist_ema9_pct:.2f}%)")
    elif score < 0 and dist_ema9_pct < -MAX_EMA9_DIST:
        score += 2
        reasons.append(f"⚠️ Вход на дне EMA9 (+2, отрыв {dist_ema9_pct:.2f}%)")
    # ──────────────────────────────────────────────────────────────────

    # ── НОВЫЙ БЛОК 3: ЖЕСТКАЯ БЛОКИРОВКА КОНТРТРЕНДА ──
    # Если тренд бычий, а баллы ушли в минус (попытка шортить) — обнуляем
    if is_strong_bull and score < 0:
        score += 2
        reasons.append("⚠️ Контртренд: бычий рынок, SHORT ослаблен (+2)")
    elif is_strong_bear and score > 0:
        score -= 2
        reasons.append("⚠️ Контртренд: медвежий рынок, LONG ослаблен (-2)")
    # ──────────────────────────────────────────────────

    direction = "LONG" if score > 0 else ("SHORT" if score < 0 else "NEUTRAL")

    # === НОВЫЕ ПАРАМЕТРЫ ДЛЯ ML ДАТАСЕТА (Мягкое добавление) ===
    # 1. Волатильность (ширина ATR в процентах)
    volatility_pct = round((atr_v / p) * 100, 2)
    
    # 2. Кумулятивная дельта объема (CVD) за последние 10 свечей
    buy_vol = df["taker_buy_base"]
    sell_vol = df["volume"] - buy_vol
    delta = buy_vol - sell_vol
    cvd_15m = float(delta.tail(10).sum())
    
    # 3. Расчет лимитной цены входа (вместо рыночной цены p)
    limit_buffer = atr_v * 0.4
    if score > 0:
        limit_entry = round(p - limit_buffer, 6)
    elif score < 0:
        limit_entry = round(p + limit_buffer, 6)
    else:
        limit_entry = round(p, 6)
    # ==========================================================

    # 🔥 ВСТАВЬ ЭТОТ БЛОК СЮДА 🔥
    # === БОЕВОЙ РЕЖИМ: ЖЕСТКИЕ БЛОКИРОВКИ ===
    if score > 0 and btc_trend == -1:
        score -= 2
        reasons.append("⚠️ BTC против LONG (-2)")
    elif score < 0 and btc_trend == 1:
        score += 2
        reasons.append("⚠️ BTC против SHORT (+2)")
        
    if score > 0 and cvd_15m < 0:
        score -= 1
        reasons.append("⚠️ CVD против LONG (-1)")
    elif score < 0 and cvd_15m > 0:
        score += 1
        reasons.append("⚠️ CVD против SHORT (+1)")
    # ===============================================

    # Эта строка у тебя уже есть, она остается:
    direction = "LONG" if score > 0 else ("SHORT" if score < 0 else "NEUTRAL")

    try:
        # Передаем limit_entry вместо p, а также 3 новые метрики
        save_ml_snapshot(symbol, timeframe, limit_entry, r, ml, adx_v, dist_ema9_pct, btc_trend, volatility_pct, cvd_15m)
    except Exception as e:
        pass # Игнорируем ошибки логирования, чтобы не прервать работу бота

    return {
        "score": score, "direction": direction, "reasons": reasons,
        "price": p, "atr": atr_v, "adx": round(adx_v, 1),
        "indicators": {
            "rsi": round(r, 2), 
            "macd": round(ml, 6), 
            "macd_signal": round(sl_v, 6),
            "ema50": round(e50, 4), 
            "ema200": round(e200, 4),
            "dist_ema200_pct": round(dist_ema200, 2),
            "vwap": round(vwap_v, 4),
            "atr_pct": round(atr_pct, 2),
            "volume_ratio": round(vol["ratio"], 2),
            "pa_body_ratio": round(pa_body, 2),
            "pa_upper_ratio": round(pa_upper, 2),
            "pa_lower_ratio": round(pa_lower, 2)
        }
    }


# ════════════════════════════════════════════
#  ГЛАВНАЯ ФУНКЦИЯ
# ════════════════════════════════════════════

async def analyze_symbol(symbol: str, timeframe: str = "1h") -> Optional[dict]:
    """
    Полный анализ символа с многоуровневой фильтрацией:
    1. Технические индикаторы (score)
    2. Time-of-Day фильтр
    3. Volatility фильтр
    4. MTFA (старший таймфрейм подтверждает)
    5. DXY макро-фильтр
    6. Imbalance (объёмный дисбаланс)
    7. Liquidity walls (стакан → умный TP/SL)
    """

    # ── 1. Загрузка и базовый скоринг ──
    # ── 1. Загрузка и базовый скоринг ──
    df = await fetch_ohlcv(symbol, timeframe, limit=200)
    if df is None:
        return None

    # === РАСЧЕТ ТРЕНДА BTC ДЛЯ ML ===
    btc_trend = 0
    df_btc = await fetch_ohlcv("BTC/USDT", timeframe, limit=60)
    if df_btc is not None:
        btc_close = df_btc["close"]
        btc_ema50 = calculate_ema(btc_close, 50)
        if float(btc_close.iloc[-1]) > float(btc_ema50.iloc[-1]):
            btc_trend = 1
        else:
            btc_trend = -1
    # ================================

    result = _score_dataframe(df, symbol, timeframe, btc_trend)
    if result is None:
        return None

    score     = result["score"]
    direction = result["direction"]
    p         = result["price"]
    atr_v     = result["atr"]
    reasons   = result["reasons"]

    # ── 2. Time-of-Day и Динамический порог ──
    is_asian, required_score = check_time_of_day()
    
    # Считаем текущую волатильность в процентах
    volatility_pct = (atr_v / p) * 100
    
    # Если мы не в "тихой" азиатской сессии, применяем адаптивный порог
    if not is_asian:
        if volatility_pct > 2.0:
            required_score = max(3, required_score - 1)  # Снижаем порог до 3
            reasons.append(f"🌪 Высокая волатильность: порог входа снижен до {required_score}")
        elif volatility_pct < 0.5:
            required_score = min(5, required_score + 1)  # Повышаем порог до 5
            reasons.append(f"💤 Низкая волатильность: порог входа повышен до {required_score}")

    if abs(score) < required_score:
        return None  # Сигнал не прошел по порогу
        
    if is_asian and abs(score) >= required_score:
        reasons.append(f"⏰ Азиатская сессия: повышенный порог пройден ({abs(score)}/{required_score})")

    if direction == "NEUTRAL":
        return None

    # ── 3. Volatility фильтр ──
    vol_ok, vol_reason = check_volatility(atr_v, p)
    if not vol_ok:
        return None  # Боковик или хаос

    # ── 4. MTFA и Глобальный Тренд (Ультимативный фильтр) ──
    # Проверяем старший таймфрейм для локального подтверждения
    mtfa_map  = {"15m": "1h", "1h": "4h", "4h": "1d", "1d": None}
    senior_tf = mtfa_map.get(timeframe)

    if senior_tf:
        df_senior = await fetch_ohlcv(symbol, senior_tf, limit=100)
        if df_senior is not None:
            senior = _score_dataframe(df_senior, symbol, senior_tf)
            if senior and senior["direction"] not in ("NEUTRAL", direction):
                score -= 2
                reasons.append(f"⚠️ MTFA: {senior_tf} против ({senior['direction']}), штраф -2")
            elif senior and senior["direction"] == direction:
                score += 2
                reasons.append(f"✅ MTFA: {senior_tf} подтверждает {direction} (+2)")

    # ── ОБНОВЛЕННЫЙ ЖЕСТКИЙ ФИЛЬТР ГЛОБАЛЬНОГО ТРЕНДА ──
    # Для 15m смотрим на 4h (разрешаем внутридневные сделки по альтам)
    # Для 1h и 4h смотрим на 1d
    if timeframe in ["15m", "1h", "4h"]:
        macro_tf = "4h" if timeframe == "15m" else "1d"
        
        df_macro = await fetch_ohlcv(symbol, macro_tf, limit=250)
        if df_macro is not None and len(df_macro) >= 200:
            close_macro = df_macro["close"]
            ema200_macro = calculate_ema(close_macro, 200)
            current_price_macro = float(close_macro.iloc[-1])
            current_ema200_macro = float(ema200_macro.iloc[-1])
            
            if direction == "LONG" and current_price_macro < current_ema200_macro:
                score -= 2
                reasons.append("⚠️ Макро-тренд против LONG (-2)")
            elif direction == "SHORT" and current_price_macro > current_ema200_macro:
                score -= 2
                reasons.append("⚠️ Макро-тренд против SHORT (-2)")
            else:
                reasons.append(f"🛡 Макро-тренд ({macro_tf}) на нашей стороне")
            
    # ───────────────────────────────────────────────────

    # ── 5. DXY макро-фильтр ──
    dxy = await check_dxy_trend()
    if direction == "LONG" and dxy == 1:
        score -= 1
        reasons.append("⚠️ DXY против LONG (-1)")
    elif direction == "SHORT" and dxy == -1:
        score += 1
        reasons.append("⚠️ DXY против SHORT (+1)")
    if (direction == "LONG" and dxy == -1) or (direction == "SHORT" and dxy == 1):
        reasons.append("✅ DXY подтверждает направление")

    # ── 6. Imbalance (объёмный дисбаланс) ──
    imbalance = await analyze_imbalance(symbol)
    if imbalance > 0 and direction == "LONG":
        score += 1
        reasons.append("📊 Объёмный дисбаланс подтверждает LONG (+1)")
    elif imbalance < 0 and direction == "SHORT":
        score -= 1
        reasons.append("📊 Объёмный дисбаланс подтверждает SHORT (-1)")
    elif imbalance < 0 and direction == "LONG":
        score -= 1
        reasons.append("⚠️ Imbalance против LONG (-1)")
    elif imbalance > 0 and direction == "SHORT":
        score += 1
        reasons.append("⚠️ Imbalance против SHORT (+1)")

    # ── 6.5 Анализ ставки финансирования (Толпа vs Крупный игрок) ──
    funding_pct = await fetch_funding_rate(symbol)
    
    if funding_pct != 0.0:
        if direction == "LONG":
            if funding_pct < -0.01:
                score += 2
                reasons.append(f"🚀 Фандинг {funding_pct:.3f}% (Толпа шортит — ждем шорт-сквиз вверх)")
            elif funding_pct >= 0.05:
                score -= 2
                reasons.append(f"⚠️ Фандинг {funding_pct:.3f}% (Лонги перегреты — высокий риск дампа)")
        elif direction == "SHORT":
            if funding_pct >= 0.05:
                score += 2
                reasons.append(f"🩸 Фандинг {funding_pct:.3f}% (Лонги перегреты — топливо для падения)")
            elif funding_pct < -0.01:
                score -= 2
                reasons.append(f"⚠️ Фандинг {funding_pct:.3f}% (Толпа уже в шортах — шортить опасно)")

    # ── 7. Расчёт уровней с учётом стакана ──
    walls = await find_liquidity_walls(symbol, p, atr_v)

    # ── ДИНАМИЧЕСКИЙ МНОЖИТЕЛЬ ВОЛАТИЛЬНОСТИ (Bollinger Bands Width) ──
    bb_upper, bb_mid, bb_low = calculate_bollinger_bands(df["close"])
    bbu_val = float(bb_upper.iloc[-1])
    bbl_val = float(bb_low.iloc[-1])
    bbm_val = float(bb_mid.iloc[-1])
    
    # Ширина канала в процентах
    bb_width_pct = (bbu_val - bbl_val) / bbm_val * 100
    
    # Нормальная ширина канала для крипты на 15m обычно 2-3%. 
    # Если канал шире, значит рынок "штормит", и нам нужен больший запас хода.
    # Коэффициент будет плавать от 1.0 (спокойно) до 1.8 (сильная волатильность).
    volatility_factor = max(1.0, min(1.8, bb_width_pct / 3.0))

    # Базовые значения
    base_sl  = {"15m": 2.0, "1h": 2.5, "4h": 3.0, "1d": 3.5}.get(timeframe, 2.0)
    base_tp1 = {"15m": 2.0, "1h": 2.5, "4h": 3.0, "1d": 3.5}.get(timeframe, 2.0)
    base_tp2 = {"15m": 3.5, "1h": 4.5, "4h": 5.5, "1d": 6.5}.get(timeframe, 3.5)
    base_tp3 = {"15m": 5.0, "1h": 6.5, "4h": 8.0, "1d": 10.0}.get(timeframe, 5.0)

    # Применяем динамический фактор
    sl_mult  = base_sl * volatility_factor
    tp1_mult = base_tp1 * volatility_factor
    tp2_mult = base_tp2 * volatility_factor
    tp3_mult = base_tp3 * volatility_factor
    
    reasons.append(f"📊 Динамический стоп: волатильность x{volatility_factor:.2f} (BB Width: {bb_width_pct:.1f}%)")
    # ──────────────────────────────────────────────────────────────────

    # ── НОВЫЙ БЛОК: РАДАР СТОЛКНОВЕНИЙ (БЛОКИРОВКА ОБ СТЕНУ) ──
    # ── НОВЫЙ БЛОК: РАДАР СТОЛКНОВЕНИЙ (БЛОКИРОВКА ОБ СТЕНУ) ──
    if direction == "LONG" and walls.get("resistance"):
        dist_to_wall = walls["resistance"] - p
        dist_to_tp1 = atr_v * tp1_mult
        if 0 < dist_to_wall < dist_to_tp1:
            score -= 2
            reasons.append(f"⚠️ Стена продавцов перед TP1 (-2)")

    elif direction == "SHORT" and walls.get("support"):
        dist_to_wall = p - walls["support"]
        dist_to_tp1 = atr_v * tp1_mult
        if 0 < dist_to_wall < dist_to_tp1:
            score += 2
            reasons.append(f"⚠️ Стена покупателей перед TP1 (+2)")
    # ──────────────────────────────────────────────────────────
            # return None  <--- УДАЛИ ИЛИ ЗАКОММЕНТИРУЙ ЭТУ СТРОКУ
    # ──────────────────────────────────────────────────────────
    # Увеличенный буфер отступа от лимитной "стены" в стакане
    # Увеличенный буфер отступа от лимитной "стены" в стакане
    WALL_BUFFER = 1.0

    # ── 1. СНАЧАЛА СЧИТАЕМ ЛИМИТКУ ──
    # Расчет идеальной лимитной точки входа (откат 0.4 ATR от текущей цены)
    limit_buffer = atr_v * 0.4
    limit_entry = round(p - limit_buffer, 6) if direction == "LONG" else round(p + limit_buffer, 6)

    # ── 2. СЧИТАЕМ УРОВНИ СТРОГО ОТ ЛИМИТКИ (limit_entry) ──
    if direction == "LONG":
        # Стоп прячем за стену поддержки
        if walls.get("support") and walls["support"] < limit_entry:
            stop_loss = round(walls["support"] - atr_v * WALL_BUFFER, 6)
            reasons.append("🛡 Стоп надежно спрятан за стену поддержки")
        else:
            stop_loss = round(limit_entry - atr_v * sl_mult, 6)

        # TP2 ставим перед стеной сопротивления
        tp2_base = round(limit_entry + atr_v * tp2_mult, 6)
        if walls.get("resistance") and limit_entry < walls["resistance"] < tp2_base:
            tp2 = round(walls["resistance"] - atr_v * 0.1, 6)
            reasons.append("🎯 TP2 перед стеной сопротивления")
        else:
            tp2 = tp2_base

        take_profit = [
            round(limit_entry + atr_v * tp1_mult, 6),
            tp2,
            round(limit_entry + atr_v * tp3_mult, 6),
        ]
    else:  # SHORT
        if walls.get("resistance") and walls["resistance"] > limit_entry:
            stop_loss = round(walls["resistance"] + atr_v * WALL_BUFFER, 6)
            reasons.append("🛡 Стоп надежно спрятан за стену сопротивления")
        else:
            stop_loss = round(limit_entry + atr_v * sl_mult, 6)

        tp2_base = round(limit_entry - atr_v * tp2_mult, 6)
        if walls.get("support") and tp2_base < walls["support"] < limit_entry:
            tp2 = round(walls["support"] + atr_v * 0.1, 6)
            reasons.append("🎯 TP2 перед стеной поддержки")
        else:
            tp2 = tp2_base

        take_profit = [
            round(limit_entry - atr_v * tp1_mult, 6),
            tp2,
            round(limit_entry - atr_v * tp3_mult, 6),
        ]

    # ── 3. ФИНАЛЬНАЯ ВАЛИДАЦИЯ RISK/REWARD ──
    risk   = abs(p - stop_loss)
    reward = abs(take_profit[1] - p)
    rr     = round(reward / risk, 2) if risk > 0 else 0

    if rr < MIN_RR:
        return None

    # ── 4. ИТОГОВЫЙ СКОРИНГ И УВЕРЕННОСТЬ ──
    max_score  = 9
    confidence = min(95, round((abs(score) / max_score) * 100, 1))
    risk_score = max(1, min(10, 10 - int(confidence / 10)))

    # ── 5. ФИНАЛЬНАЯ СНАЙПЕРСКАЯ ГИЛЬОТИНА (Без дублей) ──
    if (direction == "LONG" and score <= 0) or (direction == "SHORT" and score >= 0):
        return None

    if confidence < MIN_CONFIDENCE:
        return None

    # ── 6. ЗАГЛУШКА NLS ──
    nls_data = {"nls_score": 0.0, "skewness": 0.0, "cvd_accel": 0.0, "whale_impact": 0.0}
    base_confidence = confidence
    nls_tag = "⚖️ NEUTRAL (NLS Отключен)"

    # ── 7. ВОЗВРАТ РЕЗУЛЬТАТА ──
    return {
        "symbol":       symbol,
        "timeframe":    timeframe,
        "direction":    direction,
        "entry_price":  round(p, 6),
        "limit_entry":  limit_entry,
        "stop_loss":    stop_loss,
        "take_profit":  take_profit,
        "risk_score":   risk_score,
        "confidence":   confidence,
        "risk_reward":  rr,
        "signals":      reasons,
        "score":        score,
        "indicators":   result["indicators"],
        "nls_score":    nls_data.get("nls_score", 0.0),
        "nls_skewness": nls_data.get("skewness", 0.0),
        "nls_cvd_accel":nls_data.get("cvd_accel", 0.0),
        "nls_whale_impact": nls_data.get("whale_impact", 0.0),
        "base_confidence": base_confidence,
        "nls_tag":      nls_tag
    }

def calculate_atr_numpy(highs, lows, closes, period=14):
    """Розрахунок ATR для визначення волатильності"""
    highs = np.array(highs)
    lows = np.array(lows)
    closes = np.array(closes)
    
    high_low = np.subtract(highs, lows)
    high_close = np.abs(np.subtract(highs, np.roll(closes, 1)))
    low_close = np.abs(np.subtract(lows, np.roll(closes, 1)))
    
    ranges = np.stack([high_low, high_close, low_close], axis=1)
    true_range = np.max(ranges, axis=1)
    
    atr = np.convolve(true_range, np.ones(period)/period, mode='valid')
    return atr[-1]