from dataclasses import dataclass

from models import GroupId


@dataclass(frozen=True)
class Group:
    id: GroupId
    name: str
