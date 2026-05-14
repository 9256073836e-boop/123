import asyncio
from travelpayouts_api import search_hotels

async def test():
    hotels = await search_hotels("Стамбул", limit=3)
    for h in hotels:
        print(f"{h['name']} — {h['price']} ₽")

asyncio.run(test())
