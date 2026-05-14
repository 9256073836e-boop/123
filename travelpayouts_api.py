import aiohttp
import logging
from config import TRAVELPAYOUTS_TOKEN

logger = logging.getLogger(__name__)

# Словарь для транслитерации популярных городов с русского на английский
CITY_MAP = {
    'москва': 'Moscow',
    'санкт-петербург': 'Saint Petersburg',
    'спб': 'Saint Petersburg',
    'стамбул': 'Istanbul',
    'анкара': 'Ankara',
    'анталия': 'Antalya',
    'сочи': 'Sochi',
    'казань': 'Kazan',
    'екатеринбург': 'Yekaterinburg',
    'новосибирск': 'Novosibirsk',
}

async def search_hotels(destination: str, checkin: str = None, checkout: str = None, limit: int = 5):
    """
    Поиск отелей через Travelpayouts Brand API.
    destination: название города (например, 'Москва', 'Istanbul')
    checkin: дата заезда в формате 'YYYY-MM-DD' (опционально)
    checkout: дата выезда в формате 'YYYY-MM-DD' (опционально)
    limit: максимальное количество результатов
    """
    # Приводим название города к формату API
    city = destination.strip()
    dest_lower = city.lower()
    eng_dest = CITY_MAP.get(dest_lower, city)
    
    # Формируем параметры запроса
    params = {
        "location": eng_dest,
        "currency": "rub",
        "token": TRAVELPAYOUTS_TOKEN,
        "limit": limit,
        "lang": "ru"
    }
    
    # Добавляем даты, если они есть
    if checkin and checkout:
        params["checkIn"] = checkin
        params["checkOut"] = checkout
        # Количество гостей можно сделать параметром, пока оставим 2 по умолчанию
        params["guests"] = 2
    
    url = "https://search.hotellook.com/api/v1/hotels"  # Возможный URL (потребуется уточнение)
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, params=params, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    hotels = []
                    for hotel in data.get('hotels', [])[:limit]:
                        hotels.append({
                            "id": str(hotel.get("hotelId", "")),
                            "name": hotel.get("hotelName", "Название не указано"),
                            "price": hotel.get("priceAvg", 0),
                            "link": hotel.get("deepLink", "#")
                        })
                    return hotels
                else:
                    error_text = await resp.text()
                    logger.error(f"Travelpayouts API error: {resp.status} - {error_text}")
                    return []
        except Exception as e:
            logger.error(f"Travelpayouts request failed: {e}")
            return []
