import sqlite3
conn = sqlite3.connect('trading_bot.db')
cursor = conn.cursor()
try:
    cursor.execute("ALTER TABLE ml_dataset ADD COLUMN entry_price REAL DEFAULT NULL;")
    conn.commit()
except:
    pass
conn.close()