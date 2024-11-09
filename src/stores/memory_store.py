import logging
from collections import defaultdict
from typing import override, Optional

from bidict import bidict

from models import UserId, GroupId, PollId
from models.game import Game
from models.group import Group
from stores.store import Store, Pairings


class MemoryStore(Store):
    def __init__(self):
        self.__logger = logging.getLogger(self.__class__.__name__)
        self.__group_games: dict[GroupId, set[Game]] = defaultdict(set)
        self.__user_games: dict[UserId, set[Game]] = defaultdict(set)
        self.__game_users: dict[Game, set[UserId]] = defaultdict(set)
        self.__pairings: dict[Game, dict[UserId, UserId]] = defaultdict(dict)
        self.__polls: bidict[Game, PollId] = bidict()
        self.__leaders: dict[Game, UserId] = dict()

    @override
    async def get_game(self, poll_id: PollId) -> Game:
        return self.__polls.inverse[poll_id]

    @override
    async def get_leader(self, poll_id: PollId) -> UserId:
        game = await self.get_game(poll_id)
        return self.__leaders[game]

    @override
    async def create_game(self, game_name: str, group: Group, poll_id: PollId, leader_id: UserId):
        assert not await self.game_exists(game_name, group)
        new_game = Game(name=game_name, group_id=group.id)
        self.__group_games[group.id].add(new_game)
        self.__polls[new_game] = poll_id
        self.__leaders[new_game] = leader_id

    @override
    async def game_exists(self, game_name: str, group: Group) -> bool:
        return Game(game_name, group.id) in self.__group_games[group.id]

    @override
    async def save_pairings(self, poll_id: PollId, pairings: Pairings):
        game = await self.get_game(poll_id)
        assert game in self.__group_games[game.group_id]
        # assert all([a != b for a, b in pairings.items()]), "User should not pair with themselves"
        assert set(pairings.keys()) == set(pairings.values()) == self.__game_users[game]
        assert all((game in self.__user_games[user_id] for user_id in self.__game_users[game]))

        self.__pairings[game] = pairings
        logging.info("Saving pairings for game %s (poll_id %s): %s", game, poll_id, pairings)

    @override
    async def get_users(self, poll_id: PollId) -> list[UserId]:
        game = await self.get_game(poll_id)
        return list(self.__game_users[game])

    @override
    async def get_pairings(self, user_id: UserId) -> list[tuple[Game, UserId]]:
        logging.info("Fetching pairings for %s", user_id)
        games = self.__user_games[user_id]

        return [(game, self.__pairings[game][user_id]) for game in games if user_id in self.__pairings[game]]

    @override
    async def get_game_pairings(self, poll_id: PollId) -> Optional[Pairings]:
        game = await self.get_game(poll_id)
        if game not in self.__pairings:
            return None
        return self.__pairings.get(game)

    @override
    async def add_user_to_game(self, user_id: UserId, poll_id: PollId):
        game = await self.get_game(poll_id)
        logging.info("Adding user_id %s to %s (poll_id %s)", user_id, game, poll_id)
        self.__game_users[game].add(user_id)
        self.__user_games[user_id].add(game)

    @override
    async def remove_user_from_game(self, user_id: UserId, poll_id: PollId):
        game = await self.get_game(poll_id)
        logging.info("Removing user_id %s from game %s (poll_id %s)", user_id, game, poll_id)
        self.__game_users[game].remove(user_id)
        self.__user_games[user_id].remove(game)
