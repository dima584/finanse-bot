"""
handlers/payments.py — ОПЛАТА (с поддержкой языков)
"""
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.ext import ContextTypes
from database import get_or_create_user, update_subscription
from config import SUBSCRIPTION_PLANS

ADMIN_USERNAME  = "GGhardaxxx"
USDT_TRC20_ADDR = "TEH6Td2UKTNy8YDKrjszsp7FcV5nzqu7uW"
USDT_ERC20_ADDR = "0x089fb77d825ff05084e296baf58318f4997bffa6"
BTC_ADDR        = "13nZE2QufYYDJrrJZNn9fD1R35hYkScQ7f"

STARS_PRICES = {"basic": 1500, "pro": 3800, "vip": 7700}

PLANS_TEXT = {
    "basic": ("⭐ Basic", "$19/mo", "5 signals/day • Crypto + Forex",
              "$19/мес", "5 сигналов/день • Крипто + Форекс"),
    "pro":   ("💎 Pro",   "$49/mo", "20 signals/day • All markets • Indicators",
              "$49/мес", "20 сигналов/день • Все рынки • Индикаторы"),
    "vip":   ("👑 VIP",  "$99/mo", "Unlimited • Auto-signals • Support",
              "$99/мес", "Безлимит • Автосигналы • Поддержка"),
}


def _plan_info(plan: str, lang: str) -> tuple:
    """Возвращает (emoji_name, price, features) на нужном языке"""
    row = PLANS_TEXT[plan]
    em_name = row[0]
    if lang == "en":
        return em_name, row[1], row[2]
    return em_name, row[3], row[4]


async def payment_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    is_query = update.callback_query is not None
    if is_query:
        await update.callback_query.answer()
        send = update.callback_query.edit_message_text
        tid  = update.callback_query.from_user.id
    else:
        send = update.message.reply_text
        tid  = update.effective_user.id

    user = get_or_create_user(tid)
    lang = user.get("language", "ru")
    cur  = user.get("subscription", "free")

    if lang == "en":
        title  = "💎 <b>SUBSCRIPTION PLANS</b>\n"
        footer = "\n🆓 Free: 3 signals per week"
    else:
        title  = "💎 <b>ТАРИФНЫЕ ПЛАНЫ</b>\n"
        footer = "\n🆓 Бесплатно: 3 сигнала в неделю"

    lines = [title]
    for pid in ("basic", "pro", "vip"):
        em_name, price, features = _plan_info(pid, lang)
        mark = " ✅" if pid == cur else ""
        lines.append(f"{em_name} — <b>{price}</b>{mark}\n  {features}\n")
    lines.append(footer)

    kb = [
        [InlineKeyboardButton("⭐ Basic $19", callback_data="pay_plan_basic"),
         InlineKeyboardButton("💎 Pro $49",   callback_data="pay_plan_pro")],
        [InlineKeyboardButton("👑 VIP $99",   callback_data="pay_plan_vip")],
    ]
    await send("\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))


async def choose_payment_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    plan = query.data.replace("pay_plan_", "")
    user = get_or_create_user(query.from_user.id)
    lang = user.get("language", "ru")
    em_name, price, _ = _plan_info(plan, lang)

    if lang == "en":
        title   = f"💳 <b>Payment: {em_name} — {price}</b>\n\nChoose payment method:"
        stars_l = "⭐ Telegram Stars"
        crypto_l= "🔶 Cryptocurrency (USDT/BTC)"
        card_l  = "💳 Bank card"
        back_l  = "◀️ Back"
    else:
        title   = f"💳 <b>Оплата: {em_name} — {price}</b>\n\nВыберите способ оплаты:"
        stars_l = "⭐ Telegram Stars"
        crypto_l= "🔶 Криптовалюта (USDT/BTC)"
        card_l  = "💳 Банковская карта"
        back_l  = "◀️ Назад"

    kb = [
        [InlineKeyboardButton(stars_l,  callback_data=f"pay_stars_{plan}")],
        [InlineKeyboardButton(crypto_l, callback_data=f"pay_crypto_{plan}")],
        [InlineKeyboardButton(card_l,   callback_data=f"pay_card_{plan}")],
        [InlineKeyboardButton(back_l,   callback_data="show_plans")],
    ]
    await query.edit_message_text(title, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))


async def pay_with_stars(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    plan = query.data.replace("pay_stars_", "")
    user = get_or_create_user(query.from_user.id)
    lang = user.get("language", "ru")
    em_name, price, features = _plan_info(plan, lang)
    stars = STARS_PRICES[plan]
    tid   = query.from_user.id

    if lang == "en":
        desc = f"Trading Bot — {em_name} for 30 days\n{features}"
    else:
        desc = f"Trading Bot — {em_name} на 30 дней\n{features}"

    await context.bot.send_invoice(
        chat_id=tid,
        title=f"Subscription {em_name}",
        description=desc,
        payload=f"sub_{plan}_{tid}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label=f"{em_name} 30 days", amount=stars)],
    )


async def pre_checkout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.pre_checkout_query.answer(ok=True)


async def successful_payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payload = update.message.successful_payment.invoice_payload
    plan    = payload.split("_")[1]
    tid     = update.effective_user.id
    user    = get_or_create_user(tid)
    lang    = user.get("language", "ru")

    update_subscription(tid, plan, days=30)
    em_name, price, _ = _plan_info(plan, lang)

    if lang == "en":
        text = (f"✅ <b>Payment successful!</b>\n\nPlan: <b>{em_name}</b>\n"
                f"Active for 30 days.\n\nPress 📊 Signal to get your first signal!")
    else:
        text = (f"✅ <b>Оплата прошла!</b>\n\nТариф: <b>{em_name}</b>\n"
                f"Активен 30 дней.\n\nНажмите 📊 Сигнал для первого сигнала!")

    await update.message.reply_text(text, parse_mode="HTML")


async def pay_with_crypto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    plan  = query.data.replace("pay_crypto_", "")
    user  = get_or_create_user(query.from_user.id)
    lang  = user.get("language", "ru")
    em_name, price, _ = _plan_info(plan, lang)
    usd   = SUBSCRIPTION_PLANS.get(plan, {}).get("price", 0)
    tid   = query.from_user.id

    if lang == "en":
        text = (
            f"🔶 <b>Crypto Payment</b>\n\n"
            f"Plan: <b>{em_name} — {price}</b>\n"
            f"Amount: <b>{usd} USDT</b>\n\n"
            f"<b>Wallets:</b>\n\n"
            f"USDT (TRC-20):\n<code>{USDT_TRC20_ADDR}</code>\n\n"
            f"USDT (ERC-20):\n<code>{USDT_ERC20_ADDR}</code>\n\n"
            f"BTC:\n<code>{BTC_ADDR}</code>\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"After transfer, send screenshot to @{ADMIN_USERNAME}\n"
            f"Your ID: <code>{tid}</code> | Plan: <b>{plan}</b>\n\n"
            f"⏱ Subscription activated within 1 hour."
        )
    else:
        text = (
            f"🔶 <b>Оплата криптовалютой</b>\n\n"
            f"Тариф: <b>{em_name} — {price}</b>\n"
            f"Сумма: <b>{usd} USDT</b>\n\n"
            f"<b>Кошельки:</b>\n\n"
            f"USDT (TRC-20):\n<code>{USDT_TRC20_ADDR}</code>\n\n"
            f"USDT (ERC-20):\n<code>{USDT_ERC20_ADDR}</code>\n\n"
            f"BTC:\n<code>{BTC_ADDR}</code>\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"После перевода отправьте скриншот @{ADMIN_USERNAME}\n"
            f"Ваш ID: <code>{tid}</code> | Тариф: <b>{plan}</b>\n\n"
            f"⏱ Подписка активируется в течение 1 часа."
        )

    kb = [
        [InlineKeyboardButton(f"📩 @{ADMIN_USERNAME}", url=f"https://t.me/{ADMIN_USERNAME}")],
        [InlineKeyboardButton("◀️ Back" if lang == "en" else "◀️ Назад",
                              callback_data=f"pay_plan_{plan}")],
    ]
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))


async def pay_with_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    plan  = query.data.replace("pay_card_", "")
    user  = get_or_create_user(query.from_user.id)
    lang  = user.get("language", "ru")
    em_name, price, _ = _plan_info(plan, lang)
    tid   = query.from_user.id

    if lang == "en":
        text = (
            f"💳 <b>Bank Card Payment</b>\n\n"
            f"Plan: <b>{em_name} — {price}</b>\n\n"
            f"Write to administrator @{ADMIN_USERNAME}\n\n"
            f"Include:\n• Your ID: <code>{tid}</code>\n• Plan: <b>{plan}</b>\n\n"
            f"Administrator will send payment details."
        )
    else:
        text = (
            f"💳 <b>Оплата банковской картой</b>\n\n"
            f"Тариф: <b>{em_name} — {price}</b>\n\n"
            f"Напишите администратору @{ADMIN_USERNAME}\n\n"
            f"Укажите:\n• Ваш ID: <code>{tid}</code>\n• Тариф: <b>{plan}</b>\n\n"
            f"Администратор пришлёт реквизиты."
        )

    kb = [
        [InlineKeyboardButton(f"📩 @{ADMIN_USERNAME}", url=f"https://t.me/{ADMIN_USERNAME}")],
        [InlineKeyboardButton("◀️ Back" if lang == "en" else "◀️ Назад",
                              callback_data=f"pay_plan_{plan}")],
    ]
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))