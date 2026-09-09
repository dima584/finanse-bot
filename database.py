"""
=============================================================
  DATABASE.PY — БАЗА ДАННЫХ
=============================================================
Хранит пользователей, подписки и историю сигналов.
Используем SQLite (простая файловая БД, не нужен сервер).
"""

import sqlite3
import json
from datetime import datetime, timedelta
from config import DATABASE_URL

# Путь к файлу БД
DB_PATH = DATABASE_URL.replace("sqlite:///", "")


def get_connection():
    """Получить соединение с базой данных"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row   # Результаты как словари
    return conn


def init_database():
    """
    Создать все таблицы при первом запуске.
    """
    conn = get_connection()
    cursor = conn.cursor()
    
    # ── Таблица пользователей ──
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id              INTEGER PRIMARY KEY,
            telegram_id     INTEGER UNIQUE NOT NULL,
            username        TEXT,
            first_name      TEXT,
            subscription    TEXT DEFAULT 'free',    -- free / basic / pro / vip
            sub_expires_at  TEXT,                   -- дата истечения подписки
            signals_today   INTEGER DEFAULT 0,
            last_signal_date TEXT,
            language        TEXT DEFAULT 'ru',      -- Выбранный язык
            created_at      TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Пытаемся добавить колонку language, если БД была создана в старой версии бота
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN language TEXT DEFAULT 'ru'")
    except sqlite3.OperationalError:
        pass # Колонка уже существует
    # Пытаемся добавить колонку profit_pct для статистики, если её нет
    try:
        cursor.execute("ALTER TABLE signals ADD COLUMN profit_pct REAL DEFAULT 0.0")
    except sqlite3.OperationalError:
        pass # Колонка уже существует

    try:
        cursor.execute("ALTER TABLE users ADD COLUMN deposit REAL DEFAULT 0.0")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN risk_pct REAL DEFAULT 0.0")
    except sqlite3.OperationalError:
        pass

    conn.commit()
    
    # ── Таблица сигналов ──
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol      TEXT NOT NULL,
            timeframe   TEXT NOT NULL,
            direction   TEXT NOT NULL,
            entry_price REAL NOT NULL,
            stop_loss   REAL NOT NULL,
            take_profit TEXT NOT NULL,
            risk_score  INTEGER NOT NULL,
            confidence  REAL NOT NULL,
            indicators  TEXT,
            status      TEXT DEFAULT 'active',
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
            closed_at   TEXT,
            profit_pct  REAL DEFAULT 0.0,
            pnl_usd     REAL DEFAULT 0.0
        )
    """)
    
    # ── Таблица истории пользовательских сигналов ──
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_signals (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER,
            signal_id   INTEGER,
            sent_at     TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id)   REFERENCES users(telegram_id),
            FOREIGN KEY (signal_id) REFERENCES signals(id)
        )
    """)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ml_dataset (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            symbol TEXT,
            timeframe TEXT,
            close_price REAL,
            rsi REAL,
            macd REAL,
            adx REAL,
            ema9_dist_pct REAL
        )
    ''')
    
    conn.commit()
    conn.close()
    print("✅ База данных инициализирована")


def upgrade_database():
    """Додає колонку pnl_usd для відстеження реальних доларів, якщо її ще немає"""
    conn = sqlite3.connect('signals.db')
    cursor = conn.cursor()
    try:
        cursor.execute("ALTER TABLE signals ADD COLUMN pnl_usd REAL DEFAULT 0.0")
        conn.commit()
    except sqlite3.OperationalError:
        # Колонка вже існує
        pass
    finally:
        conn.close()

upgrade_database()


# ════════════════════════════════════════════
#  ФУНКЦИИ ДЛЯ РАБОТЫ С ПОЛЬЗОВАТЕЛЯМИ
# ════════════════════════════════════════════

def get_or_create_user(telegram_id: int, username: str = None, first_name: str = None):
    """Найти пользователя или создать нового"""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
    user = cursor.fetchone()
    
    if not user:
        cursor.execute("""
            INSERT INTO users (telegram_id, username, first_name, language)
            VALUES (?, ?, ?, 'ru')
        """, (telegram_id, username, first_name))
        conn.commit()
        cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
        user = cursor.fetchone()
    
    conn.close()
    return dict(user)


def set_user_language(telegram_id: int, lang: str):
    """Сохранить выбранный язык пользователя"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET language = ? WHERE telegram_id = ?", (lang, telegram_id))
    conn.commit()
    conn.close()


def get_user(telegram_id: int):
    """Получить пользователя по ID"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
    user = cursor.fetchone()
    conn.close()
    return dict(user) if user else None


def get_all_users():
    """Получить всех пользователей (для рассылки)"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users")
    users = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return users


def update_subscription(telegram_id: int, plan: str, days: int):
    """Обновить подписку пользователя"""
    expires_at = (datetime.now() + timedelta(days=days)).isoformat()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE users 
        SET subscription = ?, sub_expires_at = ?
        WHERE telegram_id = ?
    """, (plan, expires_at, telegram_id))
    conn.commit()
    conn.close()


def is_subscription_active(user: dict) -> bool:
    """Проверить активна ли подписка"""
    if user["subscription"] == "free":
        return True
    if not user["sub_expires_at"]:
        return False
    expires = datetime.fromisoformat(user["sub_expires_at"])
    return datetime.now() < expires


def can_receive_signal(user: dict, max_signals: int) -> bool:
    """Проверить не превышен ли лимит сигналов на сегодня"""
    today = datetime.now().date().isoformat()
    if user["last_signal_date"] != today:
        return True   # Новый день — лимит сбросился
    return user["signals_today"] < max_signals


def increment_signal_count(telegram_id: int):
    """Увеличить счётчик сигналов за день"""
    today = datetime.now().date().isoformat()
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        UPDATE users 
        SET signals_today = CASE 
                WHEN last_signal_date = ? THEN signals_today + 1
                ELSE 1
            END,
            last_signal_date = ?
        WHERE telegram_id = ?
    """, (today, today, telegram_id))
    conn.commit()
    conn.close()


# ════════════════════════════════════════════
#  ФУНКЦИИ ДЛЯ РАБОТЫ С СИГНАЛАМИ
# ════════════════════════════════════════════

def save_signal(signal_data: dict) -> int:
    """Сохранить сигнал в БД, вернуть его ID"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO signals 
        (symbol, timeframe, direction, entry_price, stop_loss, take_profit, 
         risk_score, confidence, indicators)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        signal_data["symbol"],
        signal_data["timeframe"],
        signal_data["direction"],
        signal_data["entry_price"],
        signal_data["stop_loss"],
        json.dumps(signal_data["take_profit"]),
        signal_data["risk_score"],
        signal_data["confidence"],
        json.dumps(signal_data.get("indicators", {}))
    ))
    signal_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return signal_id


def get_recent_signals(limit: int = 10):
    """Получить последние сигналы"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM signals 
        ORDER BY created_at DESC 
        LIMIT ?
    """, (limit,))
    signals = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return signals

def get_active_signals():
    """Получить все незакрытые сигналы"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM signals WHERE status LIKE 'active%'")
    signals = []
    for row in cursor.fetchall():
        d = dict(row)
        d['take_profit'] = json.loads(d['take_profit']) # Декодируем JSON массив тейков
        signals.append(d)
    conn.close()
    return signals

def update_signal_status(signal_id: int, status: str, stop_loss: float = None, pct: float = None, pnl_usd: float = 0.0):
    conn = get_connection()
    cursor = conn.cursor()
    
    if status.startswith('closed'):
        cursor.execute("""
            UPDATE signals
            SET status = ?, profit_pct = COALESCE(?, profit_pct), pnl_usd = ?,
                closed_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (status, pct, pnl_usd, signal_id))
    else:
        cursor.execute("""
            UPDATE signals 
            SET status = ?, stop_loss = ?
            WHERE id = ?
        """, (status, stop_loss, signal_id))
        
    conn.commit()
    conn.close()

def get_daily_stats(telegram_id: int = None):
    conn = get_connection()
    cursor = conn.cursor()
    
    if telegram_id:
        cursor.execute("""
            SELECT symbol, status, profit_pct, pnl_usd 
            FROM signals 
            WHERE telegram_id = ? AND created_at >= date('now') AND status LIKE 'closed_%'
        """, (telegram_id,))
    else:
        cursor.execute("""
            SELECT symbol, status, profit_pct, pnl_usd 
            FROM signals 
            WHERE created_at >= date('now') AND status LIKE 'closed_%'
        """)
        
    rows = cursor.fetchall()
    result = []
    
    for row in rows:
        result.append({
            "symbol": row[0],
            "status": row[1],
            "profit_pct": row[2],
            "pnl_usd": row[3] if len(row) > 3 and row[3] is not None else 0.0 
        })
        
    conn.close()
    return result

def update_user_deposit(telegram_id: int, deposit: float):
    """Обновить размер депозита пользователя"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET deposit = ? WHERE telegram_id = ?", (deposit, telegram_id))
    conn.commit()
    conn.close()

def update_user_risk(telegram_id: int, risk_pct: float):
    """Обновить процент риска на сделку"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET risk_pct = ? WHERE telegram_id = ?", (risk_pct, telegram_id))
    conn.commit()
    conn.close()

def cleanup_zombie_signals(tid: int):
    """Сигнали тепер глобальні, ця функція вимкнена, щоб не видаляти чужі угоди"""
    pass

def save_user_signal(user_id: int, signal_id: int):
    """Записує, що конкретний користувач отримав конкретний сигнал"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO user_signals (user_id, signal_id) 
        VALUES (?, ?)
    """, (user_id, signal_id))
    conn.commit()
    conn.close()

def get_users_for_signal(signal_id: int):
    """Отримує список користувачів, яким було надіслано цей сигнал"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT user_id FROM user_signals WHERE signal_id = ?
    """, (signal_id,))
    users = [{"telegram_id": row[0]} for row in cursor.fetchall()]
    conn.close()
    return users

def get_signal_by_id(signal_id: int):
    """Отримати всі дані конкретного сигналу за його ID"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM signals WHERE id = ?", (signal_id,))
    row = cursor.fetchone()
    conn.close()
    
    if row:
        sig = dict(row)
        sig['take_profit'] = json.loads(sig['take_profit'])
        return sig
    return None

# ── НОВАЯ ФУНКЦИЯ ДЛЯ СБОРА ДАТАСЕТА ──
def save_ml_snapshot(symbol, timeframe, entry_price, r, ml, adx_v, dist_ema9_pct, btc_trend, vol_pct, cvd):
    # Добавляем 3 новые переменные в INSERT запрос
    try:
        conn = sqlite3.connect('trading_bot.db')
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO ml_dataset (symbol, timeframe, entry_price, rsi, macd, adx, dist_ema9_pct, btc_trend, volatility_pct, cvd_15m)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (symbol, timeframe, entry_price, r, ml, adx_v, dist_ema9_pct, btc_trend, vol_pct, cvd))
        conn.commit()
    except Exception as e:
        print(f"DB Error: {e}")
    finally:
        conn.close()