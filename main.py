# from logging_config import logger

import json
from assistant import Assistant
from client import Client
import os

# logging.basicConfig(
#     format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
# )

# # set higher logging level for httpx to avoid all GET and POST requests being logged
# logging.getLogger("httpx").setLevel(logging.WARNING)

# logger = logging.getLogger(__name__)

def main():
    # with open("api_keys.json") as file:
    #     api_keys = json.load(file)

    assistant = Assistant(api_key=os.getenv('OPENAI_API_KEY'))
    client = Client(token=os.getenv('TELEGRAM_API_KEY'), assistant=assistant)

    client.run()

if __name__ == "__main__":
    main()
