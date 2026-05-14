import os
import asyncio
import logging
import sqlite3
import aiohttp
import re
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BotCommand
from openai import AsyncOpenAI

# === ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ ===
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY", "6189670b498e5b761b8ebeaf5e23d253")

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

# === ИНИЦИАЛИЗАЦИЯ БАЗЫ ДАННЫХ ===
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            first_seen TIMESTAMP,
            last_active TIMESTAMP
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            text TEXT,
            timestamp TIMESTAMP
        )
    ''')
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

# === ПОГОДА ===
async def get_weather(city: str) -> str:
    """Возвращает строку с погодой для заданного города через OpenWeatherMap"""
    url = "https://api.openweathermap.org/data/2.5/weather"
    params = {
        "q": city,
        "appid": OPENWEATHER_API_KEY,
        "units": "metric",
        "lang": "ru"
    }
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    temp = data['main']['temp']
                    feels_like = data['main']['feels_like']
                    humidity = data['main']['humidity']
                    wind = data['wind']['speed']
                    desc = data['weather'][0]['description']
                    return (f"🌍 Погода в городе *{city}*:\n"
                            f"🌡️ Температура: *{temp:.1f}°C* (ощущается как {feels_like:.1f}°C)\n"
                            f"💧 Влажность: {humidity}%\n"
                            f"💨 Ветер: {wind} м/с\n"
                            f"☁️ {desc.capitalize()}")
                elif resp.status == 404:
                    return "❌ Город не найден. Проверьте название."
                else:
                    return "⚠️ Сервис погоды временно недоступен."
        except Exception as e:
            logger.error(f"Погода ошибка: {e}")
            return "⚠️ Ошибка при запросе погоды."

# === ФУНКЦИЯ ИЗВЛЕЧЕНИЯ ГОРОДА С ПРИВЕДЕНИЕМ К ИМЕНИТЕЛЬНОМУ ПАДЕЖУ ===
def extract_city_from_weather_query(text: str) -> str | None:
    """
    Извлекает название города из запроса о погоде.
    Приводит падежную форму (в Риме -> Рим, в Милане -> Милан и т.д.)
    """
    text_lower = text.lower()
    # Поиск по шаблонам: "погода в ...", "температура в ..." и т.д.
    patterns = [
        r'погод[ауе]?\s+в\s+([а-яёa-z\-]+)',
        r'температур[ауе]?\s+в\s+([а-яёa-z\-]+)',
        r'weather\s+in\s+([a-z\-]+)',
        r'прогноз\s+погод[ы]?\s+в\s+([а-яёa-z\-]+)',
        r'сколько\s+градусов\s+в\s+([а-яёa-z\-]+)',
        r'какая\s+погода\s+в\s+([а-яёa-z\-]+)',
        r'что\s+с\s+погодой\s+в\s+([а-яёa-z\-]+)',
    ]
    city_raw = None
    for pattern in patterns:
        match = re.search(pattern, text_lower)
        if match:
            city_raw = match.group(1).strip()
            break
    if not city_raw:
        return None

    # Словарь для приведения к именительному падежу (основные туристические города)
    cases = {
        'риме': 'Рим',
        'милане': 'Милан',
        'париже': 'Париж',
        'лондоне': 'Лондон',
        'берлине': 'Берлин',
        'праге': 'Прага',
        'венеции': 'Венеция',
        'рим': 'Рим',
        'милан': 'Милан',
        'париж': 'Париж',
        'лондон': 'Лондон',
        'берлин': 'Берлин',
        'прага': 'Прага',
        'венеция': 'Венеция',
        'атине': 'Афины',
        'афины': 'Афины',
        'стамбуле': 'Стамбул',
        'стамбул': 'Стамбул',
        'дубае': 'Дубай',
        'дубай': 'Дубай',
        'токио': 'Токио',
        'нью-йорке': 'Нью-Йорк',
        'нью-йорк': 'Нью-Йорк',
        'сиднее': 'Сидней',
        'сидней': 'Сидней',
        'барселоне': 'Барселона',
        'барселона': 'Барселона',
        'мадриде': 'Мадрид',
        'мадрид': 'Мадрид',
        'амстердаме': 'Амстердам',
        'амстердам': 'Амстердам',
        'кёльне': 'Кёльн',
        'кёльн': 'Кёльн',
        'софии': 'София',
        'софия': 'София',
        'хельсинки': 'Хельсинки',
        'осло': 'Осло',
        'стокгольме': 'Стокгольм',
        'стокгольм': 'Стокгольм',
        'копенгагене': 'Копенгаген',
        'копенгаген': 'Копенгаген',
        'брюсселе': 'Брюссель',
        'брюссель': 'Брюссель',
        'вене': 'Вена',
        'вена': 'Вена',
        'занзибаре': 'Занзибар',
        'занзибар': 'Занзибар',
    }
    # Пытаемся найти в словаре
    normalized = cases.get(city_raw)
    if normalized:
        return normalized
    # Если не нашли, пробуем эвристику: убираем окончание "е", "ы", "и" для русских названий
    if city_raw.endswith('е'):
        return city_raw[:-1].capitalize()
    elif city_raw.endswith('ы'):
        return (city_raw[:-1] + 'а').capitalize()
    elif city_raw.endswith('и'):
        return (city_raw[:-1] + 'ы').capitalize()
    # Иначе возвращаем как есть, с заглавной буквы
    return city_raw.capitalize()

# === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ ДАТЫ/ВРЕМЕНИ ===
def get_current_datetime_str():
    now = datetime.utcnow() + timedelta(hours=3)  # МСК
    date_str = now.strftime("%d.%m.%Y")
    time_str = now.strftime("%H:%M")
    month = now.month
    if 3 <= month <= 5:
        season = "весна"
    elif 6 <= month <= 8:
        season = "лето"
    elif 9 <= month <= 11:
        season = "осень"
    else:
        season = "зима"
    return date_str, time_str, season

# === НАСТРОЙКА МЕНЮ КОМАНД ===
async def set_main_menu(bot: Bot):
    main_menu_commands = [
        BotCommand(command="/start", description="Главное меню"),
        BotCommand(command="/help", description="Справка о командах"),
        BotCommand(command="/reset", description="Очистить историю диалога"),
        BotCommand(command="/stats", description="Статистика бота (только админ)"),
        BotCommand(command="/time", description="Текущее время и дата"),
        BotCommand(command="/weather", description="Погода в городе (например, /weather Москва)"),
    ]
    await bot.set_my_commands(main_menu_commands)

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
        "У меня есть память о диалоге. /reset – очистить историю.\n"
        "Администратор может использовать /stats.\n"
        "Текущее время и дату можно узнать по команде /time.\n"
        "Погоду в любом городе — /weather <город> или просто спроси 'какая погода в Риме'.",
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

@dp.message(Command("time"))
async def time_cmd(message: types.Message):
    date_str, time_str, season = get_current_datetime_str()
    await message.answer(
        f"🕒 Текущее время: {time_str} (МСК)\n"
        f"📅 Дата: {date_str}\n"
        f"🍂 Сезон: {season}\n"
        f"(время указано по московскому UTC+3)"
    )

@dp.message(Command("weather"))
async def weather_cmd(message: types.Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("🌦️ Укажите город. Пример: `/weather Москва`", parse_mode="Markdown")
        return
    city = parts[1].strip()
    await message.answer(f"🔍 Ищу погоду в городе *{city}*...", parse_mode="Markdown")
    weather_info = await get_weather(city)
    await message.answer(weather_info, parse_mode="Markdown")

@dp.message(Command("help"))
async def help_cmd(message: types.Message):
    await message.answer(
        "Доступные команды:\n"
        "/start – Главное меню\n"
        "/help – Эта справка\n"
        "/reset – Очистить историю диалога\n"
        "/time – Текущее время и дата\n"
        "/weather <город> – Погода сейчас\n\n"
        "Примеры запросов:\n"
        "– Какая погода в Риме?\n"
        "– Посоветуй недорогой отель в Сочи у моря.\n"
        "– Что посмотреть в Питере за 3 дня?\n\n"
        "Администратор: /stats"
    )

@dp.message()
async def chat_handler(message: types.Message):
    user_id = message.from_user.id
    user_text = message.text

    update_user_activity(user_id)
    save_message(user_id, user_text)
    add_to_history(user_id, "user", user_text)

    # Проверяем, является ли запрос погодным (сейчас)
    weather_keywords = ['погод', 'температур', 'weather', 'прогноз', 'градус', 'на улице']
    is_weather_query = any(kw in user_text.lower() for kw in weather_keywords)
    
    if is_weather_query:
        city = extract_city_from_weather_query(user_text)
        if city:
            await bot.send_chat_action(message.chat.id, "typing")
            weather_info = await get_weather(city)
            await message.answer(weather_info, parse_mode="Markdown")
            add_to_history(user_id, "assistant", weather_info)
            return
        else:
            await message.answer("🌍 Уточните, пожалуйста, город. Например: *какая погода в Риме*", parse_mode="Markdown")
            return

    # Если не погода – обычный диалог с DeepSeek
    context = get_history(user_id, 10)
    date_str, time_str, season = get_current_datetime_str()

    system_prompt = (
        f"Сегодня {date_str}, сейчас {time_str} по московскому времени. Сейчас {season}. "
        "Ты — дружелюбный туристический ассистент. Отвечай кратко, по делу, на русском языке. "
        "Если пользователь спрашивает о погоде в реальном времени, скажи, что у тебя нет доступа к текущим данным, но можешь дать общие рекомендации. "
        "Не выдумывай конкретные температуры или погоду на сегодня, если не уверен."
    )

    messages_for_api = [
        {"role": "system", "content": system_prompt}
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
        add_to_history(user_id, "assistant", answer)
        await message.answer(answer)
    except Exception as e:
        logger.error(f"OpenRouter error: {e}")
        await message.answer("⚠️ Ошибка при обращении к ИИ. Попробуйте позже.")

async def main():
    await set_main_menu(bot)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
