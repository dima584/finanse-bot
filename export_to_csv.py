import sqlite3
import pandas as pd

def export_ml_dataset_to_csv():
    # Подключаемся к базе данных бота
    conn = sqlite3.connect('trading_bot.db')
    
    try:
        # Читаем всю таблицу ml_dataset с помощью pandas
        df = pd.read_sql_query("SELECT * FROM ml_dataset", conn)
        
        if df.empty:
            print("⚠️ Таблица ml_dataset пуста. Экспортировать нечего.")
            return
            
        # Экспортируем в CSV файл
        csv_filename = "ml_dataset_export.csv"
        df.to_csv(csv_filename, index=False, encoding='utf-8')
        
        print(f"✅ Успешно выгружено {len(df)} строк в файл: {csv_filename}")
        
    except Exception as e:
        print(f"❌ Ошибка при экспорте: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    export_ml_dataset_to_csv()