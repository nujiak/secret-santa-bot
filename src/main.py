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

def main(store: StoreChoice, save_file: Optional[os.PathLike] = None):
    logging.basicConfig(format='%(asctime)s [%(levelname)s] (%(name)s) - %(message)s', level=logging.INFO)
    application = Application.builder().token(os.getenv("SANTA_BOT_TOKEN")).build()
    if store == "memory":
        santa_bot = SantaBot(MemoryStore(), application)
    elif store == "pickle":
        pickle_store  = PickleStore.create(save_file)
        santa_bot = SantaBot(pickle_store, application)
        application.job_queue.run_repeating(lambda _: pickle_store.pickle(),
                                            name="pickle",
                                            interval=60,
                                            first=datetime.timedelta(seconds=5))

    application.run_polling(0.1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-s", "--store", type=str, choices=["memory", "pickle"], default="memory")
    parser.add_argument("-f", "--save-file", type=str, help="path to pickle file")
    args = parser.parse_args()

    main(args.store, args.save_file)