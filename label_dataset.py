import sqlite3
import pandas as pd
import requests
import time
from datetime import timedelta

def fetch_klines(symbol, start_ts, end_ts):
    """Скачивает историю свечей с Binance пакетами по 1000 штук"""
    url = "https://api.binance.com/api/v3/klines"
    clean_symbol = symbol.replace("/", "")
    limit = 1000
    all_klines = []
    
    current_start = int(start_ts.timestamp() * 1000)
    end_ms = int(end_ts.timestamp() * 1000)

    while current_start < end_ms:
        params = {
            "symbol": clean_symbol,
            "interval": "15m",
            "startTime": current_start,
            "endTime": end_ms,
            "limit": limit
        }
        try:
            res = requests.get(url, params=params)
            data = res.json()
            if not data or type(data) != list:
                break
                
            all_klines.extend(data)
            current_start = data[-1][0] + 1  # Сдвигаем старт на 1 мс после последней свечи
            time.sleep(0.2)  # Защита от бана по IP
            
        except Exception as e:
            print(f"Ошибка загрузки {symbol}: {e}")
            break
            
    df = pd.DataFrame(all_klines, columns=[
        "timestamp", "open", "high", "low", "close", "volume",
        "close_time", "qav", "num_trades", "tbb", "tbq", "ignore"
    ])
    
    if not df.empty:
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        for col in ["open", "high", "low", "close"]:
            df[col] = pd.to_numeric(df[col])
            
    return df

def label_dataset():
    print("🚀 Запуск машины времени (Разметка ML-датасета)...")
    
    # 1. Читаем базу
    conn = sqlite3.connect("trading_bot.db")
    df_ml = pd.read_sql("SELECT * FROM ml_dataset", conn)
    df_ml['timestamp'] = pd.to_datetime(df_ml['timestamp'])
    
    unique_symbols = df_ml['symbol'].unique()
    print(f"📦 Найдено {len(unique_symbols)} уникальных монет. Начинаем бэктест...\n")

    TP_PCT = 0.012   # TP1 бота ≈ 0.8×ATR ≈ 1.2% средний
    SL_PCT = 0.010   # SL бота ≈ 1.0×ATR ≈ 1.0% средний
    LOOKAHEAD_HOURS = 12  # 12 часов — реальное время жизни сделки на 1h

    labeled_data = []

    # 3. Скачиваем будущее и симулируем сделки
    for symbol in unique_symbols:
        df_sym = df_ml[df_ml['symbol'] == symbol].copy()
        
        start_ts = df_sym['timestamp'].min()
        end_ts = df_sym['timestamp'].max() + timedelta(days=1)
        
        print(f"📥 Анализируем {symbol} ({len(df_sym)} снимков)...")
        klines = fetch_klines(symbol, start_ts, end_ts)
        
        if klines.empty:
            print(f"⚠️ Нет данных с биржи по {symbol}")
            continue
            
        klines.set_index('timestamp', inplace=True)

        for idx, row in df_sym.iterrows():
            snap_time = row['timestamp']
            
            # Если бот не смог посчитать лимитку, берем цену закрытия свечи
            entry_price = row['entry_price'] if row['entry_price'] > 0 else row['close_price']
            
            # Вырезаем окно будущего (следующие 6 часов)
            future = klines.loc[snap_time : snap_time + timedelta(hours=LOOKAHEAD_HOURS)]
            
            target_long = 0
            target_short = 0
            
            if not future.empty and entry_price > 0:
                highs = future['high'].values
                lows = future['low'].values
                
                # --- СИМУЛЯЦИЯ LONG ---
                tp_price_long = entry_price * (1 + TP_PCT)
                sl_price_long = entry_price * (1 - SL_PCT)
                
                for i in range(len(future)):
                    if lows[i] <= sl_price_long:
                        break  # Выбило по стопу
                    if highs[i] >= tp_price_long:
                        target_long = 1
                        break  # Закрыли в плюс
                        
                # --- СИМУЛЯЦИЯ SHORT ---
                tp_price_short = entry_price * (1 - TP_PCT)
                sl_price_short = entry_price * (1 + SL_PCT)
                
                for i in range(len(future)):
                    if highs[i] >= sl_price_short:
                        break  # Выбило по стопу
                    if lows[i] <= tp_price_short:
                        target_short = 1
                        break  # Закрыли в плюс

            row['target_long'] = target_long
            row['target_short'] = target_short
            labeled_data.append(row)

    # 4. Сохраняем готовый датасет
    final_df = pd.DataFrame(labeled_data)
    final_df.to_csv("ml_dataset_labeled.csv", index=False)
    
    print("\n==================================================")
    print(f"✅ Разметка успешно завершена!")
    print(f"Файл сохранен как: ml_dataset_labeled.csv (Строк: {len(final_df)})")
    print(f"📈 Найдено идеальных LONG паттернов: {final_df['target_long'].sum()}")
    print(f"📉 Найдено идеальных SHORT паттернов: {final_df['target_short'].sum()}")
    print("==================================================")

if __name__ == "__main__":
    label_dataset()