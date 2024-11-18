import asyncio
import datetime
import logging
from collections.abc import Callable, Awaitable
from functools import wraps
from lib2to3.fixes.fix_input import context
from typing import Any, Union

from telegram import Update, ChatFullInfo, error, Message
from telegram.constants import ChatType, ParseMode
from telegram.ext import CallbackContext, BaseHandler, CommandHandler, Application, PollAnswerHandler, MessageHandler, \
    filters

from literals import JOIN_STRING
from models import UserId, GroupId, PollId
from models.game import Game
from models.group import Group
from stores.store import Store
from utils import shuffle_pair, fmt_name, escape


def restrict_to_chat_type(message: str, chat_types: set[ChatType]):
    def decorator(callback: Callable[[Any, Update, CallbackContext], Awaitable[None]]):
        @wraps(callback)
        async def wrapper(self: Any, update: Update, callback_context: CallbackContext):
            if update.message.chat.type not in chat_types:
                await update.message.reply_text(message)
                return
            await callback(self, update, callback_context)

        return wrapper

    return decorator


class SantaBot:
    def __init__(self, store: Store, application: Application, disable_restrictions: bool):
        self.__store = store
        self.__application = application
        self.__logger = logging.getLogger(self.__class__.__name__)
        self.__disable_restrictions = disable_restrictions
        application.add_handlers(self._get_handlers())

    @property
    def application(self) -> Application:
        return self.__application

    @staticmethod
    async def __get_chat_info(chat_id: Union[UserId, GroupId], callback_context: CallbackContext) -> ChatFullInfo:
        return await callback_context.bot.get_chat(chat_id)

    async def __get_user_reference(self, user_id: UserId, callback_context: CallbackContext) -> str:
        """Returns reference for tagging a user, formatted in Markdown V2"""
        try:
            user = await self.__get_chat_info(user_id, callback_context)
            self.__logger.debug("Fetched user id %s from server, saving to store")
            reference = fmt_name(user)
            await self.__store.save_user_reference(user_id, reference)
        except error.BadRequest:
            self.__logger.debug("Unable to fetch user id %s from server, getting from store instead",
                                user_id)
            reference = self.__store.get_user_reference(user_id)
        return reference

    @staticmethod
    def save_user(callback: Callable[[Any, Update, CallbackContext], Awaitable[None]]):
        """Saves the username for the sender of the message in update"""
        @wraps(callback)
        async def wrapper(self: 'SantaBot', update: Update, callback_context: CallbackContext):
            if update.message and update.message.from_user:
                reference = fmt_name(update.message.from_user)
                await self.__store.save_user_reference(update.message.chat.id, reference)
            await callback(self, update, callback_context)

        return wrapper

    @save_user
    @restrict_to_chat_type("Use this command in a group to start new Secret Santa game",
                           {ChatType.GROUP, ChatType.SUPERGROUP})
    async def _handle_new(self, update: Update, context: CallbackContext):
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
        leader_reference = await self.__get_user_reference(sender_id, context)

        await poll_message.reply_markdown_v2(rf"Recruitment for __{escape(new_game_name)}__ has started\! Vote on the poll "
                                             f"above to join as a Secret Santa\\.\n\n"
                                             f"When ready, the leader {leader_reference} can reply /shuffle to the "
                                             rf"poll to start allocating Santas\.")

        poll_id = poll_message.poll.id
        await self.__store.create_game(new_game_name, group, poll_id, sender_id)

    @save_user
    @restrict_to_chat_type("Use this command in a group to shuffle a Secret Santa game",
                           {ChatType.GROUP, ChatType.SUPERGROUP})
    async def _handle_shuffle(self, update: Update, callback_context: CallbackContext):
        if (not update.message.reply_to_message
                or not update.message.reply_to_message.poll
                or update.message.reply_to_message.from_user != callback_context.bot.bot):
            await update.message.reply_text("Reply /shuffle to a Secret Santa poll to shuffle the participants")
            return
        poll_id = update.message.reply_to_message.poll.id
        leader_id = await self.__store.get_leader(poll_id)
        leader_reference = await self.__get_user_reference(leader_id, callback_context)
        if leader_id != update.message.from_user.id:
            await update.message.reply_markdown_v2(f"Only the leader for this poll {leader_reference} can start shuffling")
            return
        game = await self.__store.get_game(poll_id)
        users = await self.__store.get_users(poll_id)

        if not self.__disable_restrictions and len(users) < 4:
            await update.message.reply_text("You need at least 4 players to start a Secret Santa")
            return
        pairings = shuffle_pair(users)
        await self.__store.save_pairings(poll_id, pairings)

        group: ChatFullInfo = await self.__get_chat_info(game.group_id, callback_context)

        # collect usernames while updating users
        players = []
        async def update_user(santa_id, recipient_id):
            recipient_reference = await self.__get_user_reference(recipient_id, callback_context)
            players.append(recipient_reference)
            try:
                await callback_context.bot.send_message(
                    santa_id,
                    (f"You have been assigned as the Secret Santa for {recipient_reference} "
                     rf"for '__{escape(game.name)}__' in *{group.title}*\!"),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
            except error.Forbidden:
                self.__logger.info("Cannot send message to %s", recipient_reference)

        await asyncio.gather(*[update_user(santa_id, recipient_id) for santa_id, recipient_id in pairings.items()])

        player_list = sorted(players)
        me_url = f"https://t.me/{callback_context.bot.bot.username}"

        notify_message = ("I have shuffled the Secret Santas and sent your pairings "
                          rf"[in our private chats]({me_url})\!{"\n\n"}"
                          "Participants:\n"
                          f"{"\n".join((rf"{i}\. {player_name}" for i, player_name in enumerate(player_list, 1)))}\n\n"
                          f"If you would like to reshuffle, the leader {leader_reference} can reply "
                          rf"/shuffle to the poll again\. Send /status to [me privately]({me_url}) to see your latest "
                          r"allocations\.")
        await update.message.reply_markdown_v2(notify_message)

    @save_user
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

    async def __handle_status_in_private(self, update: Update, callback_context: CallbackContext):
        user_id = update.message.from_user.id
        pairings = await self.__store.get_pairings(user_id)
        if not pairings:
            message = r"You are currently not in any Secret Santas\!"
        else:
            messages = ["These are whom you are the Secret Santa for:\n"]

            async def build_message(game: Game, recipient_id: UserId):
                group, recipient_reference = await asyncio.gather(self.__get_chat_info(game.group_id, callback_context),
                                                                  self.__get_user_reference(recipient_id, callback_context))
                return rf"__{escape(game.name)}__ \(*{escape(group.title)}*\): {recipient_reference}"

            [self.__logger.info(pairing) for pairing in pairings]
            messages.extend(await asyncio.gather(*(build_message(game, recipient_id) for game, recipient_id in pairings)))
            message = "\n".join(messages)
        await update.message.reply_markdown_v2(message)

    @save_user
    async def __handle_status_in_group(self, update: Update, callback_context: CallbackContext):
        if (not update.message.reply_to_message
                or not update.message.reply_to_message.poll
                or update.message.reply_to_message.from_user != callback_context.bot.bot):
            await update.message.reply_markdown_v2("Reply /status to a Secret Santa poll to see the game's status, "
                                                   "or send /status to me in a "
                                                   f"[private chat](https://t.me/{callback_context.bot.bot.username}) to see all your "
                                                   r"active Secret Santa participation\.")
            return
        poll = update.message.reply_to_message.poll
        poll_id = poll.id
        game, pairings, leader_id = await asyncio.gather(self.__store.get_game(poll_id),
                                                         self.__store.get_game_pairings(poll_id),
                                                         self.__store.get_leader(poll_id))
        leader_reference = await self.__get_user_reference(leader_id, callback_context)

        if pairings is not None:
            players = await asyncio.gather(*(self.__get_user_reference(user_id, callback_context) for user_id in pairings.keys()))
            player_list = sorted(players)
            message = (rf"__{escape(game.name)}__ \(leader {leader_reference}\){"\n\n"}"
                       rf"This game has been started and Secret Santas have been shuffled\. Send /status to me in "
                       rf"a [private chat](https://t.me/{callback_context.bot.bot.username}) to see your allocations\.{"\n\n"}"
                       rf"Participants:{"\n"}"
                       f"{"\n".join((rf"{i}\. {player_name}" for i, player_name in enumerate(player_list, 1)))}")
        else:
            player_ids = await self.__store.get_users(poll_id)
            players = await asyncio.gather(*(self.__get_user_reference(user_id, callback_context) for user_id in player_ids))
            player_list = sorted(players)
            message = (rf"__{escape(game.name)}__ \(leader {leader_reference}\){"\n\n"}"
                       rf"This game has not started yet\. Once everyone has joined, the leader {leader_reference} can "
                       rf"reply /shuffle to the poll to start shuffling and allocating Santas\.{"\n\n"}"
                       rf"Potential participants:{"\n"}"
                       f"{"\n".join((rf"{i}\. {player_name}" for i, player_name in enumerate(player_list, 1))) or r"No one has joined yet\!"}")

        logging.debug(message)
        await update.message.reply_to_message.reply_markdown_v2(message)

    @save_user
    async def _handle_wishlist_reply(self, update: Update, context: CallbackContext):
        """Handles user reply to a wishlist message"""
        if not update.message.reply_to_message or update.message.reply_to_message.from_user != context.bot.bot:
            return

        wishlist_id = await self.__store.get_wishlist_id(update.message.reply_to_message.id)
        if not wishlist_id:
            return

        game = await self.__store.get_game(wishlist_id)
        sender_id = update.message.from_user.id

        if not await self.__player_can_access_wishlist(wishlist_id, sender_id, update.message):
            return

        wishlist_description = update.message.text
        await self.__store.update_wishlist(wishlist_id, sender_id, wishlist_description)

        wishlist = await self.__store.get_wishlist(wishlist_id)
        wishlist_message = await self.__construct_wishlist(game, wishlist)
        await update.message.reply_to_message.edit_text(wishlist_message, parse_mode=ParseMode.MARKDOWN_V2)

    @save_user
    @restrict_to_chat_type(
        "Use /wishlist in a group to start or add on to a wishlist",
        {ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL}
    )
    async def _handle_wishlist_command(self, update: Update, context: CallbackContext):
        """Handles when a user sends a /wishlist command"""

        async def resend_wishlist(wishlist_id: PollId):
            sender_id = update.message.from_user.id
            if not await self.__player_can_access_wishlist(wishlist_id, sender_id, update.message):
                return
            splits = update.message.text.split(" ", maxsplit=1)
            if len(splits) > 1:
                new_wishlist_item = splits[1]
                await self.__store.update_wishlist(wishlist_id, sender_id, new_wishlist_item)
            game, wishlist = await asyncio.gather(self.__store.get_game(wishlist_id),
                                                  self.__store.get_wishlist(wishlist_id))
            new_message = await update.message.chat.send_message(await self.__construct_wishlist(game, wishlist),
                                                                 parse_mode=ParseMode.MARKDOWN_V2)
            try:
                await new_message.pin(disable_notification=True)
            except error.BadRequest:
                self.__logger.warning("Unable to pin wishlist")

            old_message_id = await self.__store.get_wishlist_message_id(wishlist_id)
            await self.__store.create_wishlist(wishlist_id, new_message.id)
            try:
                await context.bot.delete_message(chat_id=game.group_id, message_id=old_message_id)
            except:
                pass

        negative_response = "Reply /wishlist to a wishlist or a Secret Santa poll to add/update a wishlist"

        if not update.message.reply_to_message:
            return await update.message.reply_text(negative_response)

        if update.message.reply_to_message.poll and update.message.reply_to_message.from_user == context.bot.bot:
            poll_id = update.message.reply_to_message.poll.id
            return await resend_wishlist(poll_id)

        wishlist_id = await self.__store.get_wishlist_id(update.message.reply_to_message.message_id)
        if not wishlist_id:
            return await update.message.reply_text(negative_response)
        return await resend_wishlist(wishlist_id)

    async def __player_can_access_wishlist(self, wishlist_id: PollId, sender_id: UserId, message_to_reply: Message) -> bool:
        game, pairings = await asyncio.gather(*(self.__store.get_game(wishlist_id),
                                                self.__store.get_game_pairings(wishlist_id)))
        # check if sender is in pairings if started, or players if not
        if ((pairings and sender_id not in pairings.keys()) or
                (not pairings and sender_id not in await self.__store.get_users(wishlist_id))):
            await message_to_reply.reply_markdown_v2(f"You must join __{escape(game.name)}__ to add to the wishlist")
            return False
        return True

    async def __construct_wishlist(self, game: Game, wishlist: dict[UserId, str]):
        players = await asyncio.gather(*(self.__get_user_reference(user_id, context) for user_id in wishlist.keys()))
        players = sorted({player: player_id for player_id, player in zip(wishlist.keys(), players)}.items())

        if players:
            wishlist_item_segment = f"{"\n".join(f"{name}: _{escape(wishlist[player_id])}_" for name, player_id in players)}"
        else:
            wishlist_item_segment = r"There is nothing yet\!"

        return (
            f"*Wishlist for __{escape(game.name)}__*\n\n"
            f"{wishlist_item_segment}\n\n"
            rf"Reply to this message to update your wishlist\!"

        )

    @save_user
    @restrict_to_chat_type(
        "Use /new to start a new Secret Santa here.\n\nSend /start to me as a private message for more information.",
        {ChatType.PRIVATE})
    async def _handle_start(self, update: Update, callback_context: CallbackContext):
        await update.message.reply_text(
            "Welcome to the Secret Santa Bot! I can help you to organise a Secret Santa in a group, just add me to the group and send /new")
        await self._handle_status(update, callback_context)

    async def _handle_new_members(self, update: Update, context: CallbackContext):
        new_members = update.message.new_chat_members
        if context.bot.bot in new_members:
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
            CommandHandler("wishlist", self._handle_wishlist_command),
            MessageHandler(filters.TEXT & filters.REPLY, self._handle_wishlist_reply),
            PollAnswerHandler(self._handle_poll_answer),
        ]
