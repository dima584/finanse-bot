import sys
import os
import asyncio
from database import get_daily_stats
from database import get_active_signals
from executor import execute_binance_trade
from database import get_signal_by_id, update_signal_status
import logging
from analysis.technical import analyze_symbol
from database import get_daily_stats, get_active_signals, update_user_deposit, update_user_risk, get_user
from utils.formatter import format_signal_message
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, PreCheckoutQueryHandler, filters, ContextTypes
)
import utils.scheduler as bot_scheduler
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

if sys.platform == "win32":
    os.environ["PYTHONUTF8"] = "1"
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, PreCheckoutQueryHandler, filters
)

# Импорты ваших обработчиков
from utils.scheduler    import setup_scheduler
from handlers.start     import start_handler, help_handler
from handlers.signal    import signal_handler
from handlers.subscribe import subscribe_handler, text_button_handler, button_handler
from handlers.admin     import admin_handler, broadcast_handler, give_sub_handler
from handlers.market    import market_handler
from handlers.payments  import pre_checkout_handler, successful_payment_handler
from database           import init_database
from config             import BOT_TOKEN

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

async def set_deposit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        val = float(context.args[0])
        update_user_deposit(update.effective_user.id, val)
        await update.message.reply_text(f"💰 Размер твоего депозита установлен: <b>${val:,.2f}</b>", parse_mode="HTML")
    except (IndexError, ValueError):
        await update.message.reply_text("⚠️ Использование: /set_deposit <сумма>\nПример: /set_deposit 500")

async def set_risk_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        val = float(context.args[0].replace('%', ''))
        update_user_risk(update.effective_user.id, val)
        await update.message.reply_text(f"⚖️ Твой риск на одну сделку установлен: <b>{val}%</b>", parse_mode="HTML")
    except (IndexError, ValueError):
        await update.message.reply_text("⚠️ Использование: /set_risk <процент>\nПример: /set_risk 1.5")

async def set_limit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Проверка на админа (замени ADMIN_ID на свой реальный)
    ADMIN_ID = 983710534
    if update.message.from_user.id != ADMIN_ID:
        return

    try:
        # Берем число из сообщения (например, /set_limit 45)
        new_limit = float(context.args[0])
        bot_scheduler.GLOBAL_CONF_LIMIT = new_limit
        await update.message.reply_text(f"✅ Минимальный порог уверенности для автосигналов изменен на {new_limit}%")
    except (IndexError, ValueError):
        await update.message.reply_text("⚠️ Использование: /set_limit <число>\nПример: /set_limit 40")

async def daily_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выдает статистику по закрытым сделкам (в долларах) и показывает активные"""
    closed_signals = get_daily_stats()
    active_signals = get_active_signals()
    
    if not closed_signals and not active_signals:
        message_obj = update.message if update.message else update.callback_query.message
        await message_obj.reply_text("📊 Нет активных или закрытых сделок за сегодня.")
        return

    total_pnl_usd = 0.0
    wins = 0
    losses = 0
    be = 0
    
    msg = "📊 <b>Сводка по сигналам (Реальный PnL)</b>\n\n"
    
    # --- БЛОК 1: Закрытые сделки ---
    if closed_signals:
        msg += "📁 <b>Закрытые сделки (за сегодня):</b>\n"
        for sig in closed_signals:
            sym = sig['symbol']
            st = sig['status']
            
            # Извлекаем pnl_usd (если это старая сделка без PnL, берем 0.0)
            pnl = sig.get('pnl_usd', 0.0)
            
            if st == 'closed_tp':
                wins += 1
                total_pnl_usd += pnl
                msg += f"✅ {sym}: +${pnl:.2f} (Фулл Тейк)\n"
            elif st == 'closed_be':
                be += 1
                total_pnl_usd += pnl
                msg += f"🛡 {sym}: +${pnl:.2f} (Безубыток)\n"
            elif st == 'closed_sl':
                losses += 1
                total_pnl_usd += pnl # pnl уже отрицательный
                msg += f"❌ {sym}: -${abs(pnl):.2f} (Стоп-лосс)\n"

        winrate = ((wins + be) / len(closed_signals)) * 100 if closed_signals else 0
        msg += "━━━━━━━━━━━━━━━━━━\n"
        
        # Красивый вывод итоговой суммы
        sign = "+$" if total_pnl_usd >= 0 else "-$"
        msg += f"💵 <b>Итого за день:</b> {sign}{abs(total_pnl_usd):.2f}\n"
        msg += f"🎯 <b>Винрейт:</b> {winrate:.0f}%\n"
        msg += f"⚖️ <b>Сделок (Плюс/Б.У./Минус):</b> {wins} / {be} / {losses}\n\n"

    # --- БЛОК 2: Открытые сделки ---
    keyboard = [] # Список для кнопок
    
    if active_signals:
        msg += "🔄 <b>Открытые сделки (в процессе):</b>\n"
        for sig in active_signals:
            sym = sig['symbol']
            st = sig['status']
            sid = sig['id'] # Получаем ID сделки из базы
            
            if st == 'active':
                msg += f"⏳ {sym} (Ожидает целей)\n"
            elif st == 'active_tp1':
                msg += f"🎯 {sym} (Взят TP1, стоп в Б.У.)\n"
            elif st == 'active_tp2':
                msg += f"🎯🎯 {sym} (Взят TP2, ждем TP3)\n"
                
            # Добавляем кнопку для каждой активной монеты
            keyboard.append([InlineKeyboardButton(f"🔍 Мониторинг {sym}", callback_data=f"mon_{sid}")])

    # Формируем клавиатуру, если есть кнопки
    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None

    message_obj = update.message if update.message else update.callback_query.message
    await message_obj.reply_text(msg, parse_mode="HTML", reply_markup=reply_markup)


from database import get_signal_by_id
from utils.tracker import fetch_current_prices
from telegram import InlineKeyboardMarkup, InlineKeyboardButton

async def monitor_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробник кнопок моніторингу в реальному часі"""
    query = update.callback_query
    await query.answer() # Закриваємо "годинник" завантаження на кнопці
    
    data = query.data
    # Перевіряємо, чи це кнопка моніторингу
    if data.startswith("mon_"):
        signal_id = int(data.split("_")[1])
        sig = get_signal_by_id(signal_id)
        
        if not sig:
            await query.message.reply_text("❌ Сигнал не знайдено або він вже видалений з бази.")
            return
            
        sym = sig['symbol']
        clean_sym = sym.replace("/", "")
        entry = sig['entry_price']
        direction = sig['direction']
        
        # Отримуємо ціну з Binance
        prices = await fetch_current_prices([clean_sym])
        curr_price = prices.get(clean_sym)
        
        if not curr_price:
            await query.message.reply_text(f"⚠️ Не вдалося отримати поточну ціну для {sym} з біржі.")
            return
            
        # Розраховуємо PnL у відсотках
        if direction == 'LONG':
            pct = (curr_price - entry) / entry * 100
        else:
            pct = (entry - curr_price) / entry * 100
            
        status_emoji = "🟢" if pct > 0 else "🔴"
        
        # 1. Перевіряємо, чи не зачепило лімітку прямо зараз
        if sig['status'] == 'pending':
            if (direction == 'LONG' and curr_price <= entry) or (direction == 'SHORT' and curr_price >= entry):
                # Лімітка спрацювала! Оновлюємо статус у базі
                sig['status'] = 'active'
                update_signal_status(signal_id, 'active')

        # 2. Формуємо повідомлення залежно від статусу
        if sig['status'] == 'pending':
            # Рахуємо, скільки відсотків ціні залишилося пройти до лімітки
            dist_pct = abs(curr_price - entry) / curr_price * 100
            msg = (
                f"⏳ <b>Очікування лімітки {sym} ({direction})</b>\n\n"
                f"🎯 Лімітний ордер: <b>{entry}</b>\n"
                f"🔖 Поточна ціна: <b>{curr_price}</b>\n"
                f"📏 До входу залишилось: <b>{dist_pct:.2f}%</b>\n\n"
                f"<i>Статус у базі: {sig['status']}</i>"
            )
        elif sig['status'] == 'active':
            msg = (
                f"🔍 <b>Лайв-моніторинг {sym} ({direction})</b>\n\n"
                f"🎯 Точка входу: <b>{entry}</b>\n"
                f"🔖 Поточна ціна: <b>{curr_price}</b>\n"
                f"📈 Поточний PnL: {status_emoji} <b>{pct:+.2f}%</b>\n\n"
                f"<i>Статус у базі: {sig['status']}</i>"
            )
            
        # Кнопка для ручного оновлення
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🔄 Оновити дані", callback_data=f"mon_{signal_id}")
        ]])
    
        # Редагуємо поточне повідомлення, щоб не спамити новими в чат
        await query.edit_message_text(msg, parse_mode="HTML", reply_markup=kb)

# ... (здесь заканчивается код monitor_callback) ...

async def trade_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатия на кнопку 'Открыть сделку'"""
    query = update.callback_query
    await query.answer() 
    
    data = query.data
    
    if data.startswith("trade_"):
        signal_id = int(data.split("_")[1])
        
        # Редагуем сообщение, чтобы убрать кнопку (защита от двойного нажатия)
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("⏳ Отправляю ордера на Binance...")
        
        # Вызываем логику торговли из executor.py
        result = await execute_binance_trade(signal_id, update.effective_user.id)
        
        if result['success']:
            await query.message.reply_text(f"✅ Сделка открыта!\nОбъем: {result['qty']} монет.")
        else:
            await query.message.reply_text(f"❌ Ошибка: {result['error']}")



async def main():
    init_database()
    logger.info("Starting Trading Signal Bot...")

    app = Application.builder().token(BOT_TOKEN).build()

    # 1. Регистрация команд
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("signal", signal_handler))
    app.add_handler(CommandHandler("market", market_handler))
    app.add_handler(CommandHandler("subscribe", subscribe_handler))
    app.add_handler(CommandHandler("help", help_handler))
    app.add_handler(CommandHandler("set_deposit", set_deposit_command))
    app.add_handler(CommandHandler("set_risk", set_risk_command))
    
    # 2. Админские команды
    app.add_handler(CommandHandler("admin", admin_handler))
    app.add_handler(CommandHandler("broadcast", broadcast_handler))
    app.add_handler(CommandHandler("give_sub", give_sub_handler))
    app.add_handler(CommandHandler("set_limit", set_limit_command))
    app.add_handler(CommandHandler("stats", daily_stats_command))
    app.add_handler(MessageHandler(filters.Regex(r'^💼 (Статистика|Statistics)$'), daily_stats_command))
    # 3. Оплата
    app.add_handler(PreCheckoutQueryHandler(pre_checkout_handler))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))

    # 4. Текстовые кнопки меню (из Canvas)
    app.add_handler(MessageHandler(filters.Regex(r'^[A-Za-z/]{2,12}$'), live_crypto_analysis))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_button_handler))
    # 5. Inline кнопки (из Canvas)
    app.add_handler(CallbackQueryHandler(monitor_callback, pattern=r"^mon_")) # Додати цей рядок
    # --- ДОБАВИТЬ ЭТУ СТРОКУ ---
    app.add_handler(CallbackQueryHandler(trade_button_handler, pattern=r"^trade_"))
    app.add_handler(CallbackQueryHandler(button_handler))
    # ---------------------------
    
    

    # 5. Inline кнопки (из Canvas)

    # Планировщик
    scheduler = setup_scheduler(app)

    async with app:
        scheduler.start()
        logger.info("Bot is running!")
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        try:
            await asyncio.Event().wait()
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            scheduler.shutdown()
            await app.updater.stop()
            await app.stop()


async def live_crypto_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Берем текст, убираем пробелы и делаем большими буквами
    user_input = update.message.text.strip().upper()
    
    # Формируем правильный тикер со слэшем (BTC -> BTC/USDT)
    if "USDT" not in user_input:
        ticker = f"{user_input}/USDT"
    elif "/" not in user_input:
        ticker = user_input.replace("USDT", "/USDT")
    else:
        ticker = user_input
    
    status_msg = await update.message.reply_text(f"⏳ Анализирую <b>{ticker}</b> в реальном времени (1h)...", parse_mode="HTML")
    
    try:
        # Теперь сканируем 15-минутку (скальпинг), как и авто-бот
        signal_data = await analyze_symbol(ticker, timeframe="15m") 
        
        # Снижаем порог до 55%, чтобы он совпадал с GLOBAL_CONF_LIMIT
        if signal_data and signal_data.get('confidence', 0) >= 55:
            # Получаем настройки конкретного юзера
            db_user = get_user(update.effective_user.id) or {}
            u_dep = db_user.get('deposit', 0.0)
            u_risk = db_user.get('risk_pct', 0.0)
            
# --- БЛОКИРОВКА: ЕСЛИ ДАННЫЕ НЕ ЗАДАНЫ ---
            if u_dep <= 0 or u_risk <= 0:
                await status_msg.edit_text(
                    "⚠️ <b>Обязательная настройка</b>\n\n"
                    "Для расчета объема позиции и получения сигналов укажи свой рабочий депозит и риск на сделку:\n\n"
                    "👉 <code>/set_deposit 500</code>\n"
                    "👉 <code>/set_risk 2</code>",
                    parse_mode="HTML"
                )
                return
            # ----------------------------------------

            reply_text = format_signal_message(
                signal_data, 
                include_indicators=True,
                deposit=u_dep,
                risk_pct=u_risk
            )
            await status_msg.edit_text(reply_text, parse_mode="HTML")
        else:
            await status_msg.edit_text(f"❌ На данный момент уверенного сигнала (>=55%) по паре <b>{ticker}</b> нет.", parse_mode="HTML")

    except Exception as e:
        await status_msg.edit_text(f"⚠️ Ошибка при анализе монеты <b>{ticker}</b>. Возможно, такой пары нет на бирже.\n\nДетали: {e}", parse_mode="HTML")

if __name__ == "__main__":
    asyncio.run(main())