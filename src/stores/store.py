from abc import ABC, abstractmethod

from models import UserId, PollId
from models.game import Game
from models.group import Group

type Pairings = dict[UserId, UserId]

class Store(ABC):
    @abstractmethod
    async def create_game(self, game_name: str, group: Group, poll_id: PollId, leader_id: UserId):
        pass

    @abstractmethod
    async def get_game(self, poll_id: PollId) -> Game:
        pass

    @abstractmethod
    async def get_leader(self, poll_id: PollId) -> UserId:
        pass

    @abstractmethod
    async def game_exists(self, game_name: str, group: Group) -> bool:
        pass

    @abstractmethod
    async def save_pairings(self, poll_id: PollId, pairings: Pairings):
        pass

    @abstractmethod
    async def get_users(self, poll_id: PollId) -> list[UserId]:
        pass

    @abstractmethod
    async def get_pairings(self, user_id: UserId) -> list[tuple[Game, UserId]]:
        pass

    @abstractmethod
    async def add_user_to_game(self, user_id: UserId, poll_id: PollId):
        pass

    @abstractmethod
    async def remove_user_from_game(self, user_id: UserId, poll_id: PollId):
        pass