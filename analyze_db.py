import sqlite3
import pandas as pd
import json
import numpy as np

# 1. Подключаемся к базе данных (замени на актуальное имя файла, если оно другое)
DB_PATH = "trading_bot.db"  # или bot.db / trading.db

def run_analysis():
    print("⏳ Чтение базы данных...")
    conn = sqlite3.connect(DB_PATH)
    
    # Берем только закрытые сигналы, где зафиксирован профит или убыток
    query = """
    SELECT direction, profit_pct, indicators 
    FROM signals 
    WHERE status != 'active' AND indicators IS NOT NULL
    """
    df = pd.read_sql_query(query, conn)
    conn.close()

    if df.empty:
        print("❌ Нет закрытых сделок для анализа.")
        return

    # 2. Распаковываем JSON из колонки indicators
    print(f"✅ Найдено сделок: {len(df)}")
    
    parsed_data = []
    for index, row in df.iterrows():
        try:
            inds = json.loads(row['indicators'])
            inds['direction'] = row['direction']
            inds['profit_pct'] = row['profit_pct']
            inds['is_win'] = 1 if row['profit_pct'] > 0 else 0
            parsed_data.append(inds)
        except Exception:
            continue
            
    data = pd.DataFrame(parsed_data)
    
    # Разделяем на прибыльные и убыточные
    wins = data[data['is_win'] == 1]
    losses = data[data['is_win'] == 0]
    
    print("\n" + "="*50)
    print(f"🏆 Прибыльных сделок (WINS): {len(wins)}")
    print(f"💀 Убыточных сделок (LOSSES): {len(losses)}")
    print("="*50)

    # 3. Анализ метрик
    metrics_to_check = ['volume_ratio', 'rsi', 'macd', 'adx', 'atr']
    
    for metric in metrics_to_check:
        if metric in data.columns:
            win_median = wins[metric].median()
            loss_median = losses[metric].median()
            
            print(f"\n--- Анализ {metric.upper()} ---")
            print(f"Медиана при ПЛЮСЕ:  {win_median:.3f}")
            print(f"Медиана при МИНУСЕ: {loss_median:.3f}")
            
            # Дополнительная статистика (квантили) для прибыльных сделок
            q25 = wins[metric].quantile(0.25)
            q75 = wins[metric].quantile(0.75)
            print(f"80% успешных сделок лежат в диапазоне {metric}: от {q25:.3f} до {q75:.3f}")

if __name__ == "__main__":
    run_analysis()