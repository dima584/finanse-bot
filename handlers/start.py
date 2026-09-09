"""
handlers/start.py — СТАРТ И ГЛАВНОЕ МЕНЮ
"""

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from database import get_or_create_user
from telegram import ReplyKeyboardMarkup


def get_main_keyboard(lang: str = "ru") -> ReplyKeyboardMarkup:
    """Постоянная нижняя клавиатура на нужном языке"""
    if lang == "en":
        buttons = [
            [KeyboardButton("💼 Statistics")],
            [KeyboardButton("📊 Signal"),      KeyboardButton("🌐 Market")],
            [KeyboardButton("💎 Subscription"), KeyboardButton("❓ Help")],
        ]
        placeholder = "Choose action or currency pair (e.g., BTC/USDT)"
    else:
        buttons = [
            [KeyboardButton("💼 Статистика")],
            [KeyboardButton("📊 Сигнал"),  KeyboardButton("🌐 Рынок")],
            [KeyboardButton("💎 Подписка"), KeyboardButton("❓ Помощь")],
        ]
        placeholder = "Выберите действие или введите валютную пару (например, BTC/USDT)"

    return ReplyKeyboardMarkup(
        keyboard=buttons,
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder=placeholder
    )


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user    = update.effective_user
    db_user = get_or_create_user(
        telegram_id=user.id,
        username=user.username,
        first_name=user.first_name
    )
    lang = db_user.get("language", "ru")

    # Отправляем клавиатуру сразу на сохранённом языке
    await update.message.reply_text(
        f"👋 Привет / Hello, <b>{user.first_name}</b>!\n\n"
        f"<b>Trading Signal Bot</b> 🤖\n\n"
        f"Выберите язык / Choose language:",
        parse_mode="HTML",
        reply_markup=get_main_keyboard(lang)  # Сразу правильный язык
    )

    # Кнопки выбора языка
    lang_kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🇷🇺 Русский", callback_data="set_lang_ru"),
        InlineKeyboardButton("🇬🇧 English", callback_data="set_lang_en"),
    ]])
    await update.message.reply_text(
        "🌍 Language / Язык:",
        reply_markup=lang_kb
    )


async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Работает и как команда и как вызов из button_handler через FakeUpdate
    if hasattr(update, 'effective_user') and update.effective_user:
        db_user = get_or_create_user(telegram_id=update.effective_user.id)
    else:
        db_user = {}
    lang = db_user.get("language", "ru")

    if lang == "en":
        text = (
            "❓ <b>HOW TO USE</b>\n\n"
            "📊 <b>Signal</b> — get a trading signal now\n"
            "🌐 <b>Market</b> — market overview and active signals\n"
            "💎 <b>Subscription</b> — plans and payments\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🟢 <b>LONG</b> = buy (price going up)\n"
            "🔴 <b>SHORT</b> = sell (price going down)\n\n"
            "💰 <b>Entry</b> = price to open the trade\n"
            "🛑 <b>Stop</b> = stop-loss level\n"
            "🎯 <b>TP1/TP2/TP3</b> = take profit targets\n\n"
            "<b>Tip:</b> take 50% profit at TP1, hold rest for TP2/TP3\n\n"
            "⚠️ Not financial advice. Manage your risks."
        )
    else:
        text = (
            "❓ <b>КАК ПОЛЬЗОВАТЬСЯ</b>\n\n"
            "📊 <b>Сигнал</b> — получить торговый сигнал\n"
            "🌐 <b>Рынок</b> — обзор пар и активные сигналы\n"
            "💎 <b>Подписка</b> — тарифы и оплата\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🟢 <b>LONG</b> = покупать (ожидаем рост)\n"
            "🔴 <b>SHORT</b> = продавать (ожидаем падение)\n\n"
            "💰 <b>Вход</b> = цена для входа в сделку\n"
            "🛑 <b>Стоп</b> = стоп-лосс\n"
            "🎯 <b>TP1/TP2/TP3</b> = цели прибыли\n\n"
            "<b>Совет:</b> фиксируй 50% на TP1, остальное до TP2/TP3\n\n"
            "⚠️ Это не финансовый совет. Управляй рисками."
        )

    await update.message.reply_text(text, parse_mode="HTML")