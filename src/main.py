import logging
import os
import argparse
from typing import Literal, Optional

from telegram.ext import Application

from santa_bot import SantaBot
from stores.memory_store import MemoryStore
from stores.sqlite_store import SqliteStore

type StoreChoice = Literal["memory", "sqlite"]

POLLING_INTERVAL_SECONDS = 1

def main(store: StoreChoice, save_file: Optional[os.PathLike] = None, run_in_debug: bool = False,
         disable_restrictions: bool = False, dev_id: Optional[int] = None):
    logging.basicConfig(format='%(asctime)s [%(levelname)s] (%(name)s) - %(message)s',
                        level=logging.DEBUG if run_in_debug else logging.INFO)
    application = Application.builder().token(os.getenv("SANTA_BOT_TOKEN")).build()
    match store:
        case "memory":
            _run_with_memory_store(application, disable_restrictions, dev_id)
        case "sqlite":
            _run_with_sqlite(application, save_file, disable_restrictions, dev_id)

def _run_with_memory_store(application: Application, disable_restrictions: bool, dev_id: Optional[int]):
    santa_bot = SantaBot(MemoryStore(), application, disable_restrictions, dev_id)
    santa_bot.application.run_polling(POLLING_INTERVAL_SECONDS)

def _run_with_sqlite(application: Application, save_file: Optional[os.PathLike], disable_restrictions: bool,
                     dev_id: Optional[int]):
    sqlite_store = SqliteStore(save_file)
    santa_bot = SantaBot(sqlite_store, application, disable_restrictions, dev_id)
    santa_bot.application.run_polling(POLLING_INTERVAL_SECONDS)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-s", "--store", type=str, choices=["memory", "sqlite"], default="memory")
    parser.add_argument("-f", "--save-file", type=str, help="path to sqlite file")
    parser.add_argument("--debug", action="store_true", help="run with debug logging")
    parser.add_argument("--no-restriction", action="store_true", help="disable any restrictions")
    parser.add_argument("--dev-id", type=int, help="developer's user id. Used for sending error messages")
    args = parser.parse_args()

    main(args.store, args.save_file,
         run_in_debug=args.debug,
         disable_restrictions=args.no_restriction,
         dev_id=args.dev_id,
    )