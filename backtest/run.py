"""
backtest/run.py
Обновлено: Добавлена обработка ошибок и логирование прогресса.
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.backtest_engine import run_backtest, BacktestResult

SYMBOLS = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT",
    "ADA/USDT", "DOGE/USDT", "DOT/USDT", "LINK/USDT", "AVAX/USDT", 
    "NEAR/USDT", "ATOM/USDT", "UNI/USDT", "LTC/USDT", "BCH/USDT",
    "ARB/USDT", "OP/USDT", "INJ/USDT", "RNDR/USDT", "FET/USDT",
    "SUI/USDT", "APT/USDT", "SEI/USDT", "TIA/USDT", "FIL/USDT",
    "AAVE/USDT", "WLD/USDT", "EIGEN/USDT", "BLAST/USDT", "ZK/USDT", 
    "ZETA/USDT", "JTO/USDT", "STRK/USDT", "MANTA/USDT", "ALT/USDT", 
    "MKR/USDT", "SNX/USDT", "CRV/USDT", "LDO/USDT", "ENS/USDT"
]

TIMEFRAMES = ["15m", "1h", "4h"] 
DAYS       = 180
MIN_CONF   = 0.0

async def main():
    print(f"\n🚀 Запуск бэктеста: {len(SYMBOLS)} пар, ТФ: {TIMEFRAMES}")
    
    total_tasks = len(SYMBOLS) * len(TIMEFRAMES)
    completed = 0
    
    for symbol in SYMBOLS:
        for tf in TIMEFRAMES:
            completed += 1
            print(f"[{completed}/{total_tasks}] Обработка {symbol} ({tf})...")
            
            try:
                result = await run_backtest(
                    symbol=symbol,
                    timeframe=tf,
                    days=DAYS,
                    min_confidence=MIN_CONF
                )
                if result.total_trades > 0:
                    print(f"  → Найдено сделок: {result.total_trades}, ROI: {result.net_profit_pct:+}%")
                else:
                    print(f"  → Нет сигналов")
            except Exception as e:
                print(f"  ❌ Ошибка при тесте {symbol} ({tf}): {e}")

    print("\n✅ Тестирование завершено.")

if __name__ == "__main__":
    asyncio.run(main())