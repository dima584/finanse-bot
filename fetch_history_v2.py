import requests
import pandas as pd
import ta
import numpy as np
import time
from datetime import datetime, timedelta
from tqdm import tqdm

# Настройки для скальпинга
SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "DOGEUSDT", 
    "TRXUSDT", "LINKUSDT", "MATICUSDT", "DOTUSDT", "LTCUSDT", "BCHUSDT", "SHIBUSDT", 
    "AVAXUSDT", "XLMUSDT", "ATOMUSDT", "UNIUSDT", "XMRUSDT", "ETCUSDT", "FILUSDT", 
    "ICPUSDT", "LDOUSDT", "APTUSDT", "QNTUSDT", "VETUSDT", "NEARUSDT", "OPUSDT", 
    "MKRUSDT", "AAVEUSDT", "INJUSDT", "SNXUSDT", "RNDRUSDT", "SUIUSDT", "ARBUSDT"
]
TIMEFRAME = "15m"
DAYS_BACK = 180
TP_PCT = 0.015   # Тейк 1.5% (было 3%)
SL_PCT = -0.01   # Стоп 1% (было 2%)
LIMIT = 1500

def get_binance_data(symbol, start_time, end_time):
    url = "https://fapi.binance.com/fapi/v1/klines"
    all_klines = []
    current_start = start_time
    
    while current_start < end_time:
        params = {
            "symbol": symbol,
            "interval": TIMEFRAME,
            "startTime": current_start,
            "endTime": end_time,
            "limit": LIMIT
        }
        try:
            response = requests.get(url, params=params)
            data = response.json()
            if not data or type(data) is dict: 
                break
            all_klines.extend(data)
            current_start = data[-1][0] + 1
            time.sleep(0.1) 
        except Exception as e:
            print(f"Ошибка при загрузке {symbol}: {e}")
            break
            
    df = pd.DataFrame(all_klines, columns=[
        'timestamp', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_asset_volume', 'trades',
        'taker_buy_base', 'taker_buy_quote', 'ignore'
    ])
    df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume', 'taker_buy_base']].astype(float)
    return df

def apply_indicators(df, btc_df=None):
    if len(df) < 50:
        return pd.DataFrame()
        
    df['rsi'] = ta.momentum.RSIIndicator(close=df['close'], window=14).rsi()
    df['macd'] = ta.trend.MACD(close=df['close']).macd()
    
    adx_ind = ta.trend.ADXIndicator(high=df['high'], low=df['low'], close=df['close'], window=14)
    df['adx'] = adx_ind.adx()
        
    df['ema9'] = ta.trend.EMAIndicator(close=df['close'], window=9).ema_indicator()
    df['ema9_dist_pct'] = (df['close'] - df['ema9']) / df['ema9'] * 100
    
    df['volatility_pct'] = (df['high'] - df['low']) / df['open'] * 100
    
    buy_vol = df['taker_buy_base']
    sell_vol = df['volume'] - buy_vol
    df['delta'] = buy_vol - sell_vol
    df['cvd_15m'] = df['delta'].rolling(14).sum()
    
    if btc_df is not None:
        df = pd.merge_asof(df.sort_values('timestamp'), btc_df[['timestamp', 'btc_trend']].sort_values('timestamp'), on='timestamp', direction='backward')
    else:
        df['btc_trend'] = df['close'].pct_change(periods=14) * 100

    return df

def label_targets(df):
    targets_long = np.zeros(len(df))
    targets_short = np.zeros(len(df))
    
    close_arr = df['close'].values
    high_arr = df['high'].values
    low_arr = df['low'].values
    
    horizon = 20 # Сократили горизонт удержания до 5 часов (20 свечей) для скальпинга
    
    for i in range(len(df) - horizon):
        entry_price = close_arr[i]
        tp_price_long = entry_price * (1 + TP_PCT)
        sl_price_long = entry_price * (1 + SL_PCT)
        
        tp_price_short = entry_price * (1 - TP_PCT)
        sl_price_short = entry_price * (1 - SL_PCT)
        
        long_success = 0
        short_success = 0
        
        for j in range(i + 1, i + horizon):
            if low_arr[j] <= sl_price_long:
                break 
            if high_arr[j] >= tp_price_long:
                long_success = 1
                break
                
        for j in range(i + 1, i + horizon):
            if high_arr[j] >= sl_price_short:
                break
            if low_arr[j] <= tp_price_short:
                short_success = 1
                break
                
        targets_long[i] = long_success
        targets_short[i] = short_success
        
    df['target_long'] = targets_long
    df['target_short'] = targets_short
    return df

def main():
    print("🚀 Сбор данных V2 (Скальпинг + сырые цены)...")
    end_time = int(datetime.now().timestamp() * 1000)
    start_time = int((datetime.now() - timedelta(days=DAYS_BACK)).timestamp() * 1000)
    
    btc_df = get_binance_data("BTCUSDT", start_time, end_time)
    btc_df['btc_trend'] = ta.trend.EMAIndicator(btc_df['close'], window=14).ema_indicator().pct_change(periods=14) * 100
    
    all_data = []
    
    for symbol in tqdm(SYMBOLS, desc="Обработка монет"):
        df = get_binance_data(symbol, start_time, end_time)
        if df.empty: continue
            
        df = apply_indicators(df, btc_df if symbol != "BTCUSDT" else None)
        df = label_targets(df)
        df['symbol'] = symbol
        
        df = df.dropna().iloc[:-20] 
        all_data.append(df)
        
    final_df = pd.concat(all_data, ignore_index=True)
    
    # Теперь сохраняем и ценовые колонки для будущих локальных тестов
    cols_to_save = ['timestamp', 'symbol', 'open', 'high', 'low', 'close', 'rsi', 'macd', 'adx', 'ema9_dist_pct', 'volatility_pct', 'cvd_15m', 'btc_trend', 'target_long', 'target_short']
    final_df[cols_to_save].to_csv("deep_history_dataset.csv", index=False)
    
    print(f"\n✅ Готово! Собрано {len(final_df)} строк.")

if __name__ == "__main__":
    main()