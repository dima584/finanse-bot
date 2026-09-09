"""
train_ml.py — ИСПРАВЛЕННАЯ ВЕРСИЯ
Ключевые исправления:
  1. Разметка по реальным TP/SL уровням из БД (не фиксированные %)
  2. Раздельные модели для LONG и SHORT
  3. Правильная логика для SHORT (lows=TP, highs=SL)
  4. XGBoost вместо RandomForest (лучше на несбалансированных данных)
  5. class_weight для борьбы с дисбалансом классов
"""

import os
import pandas as pd
import numpy as np
import aiohttp
import asyncio
import pickle
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report
from sklearn.preprocessing import StandardScaler

try:
    import xgboost as xgb
    USE_XGB = True
except ImportError:
    from sklearn.ensemble import RandomForestClassifier
    USE_XGB = False
    print("XGBoost не установлен, используем RandomForest")


async def fetch_real_outcome_with_levels(
    session, symbol: str, timeframe: str,
    timestamp, entry_price: float,
    stop_loss: float, take_profit_1: float,
    direction: str
) -> int | None:
    """
    Проверяет реальный исход сделки по историческим свечам.
    Использует реальные уровни TP/SL из сигнала.

    Returns:
        1 = победа (цена достигла TP1 раньше SL)
        0 = поражение (цена достигла SL раньше TP1)
        None = данных нет
    """
    clean = symbol.replace("/", "")
    url   = "https://api.binance.com/api/v3/klines"

    try:
        start_time = int(pd.to_datetime(timestamp).timestamp() * 1000)
    except Exception:
        return None

    params = {
        "symbol":    clean,
        "interval":  timeframe,
        "startTime": start_time,
        "limit":     50   # Смотрим 50 свечей вперёд
    }

    try:
        async with session.get(url, params=params,
                               timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()

        if not data or len(data) < 3:
            return None

        # Пропускаем первую свечу (это свеча входа)
        for candle in data[1:]:
            high = float(candle[2])
            low  = float(candle[3])

            if direction == "LONG":
                if high >= take_profit_1:
                    return 1   # TP достигнут
                if low <= stop_loss:
                    return 0   # SL достигнут

            else:  # SHORT
                if low <= take_profit_1:
                    return 1   # TP достигнут (цена упала до TP)
                if high >= stop_loss:
                    return 0   # SL достигнут (цена выросла до SL)

        return 0  # За 50 свечей ни TP ни SL не достигнуты — считаем поражение

    except Exception:
        return None


async def build_dataset():
    """Строит датасет с правильной разметкой"""
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

    # Пробуем загрузить из БД
    import sqlite3
    db_path = os.path.join(BASE_DIR, "trading_bot.db")

    try:
        conn = sqlite3.connect(db_path)
        df = pd.read_sql_query("""
            SELECT s.symbol, s.timeframe, s.direction,
                   s.entry_price, s.stop_loss,
                   json_extract(s.take_profit, '$[0]') as tp1,
                   s.confidence, s.score, s.created_at as timestamp,
                   ms.rsi, ms.macd, ms.adx, ms.dist_ema9_pct,
                   ms.btc_trend, ms.volatility_pct, ms.cvd_15m
            FROM signals s
            LEFT JOIN ml_snapshots ms
                ON ms.symbol = s.symbol
                AND ms.timeframe = s.timeframe
                AND ABS(julianday(ms.timestamp) - julianday(s.created_at)) < 0.01
            WHERE s.entry_price IS NOT NULL
              AND s.stop_loss IS NOT NULL
              AND s.take_profit IS NOT NULL
            ORDER BY s.created_at
        """, conn)
        conn.close()
        print(f"Из БД загружено: {len(df)} записей")
    except Exception as e:
        print(f"Ошибка чтения БД: {e}, пробуем CSV...")
        csv_path = os.path.join(BASE_DIR, "ml_dataset_export.csv")
        df = pd.read_csv(csv_path)
        print(f"Из CSV загружено: {len(df)} записей")

    if df.empty or len(df) < 100:
        print("Недостаточно данных для обучения")
        return None

    # Удаляем строки без ключевых полей
    df = df.dropna(subset=["entry_price", "stop_loss", "tp1", "direction", "timestamp"])
    df["entry_price"] = pd.to_numeric(df["entry_price"], errors="coerce")
    df["stop_loss"]   = pd.to_numeric(df["stop_loss"],   errors="coerce")
    df["tp1"]         = pd.to_numeric(df["tp1"],          errors="coerce")
    df = df.dropna(subset=["entry_price", "stop_loss", "tp1"])

    print(f"После очистки: {len(df)} записей")
    print(f"LONG: {(df['direction']=='LONG').sum()} | SHORT: {(df['direction']=='SHORT').sum()}")

    # Разметка по реальным TP/SL
    print("Запрашиваю реальные исходы с Binance...")
    targets = []

    async with aiohttp.ClientSession() as session:
        for idx, (_, row) in enumerate(df.iterrows()):
            outcome = await fetch_real_outcome_with_levels(
                session        = session,
                symbol         = row["symbol"],
                timeframe      = row["timeframe"],
                timestamp      = row["timestamp"],
                entry_price    = float(row["entry_price"]),
                stop_loss      = float(row["stop_loss"]),
                take_profit_1  = float(row["tp1"]),
                direction      = row["direction"],
            )
            targets.append(outcome)
            if idx % 200 == 0:
                print(f"  Обработано: {idx}/{len(df)}")
            await asyncio.sleep(0.02)

    df["target"] = targets
    df = df.dropna(subset=["target"])
    df["target"] = df["target"].astype(int)

    wins = df["target"].sum()
    total = len(df)
    print(f"\nРазмечено: {total} сделок | Побед: {wins} ({wins/total*100:.1f}%)")

    return df


def train_model(df: pd.DataFrame, direction: str):
    """Обучает модель для одного направления"""
    subset = df[df["direction"] == direction].copy()
    if len(subset) < 50:
        print(f"Мало данных для {direction}: {len(subset)}")
        return None, None

    wins = subset["target"].sum()
    print(f"\n=== {direction} === {len(subset)} сделок | Побед: {wins} ({wins/len(subset)*100:.1f}%)")

    feature_cols = [
        "rsi", "macd", "adx", "dist_ema9_pct", "btc_trend",
        "volatility_pct", "cvd_15m", "confidence"
    ]
    available = [c for c in feature_cols if c in subset.columns]

    # Добавляем производные фичи
    if "rsi" in subset.columns:
        subset["rsi_slope"] = subset["rsi"].diff(1).fillna(0)
    if "adx" in subset.columns:
        subset["adx_slope"] = subset["adx"].diff(1).fillna(0)
    if "cvd_15m" in subset.columns:
        subset["cvd_accel"] = subset["cvd_15m"].diff(1).fillna(0)

    extra = ["rsi_slope", "adx_slope", "cvd_accel"]
    available += [c for c in extra if c in subset.columns]

    X = subset[available].fillna(0)
    y = subset["target"]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    # Масштабирование
    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train)
    X_test_s  = scaler.transform(X_test)

    # Вес классов для борьбы с дисбалансом
    neg = (y_train == 0).sum()
    pos = (y_train == 1).sum()
    scale_pos = neg / pos if pos > 0 else 1

    if USE_XGB:
        model = xgb.XGBClassifier(
            n_estimators    = 300,
            max_depth       = 4,
            learning_rate   = 0.05,
            scale_pos_weight= scale_pos,
            subsample       = 0.8,
            colsample_bytree= 0.8,
            use_label_encoder=False,
            eval_metric     = "logloss",
            random_state    = 42,
        )
    else:
        from sklearn.ensemble import RandomForestClassifier
        model = RandomForestClassifier(
            n_estimators  = 200,
            max_depth     = 6,
            class_weight  = "balanced",
            random_state  = 42,
        )

    model.fit(X_train_s, y_train)

    y_pred = model.predict(X_test_s)
    print(classification_report(y_test, y_pred))

    # Feature importance
    if hasattr(model, "feature_importances_"):
        importance = sorted(
            zip(available, model.feature_importances_),
            key=lambda x: x[1], reverse=True
        )
        print("Важность признаков:")
        for feat, imp in importance[:5]:
            print(f"  {feat}: {imp:.3f}")

    return model, scaler


async def main():
    df = await build_dataset()
    if df is None:
        return

    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    ml_dir   = os.path.join(BASE_DIR, "ml")
    os.makedirs(ml_dir, exist_ok=True)

    for direction in ["LONG", "SHORT"]:
        model, scaler = train_model(df, direction)
        if model is None:
            continue

        model_path  = os.path.join(ml_dir, f"model_{direction.lower()}.pkl")
        scaler_path = os.path.join(ml_dir, f"scaler_{direction.lower()}.pkl")

        with open(model_path, "wb") as f:
            pickle.dump(model, f)
        with open(scaler_path, "wb") as f:
            pickle.dump(scaler, f)

        print(f"Модель {direction} сохранена: {model_path}")

    print("\nОбучение завершено!")


if __name__ == "__main__":
    asyncio.run(main())