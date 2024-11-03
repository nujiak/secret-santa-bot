from collections import defaultdict
from typing import override

from bidict import bidict

from models import UserId, GroupId, PollId
from models.game import Game
from models.group import Group
from stores.store import Store, Pairings


class MemoryStore(Store):
    def __init__(self):
        self.__group_games: dict[GroupId, set[Game]] = defaultdict(set)
        self.__user_games: dict[UserId, set[Game]] = defaultdict(set)
        self.__game_users: dict[Game, set[UserId]] = defaultdict(set)
        self.__pairings: dict[Game, dict[UserId, UserId]] = defaultdict(dict)
        self.__polls: bidict[Game, PollId] = bidict()

    @override
    async def get_game(self, poll_id: PollId) -> Game:
        return self.__polls.inverse[poll_id]

    @override
    async def create_game(self, game_name: str, group: Group, poll_id: PollId):
        assert not await self.game_exists(game_name, group)
        new_game = Game(name=game_name, group_id=group.id)
        self.__group_games[group.id].add(new_game)
        self.__polls[new_game] = poll_id

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

        old_pairings = self.__pairings[game]
        for user in old_pairings:
            self.__user_games[user].remove(game)

        self.__pairings[game] = pairings
        for user_id in pairings:
            self.__user_games[user_id].add(game)


    @override
    async def get_users(self, poll_id: PollId) -> list[UserId]:
        game = await self.get_game(poll_id)
        return list(self.__game_users[game])

    @override
    async def get_pairings(self, user_id: UserId) -> list[tuple[Game, UserId]]:
        games = self.__user_games[user_id]

        return [(game, self.__pairings[game][user_id]) for game in games]

    @override
    async def add_user_to_game(self, user_id: UserId, poll_id: PollId):
        game = await self.get_game(poll_id)
        self.__game_users[game].add(user_id)
        self.__user_games[user_id].add(game)

    @override
    async def remove_user_from_game(self, user_id: UserId, poll_id: PollId):
        game = await self.get_game(poll_id)
        self.__game_users[game].remove(user_id)
        self.__user_games[user_id].remove(game)
