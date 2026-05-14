import sqlite3
import os

# Путь к вашей базе данных (должен совпадать с тем, что в bot.py)
# Если у вас используется общее хранилище Bothost, путь будет примерно такой:
SHARED_DIR = os.environ.get('SHARED_DIR', '/app/shared')
DB_PATH = os.path.join(SHARED_DIR, 'bot_data.db')

# Если вы запускаете локально и база в другой папке, измените путь.
# Например, для локального теста можно задать DB_PATH = 'bot_data.db'

def upgrade():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Создаём таблицу hotel_stats, если её нет
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS hotel_stats (
            hotel_id TEXT PRIMARY KEY,
            hotel_name TEXT,
            times_shown INTEGER DEFAULT 0,
            times_chosen INTEGER DEFAULT 0,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()
    print("✅ Таблица hotel_stats успешно добавлена (или уже существовала).")

if __name__ == "__main__":
    upgrade()
