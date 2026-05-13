# ... (остальные импорты) ...
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from openai import AsyncOpenAI
import sqlite3
import json
from datetime import datetime

# ... (остальные переменные окружения) ...

# Настройка базы данных
DB_NAME = "history.db"

def init_db():
    """Создаёт таблицу для истории, если её нет."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS history (
            user_id INTEGER,
            role TEXT,
            content TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def get_history(user_id: int, limit: int = 10):
    """Возвращает последние `limit` сообщений пользователя."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT role, content FROM history
        WHERE user_id = ?
        ORDER BY timestamp DESC LIMIT ?
    ''', (user_id, limit))
    rows = cursor.fetchall()
    conn.close()
    # Возвращаем список в правильном порядке (от старых к новым)
    return [{"role": role, "content": content} for role, content in reversed(rows)]

def add_to_history(user_id: int, role: str, content: str):
    """Добавляет новое сообщение в историю."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO history (user_id, role, content)
        VALUES (?, ?, ?)
    ''', (user_id, role, content))
    conn.commit()
    conn.close()

def clear_history(user_id: int):
    """Очищает историю диалога пользователя."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM history WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()
