import pandas as pd
import joblib

def run_financial_backtest():
    print("💰 Запуск финансового симулятора PnL (с учетом комиссий)...\n")
    
    df = pd.read_csv("ml_dataset_labeled.csv").sort_values('timestamp').reset_index(drop=True)
    long_model = joblib.load("xgboost_long_wf.pkl")
    
    feature_cols = ['rsi', 'macd', 'adx', 'ema9_dist_pct', 'volatility_pct', 'cvd_15m', 'btc_trend']
    X = df[feature_cols].fillna(0)
    
    # Получаем вероятности только для лонгов (шорты пока отключаем)
    df['prob_long'] = long_model.predict_proba(X)[:, 1]
    
    threshold = 0.75
    trades = df[df['prob_long'] >= threshold].copy()
    
    if len(trades) == 0:
        print("Нет сделок по заданному порогу.")
        return

    # Финансовые параметры
    tp_pct = 3.0
    sl_pct = -2.0
    fee_pct = -0.2 # Комиссия за вход и выход + проскальзывание
    
    initial_balance = 1000.0 # Стартовый депозит $1000
    balance = initial_balance
    peak_balance = initial_balance
    max_drawdown = 0.0
    
    winning_trades = 0
    losing_trades = 0
    
    print(f"Стартовый баланс: ${initial_balance:.2f} | Риск на сделку: весь депозит без плеча\n")
    
    for index, row in trades.iterrows():
        # Если сделка успешная (target_long == 1), получаем профит минус комиссия
        if row['target_long'] == 1:
            trade_pnl_pct = tp_pct + fee_pct
            winning_trades += 1
        else:
            trade_pnl_pct = sl_pct + fee_pct
            losing_trades += 1
            
        # Обновляем баланс (капитализация процентов)
        balance = balance * (1 + (trade_pnl_pct / 100))
        
        # Считаем просадку
        if balance > peak_balance:
            peak_balance = balance
        
        drawdown = (peak_balance - balance) / peak_balance * 100
        if drawdown > max_drawdown:
            max_drawdown = drawdown

    total_trades = winning_trades + losing_trades
    winrate = winning_trades / total_trades * 100
    roi = ((balance - initial_balance) / initial_balance) * 100
    
    print(f"📊 РЕЗУЛЬТАТЫ БЭКТЕСТА (Только LONG, порог > {threshold*100}%):")
    print(f"Всего сделок: {total_trades}")
    print(f"Винрейт: {winrate:.1f}% ({winning_trades} плюсов / {losing_trades} минусов)")
    print(f"Максимальная просадка (Max Drawdown): {max_drawdown:.2f}%")
    print(f"Итоговый баланс: ${balance:.2f}")
    print(f"Чистая прибыль (ROI): {roi:.2f}%")

if __name__ == '__main__':
    run_financial_backtest()