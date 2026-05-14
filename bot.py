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

# ========== ПОГОДА через Open-Meteo (геокодинг произвольных мест) ==========
async def get_coordinates(location_query: str):
    """
    Получает широту и долготу для любого географического названия (город, регион, страна, район, аэропорт).
    Использует Open-Meteo Geocoding API.
    """
    url = "https://geocoding-api.open-meteo.com/v1/search"
    params = {
        "name": location_query,
        "count": 1,
        "language": "ru",
        "format": "json"
    }
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("results"):
                        result = data["results"][0]
                        return result["latitude"], result["longitude"]
                    else:
                        return None, None
                else:
                    return None, None
        except Exception as e:
            logger.error(f"Ошибка геокодинга: {e}")
            return None, None

async def get_weather_by_coords(lat: float, lon: float, location_name: str) -> str:
    """Запрашивает текущую погоду по координатам через Open-Meteo Forecast API."""
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,apparent_temperature,wind_speed_10m,relative_humidity_2m,weather_code",
        "wind_speed_unit": "ms",
        "timezone": "Europe/Moscow"
    }
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    current = data.get("current", {})
                    temp = current.get("temperature_2m", "N/A")
                    feels_like = current.get("apparent_temperature", "N/A")
                    humidity = current.get("relative_humidity_2m", "N/A")
                    wind_speed = current.get("wind_speed_10m", "N/A")
                    weather_code = current.get("weather_code", 0)
                    weather_map = {
                        0: "☀️ Ясно", 1: "🌤️ В основном ясно", 2: "⛅ Переменная облачность",
                        3: "☁️ Пасмурно", 45: "🌫️ Туман", 48: "🌫️ Туман (иней)",
                        51: "🌦️ Морось", 53: "🌦️ Морось", 55: "🌦️ Морось",
                        61: "🌧️ Дождь", 63: "🌧️ Дождь", 65: "🌧️ Дождь",
                        71: "❄️ Снегопад", 73: "❄️ Снегопад", 75: "❄️ Снегопад",
                        80: "🌦️ Ливень", 81: "🌦️ Ливень", 82: "🌦️ Ливень",
                    }
                    weather_desc = weather_map.get(weather_code, "🌥️ Облачно")
                    return (f"🌍 Погода в местоположении *{location_name}*:\n"
                            f"🌡️ Температура: *{temp}°C* (ощущается как {feels_like}°C)\n"
                            f"💧 Влажность: {humidity}%\n"
                            f"💨 Ветер: {wind_speed} м/с\n"
                            f"☁️ {weather_desc}")
                else:
                    return f"⚠️ Не удалось получить погоду для '{location_name}'."
        except Exception as e:
            logger.error(f"Open-Meteo forecast error: {e}")
            return "⚠️ Ошибка при запросе погоды."

async def get_weather(location_query: str) -> str:
    """Главная функция получения погоды по произвольному текстовому запросу (геокодинг + погода)."""
    lat, lon = await get_coordinates(location_query)
    if lat is None or lon is None:
        return f"❌ Не удалось найти местоположение '{location_query}'. Проверьте название."
    return await get_weather_by_coords(lat, lon, location_query)

# === ИЗВЛЕЧЕНИЕ МЕСТОПОЛОЖЕНИЯ ИЗ ЗАПРОСА О ПОГОДЕ (естественный язык) ===
def extract_location_from_weather_query(text: str) -> str | None:
    """
    Извлекает название местоположения из сообщения о погоде.
    Обрабатывает опечатки (например, 'москвпе' -> 'Москва').
    """
    text_lower = text.lower()
    
    # Таблица распространённых опечаток и сокращений
    typos = {
        'москвпе': 'Москва', 'спб': 'Санкт-Петербург', 'питер': 'Санкт-Петербург',
        'ростов': 'Ростов-на-Дону', 'нск': 'Новосибирск', 'ебург': 'Екатеринбург',
        'нью-йорк': 'Нью-Йорк', 'вашингтон': 'Вашингтон', 'лондон': 'Лондон',
        'париж': 'Париж', 'берлин': 'Берлин', 'рим': 'Рим', 'милан': 'Милан',
    }
    for wrong, correct in typos.items():
        if wrong in text_lower:
            return correct
    
    # Паттерны для извлечения местоположения после ключевых слов
    patterns = [
        r'погод[ауе]?\s+в\s+(.+?)(?:[.!?]|$)',
        r'температур[ауе]?\s+в\s+(.+?)(?:[.!?]|$)',
        r'weather\s+in\s+(.+?)(?:[.!?]|$)',
        r'прогноз\s+погод[ы]?\s+в\s+(.+?)(?:[.!?]|$)',
        r'сколько\s+градусов\s+в\s+(.+?)(?:[.!?]|$)',
        r'какая\s+погода\s+в\s+(.+?)(?:[.!?]|$)',
        r'что\s+с\s+погодой\s+в\s+(.+?)(?:[.!?]|$)',
        r'погод[ауе]?\s+(.+?)(?:[.!?]|$)',          # "погода Калифорния"
    ]
    for pattern in patterns:
        match = re.search(pattern, text_lower)
        if match:
            location = match.group(1).strip()
            if location:
                return location.capitalize()
    return None

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
        BotCommand(command="/weather", description="Погода в месте (например, /weather Калифорния)"),
    ]
    await bot.set_my_commands(main_menu_commands)

# === ОБРАБОТЧИКИ КОМАНД ===
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
        "Погоду в любом месте (город, регион, страна, район) можно узнать по команде /weather или спросить в чате, например 'какая погода в Калифорнии'.",
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
        await message.answer("🌦️ Укажите местоположение. Пример: `/weather Калифорния` или `/weather Москва`", parse_mode="Markdown")
        return
    location = parts[1].strip()
    await message.answer(f"🔍 Ищу погоду в *{location}*...", parse_mode="Markdown")
    weather_info = await get_weather(location)
    await message.answer(weather_info, parse_mode="Markdown")

@dp.message(Command("help"))
async def help_cmd(message: types.Message):
    await message.answer(
        "Доступные команды:\n"
        "/start – Главное меню\n"
        "/help – Эта справка\n"
        "/reset – Очистить историю диалога\n"
        "/time – Текущее время и дата\n"
        "/weather <место> – Погода сейчас (город, регион, страна, район и т.п.)\n\n"
        "Примеры запросов:\n"
        "– Какая погода в Калифорнии?\n"
        "– Температура в районе Лефортово\n"
        "– Посоветуй недорогой отель в Сочи у моря.\n"
        "– Что посмотреть в Питере за 3 дня?\n\n"
        "Администратор: /stats"
    )

# === ГЛАВНЫЙ ОБРАБОТЧИК (естественные запросы) ===
@dp.message()
async def chat_handler(message: types.Message):
    user_id = message.from_user.id
    user_text = message.text

    update_user_activity(user_id)
    save_message(user_id, user_text)
    add_to_history(user_id, "user", user_text)

    # Проверяем, является ли запрос погодным
    weather_keywords = ['погод', 'температур', 'weather', 'прогноз', 'градус', 'на улице']
    is_weather_query = any(kw in user_text.lower() for kw in weather_keywords)
    
    if is_weather_query:
        location = extract_location_from_weather_query(user_text)
        if location:
            await bot.send_chat_action(message.chat.id, "typing")
            weather_info = await get_weather(location)
            await message.answer(weather_info, parse_mode="Markdown")
            add_to_history(user_id, "assistant", weather_info)
            return
        else:
            await message.answer("🌍 Уточните, пожалуйста, местоположение. Например: *какая погода в Калифорнии*", parse_mode="Markdown")
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
