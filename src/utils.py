import random
import re

from telegram import ChatFullInfo
from telegram.constants import ChatType


"""
Matches all single characters that have to be escaped, as documented at:

https://core.telegram.org/bots/api#markdownv2-style
"""
MARKDOWNV2_SPECIAL_CHARS_PATTERN = re.compile(r"[_*\[\]()~`>#+\-=|{}.!]")


def shuffle_pair[T](items: list[T]) -> dict[T, T]:
    items = items.copy()
    random.shuffle(items)
    return {items[i]: items[(i + 1) % len(items)] for i in range(len(items))}


def fmt_name(chat_info: ChatFullInfo) -> str:
    assert chat_info.type == ChatType.PRIVATE
    strs = []
    full_name = f"{chat_info.first_name or ""} {chat_info.last_name or ""}".strip() or "Unnamed"
    strs.append(f"[{escape(full_name)}](tg://user?id={chat_info.id})")

    username = chat_info.username
    if username:
        strs.append(rf"\(@{username}\)")

    return " ".join(strs)


def escape(text: str) -> str:
    """Escapes special characters for MarkdownV2"""
    return re.sub(MARKDOWNV2_SPECIAL_CHARS_PATTERN, lambda match: f"\\{match.group()}", text)

