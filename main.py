import os
import asyncio
import json
from dotenv import load_dotenv

from assistant import Assistant
from client import Client
from gif_fetcher import GIFFetcher
from search_engine import SearchEngine
from time_manager import TimeManager
from casino import Casino

async def main():
    load_dotenv()

    gif_fetcher = GIFFetcher(
        token=os.getenv('GOOGLE_API_KEY')
    )

    search_engine = SearchEngine(
        api_key=os.getenv('GOOGLE_API_KEY'), 
        search_engine_id=os.getenv('GOOGLE_SEARCH_ENGINE_ID')
    )

    time_manager = TimeManager()

    casino = Casino()

    assistant = Assistant(
        api_key=os.getenv('OPENAI_API_KEY')
    )

    client = Client(
        token=os.getenv('TELEGRAM_API_KEY'), 
        assistant=assistant, 
        gif_fetcher=gif_fetcher,
        search_engine=search_engine,
        casino=casino,
        time_manager=time_manager
    )

    await client.run()

if __name__ == "__main__":
    asyncio.run(main())
