from abc import ABC, abstractmethod
from typing import Optional

from models import UserId, PollId, MessageId
from models.game import Game
from models.group import Group

type Pairings = dict[UserId, UserId]


class WishlistMixin(ABC):
    @abstractmethod
    async def create_wishlist(self, poll_id: PollId, message_id: MessageId):
        """Create or update a wishlist and its latest message id"""
        pass

    async def get_wishlist_id(self, message_id: MessageId) -> Optional[PollId]:
        """Returns the poll_id of a wishlist with message_id. Returns None if no such wishlist exists."""
        pass

    async def get_wishlist_message_id(self, poll_id: PollId) -> Optional[MessageId]:
        """Returns the message_id of a wishlist with poll_id. Returns None if no such wishlist exists."""
        pass

    @abstractmethod
    async def update_wishlist(self, poll_id: PollId, user_id: UserId, description: str):
        """Upsert an item in the wishlist"""
        pass

    @abstractmethod
    async def get_wishlist(self, poll_id: PollId) -> dict[UserId, str]:
        """Get all wishlist items for a game"""
        pass


class Store(WishlistMixin, ABC):
    @abstractmethod
    async def create_game(self, game_name: str, group: Group, poll_id: PollId, leader_id: UserId):
        pass

    @abstractmethod
    async def get_game(self, poll_id: PollId) -> Optional[Game]:
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
    async def get_game_pairings(self, poll_id: PollId) -> Optional[Pairings]:
        """Returns pairings for a game with poll_id. Returns None if no pairings are saved for this game."""
        pass

    @abstractmethod
    async def add_user_to_game(self, user_id: UserId, poll_id: PollId):
        pass

    @abstractmethod
    async def remove_user_from_game(self, user_id: UserId, poll_id: PollId):
        pass

    @abstractmethod
    async def get_user_reference(self, user_id: UserId) -> Optional[str]:
        """
        Get user reference for tagging a user, formatted in Markdown V2. Returns None if not found.
        """
        pass

    @abstractmethod
    async def save_user_reference(self, user_id: UserId, reference: str):
        """
        Save user reference for tagging a user, formatted in Markdown V2.
        """
        pass
