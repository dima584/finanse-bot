"""
handlers/subscribe.py — ЦЕНТРАЛЬНЫЙ РОУТЕР ВСЕХ КНОПОК
"""

import asyncio
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from database import get_or_create_user, set_user_language
from config import CRYPTO_PAIRS
from utils.i18n import _


async def subscribe_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /subscribe"""
    from handlers.payments import payment_menu_handler
    await payment_menu_handler(update, context)


async def text_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Текстовые кнопки постоянного меню — на обоих языках"""
    text = update.message.text

    if text in ["📊 Сигнал", "📊 Signal"]:
        from handlers.signal import signal_handler
        await signal_handler(update, context)

    elif text in ["🌐 Рынок", "🌐 Market"]:
        from handlers.market import market_handler
        await market_handler(update, context)

    elif text in ["💎 Подписка", "💎 Subscription"]:
        from handlers.payments import payment_menu_handler
        await payment_menu_handler(update, context)

    elif text in ["❓ Помощь", "❓ Help"]:
        from handlers.start import help_handler
        await help_handler(update, context)


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    ЦЕНТРАЛЬНЫЙ ОБРАБОТЧИК ВСЕХ INLINE КНОПОК.
    Все callback_data проходят через здесь.
    """
    query = update.callback_query
    await query.answer()
    data = query.data
    tid  = query.from_user.id
    user = get_or_create_user(tid)
    lang = user.get("language", "ru")

    # ── Смена языка ──
    if data.startswith("set_lang_"):
        new_lang = data.replace("set_lang_", "")   # "ru" или "en"
        set_user_language(tid, new_lang)

        from handlers.start import get_main_keyboard
        msg = "✅ Language updated to English!" if new_lang == "en" else "✅ Язык изменён на Русский!"
        await context.bot.send_message(
            chat_id=tid,
            text=msg,
            reply_markup=get_main_keyboard(new_lang)
        )
        try:
            await query.message.delete()
        except Exception:
            pass

    # ── Выбор таймфрейма для сигнала ──
    elif data.startswith("signal_tf_"):
        from handlers.signal import handle_timeframe_callback
        await handle_timeframe_callback(update, context)

    # ── Тарифы — показать список ──
    elif data == "show_plans":
        from handlers.payments import payment_menu_handler
        await payment_menu_handler(update, context)

    # ── Выбор конкретного тарифа ──
    elif data.startswith("pay_plan_"):
        from handlers.payments import choose_payment_method
        await choose_payment_method(update, context)

    # ── Оплата Stars ──
    elif data.startswith("pay_stars_"):
        from handlers.payments import pay_with_stars
        await pay_with_stars(update, context)

    # ── Оплата криптой ──
    elif data.startswith("pay_crypto_"):
        from handlers.payments import pay_with_crypto
        await pay_with_crypto(update, context)

    # ── Оплата картой ──
    elif data.startswith("pay_card_"):
        from handlers.payments import pay_with_card
        await pay_with_card(update, context)

    # ── Старые кнопки buy_* (совместимость) ──
    elif data.startswith("buy_"):
        plan = data.replace("buy_", "")
        # Перенаправляем на выбор способа оплаты
        query.data = f"pay_plan_{plan}"
        from handlers.payments import choose_payment_method
        await choose_payment_method(update, context)

    # ── Обзор рынка ──
    elif data == "market_overview":
        from analysis.technical import analyze_symbol
        from utils.formatter import format_market_overview
        await query.edit_message_text("🔍 Сканирую рынок...")
        tasks = [analyze_symbol(p, "1h") for p in CRYPTO_PAIRS[:6]]
        tasks += [analyze_symbol(p, "4h") for p in CRYPTO_PAIRS[:4]]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        valid = [r for r in results if r and not isinstance(r, Exception)]
        kb = [[InlineKeyboardButton("🔄 Обновить", callback_data="market_overview")]]
        await query.edit_message_text(
            format_market_overview(valid, lang=lang),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(kb)
        )

    # ── Моя подписка ──
    elif data == "my_subscription":
        from utils.formatter import format_subscription_info
        info = format_subscription_info(user)
        kb = [[InlineKeyboardButton("📦 Тарифы", callback_data="show_plans")]]
        await query.edit_message_text(
            info, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(kb)
        )

    # ── Ещё сигнал (кнопка под сигналом) ──
    elif data == "get_signal":
        from handlers.signal import signal_handler

        class FakeUpdate:
            """Прокси чтобы вызвать signal_handler из callback"""
            effective_user = query.from_user
            message        = query.message

        await signal_handler(FakeUpdate(), context)

    # ── Назад на главную ──
    elif data == "back_main":
        kb = [
            [InlineKeyboardButton("📊 Сигнал",   callback_data="get_signal"),
             InlineKeyboardButton("🌐 Рынок",    callback_data="market_overview")],
            [InlineKeyboardButton("💎 Подписка", callback_data="show_plans"),
             InlineKeyboardButton("❓ Помощь",   callback_data="help")],
        ]
        await query.edit_message_text(
            "Главное меню / Main menu:",
            reply_markup=InlineKeyboardMarkup(kb)
        )

    # ── Помощь ──
    elif data == "help":
        from handlers.start import help_handler

        class FakeUpdate:
            effective_user = query.from_user
            message        = query.message

        await help_handler(FakeUpdate(), context)