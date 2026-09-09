import sqlite3
import pandas as pd
import ast

def analyze_real_trades():
    print("🔍 Анализ реальных сделок из таблицы signals...\n")
    try:
        conn = sqlite3.connect("trading_bot.db")
        # Берем только закрытые сделки
        df = pd.read_sql("SELECT * FROM signals WHERE status LIKE 'closed_%'", conn)
    except Exception as e:
        print(f"❌ Ошибка чтения БД: {e}")
        return

    print(f"📊 Всего закрытых сделок (включая безубыток): {len(df)}")

    # Функция для извлечения индикаторов из строки словаря
    def get_indicator(row_str, key):
        try:
            # Превращаем строку "{'rsi': 30.5, 'adx': None}" обратно в словарь Python
            data = ast.literal_eval(row_str)
            val = data.get(key)
            return float(val) if val is not None else 0.0
        except:
            return 0.0

    # Распаковываем RSI и ADX
    df['rsi'] = df['indicators'].apply(lambda x: get_indicator(x, 'rsi'))
    df['adx'] = df['indicators'].apply(lambda x: get_indicator(x, 'adx'))

    # Для чистоты эксперимента убираем сделки закрытые в БУ (closed_be), 
    # оставляем только жесткие стопы и тейки.
    df_clean = df[df['status'].isin(['closed_tp', 'closed_sl'])].copy()
    
    if len(df_clean) == 0:
        print("Нет данных по чистым TP/SL для анализа.")
        return

    # Целевая переменная: 1 если тейк, 0 если стоп-лосс
    df_clean['target'] = df_clean['status'].apply(lambda x: 1 if x == 'closed_tp' else 0)
    
    print(f"✅ Сделок для расчета (только TP и SL): {len(df_clean)}\n")
    
    base_wr = df_clean['target'].mean() * 100
    print(f"📉 Базовый винрейт (чистые TP против SL): {base_wr:.2f}%\n")

    # === ТЕСТ 1: ВЛИЯНИЕ ADX НА ВИНРЕЙТ ===
    print("📈 ТЕСТ ADX (Как сила тренда влияет на винрейт):")
    for adx_val in [0, 15, 20, 25, 30]:
        subset = df_clean[df_clean['adx'] >= adx_val]
        if len(subset) > 0:
            wr = subset['target'].mean() * 100
            print(f"Если ADX >= {adx_val:2d} | Сделок: {len(subset):3d} | Винрейт: {wr:.2f}%")

    # === ТЕСТ 2: ВЛИЯНИЕ RSI ДЛЯ ЛОНГОВ ===
    print("\n🎯 ТЕСТ RSI ДЛЯ LONG (Поиск идеальной точки входа):")
    longs = df_clean[df_clean['direction'] == 'LONG']
    for rsi_val in [60, 50, 40, 35, 30, 25]:
        subset = longs[longs['rsi'] <= rsi_val]
        if len(subset) > 0:
            wr = subset['target'].mean() * 100
            print(f"Если RSI <= {rsi_val:2d} | Сделок: {len(subset):3d} | Винрейт: {wr:.2f}%")

    # === ТЕСТ 3: ВЛИЯНИЕ RSI ДЛЯ ШОРТОВ ===
    print("\n🩸 ТЕСТ RSI ДЛЯ SHORT (Поиск перегрева):")
    shorts = df_clean[df_clean['direction'] == 'SHORT']
    for rsi_val in [40, 50, 60, 65, 70, 75]:
        subset = shorts[shorts['rsi'] >= rsi_val]
        if len(subset) > 0:
            wr = subset['target'].mean() * 100
            print(f"Если RSI >= {rsi_val:2d} | Сделок: {len(subset):3d} | Винрейт: {wr:.2f}%")

if __name__ == "__main__":
    analyze_real_trades()