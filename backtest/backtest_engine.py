"""
backtest/backtest_engine.py
Эмуляция реального торгового счёта (депозит, плечо, комиссии).
"""

import asyncio
import aiohttp
import pandas as pd
import numpy as np
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional

from analysis.technical import _score_dataframe


@dataclass
class Trade:
    symbol:      str
    timeframe:   str
    direction:   str
    entry_price: float
    stop_loss:   float
    take_profit: list
    entry_time:  object
    exit_price:  float  = 0.0
    exit_time:   object = None
    result:      str    = ""
    pnl_pct:     float  = 0.0
    score:       int    = 0
    confidence:  float  = 0.0


@dataclass
class BacktestResult:
    symbol:          str
    timeframe:       str
    total_trades:    int   = 0
    wins:            int   = 0
    losses:          int   = 0
    win_rate:        float = 0.0
    avg_win_pct:     float = 0.0
    avg_loss_pct:    float = 0.0
    profit_factor:   float = 0.0
    initial_balance: float = 100.0
    final_balance:   float = 100.0
    total_fees:      float = 0.0
    net_profit_pct:  float = 0.0
    max_drawdown:    float = 0.0
    trades:          list  = field(default_factory=list)


async def fetch_historical(symbol: str, timeframe: str, days: int = 180) -> Optional[pd.DataFrame]:
    clean  = symbol.replace("/", "")
    url    = "https://api.binance.com/api/v3/klines"
    tf_map = {"5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d"}
    tf     = tf_map.get(timeframe, "1h")
    mins_map = {"5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}
    mins  = mins_map.get(timeframe, 60)
    total = (days * 24 * 60) // mins
    pages = (total // 1000) + 1
    all_candles = []
    end_time = int(datetime.now().timestamp() * 1000)

    async with aiohttp.ClientSession() as session:
        for _ in range(min(pages, 10)):
            params = {"symbol": clean, "interval": tf, "endTime": end_time, "limit": 1000}
            try:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=20)) as r:
                    if r.status != 200: break
                    data = await r.json()
                    if not data: break
                    all_candles = data + all_candles
                    end_time = data[0][0] - 1
                    await asyncio.sleep(0.3)
            except Exception as e:
                print(f"  Fetch error: {e}")
                break

    if len(all_candles) < 100:
        return None

    df = pd.DataFrame(all_candles, columns=[
        "timestamp","open","high","low","close","volume",
        "close_time","quote_volume","trades","taker_buy_base","taker_buy_quote","ignore"
    ])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("timestamp", inplace=True)
    for col in ["open","high","low","close","volume","taker_buy_base"]:
        df[col] = pd.to_numeric(df[col])
    return df.sort_index()


def generate_signal_for_candle(
    df_main: pd.DataFrame,
    idx: int,
    timeframe: str,
    df_senior: Optional[pd.DataFrame] = None,
    df_btc: Optional[pd.DataFrame] = None   # <--- ДОДАЛИ АРГУМЕНТ
) -> Optional[dict]:
    
    # Тепер нам потрібно мінімум 200 свічок для розрахунку EMA200
    if idx < 200:
        return None

    window       = df_main.iloc[:idx]
    current_time = window.index[-1]

    # --- НОВИЙ БЛОК: BTC MARKET BIAS ---
    btc_bias = "NEUTRAL"
    if df_btc is not None:
        # Беремо зріз Биткоїна точно до поточного часу свічки альткоїна
        window_btc = df_btc[df_btc.index <= current_time]
        if len(window_btc) >= 200:
            btc_close = window_btc["close"]
            
            # Швидкий розрахунок EMA прямо в тестері
            ema50_btc = btc_close.ewm(span=50, adjust=False).mean().iloc[-1]
            ema200_btc = btc_close.ewm(span=200, adjust=False).mean().iloc[-1]
            price_btc = btc_close.iloc[-1]
            
            # Визначаємо глобальний тренд
            if price_btc > ema200_btc and ema50_btc > ema200_btc:
                btc_bias = "LONG_ONLY"
            elif price_btc < ema200_btc and ema50_btc < ema200_btc:
                btc_bias = "SHORT_ONLY"
    # -----------------------------------

    # Основний скоринг
    result = _score_dataframe(window)
    if result is None:
        return None

    score  = result["score"]
    p      = result["price"]
    atr_v  = result["atr"]

    # Жорсткий фільтр за Биткоїном
    if score >= 5:
        if btc_bias == "SHORT_ONLY":
            return None # 🚫 Блокуємо LONG на падаючому ринку
        direction = "LONG"
    elif score <= -5:
        if btc_bias == "LONG_ONLY":
            return None # 🚫 Блокуємо SHORT на зростаючому ринку
        direction = "SHORT"
    else:
        return None

    confidence = min(95, round((abs(score) / 14) * 100, 1))

    # MTFA фильтр: старший таймфрейм не должен противоречить
    if df_senior is not None:
        window_senior = df_senior[df_senior.index <= current_time]
        if len(window_senior) >= 55:
            res_senior = _score_dataframe(window_senior)
            if res_senior:
                s = res_senior["score"]
                if direction == "LONG"  and s <= -3: return None
                if direction == "SHORT" and s >= 3:  return None

    # ATR-множители (оптимизированы по бэктесту)
    if timeframe == "15m":
        sl_m, tp1, tp2, tp3 = 0.8, 0.6, 1.2, 2.0
    elif timeframe == "1h":
        sl_m, tp1, tp2, tp3 = 1.0, 0.8, 1.5, 2.5
    elif timeframe == "4h":
        sl_m, tp1, tp2, tp3 = 1.4, 1.1, 2.2, 3.5
    else:
        sl_m, tp1, tp2, tp3 = 2.0, 1.8, 3.5, 5.5

    if direction == "LONG":
        sl  = round(p - atr_v * sl_m, 6)
        tps = [round(p + atr_v * tp1, 6), round(p + atr_v * tp2, 6), round(p + atr_v * tp3, 6)]
    else:
        sl  = round(p + atr_v * sl_m, 6)
        tps = [round(p - atr_v * tp1, 6), round(p - atr_v * tp2, 6), round(p - atr_v * tp3, 6)]

    risk   = abs(p - sl)
    reward = abs(tps[1] - p)
    rr     = round(reward / risk, 2) if risk > 0 else 0
    if rr < 1.2:
        return None

    return {
        "direction": direction, "entry_price": p, "stop_loss": sl,
        "take_profit": tps, "confidence": confidence, "score": score
    }


def simulate_trade(signal: dict, future_candles: pd.DataFrame) -> dict:
    d, ep, sl, tps = signal["direction"], signal["entry_price"], signal["stop_loss"], signal["take_profit"]

    for i, row in future_candles.iterrows():
        h_c, l_c = row["high"], row["low"]
        if d == "LONG":
            if l_c <= sl:    return {"result": "LOSS", "exit": sl,    "exit_time": i, "pnl": round(((sl-ep)/ep)*100, 3)}
            if h_c >= tps[2]: return {"result": "TP3",  "exit": tps[2],"exit_time": i, "pnl": round(((tps[2]-ep)/ep)*100, 3)}
            if h_c >= tps[1]: return {"result": "TP2",  "exit": tps[1],"exit_time": i, "pnl": round(((tps[1]-ep)/ep)*100, 3)}
            if h_c >= tps[0]: return {"result": "TP1",  "exit": tps[0],"exit_time": i, "pnl": round(((tps[0]-ep)/ep)*100, 3)}
        else:
            if h_c >= sl:    return {"result": "LOSS", "exit": sl,    "exit_time": i, "pnl": round(((ep-sl)/ep)*100*-1, 3)}
            if l_c <= tps[2]: return {"result": "TP3",  "exit": tps[2],"exit_time": i, "pnl": round(((ep-tps[2])/ep)*100, 3)}
            if l_c <= tps[1]: return {"result": "TP2",  "exit": tps[1],"exit_time": i, "pnl": round(((ep-tps[1])/ep)*100, 3)}
            if l_c <= tps[0]: return {"result": "TP1",  "exit": tps[0],"exit_time": i, "pnl": round(((ep-tps[0])/ep)*100, 3)}

    last = float(future_candles["close"].iloc[-1])
    pnl  = ((last-ep)/ep)*100 if d=="LONG" else ((ep-last)/ep)*100
    return {"result": "OPEN", "exit": last, "exit_time": None, "pnl": round(pnl, 3)}


async def run_backtest(symbol: str, timeframe: str = "1h",
                       days: int = 180, min_confidence: float = 0.0) -> BacktestResult:
    # Завантажуємо основний, старший таймфрейм та Биткоїн паралельно
    senior_map = {"15m": "1h", "1h": "4h", "4h": "1d"}
    senior_tf  = senior_map.get(timeframe)

    tasks = [fetch_historical(symbol, timeframe, days)]
    
    if senior_tf:
        tasks.append(fetch_historical(symbol, senior_tf, days))
        
    # Завантажуємо BTC тільки якщо ми зараз не тестуємо сам Биткоїн
    if symbol != "BTC/USDT":
        tasks.append(fetch_historical("BTC/USDT", timeframe, days))

    fetched = await asyncio.gather(*tasks)
    
    df_main = fetched[0]
    
    # Розбираємо результати асинхронних запитів
    df_senior = None
    df_btc = None
    
    if senior_tf and symbol != "BTC/USDT":
        df_senior = fetched[1]
        df_btc = fetched[2]
    elif senior_tf and symbol == "BTC/USDT":
        df_senior = fetched[1]
        df_btc = df_main # Биткоїн сам собі поводир
    elif not senior_tf and symbol != "BTC/USDT":
        df_btc = fetched[1]

    if df_main is None:
        return BacktestResult(symbol=symbol, timeframe=timeframe)

    trades     = []
    skip_until = 0

    # ⚠️ ЗВЕРНИ УВАГУ: Починаємо з 200-ї свічки, щоб вистачило даних для EMA200
    for i in range(200, len(df_main) - 50):
        if i < skip_until:
            continue

        # ПЕРЕДАЄМО df_btc у генератор
        sig = generate_signal_for_candle(df_main, i, timeframe, df_senior, df_btc)

# Проверка на наличие сигнала и его качество
        if sig is None or sig["confidence"] < min_confidence:
            continue 

        res = simulate_trade(sig, df_main.iloc[i:i+50])

        trade = Trade(
            symbol=symbol, timeframe=timeframe,
            direction=sig["direction"], entry_price=sig["entry_price"],
            stop_loss=sig["stop_loss"], take_profit=sig["take_profit"],
            entry_time=df_main.index[i], exit_price=res["exit"],
            exit_time=res["exit_time"], result=res["result"],
            pnl_pct=res["pnl"], score=sig["score"], confidence=sig["confidence"]
        )
        trades.append(trade)

        if res["exit_time"] is not None and res["exit_time"] in df_main.index:
            skip_until = df_main.index.get_loc(res["exit_time"]) + 1
        else:
            skip_until = i + 20

    return _calc_stats(symbol, timeframe, trades)


def _calc_stats(symbol: str, timeframe: str, trades: list) -> BacktestResult:
    if not trades:
        return BacktestResult(symbol=symbol, timeframe=timeframe)

    # Эмуляция реального счёта
    balance          = 100.0
    risk_margin_pct  = 0.05    # 5% депозита на сделку
    leverage         = 10      # 10x плечо
    fee_rate         = 0.0004  # 0.04% вход + 0.04% выход = 0.08% на сделку

    total_fees   = 0.0
    peak_balance = balance
    max_dd_pct   = 0.0
    net_wins     = 0
    net_losses   = 0

    for t in trades:
        margin             = balance * risk_margin_pct
        position_size      = margin * leverage
        gross_profit       = position_size * (t.pnl_pct / 100.0)
        fee                = position_size * fee_rate * 2
        net_profit         = gross_profit - fee

        balance    += net_profit
        total_fees += fee

        if net_profit > 0:
            net_wins += 1
        else:
            net_losses += 1

        if balance > peak_balance:
            peak_balance = balance
        dd = ((peak_balance - balance) / peak_balance) * 100
        if dd > max_dd_pct:
            max_dd_pct = dd

    raw_wins   = [t.pnl_pct for t in trades if t.pnl_pct > 0]
    raw_losses = [abs(t.pnl_pct) for t in trades if t.pnl_pct <= 0]
    pf = round(sum(raw_wins) / sum(raw_losses), 2) if raw_losses and sum(raw_losses) > 0 else 0

    total = len(trades)
    return BacktestResult(
        symbol=symbol, timeframe=timeframe,
        total_trades=total,
        wins=net_wins, losses=net_losses,
        win_rate=round(net_wins / total * 100, 1) if total else 0,
        avg_win_pct=round(np.mean(raw_wins), 3) if raw_wins else 0,
        avg_loss_pct=round(np.mean(raw_losses), 3) if raw_losses else 0,
        profit_factor=pf,
        initial_balance=100.0,
        final_balance=round(balance, 2),
        total_fees=round(total_fees, 2),
        net_profit_pct=round(((balance - 100.0) / 100.0) * 100, 2),
        max_drawdown=round(max_dd_pct, 2),
        trades=trades,
    )