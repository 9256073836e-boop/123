import asyncio
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from config import BOT_TOKEN

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def start_command(message: types.Message):
    await message.answer(
        "✈️ Привет! Я бот для поиска горящих туров.\n"
        "Я успешно запущен на сервере PythonAnywhere и готов к работе! 🚀"
    )

@dp.message()
async def echo(message: types.Message):
    await message.answer(f"✅ Бот на связи. Вы написали: \"{message.text}\"")

async def main():
    print("Бот запускается...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())