import pandas as pd
from sklearn.model_selection import TimeSeriesSplit
import xgboost as xgb
import matplotlib.pyplot as plt

def plot_equity_curve():
    print("📈 Генерация кривой доходности и расчет периода...\n")
    
    df = pd.read_csv("ml_dataset_labeled.csv").sort_values('timestamp').reset_index(drop=True)
    feature_cols = ['rsi', 'macd', 'adx', 'ema9_dist_pct', 'volatility_pct', 'cvd_15m', 'btc_trend']
    
    X = df[feature_cols].fillna(0)
    y = df['target_long']
    
    tscv = TimeSeriesSplit(n_splits=5)
    out_of_sample_preds = pd.Series(index=X.index, dtype=float).fillna(0.0)
    
    for train_index, test_index in tscv.split(X):
        X_train, X_test = X.iloc[train_index], X.iloc[test_index]
        y_train = y.iloc[train_index]
        scale_pos = (len(y_train) - sum(y_train)) / sum(y_train) if sum(y_train) > 0 else 1.0
        model = xgb.XGBClassifier(n_estimators=100, max_depth=4, learning_rate=0.05, scale_pos_weight=scale_pos, random_state=42)
        model.fit(X_train, y_train)
        out_of_sample_preds.iloc[test_index] = model.predict_proba(X_test)[:, 1]

    df['oos_prob'] = out_of_sample_preds
    test_df = df[df['oos_prob'] > 0].copy()
    
    trades = test_df[test_df['oos_prob'] >= 0.75].copy()
    
    if len(trades) == 0:
        return

    # Финансовая симуляция
    balance = 1000.0
    equity_curve = [balance]
    dates = []
    
    for index, row in trades.iterrows():
        position_size = balance * 0.05 # Риск 5% от депо
        if row['target_long'] == 1:
            trade_pnl = position_size * 0.028 # +3% TP - 0.2% Fee
        else:
            trade_pnl = position_size * -0.022 # -2% SL - 0.2% Fee
            
        balance += trade_pnl
        equity_curve.append(balance)
        dates.append(row['timestamp'])
        
    start_date = pd.to_datetime(dates[0], unit='s' if isinstance(dates[0], (int, float)) and dates[0] > 1e10 else None)
    end_date = pd.to_datetime(dates[-1], unit='s' if isinstance(dates[-1], (int, float)) and dates[-1] > 1e10 else None)
    
    print(f"Календарный период: с {start_date} по {end_date}")
    print(f"Итоговых дней в тесте: {(end_date - start_date).days}")
    
    # Отрисовка графика
    plt.figure(figsize=(12, 6))
    plt.plot(equity_curve, color='blue', linewidth=2)
    plt.title('Кривая доходности (Equity Curve) - OOS Тест', fontsize=14)
    plt.xlabel('Номер сделки (хронологически)', fontsize=12)
    plt.ylabel('Баланс ($)', fontsize=12)
    plt.grid(True, linestyle='--', alpha=0.7)
    plt.tight_layout()
    plt.show()

if __name__ == '__main__':
    plot_equity_curve()