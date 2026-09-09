import asyncio
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database import get_or_create_user, can_receive_signal, increment_signal_count, save_signal
from analysis.technical import analyze_symbol
from utils.formatter import format_signal_message
from config import CRYPTO_PAIRS
from database import get_active_signals
from database import get_user



SIGNAL_LIMITS = {"free": 3, "basic": 5, "pro": 20, "vip": 9999}

TIMEFRAMES_RU = [
    ("15m", "⚡ 15 мин — скальпинг"),
    ("1h",  "📊 1 час — внутридневная"),
    ("4h",  "📈 4 часа — среднесрок"),
    ("1d",  "🗓 1 день — долгосрок"),
]
TIMEFRAMES_EN = [
    ("15m", "⚡ 15 min — scalping"),
    ("1h",  "📊 1 hour — intraday"),
    ("4h",  "📈 4 hours — swing"),
    ("1d",  "🗓 1 day — position"),
]

def can_free_get_signal(user: dict) -> tuple:
    limit      = SIGNAL_LIMITS["free"]
    week_start = (datetime.now() - timedelta(days=datetime.now().weekday())).date().isoformat()
    last_date  = user.get("last_signal_date", "")
    used       = user.get("signals_today", 0) if last_date and last_date >= week_start else 0
    return used < limit, limit - used, limit


async def signal_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tid  = update.effective_user.id
    user = get_or_create_user(tid, update.effective_user.username, update.effective_user.first_name)
    plan = user.get("subscription", "free")
    lang = user.get("language", "ru")

    # --- ДОБАВИТЬ ЭТОТ БЛОК ---
    user_dep = user.get("deposit", 0.0)
    user_risk = user.get("risk_pct", 0.0)
    
    if user_dep <= 0 or user_risk <= 0:
        if lang == "en":
            text = ("⚠️ <b>Mandatory Setup</b>\n\n"
                    "To calculate position size and receive signals, set your deposit and risk:\n\n"
                    "👉 <code>/set_deposit 500</code>\n"
                    "👉 <code>/set_risk 2</code>")
        else:
            text = ("⚠️ <b>Обязательная настройка</b>\n\n"
                    "Для расчета объема позиции и получения сигналов укажи свой рабочий депозит и риск на сделку:\n\n"
                    "👉 <code>/set_deposit 500</code>\n"
                    "👉 <code>/set_risk 2</code>")
        await update.message.reply_text(text, parse_mode="HTML")
        return
    # --------------------------

    # Проверка лимитов
    if plan == "free":
        can, left, total = can_free_get_signal(user)
        if not can:
            today       = datetime.now()
            next_monday = today + timedelta(days=(7 - today.weekday()))
            days_left   = (next_monday.date() - today.date()).days
            kb = [[InlineKeyboardButton(
                "💎 Subscribe" if lang == "en" else "💎 Оформить подписку",
                callback_data="show_plans"
            )]]
            if lang == "en":
                text = (f"⏳ <b>Limit reached</b>\n\nFree plan: <b>{total} signals/week</b>\n"
                        f"Next signals in <b>{days_left} days</b> (from Monday)\n\n🚀 Subscribe for unlimited:")
            else:
                text = (f"⏳ <b>Лимит исчерпан</b>\n\nБесплатный план: <b>{total} сигнала в неделю</b>\n"
                        f"Следующие сигналы через <b>{days_left} дн.</b> (с понедельника)\n\n🚀 Подписка снимает ограничения:")
            await update.message.reply_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))
            return
    else:
        max_s = SIGNAL_LIMITS.get(plan, 5)
        if not can_receive_signal(user, max_s):
            if lang == "en":
                text = f"⏳ Daily limit: {max_s} signals reached.\nTry tomorrow or /subscribe"
            else:
                text = f"⏳ Лимит на сегодня: {max_s} сигналов исчерпан.\nПопробуйте завтра или /subscribe"
            await update.message.reply_text(text)
            return

    # Выбор таймфрейма
    timeframes = TIMEFRAMES_EN if lang == "en" else TIMEFRAMES_RU
    kb = [[InlineKeyboardButton(label, callback_data=f"signal_tf_{tf}")] for tf, label in timeframes]
    kb.append([InlineKeyboardButton(
        "🎯 Best signal (auto)" if lang == "en" else "🎯 Лучший сигнал (авто)",
        callback_data="signal_tf_auto"
    )])

    if plan == "free":
        _, left, total = can_free_get_signal(user)
        footer = f"\n\n🆓 {'Signals left this week' if lang == 'en' else 'Осталось сигналов на неделе'}: <b>{left}/{total}</b>"
    else:
        footer = ""

    title = "📊 <b>Choose timeframe:</b>" if lang == "en" else "📊 <b>Выберите таймфрейм:</b>"
    await update.message.reply_text(
        f"{title}{footer}", parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(kb)
    )


async def run_analysis(update_or_query, context, timeframe: str, user: dict):
    
    is_query = hasattr(update_or_query, 'edit_message_text')
    lang     = user.get("language", "ru")

    if is_query:
        wait_text = "🔍 Analysing market...\n⏳ ~15-20 sec" if lang == "en" else "🔍 Анализирую рынок...\n⏳ ~15-20 секунд"
        await update_or_query.edit_message_text(wait_text)
        reply_func = update_or_query.message.reply_text
        tid = update_or_query.from_user.id
    else:
        wait_text = "🔍 Analysing market...\n⏳ ~15-20 sec" if lang == "en" else "🔍 Анализирую рынок...\n⏳ ~15-20 секунд"
        msg = await update_or_query.message.reply_text(wait_text)
        reply_func = update_or_query.message.reply_text
        tid = update_or_query.effective_user.id

    timeframes_to_try = ["1h", "4h", "1d", "15m"] if timeframe == "auto" else [timeframe]

    all_valid = []
    for tf in timeframes_to_try:
        tasks   = [analyze_symbol(p, tf) for p in CRYPTO_PAIRS[:10]]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        valid = []
        for r in results:
            if isinstance(r, Exception):
                print(f"❗️ ОШИБКА В analyze_symbol: {r}") # <--- Выведет реальную причину
            elif r:
                valid.append(r)
        all_valid.extend(valid)
        if all_valid:
            break

    if is_query:
        try: await update_or_query.delete_message()
        except: pass
    else:
        try: await msg.delete()
        except: pass

    if not all_valid:
        if lang == "en":
            text = "😔 <b>No clear signals right now</b>\n\nMarket is consolidating.\nTry another timeframe or in 30 minutes."
        else:
            text = "😔 <b>Сейчас нет чётких сигналов</b>\n\nРынок находится в консолидации.\nПопробуйте другой таймфрейм или через 30 минут."
        await reply_func(text, parse_mode="HTML")
        return

    best     = max(all_valid, key=lambda x: x["confidence"] * min(x.get("risk_reward", 1), 3))
    plan     = user.get("subscription", "free")
    show_ind = plan in ("pro", "vip")

    # Форматируем с языком
    current_user = get_user(tid) # Запрашиваем свежие данные из базы
    user_dep = current_user.get("deposit", 0.0)
    user_risk = current_user.get("risk_pct", 0.0)
    
    text = format_signal_message(
        best, 
        include_indicators=show_ind, 
        lang=lang,
        deposit=user_dep,
        risk_pct=user_risk
    )

    active_signals = get_active_signals()
    # Проверяем, есть ли активная сделка по этому же символу
    is_duplicate = any(s['symbol'] == best['symbol'] for s in active_signals)
    
    if not is_duplicate:
        # ЗБЕРІГАЄМО СИГНАЛ І ОТРИМУЄМО ЙОГО ID
        signal_id = save_signal(best)
        increment_signal_count(tid)
    else:
        # Если сигнал уже есть, можно просто отправить сообщение пользователю, что он уже активен
        await context.bot.send_message(
            chat_id=tid, 
            text="⚠️ <b>У вас уже есть активная сделка по этой паре.</b>", 
            parse_mode="HTML"
        )
        return # Выходим, чтобы не дублировать

    # ФОРМУЄМО КЛАВІАТУРУ (тепер у нас є signal_id для кнопки)
    symbol_clean = best["symbol"].replace("/", "")
    kb = [
        # --- НАША НОВА КНОПКА ДЛЯ ONE-CLICK TRADING ---
        [InlineKeyboardButton("🚀 Открыть сделку ($10 | 5x)", callback_data=f"trade_{signal_id}")],
        # ----------------------------------------------
        [
            InlineKeyboardButton("📱 Binance", url=f"https://www.binance.com/ru/futures/{symbol_clean}"),
            InlineKeyboardButton("📱 Bybit",   url=f"https://www.bybit.com/trade/usdt/{symbol_clean}"),
        ],
        [InlineKeyboardButton(
            "🔄 Another signal" if lang == "en" else "🔄 Ещё сигнал",
            callback_data="signal_tf_auto"
        )],
    ]
    if plan == "free":
        _, left, total = can_free_get_signal(user)
        kb.append([InlineKeyboardButton(
            f"⭐ Subscribe ({left-1}/{total} left)" if lang == "en" else f"⭐ Подписка (осталось {left-1}/{total})",
            callback_data="show_plans"
        )])

    await context.bot.send_message(
        chat_id=tid, text=text, parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(kb),
        protect_content=True,
    )


async def handle_timeframe_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tf   = query.data.replace("signal_tf_", "")
    tid  = query.from_user.id
    user = get_or_create_user(tid)
    await run_analysis(query, context, tf, user)