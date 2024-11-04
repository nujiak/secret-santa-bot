import asyncio
import datetime
import logging
import os
import argparse
from typing import Literal, Optional

from telegram.ext import Application

from santa_bot import SantaBot
from stores.memory_store import MemoryStore
from stores.pickle_store import PickleStore

type StoreChoice = Literal["memory", "pickle"]

POLLING_INTERVAL_SECONDS = 0.1

def main(store: StoreChoice, save_file: Optional[os.PathLike] = None):
    logging.basicConfig(format='%(asctime)s [%(levelname)s] (%(name)s) - %(message)s', level=logging.INFO)
    application = Application.builder().token(os.getenv("SANTA_BOT_TOKEN")).build()
    if store == "memory":
        _run_with_memory_store(application)
    elif store == "pickle":
        _run_with_pickle_store(application, save_file)

def _run_with_memory_store(application: Application):
    santa_bot = SantaBot(MemoryStore(), application)
    santa_bot.application.run_polling(POLLING_INTERVAL_SECONDS)

def _run_with_pickle_store(application: Application, save_file: os.PathLike):
    pickle_store = PickleStore.create(save_file)
    santa_bot = SantaBot(pickle_store, application)
    santa_bot.application.job_queue.run_repeating(lambda _: pickle_store.pickle(),
                                        name="pickle",
                                        interval=600,
                                        first=datetime.timedelta(seconds=5))

    try:
        application.run_polling(POLLING_INTERVAL_SECONDS)
    except:
        pass
    finally:
        asyncio.run(pickle_store.pickle())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-s", "--store", type=str, choices=["memory", "pickle"], default="memory")
    parser.add_argument("-f", "--save-file", type=str, help="path to pickle file")
    args = parser.parse_args()

    main(args.store, args.save_file)