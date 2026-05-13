import os
import asyncio
import logging
import sqlite3
import aiohttp
from datetime import datetime
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from openai import AsyncOpenAI

# === ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ ===
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))

if not BOT_TOKEN or not OPENROUTER_API_KEY or not ADMIN_ID:
    raise ValueError("Не заданы BOT_TOKEN, OPENROUTER_API_KEY или ADMIN_ID")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

client = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
)

# === ПУТЬ К БАЗЕ ДАННЫХ (общее хранилище Bothost) ===
SHARED_DIR = os.environ.get('SHARED_DIR', '/app/shared')
os.makedirs(SHARED_DIR, exist_ok=True)
DB_PATH = os.path.join(SHARED_DIR, 'bot_data.db')

# === ИНИЦИАЛИЗАЦИЯ БАЗЫ ДАННЫХ (таблицы для статистики и истории) ===
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # Таблица пользователей
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            first_seen TIMESTAMP,
            last_active TIMESTAMP
        )
    ''')
    # Таблица сообщений (статистика)
    c.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            text TEXT,
            timestamp TIMESTAMP
        )
    ''')
    # Таблица истории диалогов
    c.execute('''
        CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            role TEXT,
            content TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# === ФУНКЦИИ ДЛЯ СТАТИСТИКИ ===
def update_user_activity(user_id: int):
    now = datetime.now().isoformat()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT user_id FROM users WHERE user_id = ?', (user_id,))
    if c.fetchone() is None:
        c.execute('INSERT INTO users (user_id, first_seen, last_active) VALUES (?, ?, ?)',
                  (user_id, now, now))
    else:
        c.execute('UPDATE users SET last_active = ? WHERE user_id = ?', (now, user_id))
    conn.commit()
    conn.close()

def save_message(user_id: int, text: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('INSERT INTO messages (user_id, text, timestamp) VALUES (?, ?, ?)',
              (user_id, text, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_stats():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM users')
    users_count = c.fetchone()[0]
    c.execute('SELECT COUNT(*) FROM messages')
    messages_count = c.fetchone()[0]
    conn.close()
    return users_count, messages_count

# === ФУНКЦИИ ДЛЯ ИСТОРИИ ДИАЛОГА (SQLite) ===
def get_history(user_id: int, limit: int = 10):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        SELECT role, content FROM chat_history
        WHERE user_id = ?
        ORDER BY timestamp DESC LIMIT ?
    ''', (user_id, limit))
    rows = c.fetchall()
    conn.close()
    # возвращаем в хронологическом порядке (от старых к новым)
    return [{"role": r[0], "content": r[1]} for r in reversed(rows)]

def add_to_history(user_id: int, role: str, content: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        INSERT INTO chat_history (user_id, role, content)
        VALUES (?, ?, ?)
    ''', (user_id, role, content))
    conn.commit()
    conn.close()

def clear_history(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('DELETE FROM chat_history WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

# === ПОЛУЧЕНИЕ БАЛАНСА OPENROUTER ===
async def get_openrouter_balance():
    url = "https://openrouter.ai/api/v1/auth/key"
    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}"}
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    credits = data.get('credits', 0)
                    return float(credits)
                else:
                    logger.error(f"Ошибка получения баланса: {resp.status}")
                    return None
        except Exception as e:
            logger.error(f"Ошибка запроса баланса: {e}")
            return None

# === ОБРАБОТЧИКИ ===
@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    user_id = message.from_user.id
    update_user_activity(user_id)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Начать заново", callback_data="fake_start")]
    ])
    await message.answer(
        "✈️ Добро пожаловать в туристического помощника!\n\n"
        "Я помогу с выбором отелей, расскажу о погоде и достопримечательностях.\n"
        "Просто задай вопрос.\n\n"
        "У меня есть память о диалоге (сохраняется в базе). /reset – очистить историю.\n"
        "Администратор может использовать /stats.",
        reply_markup=keyboard
    )

@dp.callback_query(lambda c: c.data == "fake_start")
async def fake_start_callback(callback: types.CallbackQuery):
    await callback.answer("Начинаем заново! Используйте /reset, чтобы очистить память.")
    await callback.message.answer("Чтобы очистить историю диалога, напишите /reset.")

@dp.message(Command("reset"))
async def reset_cmd(message: types.Message):
    user_id = message.from_user.id
    clear_history(user_id)
    await message.answer("🧹 История диалога очищена. Начинаем с чистого листа!")

@dp.message(Command("stats"))
async def stats_cmd(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔️ Доступ запрещён. Эта команда только для администратора.")
        return
    users, msgs = get_stats()
    balance = await get_openrouter_balance()
    balance_text = f"{balance:.2f} USD" if balance is not None else "не удалось получить"
    await message.answer(
        f"📊 Статистика бота:\n"
        f"👤 Уникальных пользователей: {users}\n"
        f"💬 Всего сообщений: {msgs}\n"
        f"💰 Баланс OpenRouter: {balance_text}"
    )

@dp.message(Command("help"))
async def help_cmd(message: types.Message):
    await message.answer(
        "Примеры запросов:\n"
        "– Какая погода в Турции в мае?\n"
        "– Посоветуй недорогой отель в Сочи у моря.\n"
        "– Что посмотреть в Питере за 3 дня?\n\n"
        "Команды: /reset – очистить память, /start – приветствие."
    )

@dp.message()
async def chat_handler(message: types.Message):
    user_id = message.from_user.id
    user_text = message.text

    # Обновляем статистику
    update_user_activity(user_id)
    save_message(user_id, user_text)

    # Сохраняем сообщение пользователя в историю
    add_to_history(user_id, "user", user_text)

    # Получаем последние 10 сообщений из истории
    context = get_history(user_id, 10)

    messages_for_api = [
        {"role": "system", "content": "Ты — дружелюбный туристический ассистент. Отвечай кратко, на русском языке."}
    ] + context

    await bot.send_chat_action(message.chat.id, "typing")

    try:
        response = await client.chat.completions.create(
            model="deepseek/deepseek-v4-flash",
            messages=messages_for_api,
            max_tokens=500,
            temperature=0.7,
        )
        answer = response.choices[0].message.content
        # Сохраняем ответ бота в историю
        add_to_history(user_id, "assistant", answer)
        await message.answer(answer)
    except Exception as e:
        logger.error(f"OpenRouter error: {e}")
        await message.answer("⚠️ Ошибка при обращении к ИИ. Попробуйте позже.")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
