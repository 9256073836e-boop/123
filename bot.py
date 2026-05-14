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
TRAVELPAYOUTS_TOKEN = os.getenv("TRAVELPAYOUTS_TOKEN", "")  # опционально

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
    # Таблица для статистики отелей (если понадобится)
    c.execute('''
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

# === ПОГОДА через wttr.in ===
async def get_weather(location: str) -> str:
    url = f"https://wttr.in/{location}?format=%t:+%C&lang=ru"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    text = text.strip()
                    if text and not text.startswith("Unknown"):
                        return f"🌍 Погода в *{location}*: {text}"
                    else:
                        return f"❌ Не удалось найти '{location}'"
                else:
                    return f"⚠️ Ошибка {resp.status}"
        except Exception as e:
            logger.error(f"wttr.in error: {e}")
            return "⚠️ Ошибка запроса погоды"

def extract_location_from_weather_query(text: str) -> str | None:
    text_lower = text.lower()
    patterns = [
        r'погод[ауе]?\s+в\s+([а-яёa-z\s\-]+?)(?:[.!?]|$)',
        r'температур[ауе]?\s+в\s+([а-яёa-z\s\-]+?)(?:[.!?]|$)',
        r'weather\s+in\s+([a-z\s\-]+?)(?:[.!?]|$)',
        r'какая\s+погода\s+в\s+([а-яёa-z\s\-]+?)(?:[.!?]|$)',
        r'сколько\s+градусов\s+в\s+([а-яёa-z\s\-]+?)(?:[.!?]|$)',
    ]
    for pattern in patterns:
        m = re.search(pattern, text_lower)
        if m:
            loc = m.group(1).strip()
            if loc:
                return loc.capitalize()
    return None

# === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===
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

# === ФУНКЦИЯ АВТОИСПРАВЛЕНИЯ ОПЕЧАТОК ===
def fix_typos(text: str) -> str:
    """Исправляет типичные орфографические ошибки в ответах бота."""
    replacements = {
        "Стамбукву": "Стамбул",
        "Стамбулбул": "Стамбул",
        "Стамбуква": "Стамбул",
        "Москвпе": "Москва",
        "Москвеы": "Москве",
        "Питерб": "Питер",
        "Египета": "Египет",
        "Туцрция": "Турция",
        "Анталиья": "Анталья",
        "Сочии": "Сочи",
        "Казаньь": "Казань",
    }
    for wrong, correct in replacements.items():
        text = text.replace(wrong, correct)
    return text

# === НАСТРОЙКА МЕНЮ КОМАНД ===
async def set_main_menu(bot: Bot):
    commands = [
        BotCommand(command="/start", description="Главное меню"),
        BotCommand(command="/help", description="Справка"),
        BotCommand(command="/reset", description="Очистить историю диалога"),
        BotCommand(command="/stats", description="Статистика (админ)"),
        BotCommand(command="/time", description="Текущее время и дата"),
        BotCommand(command="/weather", description="Погода в городе (например, /weather Москва)"),
    ]
    await bot.set_my_commands(commands)

# === ОБРАБОТЧИКИ КОМАНД ===
@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    user_id = message.from_user.id
    update_user_activity(user_id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Начать заново", callback_data="fake_start")]
    ])
    await message.answer(
        "✈️ Добро пожаловать в туристического помощника Drygin Travel!\n\n"
        "Я помогу подобрать тур, расскажу о погоде и достопримечательностях.\n"
        "Просто задай вопрос в свободной форме.\n\n"
        "Команды: /help – список команд, /reset – очистить память, /time – время, /weather <город>.",
        reply_markup=kb
    )

@dp.callback_query(lambda c: c.data == "fake_start")
async def fake_start_cb(callback: types.CallbackQuery):
    await callback.answer("Начинаем заново! Используйте /reset для очистки истории.")
    await callback.message.answer("Напишите /reset, чтобы очистить историю диалога.")

@dp.message(Command("reset"))
async def reset_cmd(message: types.Message):
    clear_history(message.from_user.id)
    await message.answer("🧹 История диалога очищена.")

@dp.message(Command("stats"))
async def stats_cmd(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔️ Доступ запрещён.")
        return
    users, msgs = get_stats()
    balance = await get_openrouter_balance()
    bal_text = f"{balance:.2f} USD" if balance else "не удалось"
    await message.answer(f"📊 Статистика:\n👤 Пользователей: {users}\n💬 Сообщений: {msgs}\n💰 Баланс: {bal_text}")

@dp.message(Command("time"))
async def time_cmd(message: types.Message):
    d, t, s = get_current_datetime_str()
    await message.answer(f"🕒 {t} МСК\n📅 {d}\n🍂 {s}")

@dp.message(Command("weather"))
async def weather_cmd(message: types.Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("🌦️ Укажите город: /weather Москва")
        return
    loc = parts[1].strip()
    await message.answer(f"🔍 Ищу погоду в {loc}...")
    info = await get_weather(loc)
    await message.answer(info, parse_mode="Markdown")

@dp.message(Command("help"))
async def help_cmd(message: types.Message):
    await message.answer(
        "Команды:\n"
        "/start – приветствие\n"
        "/help – эта справка\n"
        "/reset – очистить память диалога\n"
        "/time – текущее время и дата\n"
        "/weather <город> – погода\n"
        "/stats – статистика (админ)\n\n"
        "Просто напишите, что ищете: «Тур в Турцию на 7 ночей», «Отели в Стамбуле», «Погода в Сочи»."
    )

# === ГЛАВНЫЙ ОБРАБОТЧИК С НОВЫМ ПРОМПТОМ И АВТОИСПРАВЛЕНИЕМ ===
@dp.message()
async def chat_handler(message: types.Message):
    user_id = message.from_user.id
    user_text = message.text

    update_user_activity(user_id)
    save_message(user_id, user_text)
    add_to_history(user_id, "user", user_text)

    # Проверка на погоду
    if any(kw in user_text.lower() for kw in ('погод', 'температур', 'weather')):
        loc = extract_location_from_weather_query(user_text)
        if loc:
            await bot.send_chat_action(message.chat.id, "typing")
            weather_info = await get_weather(loc)
            await message.answer(weather_info, parse_mode="Markdown")
            add_to_history(user_id, "assistant", weather_info)
            return
        else:
            await message.answer("🌍 Уточните город, например: 'погода в Риме'")
            return

    # --- НОВЫЙ СИСТЕМНЫЙ ПРОМПТ (с требованием грамотности) ---
    date_str, time_str, season = get_current_datetime_str()
    system_prompt = (
        "Ты — добрый и ненавязчивый туристический помощник бота 'Drygin Travel'. "
        "Твоя речь должна быть грамотной: пиши названия стран, городов, отелей и других имён собственных без ошибок. "
        "Если сомневаешься в написании — используй самый распространённый вариант. Не выдумывай несуществующих слов.\n\n"
        "Твоя главная цель — помочь клиенту найти идеальный тур без давления. "
        "Относись к пользователю как к другу, который выбирает отпуск.\n\n"
        "Правила общения:\n"
        "1. Сначала спроси, чем тебе помочь, поздоровайся. Будь вежлив и приветлив.\n"
        "2. Если клиент хочет подобрать тур, задавай только один вопрос за раз, чтобы не перегружать. "
        "Двигайся по шагам: направление → даты → продолжительность → состав (взрослые, дети) → бюджет (только если клиент сам упоминает цену) → пожелания по отелю.\n"
        "3. Не будь продавцом-консультантом, который давит. Используй мягкие фразы: «Может быть, вам понравится...», «Вот несколько вариантов...», «Как смотрите на...». "
        "Избегай слов «успейте», «только сегодня», «скидка», «лучшее предложение».\n"
        "4. Если клиент не готов или говорит «просто смотрю», поддержи его: «Понимаю, выбор отпуска — это важно. Не торопитесь. Я здесь, если будут вопросы».\n"
        "5. Никогда не выводи список параметров в блок === ПАРАМЕТРЫ ПОИСКА === без явного согласия клиента. "
        "Сначала спроси: «Хотите, я поищу актуальные туры по этим параметрам?». Если клиент говорит «да» или «конечно» — тогда покажи блок. "
        "Если «нет» или «позже» — поблагодари и предложи вернуться.\n"
        "6. Если клиент согласился на поиск, выведи блок в строгом формате:\n"
        "=== ПАРАМЕТРЫ ПОИСКА ===\n"
        "Направление: {страна, регион}\n"
        "Даты: {диапазон или месяц}\n"
        "Ночей: {количество}\n"
        "Взрослые: {число}\n"
        "Дети: {возраста или 'нет'}\n"
        "Бюджет: {цифра или 'не указан'}\n"
        "Звёздность: {от 1 до 5 или 'не указана'}\n"
        "Питание: {тип или 'не указано'}\n"
        "Расположение: {пожелание или 'не указано'}\n\n"
        "Всегда сохраняй искреннюю заботу и уважение. Не переходи к параметрам, если клиент явно не хочет обсуждать. "
        "Пиши без орфографических ошибок, особенно названия городов (например, Стамбул, Москва, Анталья)."
    )

    context = get_history(user_id, 10)
    messages_for_api = [
        {"role": "system", "content": system_prompt}
    ] + context

    await bot.send_chat_action(message.chat.id, "typing")

    try:
        response = await client.chat.completions.create(
            model="deepseek/deepseek-v4-flash",
            messages=messages_for_api,
            max_tokens=800,
            temperature=0.7,
        )
        answer = response.choices[0].message.content

        # Исправляем типичные опечатки
        answer = fix_typos(answer)

        add_to_history(user_id, "assistant", answer)
        await message.answer(answer)

        # Если в ответе есть блок параметров, логируем его (для будущей интеграции с API туров)
        if "=== ПАРАМЕТРЫ ПОИСКА ===" in answer:
            logger.info(f"Параметры поиска от {user_id}:\n{answer}")

    except Exception as e:
        logger.error(f"OpenRouter error: {e}")
        await message.answer("⚠️ Ошибка при обращении к ИИ. Попробуйте позже.")

# === ЗАПУСК ===
async def main():
    await set_main_menu(bot)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
