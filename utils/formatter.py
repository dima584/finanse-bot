"""
=============================================================
  utils/formatter.py — ФОРМАТИРОВАНИЕ СООБЩЕНИЙ
=============================================================
Превращает сырые данные анализа в красивые сообщения для Telegram.
Теперь с поддержкой мультиязычности (i18n).
"""

from datetime import datetime
from utils.i18n import _


def format_signal_message(signal: dict, include_indicators: bool = False, lang: str = "ru", deposit: float = 1000.0, risk_pct: float = 1.0) -> str:
    """Форматирует сигнал в красивое Telegram-сообщение на выбранном языке."""
    
    symbol    = signal["symbol"]
    direction = signal["direction"]
    entry     = signal["entry_price"]
    sl        = signal["stop_loss"]
    tps       = signal["take_profit"]
    conf      = signal["confidence"]
    risk      = signal["risk_score"]
    rr        = signal["risk_reward"]
    
    # Иконки направления
    dir_emoji = "🟢" if direction == "LONG" else "🔴"
    dir_text  = _("long", lang) if direction == "LONG" else _("short", lang)
    
    # Бар уверенности (визуализация)
    filled  = int(conf / 10)
    bar     = "█" * filled + "░" * (10 - filled)
    
    # Риск цветом
    if risk <= 3:
        risk_text = f"🟢 {_('risk_low', lang)} ({risk}/10)"
    elif risk <= 6:
        risk_text = f"🟡 {_('risk_med', lang)} ({risk}/10)"
    else:
        risk_text = f"🔴 {_('risk_high', lang)} ({risk}/10)"
    
    # Форматирование цены (без лишних нулей)
    def fmt_price(p):
        if p > 1000:
            return f"${p:,.2f}"
        elif p > 1:
            return f"${p:.4f}"
        else:
            return f"${p:.6f}"
    
    limit_p = signal.get("limit_entry", entry)

    # --- БЛОК РАСЧЕТА РИСК-МЕНЕДЖМЕНТА ---
    risk_amount_usd = deposit * (risk_pct / 100.0)
    sl_pct = abs(limit_p - sl) / limit_p if limit_p > 0 else 0.001
    
    if sl_pct > 0:
        position_size_usd = risk_amount_usd / sl_pct
        position_size_coins = position_size_usd / limit_p
    else:
        position_size_usd = 0
        position_size_coins = 0

    # Считаем профит в долларах
    profit_tp1 = position_size_coins * abs(tps[0] - limit_p)
    profit_tp2 = position_size_coins * abs(tps[1] - limit_p)
    profit_tp3 = position_size_coins * abs(tps[2] - limit_p)
    # --- КОНЕЦ БЛОКА РАСЧЕТОВ ---
    
    msg = f"""
╔══════════════════════════╗
║  {dir_emoji}  {_('signal_title', lang)}       ║
╚══════════════════════════╝

{dir_text}
💎 {_('pair', lang)}: <b>{symbol}</b>
⏱ {_('timeframe', lang)}: <b>{signal['timeframe']}</b>

━━━━━━━━━━━━━━━━━━━━━━━━━━

🔖 <b>Текущая цена:</b>  {fmt_price(entry)}
🛒 <b>Вход (Limit):</b>  {fmt_price(limit_p)}
🛑 <b>{_('stop', lang)}:</b>  {fmt_price(sl)}

🎯 <b>{_('tp1', lang)}:</b>  {fmt_price(tps[0])}  <i>{_('tp1_desc', lang)}</i>
🎯 <b>{_('tp2', lang)}:</b>  {fmt_price(tps[1])}  <i>{_('tp2_desc', lang)}</i>
🎯 <b>{_('tp3', lang)}:</b>  {fmt_price(tps[2])}  <i>{_('tp3_desc', lang)}</i>

━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 <b>{_('confidence', lang)}:</b>  {conf:.0f}%
{bar}

💼 <b>Объем позиции:</b> ${position_size_usd:.2f} 
🪙 <b>Купить монет:</b> {position_size_coins:.4f} {symbol.split('/')[0] if '/' in symbol else symbol.replace('USDT', '')}
📉 <b>Риск в сделке:</b> -${risk_amount_usd:.2f} ({risk_pct}%)

━━━━━━━━━━━━━━━━━━━━━━━━━━

⚖️ <b>{_('rr', lang)}:</b>  1:{rr}
🔰 <b>{_('risk_level', lang)}:</b>  {risk_text}"""

    # Добавляем индикаторы (для Pro/VIP)
    if include_indicators and "indicators" in signal:
        ind = signal["indicators"]
        msg += f"""

━━━━━━━━━━━━━━━━━━━━━━━━━━
📈 <b>{_('indicators', lang)}:</b>
  RSI:        {ind['rsi']:.1f}
  MACD:       {'▲' if ind['macd'] > ind['macd_signal'] else '▼'}  {ind['macd']:.4f}
  EMA 50/200: {ind['ema50']:.2f} / {ind['ema200']:.2f}
  {_('volume', lang)}:      {ind['volume_ratio']:.1f} {_('volume_desc', lang)}"""

    # Сигналы которые сработали
    # Сигналы которые сработали
    if "signals" in signal and signal["signals"]:
        msg += f"\n\n🔍 <b>{_('reasons', lang)}:</b>"
        for s in signal["signals"][:4]:  # Максимум 4
            # Экранируем знаки больше/меньше для Telegram HTML
            safe_s = s.replace('<', '&lt;').replace('>', '&gt;')
            msg += f"\n  • {safe_s}"
    
    msg += f"""

━━━━━━━━━━━━━━━━━━━━━━━━━━
<i>{_('disclaimer', lang)}</i>
🕐 {datetime.now().strftime('%d.%m.%Y %H:%M')} UTC
"""

    nls_tag = signal.get("nls_tag", "⚖️ NEUTRAL")
    nls_score = signal.get("nls_score", 0.0)
    
    # Визуальный блок NLS
    msg += f"<b>🧠 Квантовый NLS Фильтр:</b> {nls_tag}\n"
    if "BOOST" in nls_tag or "DANGER" in nls_tag:
        msg += f"├ Индекс давления: <b>{nls_score}</b>\n"
        msg += f"├ Ускорение ордеров: {signal.get('nls_cvd_accel', 0.0)}%\n"
        msg += f"└ Давление стакана: {signal.get('nls_skewness', 0.0)}%\n"
    msg += "\n"
    return msg.strip()



def format_market_overview(analyses: list, lang: str = "ru") -> str:
    """Форматирует обзор рынка — список всех активных сигналов"""
    
    if not analyses:
        return _("market_empty", lang)
    
    longs  = [a for a in analyses if a["direction"] == "LONG"]
    shorts = [a for a in analyses if a["direction"] == "SHORT"]
    
    msg = f"📊 <b>{_('market_title', lang)}</b>\n"
    msg += f"🕐 {datetime.now().strftime('%d.%m.%Y %H:%M')}\n\n"
    
    if longs:
        msg += f"🟢 <b>{_('buy_signals', lang)}:</b>\n"
        for a in sorted(longs, key=lambda x: x["confidence"], reverse=True):
            msg += f"  • {a['symbol']} — {a['confidence']:.0f}%\n"
    
    if shorts:
        msg += f"\n🔴 <b>{_('sell_signals', lang)}:</b>\n"
        for a in sorted(shorts, key=lambda x: x["confidence"], reverse=True):
            msg += f"  • {a['symbol']} — {a['confidence']:.0f}%\n"
    
    msg += f"\n📈 {_('total_signals', lang)}: {len(analyses)}"
    return msg


def format_subscription_info(user: dict) -> str:
    """Форматирует информацию о подписке пользователя (язык берется прямо из словаря user)"""
    
    lang = user.get("language", "ru")
    plan = user.get("subscription", "free")
    
    plan_names = {
        "free":  _("free", lang),
        "basic": _("basic", lang),
        "pro":   _("pro", lang),
        "vip":   _("vip", lang)
    }
    
    plan_limits = {
        "free":  _("limit_free", lang),
        "basic": _("limit_basic", lang),
        "pro":   _("limit_pro", lang),
        "vip":   _("unlimited", lang)
    }
    
    msg = f"👤 <b>{_('sub_title', lang)}</b>\n\n"
    msg += f"📦 {_('plan', lang)} <b>{plan_names.get(plan, plan)}</b>\n"
    msg += f"📊 {_('limit', lang)} {plan_limits.get(plan, '-')}\n"
    
    if user.get("sub_expires_at"):
        from datetime import datetime
        exp = datetime.fromisoformat(user["sub_expires_at"])
        days_left = (exp - datetime.now()).days
        msg += f"📅 {_('expires', lang)} {exp.strftime('%d.%m.%Y')} ({days_left} {_('days_left', lang)})\n"
    
    return msg

def format_daily_stats_pnl(closed_trades: list, lang: str = "ru") -> str:
    """
    Формирует красивую сводку по закрытым сделкам с реальным PnL (в долларах).
    closed_trades — это список кортежей из БД: [(symbol, pnl_usd, status), ...]
    """
    if not closed_trades:
        return "📊 <b>Сводка по сигналам</b>\n\nЗа сегодня закрытых сделок нет."

    # Считаем общий итог в долларах
    total_pnl_usd = sum(trade[1] for trade in closed_trades if trade[1] is not None)
    
    # Считаем статистику для винрейта
    wins = sum(1 for trade in closed_trades if trade[1] and trade[1] > 0)
    losses = sum(1 for trade in closed_trades if trade[1] and trade[1] < 0)
    be = sum(1 for trade in closed_trades if trade[1] == 0)
    total_trades = len(closed_trades)
    
    winrate = (wins / total_trades) * 100 if total_trades > 0 else 0

    msg = "📊 <b>Сводка по сигналам (Реальный PnL)</b>\n\n"
    msg += "📁 <b>Закрытые сделки:</b>\n"
    
    for trade in closed_trades:
        symbol = trade[0]
        pnl = trade[1] if trade[1] is not None else 0.0
        
        # Определяем иконку и знак
        if pnl > 0:
            icon = "✅"
            sign = "+$"
        elif pnl < 0:
            icon = "❌"
            sign = "-$"
        else:
            icon = "🛡"
            sign = "$"
            
        msg += f"{icon} {symbol}: {sign}{abs(pnl):.2f}\n"

    msg += "\n━━━━━━━━━━━━━━━━━━\n"
    msg += f"💵 <b>Итого за день:</b> {'+$' if total_pnl_usd > 0 else '-$'}{abs(total_pnl_usd):.2f}\n"
    msg += f"🎯 <b>Винрейт:</b> {winrate:.0f}%\n"
    msg += f"⚖️ <b>Сделок (Плюс/Б.У./Минус):</b> {wins} / {be} / {losses}\n"
    
    return msg