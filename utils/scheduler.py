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

        signal_id = save_signal(sig)
        symbol_clean = sig["symbol"].replace("/", "")
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("📱 Binance", url=f"https://www.binance.com/ru/futures/{symbol_clean}"),
            InlineKeyboardButton("📱 Bybit", url=f"https://www.bybit.com/trade/usdt/{symbol_clean}"),
        ]])

        sent_count = 0
        for user in users:
            tid = user["telegram_id"]
            lang = user.get("language", "ru")
            dep = float(user.get("deposit", 0.0) or 1000.0)
            risk = float(user.get("risk_pct", 0.0) or 2.0)
            header = (f"🚨 <b>AUTO SIGNAL</b> | {sig['confidence']:.0f}% confidence\n\n"
                      if lang == "en" else
                      f"🚨 <b>АВТОСИГНАЛ</b> | Уверенность {sig['confidence']:.0f}%\n\n")
            text = header + format_signal_message(sig, include_indicators=True, lang=lang,
                                                   deposit=dep, risk_pct=risk)
            try:
                await app.bot.send_message(chat_id=tid, text=text, parse_mode="HTML", reply_markup=kb, protect_content=True)
                save_user_signal(tid, signal_id)
                sent_count += 1
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
    scheduler.add_job(auto_scan_and_send, "cron", minute="*/15", args=[app, "15m"],
                       id="scan_15m", max_instances=1, coalesce=True, misfire_grace_time=120)
    scheduler.add_job(auto_scan_and_send, "cron", minute="5", args=[app, "1h"],
                       id="scan_1h", max_instances=1, coalesce=True, misfire_grace_time=180)
    scheduler.add_job(auto_scan_and_send, "cron", hour="0,4,8,12,16,20", minute="10",
                       args=[app, "4h"], id="scan_4h", max_instances=1, coalesce=True, misfire_grace_time=300)
    logger.info("Планировщик: фиксированные SCALP/SWING пары, concurrency=%d, max auto=%d",
                SCAN_CONCURRENCY, MAX_AUTO_SIGNALS)
    return scheduler
