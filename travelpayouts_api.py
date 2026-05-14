# travelpayouts_api.py
import aiohttp
import logging
from config import TRAVELPAYOUTS_TOKEN

logger = logging.getLogger(__name__)

async def search_hotels(destination: str, limit: int = 5):
    """
    Поиск отелей через Travelpayouts Hotellook API.
    destination: название города (например, 'Стамбул', 'Москва')
    limit: максимальное количество результатов
    """
    url = "https://engine.hotellook.com/api/v2/cache.json"
    params = {
        "location": destination,
        "currency": "rub",
        "token": TRAVELPAYOUTS_TOKEN,
        "limit": limit,
        "lang": "ru"
    }
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, params=params, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    hotels = []
                    for hotel in data.get('hotels', [])[:limit]:
                        hotels.append({
                            "id": str(hotel.get("locationId", "")),
                            "name": hotel.get("name", "Название не указано"),
                            "price": hotel.get("priceAvg", 0),
                            "link": f"https://hotellook.com/search?destId={hotel.get('locationId')}"
                        })
                    return hotels
                else:
                    logger.error(f"Travelpayouts API error: {resp.status}")
                    return []
        except Exception as e:
            logger.error(f"Travelpayouts request failed: {e}")
            return []
