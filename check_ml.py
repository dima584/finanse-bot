import sqlite3
import pandas as pd

def check_ml_dataset():
    # Подключаемся к базе
    conn = sqlite3.connect('trading_bot.db')
    
    try:
        # Укажи тут точное название своей таблицы, куда пишет save_ml_snapshot
        df = pd.read_sql_query("SELECT * FROM ml_dataset ORDER BY id DESC LIMIT 10", conn)
        
        if df.empty:
            print("Таблица пуста. Данные еще не собрались.")
        else:
            print(f"✅ База работает! Последние 10 слепков:")
            print(df.to_string())
            
            # Проверим общее количество строк
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM ml_snapshots")
            count = cursor.fetchone()[0]
            print(f"\n📊 Всего собрано датапоинтов для ML: {count}")
            
    except Exception as e:
        print(f"Ошибка при чтении: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    check_ml_dataset()