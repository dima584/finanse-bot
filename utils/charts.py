import io
import matplotlib.pyplot as plt

def create_signal_chart(df_prices, symbol, entry, sl, tp_list):
    """
    Строит график цены и уровней риска в оперативную память.
    Поддерживает отрисовку нескольких тейк-профитов.
    """
    fig, ax = plt.subplots(figsize=(10, 5), facecolor='#1e1e1e')
    ax.set_facecolor('#121212')
    
    # Линия цены по свечам
    ax.plot(df_prices.index, df_prices['close'], color='#00ffcc', linewidth=1.5, label='Цена')
    
    # Уровни входа и стопа
    ax.axhline(entry, color='#3498db', linestyle='--', linewidth=1.2, label=f'Вход: {entry}')
    ax.axhline(sl, color='#e74c3c', linestyle='-', linewidth=1.2, label=f'Стоп: {sl}')
    
    # Отрисовка тейк-профитов (TP1, TP2, TP3)
    if isinstance(tp_list, list) and len(tp_list) > 0:
        colors = ['#2ecc71', '#27ae60', '#1e8449'] # Разные оттенки зеленого для разных TP
        for i, tp in enumerate(tp_list):
            # Берем цвет по индексу (если тейков больше 3, цвета пойдут по кругу)
            color = colors[i % len(colors)]
            ax.axhline(tp, color=color, linestyle=':', linewidth=1.2, label=f'TP{i+1}: {tp}')
    else:
        # На случай, если передали одно число
        ax.axhline(tp_list, color='#2ecc71', linestyle='-', linewidth=1.2, label=f'Тейк: {tp_list}')
    
    ax.set_title(f'VEXORA Signal: {symbol}', color='white', fontsize=12, fontweight='bold')
    ax.tick_params(colors='white')
    ax.grid(True, color='#333333', linestyle='--', alpha=0.5)
    
    # loc='upper left' чтобы легенда не перекрывала правый край графика с текущей ценой
    ax.legend(facecolor='#1e1e1e', edgecolor='none', labelcolor='white', loc='upper left')
    
    # Сохраняем в байтовый буфер
    buf = io.BytesIO()
    plt.savefig(buf, format='png', bbox_inches='tight', dpi=100)
    buf.seek(0)
    plt.close(fig)
    
    return buf