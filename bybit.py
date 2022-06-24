import json
import os
from queue import PriorityQueue
import time
from pybit import inverse_perpetual, spot, usdt_perpetual, usdc_perpetual
from dotenv import load_dotenv
import logging
from typing import Optional, Tuple
import asyncio
from syncer import sync

from telegram import __version__ as TG_VER

try:
    from telegram import __version_info__
except ImportError:
    __version_info__ = (0, 0, 0, 0, 0)  # type: ignore[assignment]

if __version_info__ < (20, 0, 0, "alpha", 1):
    raise RuntimeError(
        f"This example is not compatible with your current PTB version {TG_VER}. To view the "
        f"{TG_VER} version of this example, "
        f"visit https://docs.python-telegram-bot.org/en/v{TG_VER}/examples.html"
    )
from telegram import Chat, ChatMember, ChatMemberUpdated, Update
from telegram.constants import ParseMode
from telegram.ext import (Application, ChatMemberHandler, CommandHandler,
                          ContextTypes, MessageHandler, filters, PicklePersistence)

# Enable logging

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

logger = logging.getLogger(__name__)

load_dotenv()

loop = asyncio.get_event_loop()

BYBIT_API_KEY = os.environ.get("BYBIT_API_KEY")
BYBIT_API_SECRET = os.environ.get("BYBIT_API_SECRET")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

session_auth = inverse_perpetual.HTTP(
    api_key=BYBIT_API_KEY,
    api_secret=BYBIT_API_SECRET
)

ws_usdt_perpetual = usdt_perpetual.WebSocket(
    test=False,
    api_key=BYBIT_API_KEY,
    api_secret=BYBIT_API_SECRET
)

ws_spot = spot.WebSocket(
    test=False,
    api_key=BYBIT_API_KEY,
    api_secret=BYBIT_API_SECRET
)

ws_usdc_perpetual = usdc_perpetual.WebSocket(
    test=False,
    api_key=BYBIT_API_KEY,
    api_secret=BYBIT_API_SECRET
)

my_persistence = PicklePersistence(filepath='my_file')
# Create the Application and pass it your bot's token.
tg_app = Application.builder().token(
    TELEGRAM_BOT_TOKEN).persistence(my_persistence).build()


def extract_status_change(chat_member_update: ChatMemberUpdated) -> Optional[Tuple[bool, bool]]:
    """Takes a ChatMemberUpdated instance and extracts whether the 'old_chat_member' was a member
    of the chat and whether the 'new_chat_member' is a member of the chat. Returns None, if
    the status didn't change.
    """
    status_change = chat_member_update.difference().get("status")
    old_is_member, new_is_member = chat_member_update.difference().get("is_member",
                                                                       (None, None))

    if status_change is None:
        return None

    old_status, new_status = status_change
    was_member = old_status in [
        ChatMember.MEMBER,
        ChatMember.OWNER,
        ChatMember.ADMINISTRATOR,
    ] or (old_status == ChatMember.RESTRICTED and old_is_member is True)
    is_member = new_status in [
        ChatMember.MEMBER,
        ChatMember.OWNER,
        ChatMember.ADMINISTRATOR,
    ] or (new_status == ChatMember.RESTRICTED and new_is_member is True)

    return was_member, is_member


async def track_chats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tracks the chats the bot is in."""
    result = extract_status_change(update.my_chat_member)
    if result is None:
        return
    was_member, is_member = result

    # Let's check who is responsible for the change
    cause_name = update.effective_user.full_name

    # Handle chat types differently:
    chat = update.effective_chat
    if chat.type == Chat.PRIVATE:
        if not was_member and is_member:
            logger.info("%s started the bot", cause_name)
            context.bot_data.setdefault("user_ids", set()).add(chat.id)
        elif was_member and not is_member:
            logger.info("%s blocked the bot", cause_name)
            context.bot_data.setdefault("user_ids", set()).discard(chat.id)
    elif chat.type in [Chat.GROUP, Chat.SUPERGROUP]:
        if not was_member and is_member:
            logger.info("%s added the bot to the group %s",
                        cause_name, chat.title)
            context.bot_data.setdefault("group_ids", set()).add(chat.id)
        elif was_member and not is_member:
            logger.info("%s removed the bot from the group %s",
                        cause_name, chat.title)
            context.bot_data.setdefault("group_ids", set()).discard(chat.id)
    else:
        if not was_member and is_member:
            logger.info("%s added the bot to the channel %s",
                        cause_name, chat.title)
            context.bot_data.setdefault("channel_ids", set()).add(chat.id)
        elif was_member and not is_member:
            logger.info("%s removed the bot from the channel %s",
                        cause_name, chat.title)
            context.bot_data.setdefault("channel_ids", set()).discard(chat.id)


async def show_chats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Shows which chats the bot is in"""
    user_ids = ", ".join(str(uid)
                         for uid in context.bot_data.setdefault("user_ids", set()))
    group_ids = ", ".join(str(gid)
                          for gid in context.bot_data.setdefault("group_ids", set()))
    channel_ids = ", ".join(
        str(cid) for cid in context.bot_data.setdefault("channel_ids", set()))
    text = (
        f"@{context.bot.username} is currently in a conversation with the user IDs {user_ids}."
        f" Moreover it is a member of the groups with IDs {group_ids} "
        f"and administrator in the channels with IDs {channel_ids}."
    )
    await update.effective_message.reply_text(text)


async def greet_chat_members(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Greets new users in chats and announces when someone leaves"""
    result = extract_status_change(update.chat_member)
    if result is None:
        return

    was_member, is_member = result
    cause_name = update.chat_member.from_user.mention_html()
    member_name = update.chat_member.new_chat_member.user.mention_html()

    if not was_member and is_member:
        await update.effective_chat.send_message(
            f"{member_name} was added by {cause_name}. Welcome!",
            parse_mode=ParseMode.HTML,
        )
    elif was_member and not is_member:
        await update.effective_chat.send_message(
            f"{member_name} is no longer with us. Thanks a lot, {cause_name} ...",
            parse_mode=ParseMode.HTML,
        )


@sync
async def handle_execution(message):
    """
    docstring
    """
    logger.info("Order Received!")
    chat_ids = tg_app.bot_data.setdefault("channel_ids", set())
    for chat_id in chat_ids:
        await tg_app.bot.send_message(
            chat_id=chat_id, text=json.dumps(message))


async def pybit_handle_message(message):
    logger.info("Update Received!")
    chat_ids = tg_app.bot_data.setdefault("channel_ids", set())
    for chat_id in chat_ids:
        logger.info(f"Sending message to Chat Id: {chat_id}")
        await tg_app.bot.send_message(
            chat_id=chat_id, text=json.dumps(message))


async def send_test(update, context):
    """
    docstring
    """
    chat_ids = tg_app.bot_data.setdefault("channel_ids", set())
    for chat_id in chat_ids:
        logger.info(f"Sending message to chat_id: {chat_id}")
        await tg_app.bot.send_message(chat_id=chat_id, text="Test")


async def handle_messages(update, context):
    logger.info(update)


async def enroll_this(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    docstring
    """
    chat = update.effective_chat
    context.bot_data.setdefault("channel_ids", set()).add(chat.id)
    logger.info(f"Chat Id: {chat.id}")
    await update.message.reply_text("This channel will now receive trading signals")


def main() -> None:
    """Start the bot."""
    # Keep track of which chats the bot is in
    tg_app.add_handler(ChatMemberHandler(
        track_chats, ChatMemberHandler.MY_CHAT_MEMBER))
    tg_app.add_handler(CommandHandler("show_chats", show_chats))
    tg_app.add_handler(CommandHandler("enroll_this", enroll_this))
    tg_app.add_handler(CommandHandler("send_test", send_test))
    # Handle members joining/leaving chats.
    # tg_app.add_handler(ChatMemberHandler(
    #     greet_chat_members, ChatMemberHandler.CHAT_MEMBER))

    tg_app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, handle_messages))
    ws_usdt_perpetual.order_stream(
        lambda message:  loop.run_until_complete(pybit_handle_message(message)))
    ws_usdt_perpetual.execution_stream(
        lambda message: loop.run_until_complete(pybit_handle_message(message)))
    ws_usdc_perpetual.execution_stream(
        lambda message: loop.run_until_complete(pybit_handle_message(message)))
    ws_usdc_perpetual.order_stream(
        lambda message: loop.run_until_complete(pybit_handle_message(message)))
    ws_spot.execution_report_stream(
        lambda message: loop.run_until_complete(pybit_handle_message(message)))

    # Run the bot until the user presses Ctrl-C
    # We pass 'allowed_updates' handle *all* updates including `chat_member` updates
    # To reset this, simply pass `allowed_updates=[]`

    tg_app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
