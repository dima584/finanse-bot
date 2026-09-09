import pandas as pd

def add_higher_timeframes():
    print("⏳ Интеграция старших таймфреймов (1H и 4H)...")
    
    try:
        df = pd.read_csv("deep_history_dataset.csv")
    except FileNotFoundError:
        print("❌ Файл не найден!")
        return

    # Наклон цены за 1 час (4 свечи по 15м)
    df['trend_1h'] = df.groupby('symbol')['close'].pct_change(4) * 100
    
    # Расчет волатильности за 1 час
    df['high_1h'] = df.groupby('symbol')['high'].rolling(4).max().reset_index(0, drop=True)
    df['low_1h'] = df.groupby('symbol')['low'].rolling(4).min().reset_index(0, drop=True)
    df['volatility_1h'] = (df['high_1h'] - df['low_1h']) / df['low_1h'] * 100
    
    # Наклон цены за 4 часа (16 свечей)
    df['trend_4h'] = df.groupby('symbol')['close'].pct_change(16) * 100

    # Удаляем пустые строки, возникшие из-за сдвига, и технические колонки
    df = df.dropna().drop(columns=['high_1h', 'low_1h']).reset_index(drop=True)
    
    df.to_csv("deep_history_dataset.csv", index=False)
    print("✅ Контекст успешно добавлен в датасет!")

if __name__ == "__main__":
    add_higher_timeframes()