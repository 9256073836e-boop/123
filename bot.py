import os
import asyncio
import logging
import sqlite3
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from openai import AsyncOpenAI

# === НАСТРОЙКИ ===
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

if not BOT_TOKEN or not OPENROUTER_API_KEY:
    raise ValueError("Не заданы BOT_TOKEN или OPENROUTER_API_KEY")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

client = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
)

# === ХРАНИЛИЩЕ (SQLite или память) ===
use_sqlite = False
try:
    # Проверяем, можем ли мы создать и записать в файл
    test_conn = sqlite3.connect("history.db")
    test_conn.execute("CREATE TABLE IF NOT EXISTS test (id INTEGER)")
    test_conn.close()
    use_sqlite = True
    logger.info("✅ SQLite доступен, история будет сохранена в файл history.db")
except Exception as e:
    logger.warning(f"⚠️ SQLite не работает: {e}. Буду хранить историю в памяти (потеряется при перезапуске).")
    use_sqlite = False

if use_sqlite:
    # Инициализация таблицы
    conn = sqlite3.connect("history.db")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS history (
            user_id INTEGER,
            role TEXT,
            content TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.close()
else:
    memory_storage = {}  # user_id -> list of messages

def get_history(user_id: int, limit: int = 10):
    if use_sqlite:
        try:
            conn = sqlite3.connect("history.db")
            cur = conn.execute(
                "SELECT role, content FROM history WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?",
                (user_id, limit)
            )
            rows = cur.fetchall()
            conn.close()
            return [{"role": r[0], "content": r[1]} for r in reversed(rows)]
        except Exception as e:
            logger.error(f"Ошибка чтения из SQLite: {e}")
            return []
    else:
        hist = memory_storage.get(user_id, [])
        return hist[-limit:]

def add_to_history(user_id: int, role: str, content: str):
    if use_sqlite:
        try:
            conn = sqlite3.connect("history.db")
            conn.execute(
                "INSERT INTO history (user_id, role, content) VALUES (?, ?, ?)",
                (user_id, role, content)
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Ошибка записи в SQLite: {e}")
    else:
        if user_id not in memory_storage:
            memory_storage[user_id] = []
        memory_storage[user_id].append({"role": role, "content": content})
        # ограничим до 50 сообщений
        if len(memory_storage[user_id]) > 50:
            memory_storage[user_id] = memory_storage[user_id][-50:]

def clear_history(user_id: int):
    if use_sqlite:
        try:
            conn = sqlite3.connect("history.db")
            conn.execute("DELETE FROM history WHERE user_id = ?", (user_id,))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Ошибка очистки SQLite: {e}")
    else:
        memory_storage.pop(user_id, None)

# === ОБРАБОТЧИКИ КОМАНД ===
@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    await message.answer(
        "✈️ Добро пожаловать!\n\n"
        "Я туристический помощник на базе DeepSeek V4 Flash.\n"
        "Могу посоветовать отели, рассказать о погоде, найти интересные места.\n\n"
        "Я помню последние 10 сообщений. Чтобы очистить память, напишите /reset"
    )

@dp.message(Command("reset"))
async def reset_cmd(message: types.Message):
    clear_history(message.from_user.id)
    await message.answer("🧹 История диалога очищена. Начинаем с чистого листа!")

@dp.message(Command("help"))
async def help_cmd(message: types.Message):
    await message.answer(
        "Просто напиши свой вопрос. Примеры:\n"
        "– Какая погода в Турции в мае?\n"
        "– Посоветуй недорогой отель в Сочи у моря.\n"
        "– Что посмотреть в Санкт-Петербурге за 3 дня?\n\n"
        "Я отвечаю с учётом предыдущих сообщений. /reset – очистить память."
    )

# === ОСНОВНОЙ ОБРАБОТЧИК ===
@dp.message()
async def chat_with_deepseek(message: types.Message):
    user_id = message.from_user.id
    user_text = message.text

    # Сохраняем вопрос пользователя
    add_to_history(user_id, "user", user_text)

    # Берём последние 10 сообщений из истории
    context = get_history(user_id, 10)

    # Формируем запрос к OpenAI
    messages_for_api = [
        {"role": "system", "content": "Ты — дружелюбный и полезный туристический ассистент. Отвечай кратко, по делу, на русском языке."}
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

        # Сохраняем ответ бота
        add_to_history(user_id, "assistant", answer)

        await message.answer(answer)
    except Exception as e:
        logger.error(f"OpenRouter error: {e}")
        await message.answer("⚠️ Ошибка при обращении к ИИ. Попробуйте позже.")

# === ЗАПУСК ===
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
