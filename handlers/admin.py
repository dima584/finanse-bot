"""
=============================================================
  handlers/admin.py — АДМИН-КОМАНДЫ
=============================================================
Только для администраторов: статистика, рассылка, управление
подписками пользователей вручную.
"""

from telegram import Update
from telegram.ext import ContextTypes

from database import get_all_users, update_subscription
from config import ADMIN_IDS


def is_admin(telegram_id: int) -> bool:
    """Проверить является ли пользователь администратором"""
    return telegram_id in ADMIN_IDS


async def admin_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /admin — статистика бота"""
    
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Нет доступа.")
        return
    
    users = get_all_users()
    
    # Считаем статистику
    total    = len(users)
    free_u   = sum(1 for u in users if u["subscription"] == "free")
    basic_u  = sum(1 for u in users if u["subscription"] == "basic")
    pro_u    = sum(1 for u in users if u["subscription"] == "pro")
    vip_u    = sum(1 for u in users if u["subscription"] == "vip")
    
    # Месячный доход
    revenue = (basic_u * 19) + (pro_u * 49) + (vip_u * 99)
    
    text = f"""
👨‍💼 <b>ПАНЕЛЬ АДМИНИСТРАТОРА</b>

👥 Пользователей всего: {total}
  🆓 Бесплатных: {free_u}
  ⭐ Basic:       {basic_u}
  💎 Pro:         {pro_u}
  👑 VIP:         {vip_u}

💰 Расчётный доход: ~${revenue}/мес

━━━━━━━━━━━━━━━━━━━━
<b>Команды:</b>
/broadcast [текст] — рассылка всем
/give_sub [user_id] [plan] [дни] — выдать подписку
    """
    
    await update.message.reply_text(text, parse_mode="HTML")


async def broadcast_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отправляет сообщение всем пользователям бота."""
    if not is_admin(update.effective_user.id):
        return
    
    if not context.args:
        await update.message.reply_text("Использование: /broadcast Ваш текст здесь")
        return
    
    message_text = " ".join(context.args)
    users = get_all_users()
    sent, failed = 0, 0
    
    status_msg = await update.message.reply_text(f"📤 Отправляю {len(users)} пользователям...")
    
    for user in users:
        try:
            await context.bot.send_message(
                chat_id=user["telegram_id"],
                text=f"📢 <b>Сообщение от команды бота:</b>\n\n{message_text}",
                parse_mode="HTML"
            )
            sent += 1
        except Exception:
            failed += 1
    
    await status_msg.edit_text(f"✅ Рассылка завершена!\nОтправлено: {sent}\nОшибок: {failed}")

async def give_sub_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выдача VIP-подписки пользователю (для тестов и администрирования)"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Нет доступа.")
        return

    try:
        target_id = int(context.args[0])
        plan = context.args[1].lower()
        days = int(context.args[2])
    except (IndexError, ValueError):
        await update.message.reply_text("Использование: /give_sub [telegram_id] [basic/pro/vip] [дни]")
        return

    if plan not in ["free", "basic", "pro", "vip"]:
        await update.message.reply_text("❌ Неверный план. Доступны: free, basic, pro, vip")
        return

    update_subscription(target_id, plan, days)
    await update.message.reply_text(f"✅ Пользователю <code>{target_id}</code> успешно выдан тариф <b>{plan.upper()}</b> на {days} дней!", parse_mode="HTML")