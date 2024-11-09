import asyncio
import datetime
import logging
from collections.abc import Callable, Awaitable
from functools import wraps
from typing import Any, Union, Optional

from telegram import Update, ChatFullInfo, User, error
from telegram.constants import ChatType, ParseMode
from telegram.ext import CallbackContext, BaseHandler, CommandHandler, Application, PollAnswerHandler, MessageHandler, \
    filters

from literals import JOIN_STRING
from models import UserId, GroupId
from models.game import Game
from models.group import Group
from stores.store import Store
from utils import shuffle_pair, fmt_name, escape


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
        self.__me: Optional[User] = None
        self.__application = application
        self.__logger = logging.getLogger(self.__class__.__name__)
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
            if self.__me is None:
                self.__me = await self.__application.bot.get_me()
            await callback(self, update, context)

        return wrapper

    @restrict_to_chat_type("Use this command in a group to start new Secret Santa game",
                           {ChatType.GROUP, ChatType.SUPERGROUP})
    async def _handle_new(self, update: Update, _: CallbackContext):
        splits = update.message.text.split(" ", 1)
        if len(splits) == 1:
            await update.message.reply_markdown_v2(r"Please provide a name to identify the new Secret Santa game\. "
                                                   "For example:\n\n"
                                                   f"/new _Christmas '{str(datetime.date.today().year)[2:]}_")
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

        await poll_message.reply_markdown_v2(rf"Recruitment for __{escape(new_game_name)}__ has started\! Vote on the poll "
                                             f"above to join as a Secret Santa\\.\n\n"
                                             f"When ready, the leader {fmt_name(leader)} can reply /shuffle to the "
                                             rf"poll to start allocating Santas\.")

        poll_id = poll_message.poll.id
        await self.__store.create_game(new_game_name, group, poll_id, sender_id)

    @require_me
    @restrict_to_chat_type("Use this command in a group to shuffle a Secret Santa game",
                           {ChatType.GROUP, ChatType.SUPERGROUP})
    async def _handle_shuffle(self, update: Update, _: CallbackContext):
        assert self.__me is not None
        if (not update.message.reply_to_message
                or not update.message.reply_to_message.poll
                or update.message.reply_to_message.from_user != self.__me):
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
        players = []
        async def update_user(santa_id, recipient_id):
            recipient: ChatFullInfo = await self.__get_chat_info(recipient_id)
            if recipient.username:
                players.append(recipient)
            try:
                await self.__application.bot.send_message(
                    santa_id,
                    (f"You have been assigned as the Secret Santa for {fmt_name(recipient)} "
                     rf"for '__{escape(game.name)}__' in *{group.title}*\!"),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
            except error.Forbidden:
                self.__logger.info("Cannot send message to %s", recipient)

        await asyncio.gather(*[update_user(santa_id, recipient_id) for santa_id, recipient_id in pairings.items()])

        player_list = sorted((fmt_name(player) for player in players))
        me_url = f"https://t.me/{self.__me.username}"

        notify_message = ("I have shuffled the Secret Santas and sent your pairings "
                          rf"[in our private chats]({me_url})\!{"\n\n"}"
                          "Participants:\n"
                          f"{"\n".join((rf"{i}\. {player_name}" for i, player_name in enumerate(player_list, 1)))}\n\n"
                          f"If you would like to reshuffle, the leader {fmt_name(leader)} can reply "
                          rf"/shuffle to the poll again\. Send /status to [me privately]({me_url}) to see your latest "
                          r"allocations\.")
        await update.message.reply_markdown_v2(notify_message)

    async def _handle_poll_answer(self, update: Update, _: CallbackContext):
        if 0 in update.poll_answer.option_ids:
            await self.__store.add_user_to_game(update.poll_answer.user.id, update.poll_answer.poll_id)
        else:
            await self.__store.remove_user_from_game(update.poll_answer.user.id, update.poll_answer.poll_id)

    async def _handle_status(self, update: Update, callback_context: CallbackContext):
        if update.message.chat.type == ChatType.PRIVATE:
            return await self.__handle_status_in_private(update, callback_context)
        else:
            return await self.__handle_status_in_group(update, callback_context)

    async def __handle_status_in_private(self, update: Update, _: CallbackContext):
        user_id = update.message.from_user.id
        pairings = await self.__store.get_pairings(user_id)
        if not pairings:
            message = r"You are currently not in any Secret Santas\!"
        else:
            messages = ["These are whom you are the Secret Santa for:\n"]

            async def build_message(game: Game, recipient_id: UserId):
                group, recipient = await asyncio.gather(self.__get_chat_info(game.group_id),
                                                        self.__get_chat_info(recipient_id))
                return rf"__{escape(game.name)}__ \(*{escape(group.title)}*\): {fmt_name(recipient)}"

            messages.extend(await asyncio.gather(*(build_message(game, recipient_id) for game, recipient_id in pairings)))
            message = "\n".join(messages)
        await update.message.reply_markdown_v2(message)

    @require_me
    async def __handle_status_in_group(self, update: Update, _: CallbackContext):
        if (not update.message.reply_to_message
                or not update.message.reply_to_message.poll
                or update.message.reply_to_message.from_user != self.__me):
            await update.message.reply_markdown_v2("Reply /status to a Secret Santa poll to see the game's status, "
                                                   "or send /status to me in a "
                                                   f"[private chat](https://t.me/{self.__me.username}) to see all your "
                                                   r"active Secret Santa participation\.")
            return
        poll = update.message.reply_to_message.poll
        poll_id = poll.id
        game, pairings, leader_id = await asyncio.gather(self.__store.get_game(poll_id),
                                                         self.__store.get_game_pairings(poll_id),
                                                         self.__store.get_leader(poll_id))
        leader = await self.__get_chat_info(leader_id)

        if pairings is not None:
            players = await asyncio.gather(*(self.__get_chat_info(user_id) for user_id in pairings.keys()))
            player_list = sorted((fmt_name(player) for player in players))
            message = (rf"__{escape(game.name)}__ \(leader {fmt_name(leader)}\){"\n\n"}"
                       rf"This game has been started and Secret Santas have been shuffled\. Send /status to me in "
                       rf"a [private chat](https://t.me/{self.__me.username}) to see your allocations\.{"\n\n"}"
                       rf"Participants:{"\n"}"
                       f"{"\n".join((rf"{i}\. {player_name}" for i, player_name in enumerate(player_list, 1)))}\n\n")
        else:
            player_ids = await self.__store.get_users(poll_id)
            players = await asyncio.gather(*(self.__get_chat_info(user_id) for user_id in player_ids))
            player_list = sorted((fmt_name(player) for player in players))
            message = (rf"__{escape(game.name)}__ \(leader {fmt_name(leader)}\){"\n\n"}"
                       rf"This game has not started yet\. Once everyone has joined, the leader {fmt_name(leader)} can "
                       rf"reply /shuffle to the poll to start shuffling and allocating Santas\.{"\n\n"}"
                       rf"Potential participants:{"\n"}"
                       f"{"\n".join((rf"{i}\. {player_name}" for i, player_name in enumerate(player_list, 1))) or r"No one has joined yet\!"}")

        await update.message.reply_to_message.reply_markdown_v2(message)


    @restrict_to_chat_type(
        "Use /new to start a new Secret Santa here.\n\nSend /start to me as a private message for more information.",
        {ChatType.PRIVATE})
    async def _handle_start(self, update: Update, callback_context: CallbackContext):
        await update.message.reply_text(
            "Welcome to the Secret Santa Bot! I can help you to organise a Secret Santa in a group, just add me to the group and send /new")
        await self._handle_status(update, callback_context)

    @require_me
    async def _handle_new_members(self, update: Update, _: CallbackContext):
        assert self.__me is not None
        new_members = update.message.new_chat_members
        if self.__me in new_members:
            await update.message.chat.send_message(r"Hi\! I am here to help organise Secret Santas in this group\. "
                                                   "To get started, send:\n\n" 
                                                   "/new _name of Secret Santa Game_",
                                                   parse_mode=ParseMode.MARKDOWN_V2)

    def _get_handlers(self) -> list[BaseHandler]:
        return [
            MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, self._handle_new_members),
            CommandHandler("new", self._handle_new),
            CommandHandler("shuffle", self._handle_shuffle),
            CommandHandler(["status"], self._handle_status),
            CommandHandler(["start"], self._handle_start),
            PollAnswerHandler(self._handle_poll_answer),
        ]
