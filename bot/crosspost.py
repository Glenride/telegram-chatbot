"""Cross-posting: mirror messages from a source chat to destination chats.

Rules come from each chat's ``crosspost`` config: ``to`` is a list of
destination chat IDs, with optional header/footer templates. Commands are
never mirrored, and the bot never mirrors its own messages (loop safety).
"""
from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

log = logging.getLogger(__name__)


async def on_crosspost(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    chat = update.effective_chat
    user = update.effective_user
    if message is None or chat is None or user is None:
        return
    if message.text and message.text.startswith("/"):
        return  # never mirror commands
    if user.id == context.bot.id:
        return  # never mirror our own posts (loop safety)

    config = context.application.bot_data["config"]
    chat_cfg = config.chats.get(chat.id)
    if chat_cfg is None:
        return
    destinations = chat_cfg.crosspost.to
    if not destinations:
        return

    header = chat_cfg.crosspost.header.strip()
    footer = chat_cfg.crosspost.footer.strip()

    for dest_id in destinations:
        try:
            if message.text and (header or footer):
                # Text message with templates: compose a new message.
                parts = [p for p in (header, message.text, footer) if p]
                await context.bot.send_message(chat_id=dest_id, text="\n\n".join(parts))
            else:
                # Media / stickers / plain text without templates: exact copy.
                await message.copy(chat_id=dest_id)
        except Exception:
            log.exception("Crosspost failed: chat %s -> %s", chat.id, dest_id)
