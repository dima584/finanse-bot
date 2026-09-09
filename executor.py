import ccxt.async_support as ccxt_async
import os
from database import get_signal_by_id # Твоя функція для отримання даних з БД

async def execute_binance_trade(signal_id: int):
    # Отримуємо сигнал з твоєї БД
    sig = get_signal_by_id(signal_id) 
    if not sig:
        return {"success": False, "error": "Сигнал не знайдено в базі"}

    symbol = sig['symbol']
    direction = sig['direction']
    sl_price = sig['stop_loss']
    tps = sig['take_profit'] # Список з 3 тейків

    # Налаштування API (ключі мають бути у .env файлі)
    exchange = ccxt_async.binance({
        'apiKey': os.getenv('BINANCE_API_KEY'),
        'secret': os.getenv('BINANCE_SECRET_KEY'),
        'enableRateLimit': True,
        'options': {
            'defaultType': 'future' # Обов'язково вказуємо ф'ючерси
        }
    })

    try:
        # 1. Встановлюємо плече (10x)
        await exchange.set_leverage(10, symbol)
        
        # 2. Розраховуємо об'єм позиції
        margin_usd = 5.0
        leverage = 10
        total_volume_usd = margin_usd * leverage
        
        # Дізнаємось поточну ціну для розрахунку кількості монет
        ticker = await exchange.fetch_ticker(symbol)
        current_price = ticker['last']
        
        raw_qty = total_volume_usd / current_price
        
        # Округлюємо кількість монет під вимоги біржі (щоб не було помилок precision)
        markets = await exchange.load_markets()
        qty = exchange.amount_to_precision(symbol, raw_qty)
        qty = float(qty)

        side = 'buy' if direction == 'LONG' else 'sell'
        reverse_side = 'sell' if direction == 'LONG' else 'buy'

        # 3. Відкриваємо позицію (Market order)
        entry_order = await exchange.create_market_order(
            symbol=symbol,
            side=side,
            amount=qty
        )

        # 4. Виставляємо Stop-Loss (Stop Market з reduceOnly)
        await exchange.create_order(
            symbol=symbol,
            type='stop_market',
            side=reverse_side,
            amount=qty,
            price=sl_price,
            params={'stopPrice': sl_price, 'reduceOnly': True}
        )

        # 5. Виставляємо сітку Take-Profit (ділимо об'єм на 3 частини)
        # Наприклад: 30% на TP1, 30% на TP2, 40% на TP3
        tp_amounts = [
            float(exchange.amount_to_precision(symbol, qty * 0.3)),
            float(exchange.amount_to_precision(symbol, qty * 0.3)),
        ]
        tp_amounts.append(round(qty - tp_amounts[0] - tp_amounts[1], 4)) # Залишок для TP3

        for i, tp_price in enumerate(tps):
            if tp_amounts[i] > 0:
                await exchange.create_order(
                    symbol=symbol,
                    type='take_profit_market',
                    side=reverse_side,
                    amount=tp_amounts[i],
                    price=tp_price,
                    params={'stopPrice': tp_price, 'reduceOnly': True}
                )

        return {"success": True, "qty": qty}

    except Exception as e:
        return {"success": False, "error": str(e)}
    
    finally:
        await exchange.close()