import pandas as pd

def find_thresholds():
    df = pd.read_csv("ml_dataset_labeled.csv")
    
    print("📊 Базовый шанс успеха (если заходить в случайную секунду без индикаторов):")
    print(f"LONG: {df['target_long'].mean()*100:.2f}%")
    print(f"SHORT: {df['target_short'].mean()*100:.2f}%\n")

    print("🎯 Оптимизация RSI для LONG (Ловим перепроданность):")
    for rsi in [45, 40, 35, 30, 25, 20]:
        subset = df[df['rsi'] <= rsi]
        if len(subset) > 0:
            wr = subset['target_long'].mean() * 100
            print(f"RSI <= {rsi:2d} | Сигналов: {len(subset):4d} | Винрейт: {wr:.2f}%")

    print("\n🩸 Оптимизация RSI для SHORT (Ловим перегрев):")
    for rsi in [55, 60, 65, 70, 75, 80]:
        subset = df[df['rsi'] >= rsi]
        if len(subset) > 0:
            wr = subset['target_short'].mean() * 100
            print(f"RSI >= {rsi:2d} | Сигналов: {len(subset):4d} | Винрейт: {wr:.2f}%")

    print("\n📈 Влияние трендового фильтра ADX:")
    for adx in [15, 20, 25, 30, 35]:
        subset = df[df['adx'] >= adx]
        if len(subset) > 0:
            wr_l = subset['target_long'].mean() * 100
            wr_s = subset['target_short'].mean() * 100
            print(f"ADX >= {adx:2d} | Сигналов: {len(subset):4d} | Винрейт LONG: {wr_l:.2f}% | Винрейт SHORT: {wr_s:.2f}%")

    print("\n🔥 СНАЙПЕРСКИЕ КОМБИНАЦИИ (ADX >= 25 + Экстремальный RSI):")
    comb_long = df[(df['rsi'] <= 35) & (df['adx'] >= 25)]
    comb_short = df[(df['rsi'] >= 65) & (df['adx'] >= 25)]
    
    if len(comb_long) > 0:
        print(f"LONG  (RSI <= 35 & ADX >= 25) | Сигналов: {len(comb_long):4d} | Винрейт: {comb_long['target_long'].mean()*100:.2f}%")
    if len(comb_short) > 0:
        print(f"SHORT (RSI >= 65 & ADX >= 25) | Сигналов: {len(comb_short):4d} | Винрейт: {comb_short['target_short'].mean()*100:.2f}%")

if __name__ == "__main__":
    find_thresholds()