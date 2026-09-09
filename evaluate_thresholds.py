import pandas as pd
from sklearn.model_selection import train_test_split
import joblib

def evaluate_model_thresholds():
    print("🔍 Анализ эффективности порогов уверенности (Threshold Tuning)...\n")
    
    try:
        df = pd.read_csv("ml_dataset_labeled.csv")
        model = joblib.load("xgboost_long_model.pkl")
    except Exception as e:
        print(f"❌ Ошибка загрузки файлов: {e}")
        return

    feature_cols = ['rsi', 'macd', 'adx', 'ema9_dist_pct', 'volatility_pct', 'cvd_15m', 'btc_trend']
    
    df_model = df.dropna(subset=['target_long']).copy()
    for col in feature_cols:
        df_model[col] = df_model[col].fillna(0)

    X = df_model[feature_cols]
    y = df_model['target_long']

    # Используем тот же random_state, что и при обучении, чтобы получить ровно ту же тестовую выборку
    _, X_test, _, y_test = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)
    
    # Получаем вероятности для класса 1 (успешный лонг)
    probs = model.predict_proba(X_test)[:, 1]
    
    eval_df = pd.DataFrame({'y_true': y_test, 'prob': probs})
    
    print(f"Всего тестовых примеров: {len(eval_df)}")
    print(f"Реально успешных в тесте: {eval_df['y_true'].sum()} ({eval_df['y_true'].mean()*100:.1f}%)\nnot")
    
    print(f"{'Порог (Threshold)':<18} | {'Сигналов прошло':<15} | {'Точность (Winrate)':<18} | {'Пропущено профита'}")
    print("-" * 65)
    
    total_positive = eval_df['y_true'].sum()

    for threshold in [0.50, 0.60, 0.70, 0.75, 0.80, 0.85, 0.90]:
        filtered = eval_df[eval_df['prob'] >= threshold]
        count = len(filtered)
        if count > 0:
            winrate = filtered['y_true'].mean() * 100
            captured = filtered['y_true'].sum()
            print(f">= {threshold * 100:.0f}%              | {count:<15} | {winrate:.2f}%             | {captured} из {total_positive}")
        else:
            print(f">= {threshold * 100:.0f}%              | 0               | 0.00%              | 0 из {total_positive}")

if __name__ == "__main__":
    evaluate_model_thresholds()