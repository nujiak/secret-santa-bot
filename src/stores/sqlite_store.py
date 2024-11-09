import logging
import os
import sqlite3
from typing import Callable, Optional, Union

from models import UserId, PollId
from models.game import Game
from models.group import Group
from stores.store import Store, Pairings


class SchemaManager:
    @staticmethod
    def set_up_schema(connection: sqlite3.Connection):
        logger = logging.getLogger(SchemaManager.__name__)
        current_version = connection.execute("PRAGMA user_version").fetchone()[0]

        logger.info(f"Current sqlite schema version: {current_version}")

        upgrade_functions: list[Callable[[sqlite3.Connection], None]] = [SchemaManager.__upgrade_schema_1]

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


class SqliteStore(Store):
    def __init__(self, db_file_path: Union[str, os.PathLike] = "./secret-santa.sqlite3"):
        if db_file_path is None:
            db_file_path = ":memory:"
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
