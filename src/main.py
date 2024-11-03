import logging
import os

from telegram.ext import Application

from santa_bot import SantaBot
from stores.memory_store import MemoryStore

if __name__ == "__main__":
    logging.basicConfig(format='%(asctime)s [%(levelname)s] (%(name)s) - %(message)s', level=logging.INFO)
    application = Application.builder().token(os.getenv("SANTA_BOT_TOKEN")).build()
    santa_bot = SantaBot(MemoryStore(), application)
    application.run_polling(0.1)