from dataclasses import dataclass

from models import GroupId


@dataclass(frozen=True)
class Game:
    name: str
    group_id: GroupId
