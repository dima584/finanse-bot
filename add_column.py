import sqlite3
# Імпортуємо функцію підключення безпосередньо з твого файлу бази даних
from database import get_connection

try:
    # Використовуємо те саме підключення, що й бот
    conn = get_connection()
    cursor = conn.cursor()
    
    # Додаємо нову колонку
    cursor.execute("ALTER TABLE signals ADD COLUMN pnl_usd REAL DEFAULT 0.0")
    conn.commit()
    print("✅ Колонка pnl_usd успішно додана до таблиці signals!")
    
except sqlite3.OperationalError as e:
    print(f"⚠️ Результат: {e}")
finally:
    if conn:
        conn.close()