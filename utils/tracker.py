import asyncio
import time
import aiohttp
import logging
from telegram.ext import Application
import ccxt
import numpy as np
from database import get_active_signals, update_signal_status, get_all_users
from ccxt import async_support as ccxt_async
from typing import Dict

import aiohttp

async def get_top_volume_pairs(limit: int = 30) -> list:
    """
    Запитує у Binance Futures список всіх торгових пар і повертає топ-N
    найбільш ліквідних пар (USDT) за обсягом торгів за 24 години.
    """
    url = "https://fapi.binance.com/fapi/v1/ticker/24hr"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    
                    # Фільтруємо: залишаємо тільки USDT пари, прибираємо індекси та всяке сміття
                    valid_pairs = [
                        item for item in data 
                        if item['symbol'].endswith('USDT') and '_' not in item['symbol']
                    ]
                    
                    # Сортуємо за quoteVolume (обсяг у доларах) за спаданням
                    valid_pairs.sort(key=lambda x: float(x['quoteVolume']), reverse=True)
                    
                    # Беремо топ-limit і форматуємо під наш стандарт (напр. "BTC/USDT")
                    top_symbols = []
                    for item in valid_pairs[:limit]:
                        symbol = item['symbol'].replace('USDT', '/USDT')
                        top_symbols.append(symbol)
                        
                    return top_symbols
                else:
                    logger.error(f"Помилка Binance API: {resp.status}")
    except Exception as e:
        logger.error(f"Помилка при отриманні топ пар: {e}")
        
    # Якщо сталася помилка, повертаємо базовий фолбек-список
    return ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT"]

logger = logging.getLogger(__name__)

async def fetch_current_prices(symbols: list[str]) -> dict[str, float]:
    """Быстрый запрос текущих цен через Binance Ticker API"""
    if not symbols:
        return {}
    
    clean_symbols = [s.replace("/", "") for s in symbols]
    url = 'https://api.binance.com/api/v3/ticker/price'
    
    # Формируем JSON массив для запроса нескольких пар разом
    symbols_param = '["' + '","'.join(clean_symbols) + '"]'
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{url}?symbols={symbols_param}") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    # Возвращаем словарь {'BTCUSDT': 65000.0, ...}
                    return {item['symbol']: float(item['price']) for item in data}
    except Exception as e:
        logger.error(f"Ошибка при запросе цен в трекере: {e}")
    return {}

from database import get_users_for_signal # Змінюємо імпорт

async def broadcast_trade_update(app: Application, message: str, signal_id: int):
    """Розіслати сповіщення ТІЛЬКИ тим, хто отримував цей сигнал"""
    users = get_users_for_signal(signal_id)
    for user in users:
        try:
            await app.bot.send_message(
                chat_id=user["telegram_id"],
                text=message,
                parse_mode="HTML"
            )
            await asyncio.sleep(0.05)
        except Exception:
            pass

atr_cache = {}

async def update_atr_cache(signals: list):
    exchange = ccxt_async.binance()

    try:
        for sig in signals:
            symbol = sig["symbol"]
            timeframe = sig["timeframe"]

            key = (symbol, timeframe)

            # Проверяем кэш
            cached = atr_cache.get(key)

            if cached:
                age = time.time() - cached["updated"]

                refresh_time = {
                    "15m": 15 * 60,
                    "1h": 60 * 60,
                    "4h": 4 * 60 * 60,
                }.get(timeframe, 15 * 60)

                # ATR ещё актуален
                if age < refresh_time:
                    continue

            try:
                bars = await exchange.fetch_ohlcv(
                    symbol,
                    timeframe=timeframe,
                    limit=20
                )

                if len(bars) < 14:
                    continue

                highs = [b[2] for b in bars]
                lows = [b[3] for b in bars]
                closes = [b[4] for b in bars]

                from analysis.technical import calculate_atr_numpy # Изменили импорт

                atr = calculate_atr_numpy( # Изменили вызов функции
                    highs,
                    lows,
                    closes
                )

                atr_cache[key] = {
                    "atr": atr,
                    "updated": time.time()
                }

            except Exception as e:
                logger.error(
                    f"ATR update error {symbol} {timeframe}: {e}"
                )

    finally:
        await exchange.close()


async def check_active_trades(app):
    """Оптимізований трекінг з ATR Trailing Stop"""
    active_signals = get_active_signals()
    if not active_signals:
        return

    symbols = list(set(s['symbol'] for s in active_signals))
    
    # 1. Оновлюємо кеш ATR перед циклом (один запит на символ)
    # Обновляем ATR для всех активных сигналов
    await update_atr_cache(active_signals)

# Получаем текущие цены
    symbols = list(set(s["symbol"] for s in active_signals))
    prices = await fetch_current_prices(symbols)

    for sig in active_signals:
        clean_symbol = sig['symbol'].replace("/", "")
        curr_price = prices.get(clean_symbol)
        if not curr_price: continue
            
        sid = sig['id']
        direction = sig['direction']
        status = sig['status']
        entry = sig['entry_price']
        sl = sig['stop_loss']
        tps = sig['take_profit']
        pct = (curr_price - entry) / entry * 100 if direction == 'LONG' else (entry - curr_price) / entry * 100
        # Служебный PnL для статистики: 1R = $10 по исторической схеме.
        risk_usd = 10.0
        
        # 3. ATR TRAILING STOP (динамічне оновлення стопу)
        # Працює тільки для активних угод, які вже в плюсі > 1%
        if status in ("active", "active_tp1", "active_tp2") and pct > 1.0:
            cached = atr_cache.get(
    (
        sig["symbol"],
        sig["timeframe"]
    )
)
            atr_value = cached["atr"] if cached else None

            if atr_value is not None:
                if direction == "LONG":
                    new_sl = curr_price - (atr_value * 2.0)
                    if new_sl > sl: # Підтягуємо стоп тільки вгору
                        sl = new_sl
                        update_signal_status(sid, status, stop_loss=new_sl)
                elif direction == "SHORT":
                    new_sl = curr_price + (atr_value * 2.0)
                    if new_sl < sl: # Підтягуємо стоп тільки вниз
                        sl = new_sl
                        update_signal_status(sid, status, stop_loss=new_sl)

        # 4. ЛОГИКА TP/SL (використовує оновлений sl)
        if direction == 'LONG':
            if curr_price <= sl:
                pnl_usd = -risk_usd if status == 'active' else risk_usd * 0.5
                update_signal_status(sid, 'closed_sl' if status == 'active' else 'closed_be', sl, pct, pnl_usd)
                await broadcast_trade_update(app, f"🔴 <b>{sig['symbol']}</b> закрито (SL/Trailing).\nІтог: ${pnl_usd:.2f}", sid)
                continue
            
            # Логіка тейків (без змін)
            if status == 'active' and curr_price >= tps[0]:
                update_signal_status(sid, 'active_tp1', stop_loss=entry)
                await broadcast_trade_update(app, f"🎯 <b>{sig['symbol']}</b> TP1 (+{pct:.2f}%)!\n🔒 Переведи Stop-Loss в безубыток: <b>{entry}</b>", sid)
            elif status == 'active_tp1' and curr_price >= tps[1]:
                update_signal_status(sid, 'active_tp2', stop_loss=tps[0])
                await broadcast_trade_update(app, f"🎯🎯 <b>{sig['symbol']}</b> TP2!\n🔒 Перенеси Stop-Loss на уровень TP1: <b>{tps[0]}</b>", sid)
            elif status == 'active_tp2' and curr_price >= tps[2]:
                update_signal_status(sid, 'closed_tp', sl, pct, risk_usd * 2.0)
                await broadcast_trade_update(app, f"🚀 <b>{sig['symbol']}</b> TP3! Профіт: +$20.00", sid)

        elif direction == 'SHORT':
            if curr_price >= sl:
                pnl_usd = -risk_usd if status == 'active' else risk_usd * 0.5
                update_signal_status(sid, 'closed_sl' if status == 'active' else 'closed_be', sl, pct, pnl_usd)
                await broadcast_trade_update(app, f"🔴 <b>{sig['symbol']}</b> закрито (SL/Trailing).\nІтог: ${pnl_usd:.2f}", sid)
                continue

            if status == 'active' and curr_price <= tps[0]:
                update_signal_status(sid, 'active_tp1', stop_loss=entry)
                await broadcast_trade_update(app, f"🎯 <b>{sig['symbol']}</b> TP1 (-{pct:.2f}%)!\n🔒 Переведи Stop-Loss в безубыток: <b>{entry}</b>", sid)
            elif status == 'active_tp1' and curr_price <= tps[1]:
                update_signal_status(sid, 'active_tp2', stop_loss=tps[0])
                await broadcast_trade_update(app, f"🎯🎯 <b>{sig['symbol']}</b> TP2!\n🔒 Перенеси Stop-Loss на уровень TP1: <b>{tps[0]}</b>", sid)
            elif status == 'active_tp2' and curr_price <= tps[2]:
                update_signal_status(sid, 'closed_tp', sl, pct, risk_usd * 2.0)
                await broadcast_trade_update(app, f"🚀 <b>{sig['symbol']}</b> TP3! Профіт: +$20.00", sid)