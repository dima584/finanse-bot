import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report
import xgboost as xgb
import joblib

def train_advanced_models():
    print("🚀 Запуск финального обучения с продвинутыми параметрами...\n")
    
    try:
        df = pd.read_csv("ml_dataset_labeled.csv")
    except FileNotFoundError:
        print("❌ Файл ml_dataset_labeled.csv не найден!")
        return

    feature_cols = ['rsi', 'macd', 'adx', 'ema9_dist_pct', 'volatility_pct', 'cvd_15m', 'btc_trend']
    
    for target, model_name in [('target_long', 'xgboost_long_model.pkl'), ('target_short', 'xgboost_short_model.pkl')]:
        df_model = df.dropna(subset=[target]).copy()
        for col in feature_cols:
            if col in df_model.columns:
                df_model[col] = df_model[col].fillna(0)
            
        X = df_model[feature_cols]
        y = df_model[target]
        
        # Используем новый random_state (777), чтобы тест прошел на других участках рынка
        X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=777, stratify=y)
        
        scale_pos_weight = (len(y_train) - sum(y_train)) / sum(y_train) if sum(y_train) > 0 else 1.0
        
        # Продвинутые параметры XGBoost (защита от шума и переобучения)
        model = xgb.XGBClassifier(
            n_estimators=200,      # Больше деревьев для более глубокого анализа (было 100)
            max_depth=5,           # Немного увеличиваем глубину поиска паттернов
            learning_rate=0.03,    # Уменьшаем шаг обучения для большей точности
            subsample=0.8,         # Игнорируем 20% случайного шума в строках
            colsample_bytree=0.8,  # Игнорируем часть индикаторов при построении каждого дерева
            scale_pos_weight=scale_pos_weight,
            random_state=777
        )
        
        model.fit(X_train, y_train)
        preds = model.predict(X_test)
        
        target_name = "ЛОНГА" if "long" in target else "ШОРТА"
        print(f"📈 Метрики для {target_name} на новых тестовых данных:")
        print(classification_report(y_test, preds))
        joblib.dump(model, model_name)
        print(f"✅ Улучшенная модель перезаписана: {model_name}\n" + "-"*55 + "\n")

if __name__ == "__main__":
    train_advanced_models()