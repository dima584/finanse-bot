import pandas as pd
import numpy as np
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import precision_score
import xgboost as xgb
import joblib

def train_walkforward():
    print("⏳ Запуск Walk-Forward валидации (строго по времени)...\n")
    
    try:
        df = pd.read_csv("deep_history_dataset.csv")
    except FileNotFoundError:
        print("❌ Файл deep_history_dataset.csv.csv не найден!")
        return

    # Жесткая привязка ко времени, чтобы исключить Data Leakage
    if 'timestamp' in df.columns:
        df = df.sort_values('timestamp').reset_index(drop=True)
        print("✅ Данные отсортированы по времени (timestamp).")
    elif 'id' in df.columns:
        df = df.sort_values('id').reset_index(drop=True)
        print("⚠️ Колонки 'timestamp' нет, сортируем по 'id'.")

    feature_cols = ['rsi', 'macd', 'adx', 'ema9_dist_pct', 'volatility_pct', 'cvd_15m', 'btc_trend', 'trend_1h', 'volatility_1h', 'trend_4h']
    
    for target, model_name in [('target_long', 'xgboost_long_wf.pkl'), ('target_short', 'xgboost_short_wf.pkl')]:
        print(f"\n=== Анализ для {target.upper()} ===")
        df_model = df.dropna(subset=[target]).copy()
        for col in feature_cols:
            if col in df_model.columns:
                df_model[col] = df_model[col].fillna(0)
            
        X = df_model[feature_cols]
        y = df_model[target]
        
        # Walk-forward разбиение: 5 хронологических шагов
        tscv = TimeSeriesSplit(n_splits=5)
        
        fold = 1
        precisions_50 = []
        precisions_75 = []
        
        for train_index, test_index in tscv.split(X):
            X_train, X_test = X.iloc[train_index], X.iloc[test_index]
            y_train, y_test = y.iloc[train_index], y.iloc[test_index]
            
            scale_pos_weight = (len(y_train) - sum(y_train)) / sum(y_train) if sum(y_train) > 0 else 1.0
            
            # Используем базовые параметры, но тестируем честно
            model = xgb.XGBClassifier(
                n_estimators=100,
                max_depth=4,
                learning_rate=0.05,
                scale_pos_weight=scale_pos_weight,
                random_state=42
            )
            
            model.fit(X_train, y_train)
            
            # Проверка порога 50%
            preds = model.predict(X_test)
            prec_50 = precision_score(y_test, preds, zero_division=0)
            precisions_50.append(prec_50)
            
            # Проверка жесткого порога 75%
            probs = model.predict_proba(X_test)[:, 1]
            preds_75 = (probs >= 0.75).astype(int)
            prec_75 = precision_score(y_test, preds_75, zero_division=0) if sum(preds_75) > 0 else 0
            precisions_75.append(prec_75)
            
            print(f"  Фолд {fold} (тест на {len(X_test)} строках): Precision(>50%) = {prec_50*100:.1f}% | Precision(>75%) = {prec_75*100:.1f}% (Сигналов: {sum(preds_75)})")
            fold += 1
        
        print(f"\n📊 ИТОГО Out-of-Sample для {target}:")
        print(f"  Средний Precision (>50%): {np.mean(precisions_50)*100:.1f}%")
        print(f"  Средний Precision (>75%): {np.mean(precisions_75)*100:.1f}%")
        print("-" * 55)
        
        # Финальное обучение на всем массиве для прода
        model.fit(X, y)
        joblib.dump(model, model_name)
        print(f"✅ Финальная Walk-Forward модель сохранена: {model_name}")

if __name__ == '__main__':
    train_walkforward()