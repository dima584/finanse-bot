import os
import pandas as pd
import numpy as np
import aiohttp
import asyncio
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix

async def fetch_real_outcome(session, symbol, timeframe, timestamp, entry_price):
    clean = symbol.replace("/", "")
    url = "https://api.binance.com/api/v3/klines"
    
    try:
        start_time = int(pd.to_datetime(timestamp).timestamp() * 1000)
    except Exception:
        return None
        
    params = {
        "symbol": clean,
        "interval": timeframe,
        "startTime": start_time,
        "limit": 30
    }
    
    try:
        async with session.get(url, params=params, timeout=10) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            
        if not data or len(data) < 5:
            return None
            
        highs = [float(candle[2]) for candle in data[1:]]
        lows = [float(candle[3]) for candle in data[1:]]
        
        tp_target = entry_price * 1.018  # +1.8%
        sl_target = entry_price * 0.990  # -1.0%
        
        for h, l in zip(highs, lows):
            if h >= tp_target:
                return 1
            if l <= sl_target:
                return 0
                
        return 0
    except Exception:
        return None

async def build_advanced_features_dataset():
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    csv_file = os.path.join(BASE_DIR, "ml_dataset_export.csv")
    
    try:
        df = pd.read_csv(csv_file)
    except Exception as e:
        print(f"❌ Ошибка чтения CSV: {e}")
        return None, None
        
    if df.empty:
        return None, None
        
    if 'timeframe' in df.columns:
        df['timeframe_code'] = df['timeframe'].astype('category').cat.codes
    else:
        return None, None

    # Сортируем по времени, чтобы дельта (diff) считалась корректно по хронологии
    if 'timestamp' in df.columns:
        df = df.sort_values(by='timestamp').reset_index(drop=True)

    # ── ГЕНЕРАЦИЯ ПРОДВИНУТЫХ ФИЧЕЙ (FEATURE ENGINEERING) ПОСТФАКТУМ ──
    # Создаем динамические метрики прямо из существующих колонок без изменения прода
    df['rsi_slope'] = df['rsi'].diff(1).fillna(0)         # Скорость изменения RSI
    df['cvd_acceleration'] = df['cvd_15m'].diff(1).fillna(0) # Ускорение CVD
    df['adx_slope'] = df['adx'].diff(1).fillna(0)         # Динамика силы тренда
    df['volatility_delta'] = df['volatility_pct'].pct_change().fillna(0) # Изменение волатильности
    
    feature_cols = [
        'rsi', 'macd', 'adx', 'dist_ema9_pct', 'btc_trend', 
        'volatility_pct', 'cvd_15m', 'timeframe_code',
        'rsi_slope', 'cvd_acceleration', 'adx_slope', 'volatility_delta'
    ]
    
    df = df.dropna(subset=feature_cols + ['entry_price', 'timestamp', 'symbol', 'timeframe'])
    
    print(f"🔄 Разметка датасета с новыми фичами (строк: {len(df)})...")
    
    targets = []
    async with aiohttp.ClientSession() as session:
        for idx, row in df.iterrows():
            outcome = await fetch_real_outcome(
                session, row['symbol'], row['timeframe'], row['timestamp'], row['entry_price']
            )
            targets.append(outcome)
            await asyncio.sleep(0.01)
            
    df['target'] = targets
    df = df.dropna(subset=['target'])
    
    print(f"✅ Готово сэмплов для теста: {len(df)}")
    return df[feature_cols], df['target']

def train_with_new_features():
    X, y = asyncio.run(build_advanced_features_dataset())
    
    if X is None or len(X) < 100:
        print("⚠️ Мало данных.")
        return

    split_idx = int(len(X) * 0.8)
    X_train, X_test = X.iloc[:split_idx], X.iloc[split_idx:]
    y_train, y_test = y.iloc[:split_idx], y.iloc[split_idx:]
    
    print(f"\n🧠 Обучение модели с динамическими фичами ({len(X_train)} трейн, {len(X_test)} тест)...")
    
    model = RandomForestClassifier(
        n_estimators=400, 
        max_depth=10, 
        min_samples_split=8,
        class_weight={0: 1.0, 1: 6.0}, 
        random_state=42
    )
    model.fit(X_train, y_train)
    
    predictions = model.predict(X_test)
    acc = accuracy_score(y_test, predictions)
    
    print(f"\n🎯 Точность с новыми фичами (Accuracy): {acc * 100:.2f}%")
    print("\n📋 Детальный отчет классификации:")
    print(classification_report(y_test, predictions, zero_division=0))
    
    print("📉 Матрица ошибок (Confusion Matrix):")
    print(confusion_matrix(y_test, predictions))
    
    importances = model.feature_importances_
    print("\n🔍 Важность признаков (какие фичи реально помогли):")
    for f, imp in sorted(zip(X.columns, importances), key=lambda x: x[1], reverse=True):
        print(f"  • {f}: {imp:.4f}")

if __name__ == "__main__":
    train_with_new_features()