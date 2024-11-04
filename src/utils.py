import random

from telegram import ChatFullInfo
from telegram.constants import ChatType


def shuffle_pair[T](items: list[T]) -> dict[T, T]:
    items = items.copy()
    random.shuffle(items)
    return {items[i]: items[(i + 1) % len(items)] for i in range(len(items))}

def fmt_name(chat_info: ChatFullInfo) -> str:
    assert chat_info.type == ChatType.PRIVATE
    strs = []
    full_name = f"{chat_info.first_name or ""} {chat_info.last_name or ""}".strip() or "Unnamed"
    strs.append(f"[{full_name}](tg://user?id={chat_info.id})")

    username = chat_info.username
    if username:
        strs.append(rf"\(@{username}\)")

    return " ".join(strs)
