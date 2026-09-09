import pandas as pd
import numpy as np
from sklearn.model_selection import TimeSeriesSplit
import xgboost as xgb
import ta

def run_final_pipeline():
    print("🚀 Запуск финального пайплайна: Фильтр тренда + ML + ATR Бэктест...\n")
    
    try:
        df = pd.read_csv("deep_history_dataset.csv").sort_values('timestamp').reset_index(drop=True)
    except FileNotFoundError:
        print("❌ Файл не найден!")
        return

    # РАСЧЕТ ATR НА ЛЕТУ
    df['atr'] = ta.volatility.AverageTrueRange(
        high=df['high'], low=df['low'], close=df['close'], window=14
    ).average_true_range()
    df = df.dropna(subset=['atr']).reset_index(drop=True)

    # 1. ЖЕСТКИЙ ФИЛЬТР РЕЖИМА РЫНКА
    original_len = len(df)
    df = df[df['adx'] > 25].copy().reset_index(drop=True)
    print(f"📉 Отфильтрован рыночный шум (ADX < 25). Оставлено строк: {len(df)} из {original_len}\n")

    feature_cols = ['rsi', 'macd', 'adx', 'ema9_dist_pct', 'volatility_pct', 'cvd_15m', 'btc_trend', 'trend_1h', 'volatility_1h', 'trend_4h']
    
    # Заполняем пропуски нулями, если они есть
    X = df[feature_cols].fillna(0)
    y_long = df['target_long']
    
    tscv = TimeSeriesSplit(n_splits=5)
    oos_preds = pd.Series(index=X.index, dtype=float).fillna(0.0)
    
    print("⏳ Обучение Walk-Forward (Только LONG)...")
    for train_index, test_index in tscv.split(X):
        X_train, X_test = X.iloc[train_index], X.iloc[test_index]
        y_train = y_long.iloc[train_index]
        
        scale_pos = (len(y_train) - sum(y_train)) / sum(y_train) if sum(y_train) > 0 else 1.0
        
        model = xgb.XGBClassifier(
            n_estimators=100, max_depth=4, learning_rate=0.05,
            scale_pos_weight=scale_pos, random_state=42
        )
        model.fit(X_train, y_train)
        oos_preds.iloc[test_index] = model.predict_proba(X_test)[:, 1]

    df['oos_prob'] = oos_preds
    
    # 2. ФИНАНСОВЫЙ СИМУЛЯТОР ПО ATR
    test_df = df[df['oos_prob'] > 0].copy()
    threshold = 0.75
    trades = test_df[test_df['oos_prob'] >= threshold].copy()
    
    if len(trades) == 0:
        print("❌ Нет сигналов по заданному порогу на OOS данных.")
        return

    fee_pct = -0.002 # 0.2% комиссии в десятичном виде
    initial_balance = 1000.0
    balance = initial_balance
    peak_balance = initial_balance
    max_drawdown = 0.0
    risk_per_trade = 0.05 # 5% капитала на вход
    
    winning_trades = 0
    losing_trades = 0
    
    for index, row in trades.iterrows():
        position_size = balance * risk_per_trade
        entry_price = row['close']
        current_atr = row['atr']
        
        # Считаем процентный эквивалент ATR для конкретной сделки
        tp_pct = (current_atr * 0.8) / entry_price
        sl_pct = (current_atr * 1.0) / entry_price
        
        if row['target_long'] == 1:
            trade_pnl = position_size * (tp_pct + fee_pct)
            winning_trades += 1
        else:
            trade_pnl = position_size * (-sl_pct + fee_pct)
            losing_trades += 1
            
        balance += trade_pnl
        if balance <= 0:
            print(f"\n💀 Депозит ликвидирован на сделке {winning_trades + losing_trades}!")
            break
        
        if balance > peak_balance:
            peak_balance = balance
        
        drawdown = (peak_balance - balance) / peak_balance * 100
        if drawdown > max_drawdown:
            max_drawdown = drawdown

    total_trades = winning_trades + losing_trades
    winrate = winning_trades / total_trades * 100 if total_trades > 0 else 0
    roi = ((balance - initial_balance) / initial_balance) * 100
    
    print(f"📊 ФИНАЛЬНЫЕ РЕЗУЛЬТАТЫ (Порог > {threshold*100}%, Риск 5%):")
    print(f"Всего OOS сделок: {total_trades}")
    print(f"Винрейт: {winrate:.1f}% ({winning_trades} плюсов / {losing_trades} минусов)")
    print(f"Максимальная просадка: {max_drawdown:.2f}%")
    print(f"Итоговый баланс: ${balance:.2f} (ROI: {roi:.2f}%)")

if __name__ == '__main__':
    run_final_pipeline()