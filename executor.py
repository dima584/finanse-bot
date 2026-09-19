import ccxt.async_support as ccxt_async
import os
from database import get_signal_by_id, get_user

async def execute_binance_trade(signal_id: int, telegram_user_id: int):
    # Отримуємо сигнал та дані користувача з БД
    sig = get_signal_by_id(signal_id)
    user = get_user(telegram_user_id)
    
    if not sig:
        return {"success": False, "error": "Сигнал не знайдено в базі"}
    if not user:
        return {"success": False, "error": "Користувача не знайдено"}

    deposit = user.get('deposit', 0.0)
    risk_pct = user.get('risk_pct', 0.0)
    
    if deposit <= 0 or risk_pct <= 0:
        return {"success": False, "error": "Не налаштований депозит або ризик (/set_deposit, /set_risk)"}

    symbol = sig['symbol']
    direction = sig['direction']
    entry_price = float(sig['entry_price'])  # З нашими змінами це вже limit_entry
    sl_price = float(sig['stop_loss'])

    # Жорсткі налаштування для автоматики
    leverage = 10
    target_roi = 0.20  # +20% ROI

    # Налаштування API (ключі мають бути у .env файлі)
    exchange = ccxt_async.binance({
        'apiKey': os.getenv('BINANCE_API_KEY'),
        'secret': os.getenv('BINANCE_API_SECRET'),
        'enableRateLimit': True,
        'options': {
            'defaultType': 'future' # Обов'язково вказуємо ф'ючерси
        }
    })

    try:
        # Завантажуємо ринки для правильного округлення
        await exchange.load_markets()
        
        # 1. Встановлюємо плече (10x)
        await exchange.set_leverage(leverage, symbol)
        
        # 2. Розрахунок Take-Profit (+20% ROI)
        # Формула: Рух ціни = Цільовий ROI / Плече (0.20 / 10 = 0.02 або 2%)
        price_move = target_roi / leverage
        if direction == 'LONG':
            tp_price = entry_price * (1 + price_move)
        else:
            tp_price = entry_price * (1 - price_move)

        # 3. Розраховуємо об'єм позиції за Ризик-Менеджментом
        risk_usd = deposit * (risk_pct / 100.0)
        price_distance = abs(entry_price - sl_price)
        
        if price_distance == 0:
            return {"success": False, "error": "Дистанція до стоп-лосу дорівнює нулю"}
            
        raw_qty = risk_usd / price_distance
        
        # Округлюємо під вимоги біржі
        qty = float(exchange.amount_to_precision(symbol, raw_qty))
        entry_price = float(exchange.price_to_precision(symbol, entry_price))
        sl_price = float(exchange.price_to_precision(symbol, sl_price))
        tp_price = float(exchange.price_to_precision(symbol, tp_price))

        side = 'buy' if direction == 'LONG' else 'sell'
        reverse_side = 'sell' if direction == 'LONG' else 'buy'

        # 4. Відкриваємо ЛІМІТНУ позицію замість Market
        await exchange.create_order(
            symbol=symbol,
            type='limit',
            side=side,
            amount=qty,
            price=entry_price,
            params={'timeInForce': 'GTC'}
        )

        # 5. Виставляємо Stop-Loss (Stop Market з reduceOnly)
        await exchange.create_order(
            symbol=symbol,
            type='stop_market',
            side=reverse_side,
            amount=qty,
            params={'stopPrice': sl_price, 'reduceOnly': True}
        )

        # 6. Виставляємо ОДИН Take-Profit на +20% ROI (Take Profit Market з reduceOnly)
        await exchange.create_order(
            symbol=symbol,
            type='take_profit_market',
            side=reverse_side,
            amount=qty,
            params={'stopPrice': tp_price, 'reduceOnly': True}
        )

        return {"success": True, "qty": qty, "entry": entry_price, "tp": tp_price}

    except Exception as e:
        return {"success": False, "error": str(e)}
    
    finally:
        await exchange.close()