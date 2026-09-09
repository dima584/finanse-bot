"""
Запусти на сервере: python3 /tmp/quick_debug.py
Покажет на каком именно фильтре блокируются сигналы
"""

import sys
import os

# Поднимаемся на одну папку вверх (в корень проекта) и добавляем её в пути Python
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# А теперь твои импорты сработают
from analysis.technical import fetch_ohlcv, _score_dataframe
# ... остальной код
import asyncio, sys, os
sys.path.insert(0, '/root/Finanse_bot')

async def main():
    # Тест 1: просто получаем данные
    import aiohttp
    print("=== ТЕСТ 1: Binance API ===")
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                "https://api.binance.com/api/v3/klines",
                params={"symbol":"BTCUSDT","interval":"1h","limit":"10"},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                print(f"Статус: {r.status}")
                if r.status == 200:
                    data = await r.json()
                    print(f"Свечей получено: {len(data)} ✅")
                else:
                    print(f"Ошибка: {await r.text()}")
    except Exception as e:
        print(f"Ошибка соединения: {e}")

    # Тест 2: score без MTFA/DXY/imbalance
    print("\n=== ТЕСТ 2: Базовый score ===")
    try:
        from analysis.technical import fetch_ohlcv, _score_dataframe
        df = await fetch_ohlcv("BTC/USDT", "1h", 200)
        if df is not None:
            result = _score_dataframe(df)
            if result:
                print(f"Score: {result['score']:+d}")
                print(f"Direction: {result['direction']}")
                print(f"Price: {result['price']}")
                print(f"ATR: {result['atr']:.4f}")
                print(f"ATR/Price ratio: {result['atr']/result['price']:.4%}")
                print(f"Reasons: {result['reasons'][:3]}")
            else:
                print("_score_dataframe вернул None — мало данных")
        else:
            print("fetch_ohlcv вернул None — проблема с API")
    except Exception as e:
        print(f"Ошибка: {e}")
        import traceback; traceback.print_exc()

    # Тест 3: полный analyze_symbol с логами каждого фильтра
    print("\n=== ТЕСТ 3: analyze_symbol (полный) ===")
    try:
        # Читаем technical.py и ищем параметры
        import analysis.technical as tech
        print(f"MIN_SCORE: {getattr(tech, 'MIN_SCORE', '?')}")
        print(f"ASIAN_MIN_SCORE: {getattr(tech, 'ASIAN_MIN_SCORE', '?')}")
        print(f"ATR_MIN_RATIO: {getattr(tech, 'ATR_MIN_RATIO', '?')}")
        print(f"ATR_MAX_RATIO: {getattr(tech, 'ATR_MAX_RATIO', '?')}")
        print(f"MIN_RR: {getattr(tech, 'MIN_RR', '?')}")
        print(f"MIN_CONFIDENCE: {getattr(tech, 'MIN_CONFIDENCE', '?')}")

        from datetime import datetime, timezone
        hour = datetime.now(timezone.utc).hour
        asian = hour >= 22 or hour < 7
        required = getattr(tech, 'ASIAN_MIN_SCORE', 6) if asian else getattr(tech, 'MIN_SCORE', 4)
        print(f"\nТекущий час UTC: {hour}")
        print(f"Азиатская сессия: {asian}")
        print(f"Требуемый score: {required}")

        result = await tech.analyze_symbol("BTC/USDT", "1h")
        if result:
            print(f"\n✅ СИГНАЛ НАЙДЕН: {result['direction']} {result['symbol']}")
            print(f"Confidence: {result['confidence']}%")
            print(f"Score: {result['score']}")
        else:
            print("\n❌ analyze_symbol вернул None")
            print("Причина: один из фильтров заблокировал сигнал")
            print("(DXY, MTFA, volatility, score, или RR)")
    except Exception as e:
        print(f"Ошибка: {e}")
        import traceback; traceback.print_exc()

    # Тест 4: проверяем DXY
    print("\n=== ТЕСТ 4: DXY фильтр ===")
    try:
        from analysis.technical import check_dxy_trend
        dxy = await check_dxy_trend()
        print(f"DXY trend: {dxy} (0=нейтрально, 1=доллар растёт, -1=доллар падает)")
        if dxy != 0:
            print("⚠️  DXY блокирует часть сигналов!")
    except Exception as e:
        print(f"DXY недоступен: {e}")

asyncio.run(main())