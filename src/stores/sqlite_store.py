import logging
import os
import sqlite3
from typing import Callable, Optional, Union, override

from models import UserId, PollId, MessageId
from models.game import Game
from models.group import Group
from stores.store import Store, Pairings

DEFAULT_SAVE_FILE_PATH = "./secret_santa.sqlite3"

class SchemaManager:
    @staticmethod
    def set_up_schema(connection: sqlite3.Connection):
        logger = logging.getLogger(SchemaManager.__name__)
        current_version = connection.execute("PRAGMA user_version").fetchone()[0]

        logger.info(f"Current sqlite schema version: {current_version}")

        upgrade_functions: list[Callable[[sqlite3.Connection], None]] = [
            SchemaManager.__upgrade_schema_1,
            SchemaManager.__upgrade_schema_2,
            SchemaManager.__upgrade_schema_3,
        ]

        for upgrade_fn in upgrade_functions[current_version:]:
            upgrade_fn(connection)

        if logger.isEnabledFor(logging.INFO):
            logger.info(f"Upgraded sqlite schema version to: %s",
                        connection.execute("PRAGMA user_version").fetchone()[0])


    @staticmethod
    def __upgrade_schema_1(connection: sqlite3.Connection):
        with connection:
            connection.execute("""CREATE TABLE game(
                                    poll_id TEXT PRIMARY KEY,
                                    name TEXT NOT NULL,
                                    group_id INTEGER NOT NULL,
                                    leader_id INTEGER NOT NULL,
                                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                                    UNIQUE (name, group_id)
                                  )""")
            connection.execute("""CREATE TABLE participant(
                                    game_id INTEGER REFERENCES game(poll_id),
                                    user_id INTEGER NOT NULL
                                  )""")
            connection.execute("""CREATE TABLE pairing(
                                    poll_id INTEGER REFERENCES game(poll_id),
                                    reshuffle INTEGER NOT NULL DEFAULT 0,
                                    santa_id INTEGER NOT NULL,
                                    recipient_id INTEGER NOT NULL,
                                    UNIQUE (poll_id, reshuffle, santa_id),
                                    UNIQUE (poll_id, reshuffle, recipient_id)
                                    )""")
            connection.execute("""PRAGMA user_version = 1""")

    @staticmethod
    def __upgrade_schema_2(connection: sqlite3.Connection):
        with connection:
            connection.execute("""CREATE TABLE wishlist(
                                    poll_id TEXT PRIMARY KEY REFERENCES game(poll_id),
                                    message_id INTEGER UNIQUE
                                  )""")
            connection.execute("""CREATE TABLE wishlist_item(
                                    wishlist_id TEXT REFERENCES wishlist(poll_id),
                                    user_id INTEGER NOT NULL,
                                    description TEXT NOT NULL,
                                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                                    PRIMARY KEY (wishlist_id, user_id)
                                  )""")
            connection.execute("""PRAGMA user_version = 2""")

    @staticmethod
    def __upgrade_schema_3(connection: sqlite3.Connection):
        with connection:
            connection.execute("""CREATE TABLE user(
                                    user_id INTEGER PRIMARY KEY,
                                    reference TEXT NOT NULL,
                                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                                  )""")
            connection.execute("""PRAGMA user_version = 3""")


class SqliteStore(Store):
    def __init__(self, db_file_path: Union[str, os.PathLike] = DEFAULT_SAVE_FILE_PATH):
        if db_file_path is None:
            db_file_path = DEFAULT_SAVE_FILE_PATH
        self.__connection = sqlite3.connect(db_file_path, autocommit=False)
        self.__logger = logging.getLogger(self.__class__.__name__)
        SchemaManager.set_up_schema(self.__connection)

    async def create_game(self, game_name: str, group: Group, poll_id: PollId, leader_id: UserId):
        data = {
            "poll_id": poll_id,
            "name": game_name,
            "group_id": group.id,
            "leader_id": leader_id
        }
        with self.__connection:
            self.__connection.execute("""INSERT INTO game(poll_id, name, group_id, leader_id)
                                         VALUES (:poll_id, :name, :group_id, :leader_id)""",
                                      data)

    async def get_game(self, poll_id: PollId) -> Optional[Game]:
        with self.__connection:
            data = self.__connection.execute("""SELECT name, group_id FROM game WHERE poll_id = :poll_id""",
                                             {"poll_id": poll_id}).fetchone()
        if data is None:
            return None
        name, group_id = data
        return Game(name, group_id)

    async def get_leader(self, poll_id: PollId) -> UserId:
        self.__logger.debug("Getting leader for poll_id: %s", poll_id)
        with self.__connection:
            data = self.__connection.execute("""SELECT leader_id FROM game WHERE poll_id = :poll_id""",
                                             {"poll_id": poll_id}).fetchone()
        return data[0]

    async def game_exists(self, game_name: str, group: Group) -> bool:
        with self.__connection:
            data = self.__connection.execute("""SELECT 1 FROM game WHERE name = :name AND group_id = :group_id""",
                                             {"name": game_name, "group_id": group.id}).fetchone()
        return data is not None

    async def save_pairings(self, poll_id: PollId, pairings: Pairings):
        with self.__connection:
            reshuffle_data = self.__connection.execute("""SELECT COALESCE(MAX(reshuffle), 0) 
                                                          FROM pairing 
                                                          WHERE poll_id = :poll_id""",
                                                       {"poll_id": poll_id}).fetchone()
            reshuffles = reshuffle_data[0]
            data = ({"poll_id": poll_id, "reshuffle": reshuffles + 1,
                     "santa_id": santa_id, "recipient_id": recipient_id}
                    for santa_id, recipient_id in pairings.items())
            self.__connection.executemany("""INSERT INTO pairing 
                                             VALUES (:poll_id, :reshuffle, :santa_id, :recipient_id)""",
                                          data)

    async def get_users(self, poll_id: PollId) -> list[UserId]:
        with self.__connection:
            data = self.__connection.execute("""SELECT user_id FROM participant WHERE game_id = :poll_id""",
                                      {"poll_id": poll_id}).fetchall()
        return [user_id for user_id, in data]

    async def get_pairings(self, user_id: UserId) -> list[tuple[Game, UserId]]:
        with self.__connection:
            data = self.__connection.execute("""WITH last_shuffle AS (
                                                    SELECT poll_id, MAX(reshuffle) as reshuffle
                                                    FROM pairing GROUP BY poll_id
                                                  )
                                                  SELECT name, group_id, recipient_id
                                                  FROM pairing INNER JOIN last_shuffle USING (poll_id, reshuffle)
                                                    INNER JOIN game USING (poll_id)
                                                  WHERE santa_id = :user_id""",
                               {"user_id": user_id}).fetchall()
        if data is None:
            return []
        return [(Game(name, group_id), recipient_id) for name, group_id, recipient_id in data]

    async def get_game_pairings(self, poll_id: PollId) -> Optional[Pairings]:
        with self.__connection:
            data = self.__connection.execute("""WITH last_shuffle AS (
                                                    SELECT poll_id, MAX(reshuffle) as reshuffle
                                                    FROM pairing GROUP BY poll_id
                                                  )
                                                  SELECT santa_id, recipient_id
                                                  FROM pairing INNER JOIN last_shuffle USING (poll_id, reshuffle)
                                                  WHERE poll_id = :poll_id""",
                                           {"poll_id": poll_id}).fetchall()
        self.__logger.debug("Fetched pairings for %s: %s", poll_id, data)
        if not data:
            return None
        return {santa_id: recipient_id for santa_id, recipient_id in data}

    async def add_user_to_game(self, user_id: UserId, poll_id: PollId):
        with self.__connection:
            self.__connection.execute("""INSERT INTO participant VALUES (:poll_id, :user_id)""",
                                      {"poll_id": poll_id, "user_id": user_id})

    async def remove_user_from_game(self, user_id: UserId, poll_id: PollId):
        with self.__connection:
            self.__connection.execute("""DELETE FROM participant
                                         WHERE game_id = :poll_id AND user_id = :user_id""",
                                      {"poll_id": poll_id, "user_id": user_id})

    @override
    async def create_wishlist(self, poll_id: PollId, message_id: MessageId):
        with self.__connection:
            self.__connection.execute("""INSERT INTO wishlist(poll_id, message_id) VALUES (:poll_id, :message_id)
                                         ON CONFLICT (poll_id) DO UPDATE SET message_id = excluded.message_id""",
                                      {"poll_id": poll_id, "message_id": message_id})

    @override
    async def update_wishlist(self, poll_id: PollId, user_id: UserId, description: str):
        with self.__connection:
            self.__connection.execute("""INSERT INTO wishlist_item(wishlist_id, user_id, description, updated_at)
                                         VALUES (:poll_id, :user_id, :description, CURRENT_TIMESTAMP)
                                         ON CONFLICT (wishlist_id, user_id) DO 
                                           UPDATE SET description = excluded.description,
                                                      updated_at = excluded.updated_at""",
                                      {"poll_id": poll_id, "user_id": user_id, "description": description})

    @override
    async def get_wishlist_id(self, message_id: MessageId) -> Optional[PollId]:
        with self.__connection:
            data = self.__connection.execute("SELECT poll_id FROM wishlist WHERE message_id = :message_id",
                                             {"message_id": message_id}).fetchone()
        if data is None:
            return None
        return data[0]

    @override
    async def get_wishlist(self, poll_id: PollId) -> dict[UserId, str]:
        with self.__connection:
            data = self.__connection.execute("""SELECT user_id, description FROM wishlist_item
                                                WHERE wishlist_id = :poll_id""",
                                             {"poll_id": poll_id}).fetchall()
        return {user_id: description for user_id, description in data}

    @override
    async def get_wishlist_message_id(self, poll_id: PollId) -> Optional[MessageId]:
        with self.__connection:
            data = self.__connection.execute("SELECT message_id FROM wishlist WHERE poll_id = :poll_id",
                                             {"poll_id": poll_id}).fetchone()
        if data is None:
            return None
        return data[0]

    @override
    async def get_user_reference(self, user_id: UserId) -> Optional[str]:
        with self.__connection:
            data = self.__connection.execute("""SELECT reference FROM user WHERE user_id = :user_id""",
                                             {"user_id": user_id}).fetchone()
        if data is None:
            return None
        return data[0]

    @override
    async def save_user_reference(self, user_id: UserId, reference: str):
        with self.__connection:
            self.__connection.execute("""INSERT INTO user
                                         VALUES (:user_id, :reference, CURRENT_TIMESTAMP)
                                         ON CONFLICT (user_id) DO
                                           UPDATE SET reference = excluded.reference,
                                                      updated_at = excluded.updated_at""",
                                      {"user_id": user_id, "reference": reference})
