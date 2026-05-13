import os
import asyncio
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from openai import AsyncOpenAI

# Загружаем переменные окружения (на Bothost они задаются через панель)
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

if not BOT_TOKEN or not OPENROUTER_API_KEY:
    raise ValueError("Не заданы переменные окружения BOT_TOKEN или OPENROUTER_API_KEY")

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Инициализируем клиент OpenRouter
client = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
)

# Команда /start
@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    await message.answer(
        "✈️ Привет!Я уникальный бот который поможет тебе выбрать идеальный отдых!\n"
        "Задавай любые вопросы о путешествиях, отелях, странах – я помогу и подберу самый лучший тур!\n\n"
        ""
    )

# Команда /help
@dp.message(Command("help"))
async def help_cmd(message: types.Message):
    await message.answer(
        "Просто напиши свой вопрос. Примеры:\n"
        "– Какая погода в Турции в мае?\n"
        "– Посоветуй недорогой отель в Сочи у моря.\n"
        "– Что посмотреть в Санкт-Петербурге за 3 дня?\n\n"
        "Я отвечу с использованием DeepSeek V4 Flash."
    )

# Обработчик всех текстовых сообщений
@dp.message()
async def chat_with_deepseek(message: types.Message):
    user_text = message.text
    await bot.send_chat_action(message.chat.id, "typing")
    try:
        response = await client.chat.completions.create(
            model="deepseek/deepseek-v4-flash",
            messages=[
                {"role": "system", "content": "Ты — дружелюбный и полезный туристический ассистент. Отвечай кратко, по делу, на русском языке."},
                {"role": "user", "content": user_text}
            ],
            max_tokens=500,
            temperature=0.7,
        )
        answer = response.choices[0].message.content
        await message.answer(answer)
    except Exception as e:
        logging.error(f"OpenRouter error: {e}")
        await message.answer("⚠️ Ошибка при обращении к ИИ. Попробуйте позже.")

# Запуск поллинга
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
