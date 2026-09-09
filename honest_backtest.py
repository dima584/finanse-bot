import pandas as pd
import numpy as np
from sklearn.model_selection import TimeSeriesSplit
import xgboost as xgb

def run_honest_backtest():
    print("⚖️ Запуск честного Out-of-Sample бэктеста...\n")
    
    df = pd.read_csv("deep_history_dataset.csv").sort_values('timestamp').reset_index(drop=True)
    feature_cols = ['rsi', 'macd', 'adx', 'ema9_dist_pct', 'volatility_pct', 'cvd_15m', 'btc_trend', 'trend_1h', 'volatility_1h', 'trend_4h']
    
    X = df[feature_cols].fillna(0)
    y = df['target_long']
    
    tscv = TimeSeriesSplit(n_splits=5)
    out_of_sample_preds = pd.Series(index=X.index, dtype=float).fillna(0.0)
    
    fold = 1
    # Собираем прогнозы ТОЛЬКО на тестовых фолдах
    for train_index, test_index in tscv.split(X):
        X_train, X_test = X.iloc[train_index], X.iloc[test_index]
        y_train = y.iloc[train_index]
        
        scale_pos = (len(y_train) - sum(y_train)) / sum(y_train) if sum(y_train) > 0 else 1.0
        
        model = xgb.XGBClassifier(
            n_estimators=100, max_depth=4, learning_rate=0.05,
            scale_pos_weight=scale_pos, random_state=42
        )
        model.fit(X_train, y_train)
        
        out_of_sample_preds.iloc[test_index] = model.predict_proba(X_test)[:, 1]
        fold += 1

    # Добавляем честные вероятности в датафрейм
    df['oos_prob'] = out_of_sample_preds
    
    # Отсекаем тренировочный кусок (первый фолд, для которого нет oos_prob)
    test_df = df[df['oos_prob'] > 0].copy()
    
    threshold = 0.75
    trades = test_df[test_df['oos_prob'] >= threshold].copy()
    
    if len(trades) == 0:
        print("❌ Нет сигналов по заданному порогу на OOS данных.")
        return

    # Реалистичные финансовые параметры
    tp_pct = 1.5
    sl_pct = -1.0
    fee_pct = -0.2
    
    initial_balance = 1000.0
    balance = initial_balance
    peak_balance = initial_balance
    max_drawdown = 0.0
    
    risk_per_trade = 0.05 # Входим на 5% от текущего депозита
    
    winning_trades = 0
    losing_trades = 0
    
    for index, row in trades.iterrows():
        position_size = balance * risk_per_trade
        
        if row['target_long'] == 1:
            trade_pnl = position_size * ((tp_pct + fee_pct) / 100)
            winning_trades += 1
        else:
            trade_pnl = position_size * ((sl_pct + fee_pct) / 100) # fee_pct уже отрицательный
            losing_trades += 1
            
        balance += trade_pnl
        
        if balance > peak_balance:
            peak_balance = balance
        
        drawdown = (peak_balance - balance) / peak_balance * 100
        if drawdown > max_drawdown:
            max_drawdown = drawdown

    total_trades = winning_trades + losing_trades
    winrate = winning_trades / total_trades * 100
    roi = ((balance - initial_balance) / initial_balance) * 100
    
    print(f"📊 ЧЕСТНЫЕ РЕЗУЛЬТАТЫ (Порог > {threshold*100}%, Риск на сделку 5%):")
    print(f"Всего OOS сделок: {total_trades}")
    print(f"Винрейт: {winrate:.1f}% ({winning_trades} плюсов / {losing_trades} минусов)")
    print(f"Максимальная просадка: {max_drawdown:.2f}%")
    print(f"Итоговый баланс: ${balance:.2f} (ROI: {roi:.2f}%)")

if __name__ == '__main__':
    run_honest_backtest()