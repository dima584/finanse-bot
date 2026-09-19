"""utils/scheduler.py — надежный планировщик автосигналов."""

import asyncio
import logging
from datetime import datetime, timezone
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram.ext import Application
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from utils.tracker import check_active_trades
from analysis.technical import analyze_symbol
from utils.formatter import format_signal_message
from database import get_all_users, save_signal, save_user_signal
from config import SCALP_PAIRS, SWING_PAIRS
import os
from telegram import InputFile
from analysis.technical import fetch_ohlcv
from utils.charts import create_signal_chart

logger = logging.getLogger(__name__)

_sent_cache: set[str] = set()
GLOBAL_CONF_LIMIT = 44
SCAN_CONCURRENCY = 4
MAX_AUTO_SIGNALS = 3


async def _analyze_pair(pair: str, timeframe: str, sem: asyncio.Semaphore):
    async with sem:
        try:
            return await analyze_symbol(pair, timeframe)
        except Exception as e:
            logger.exception("[SCAN %s] Ошибка %s: %s", timeframe, pair, e)
            return None


def _pairs_for_timeframe(timeframe: str) -> list[str]:
    # Возвращаем проверенный фиксированный набор. Динамический top-150
    # создавал лишнюю нагрузку и ухудшал воспроизводимость стратегии.
    return list(SCALP_PAIRS if timeframe == "15m" else SWING_PAIRS)


async def auto_scan_and_send(app: Application, timeframe: str):
    started = asyncio.get_running_loop().time()
    now = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    pairs = _pairs_for_timeframe(timeframe)
    logger.info("[SCAN %s] Старт %s, %d пар", timeframe, now, len(pairs))

    sem = asyncio.Semaphore(SCAN_CONCURRENCY)
    results = await asyncio.gather(*(_analyze_pair(p, timeframe, sem) for p in pairs))

    signals = [r for r in results if r and r.get("confidence", 0) >= GLOBAL_CONF_LIMIT]
    signals.sort(key=lambda x: (x.get("confidence", 0), abs(x.get("score", 0))), reverse=True)
    signals = signals[:MAX_AUTO_SIGNALS]

    elapsed = asyncio.get_running_loop().time() - started
    logger.info("[SCAN %s] Кандидатов: %d/%d, отправляем: %d, время %.1fs",
                timeframe, len([r for r in results if r]), len(pairs), len(signals), elapsed)

    if not signals:
        logger.info("[SCAN %s] Нет качественных сигналов", timeframe)
        return

    users = get_all_users()
    for sig in signals:
        hour_key = datetime.now(timezone.utc).strftime("%Y%m%d%H")
        sig_key = f"{sig['symbol']}_{sig['direction']}_{timeframe}_{hour_key}"
        if sig_key in _sent_cache:
            continue
        _sent_cache.add(sig_key)

        # Сохраняем сигнал в базу
        signal_id = save_signal(sig)
        symbol_clean = sig["symbol"].replace("/", "")
        
        # === ДОБАВЛЯЕМ КНОПКУ АВТО-ТОРГОВЛИ В КЛАВИАТУРУ ===
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("⚡ Відкрити угоду (Limit)", callback_data=f"trade_{signal_id}")],
            [
                InlineKeyboardButton("📱 Binance", url=f"https://www.binance.com/ru/futures/{symbol_clean}"),
                InlineKeyboardButton("📱 Bybit", url=f"https://www.bybit.com/trade/usdt/{symbol_clean}")
            ]
        ])

        # Генерируем график ОДИН раз для текущего сигнала
        chart_bytes = None
        try:
            df_c = await fetch_ohlcv(sig["symbol"], timeframe, limit=100)
            if df_c is not None and not df_c.empty:
                buf = create_signal_chart(
                    df_prices=df_c,
                    symbol=sig["symbol"],
                    entry=sig["entry_price"],
                    sl=sig["stop_loss"],
                    tp_list=sig["take_profit"]
                )
                if buf:
                    chart_bytes = buf.getvalue()
        except Exception as chart_err:
            logger.warning(f"[SCAN {timeframe}] Ошибка графика {sig['symbol']}: {chart_err}")

        # === 2. РАССЫЛКА ВСЕМ ПОЛЬЗОВАТЕЛЯМ ===
        sent_count = 0
        for user in users:
            tid = user["telegram_id"]
            lang = user.get("language", "ru")
            dep = float(user.get("deposit", 0.0) or 1000.0)
            risk = float(user.get("risk_pct", 0.0) or 2.0)
            
            header = (f"🚨 <b>AUTO SIGNAL</b> | {sig['confidence']:.0f}% confidence\n\n"
                      if lang == "en" else
                      f"🚨 <b>АВТОСИГНАЛ</b> | Уверенность {sig['confidence']:.0f}%\n\n")
            text = header + format_signal_message(sig, include_indicators=True, lang=lang, deposit=dep, risk_pct=risk)
            
            try:
                # Отправляем сообщение с графиком и кнопками
                if chart_bytes:
                    try:
                        photo_file = InputFile(chart_bytes, filename=f"{symbol_clean}.png")
                        await app.bot.send_photo(
                            chat_id=tid, photo=photo_file, caption=text, 
                            parse_mode="HTML", reply_markup=kb, protect_content=True
                        )
                    except Exception as photo_err:
                        logger.warning(f"[SCAN {timeframe}] Не удалось отправить фото для {tid}: {photo_err}")
                        await app.bot.send_message(
                            chat_id=tid, text=text, 
                            parse_mode="HTML", reply_markup=kb, protect_content=True
                        )
                else:
                    await app.bot.send_message(
                        chat_id=tid, text=text, 
                        parse_mode="HTML", reply_markup=kb, protect_content=True
                    )
                
                # Фиксируем успешную отправку в базе
                save_user_signal(tid, signal_id)
                sent_count += 1
                
                # === БЛОК АВТОПИЛОТА (НОЧНОЙ РЕЖИМ) ===
                # Получаем текущий час по UTC
                current_utc_hour = datetime.now(timezone.utc).hour
                # 21:00 UTC = 00:00 Киев (зимой). 05:00 UTC = 08:00 Киев.
                # Если сейчас от 21:00 до 05:00 по UTC (от 00:00 до 08:00 по Киеву):
                is_night_mode = (current_utc_hour >= 21) or (current_utc_hour < 5)
                
                if is_night_mode:
                    # Ждем 2 секунды после отправки сигнала, чтобы не спамить API
                    await asyncio.sleep(2)
                    
                    # Запускаем торговлю автоматически
                    from executor import execute_binance_trade
                    trade_res = await execute_binance_trade(signal_id, tid)
                    
                    if trade_res['success']:
                        auto_msg = (
                            f"🌙 <b>Нічний автопілот:</b>\n"
                            f"✅ Лімітний ордер успішно виставлено!\n"
                            f"▫️ Об'єм: {trade_res['qty']} монет\n"
                            f"▫️ Вхід: {trade_res['entry']}\n"
                            f"▫️ Тейк-профіт (+20% ROI): {trade_res['tp']}"
                        )
                        await app.bot.send_message(chat_id=tid, text=auto_msg, parse_mode="HTML")
                    else:
                        err_msg = f"🌙 <b>Нічний автопілот (Помилка):</b>\n❌ {trade_res['error']}"
                        await app.bot.send_message(chat_id=tid, text=err_msg, parse_mode="HTML")
                # =======================================
                
                await asyncio.sleep(0.05)
            except Exception as e:
                logger.warning("[SCAN %s] Не удалось отправить %s: %s", timeframe, tid, e)

        logger.info("[SCAN %s] ✅ %s %s (%.0f%%) → %d пользователей",
                    timeframe, sig['direction'], sig['symbol'], sig['confidence'], sent_count)

    if len(_sent_cache) > 500:
        _sent_cache.clear()


def setup_scheduler(app: Application) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(check_active_trades, "cron", minute="*/3", args=[app],
                       id="trade_tracker", max_instances=1, coalesce=True, misfire_grace_time=60)
    
    scheduler.add_job(auto_scan_and_send, "cron", minute="5", args=[app, "1h"],
                       id="scan_1h", max_instances=1, coalesce=True, misfire_grace_time=180)
    scheduler.add_job(auto_scan_and_send, "cron", hour="0,4,8,12,16,20", minute="10",
                       args=[app, "4h"], id="scan_4h", max_instances=1, coalesce=True, misfire_grace_time=300)
    logger.info("Планировщик: фиксированные SCALP/SWING пары, concurrency=%d, max auto=%d",
                SCAN_CONCURRENCY, MAX_AUTO_SIGNALS)
    return scheduler