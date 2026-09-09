"""
handlers/market.py — ОБЗОР РЫНКА (с языками)
"""
import asyncio
from telegram import Update
from telegram.ext import ContextTypes
from analysis.technical import analyze_symbol
from utils.formatter import format_market_overview
from config import CRYPTO_PAIRS
from database import get_or_create_user


async def market_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_or_create_user(update.effective_user.id)
    lang = user.get("language", "ru")

    wait_text = f"🌐 Scanning market...\nAnalysing {len(CRYPTO_PAIRS[:8])} pairs, ~20 sec" \
                if lang == "en" else \
                f"🌐 Сканирую рынок...\nАнализирую {len(CRYPTO_PAIRS[:8])} пар, ~20 сек"

    wait = await update.message.reply_text(wait_text)

    tasks   = [analyze_symbol(pair, "1h") for pair in CRYPTO_PAIRS[:8]]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    valid   = [r for r in results if r and not isinstance(r, Exception)]

    await wait.delete()
    await update.message.reply_text(
        format_market_overview(valid, lang=lang),
        parse_mode="HTML"
    )