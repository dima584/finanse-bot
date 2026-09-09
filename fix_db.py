import sqlite3

conn = sqlite3.connect('trading_bot.db')
cursor = conn.cursor()

# Удаляем старую таблицу
cursor.execute("DROP TABLE IF EXISTS ml_dataset")

# Создаем новую с правильными колонками, которые ждет save_ml_snapshot
cursor.execute('''
    CREATE TABLE ml_dataset (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        symbol TEXT,
        timeframe TEXT,
        entry_price REAL,
        rsi REAL,
        macd REAL,
        adx REAL,
        dist_ema9_pct REAL,
        btc_trend INTEGER,
        volatility_pct REAL,
        cvd_15m REAL
    )
''')
conn.commit()
conn.close()
print("✅ Таблица ml_dataset успешно пересоздана с новыми колонками!")