import asyncio
from collections.abc import Callable, Awaitable
from functools import wraps
from typing import Any, Union

from telegram import Update, ChatFullInfo
from telegram.constants import ChatType, ParseMode
from telegram.ext import CallbackContext, BaseHandler, CommandHandler, Application, PollAnswerHandler

from literals import JOIN_STRING
from models import UserId, GroupId
from models.game import Game
from models.group import Group
from stores.store import Store
from utils import shuffle_pair, fmt_name


def restrict_to_chat_type(message: str, chat_types: set[ChatType]):
    def decorator(callback: Callable[[Any, Update, CallbackContext], Awaitable[None]]):
        @wraps(callback)
        async def wrapper(self: Any, update: Update, context: CallbackContext):
            if update.message.chat.type not in chat_types:
                await update.message.reply_text(message)
                return
            await callback(self, update, context)

        return wrapper

    return decorator


class SantaBot:
    def __init__(self, store: Store, application: Application):
        self.__store = store
        self.__id = None
        self.__application = application
        application.add_handlers(self._get_handlers())

    @property
    def application(self) -> Application:
        return self.__application

    async def __get_chat_info(self, chat_id: Union[UserId, GroupId]) -> ChatFullInfo:
        return await self.__application.bot.get_chat(chat_id)

    @staticmethod
    def require_me(callback: Callable[[Any, Update, CallbackContext], Awaitable[None]]):
        @wraps(callback)
        async def wrapper(self: 'SantaBot', update: Update, context: CallbackContext):
            if self.__id is None:
                me = await self.__application.bot.get_me()
                self.__id = me.id
            await callback(self, update, context)

        return wrapper

    @restrict_to_chat_type("Use this command in a group to start new Secret Santa game",
                           {ChatType.GROUP, ChatType.SUPERGROUP})
    async def _handle_new(self, update: Update, _: CallbackContext):
        splits = update.message.text.split(" ", 1)
        if len(splits) == 1:
            await update.message.reply_text("Please provide a name to identify the new Secret Santa game")
            return

        new_game_name = splits[1]
        group = Group(update.message.chat.id, update.message.chat.title)

        if await self.__store.game_exists(new_game_name, group):
            await update.message.reply_text(f"The game {new_game_name} already exists in this group")
            return

        poll_message = await update.message.chat.send_poll(
            question=new_game_name,
            options=[JOIN_STRING, "No thanks"],
            is_anonymous=False,
        )
        sender_id = update.message.from_user.id
        leader = await self.__get_chat_info(sender_id)

        await poll_message.reply_markdown_v2(f"Recruitment for __{new_game_name}__ has started\! Vote on the poll "
                                             f"above to join as a Secret Santa\\.\n\n"
                                             f"When ready, the leader {fmt_name(leader)} can reply /shuffle to the "
                                             f"poll to start allocating Santas\\.")

        poll_id = poll_message.poll.id
        await self.__store.create_game(new_game_name, group, poll_id, sender_id)

    @require_me
    @restrict_to_chat_type("Use this command in a group to shuffle a Secret Santa game",
                           {ChatType.GROUP, ChatType.SUPERGROUP})
    async def _handle_shuffle(self, update: Update, _: CallbackContext):
        if (not update.message.reply_to_message
                or not update.message.reply_to_message.poll
                or update.message.reply_to_message.from_user.id != self.__id):
            await update.message.reply_text("Reply /shuffle to a Secret Santa poll to shuffle the participants")
            return
        poll_id = update.message.reply_to_message.poll.id
        leader_id = await self.__store.get_leader(poll_id)
        leader = await self.__get_chat_info(leader_id)
        if leader_id != update.message.from_user.id:
            await update.message.reply_markdown_v2(f"Only the leader for this poll {fmt_name(leader)} can start shuffling")
            return
        game = await self.__store.get_game(poll_id)
        users = await self.__store.get_users(poll_id)

        if len(users) < 4:
            await update.message.reply_text("You need at least 4 players to start a Secret Santa")
            return
        pairings = shuffle_pair(users)
        await self.__store.save_pairings(poll_id, pairings)

        group: ChatFullInfo = await self.__get_chat_info(game.group_id)

        # collect usernames while updating users
        usernames = []
        async def update_user(santa_id, recipient_id):
            recipient: ChatFullInfo = await self.__get_chat_info(recipient_id)
            if recipient.username:
                usernames.append(recipient.username)
            await self.__application.bot.send_message(
                santa_id,
                (f"You have been assigned as the Secret Santa for {fmt_name(recipient)} "
                 rf"for '__{game.name}__' in *{group.title}*\!"),
                parse_mode=ParseMode.MARKDOWN_V2
            )

        await asyncio.gather(*[update_user(santa_id, recipient_id) for santa_id, recipient_id in pairings.items()])

        notify_message = "\n".join(["I have shuffled the Secret Santas and sent your pairings in our private chats! If "
                                    "you did not receive a message from me, /start a private chat with me now.\n",
                                    ", ".join((f"@{username}" for username in usernames)),
                                    f"\nIf you would like to reshuffle, the leader {fmt_name(leader)} can reply "
                                    f"/shuffle to the poll again. Send /status to me privately to see your latest "
                                    "allocations."])
        await update.message.reply_text(notify_message)

    async def _handle_poll_answer(self, update: Update, _: CallbackContext):
        if 0 in update.poll_answer.option_ids:
            await self.__store.add_user_to_game(update.poll_answer.user.id, update.poll_answer.poll_id)
        else:
            await self.__store.remove_user_from_game(update.poll_answer.user.id, update.poll_answer.poll_id)

    @restrict_to_chat_type("Send this to me as a private message instead",
                           {ChatType.PRIVATE})
    async def _handle_status(self, update: Update, _: CallbackContext):
        user_id = update.message.from_user.id
        pairings = await self.__store.get_pairings(user_id)
        if not pairings:
            message = r"You are currently not in any Secret Santas\!"
        else:
            messages = ["These are whom you are the Secret Santa for:\n"]

            async def build_message(game: Game, recipient_id: UserId):
                group, recipient = await asyncio.gather(self.__get_chat_info(game.group_id),
                                                        self.__get_chat_info(recipient_id))
                return rf"__{game.name}__ \(*{group.title}*\): {fmt_name(recipient)}"

            messages.extend(await asyncio.gather(*(build_message(game, recipient_id) for game, recipient_id in pairings)))
            message = "\n".join(messages)
        await update.message.reply_markdown_v2(message)

    @restrict_to_chat_type(
        "Use /new to start a new Secret Santa here.\n\nSend /start to me as a private message for more information.",
        {ChatType.PRIVATE})
    async def _handle_start(self, update: Update, callback_context: CallbackContext):
        await update.message.reply_text(
            "Welcome to the Secret Santa Bot! I can help you to organise a Secret Santa in a group, just add me to the group and send /new")
        await self._handle_status(update, callback_context)

    def _get_handlers(self) -> list[BaseHandler]:
        return [
            CommandHandler("new", self._handle_new),
            CommandHandler("shuffle", self._handle_shuffle),
            CommandHandler(["status"], self._handle_status),
            CommandHandler(["start"], self._handle_start),
            PollAnswerHandler(self._handle_poll_answer),
        ]
