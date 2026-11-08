"""Welcome / onboarding for new members.

Sends the per-chat welcome text (with {name} / {chat} templating), optional
rules block, and an optional inline button. New members are recorded in the
database so moderation can enforce new-member rules (e.g. no links at first).
"""
from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from .database import Database

log = logging.getLogger(__name__)


async def on_new_members(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle Telegram's new_chat_members service message."""
    message = update.message
    chat = update.effective_chat
    if message is None or chat is None:
        return

    config = context.application.bot_data["config"]
    db: Database = context.application.bot_data["db"]
    chat_cfg = config.chats.get(chat.id)
    if chat_cfg is None:
        return

    for member in message.new_chat_members or []:
        if member.is_bot:
            continue  # don't welcome other bots
        db.log_new_member(chat.id, member.id, member.username)

        welcome = chat_cfg.welcome
        if not welcome.enabled or not welcome.text.strip():
            continue

        try:
            text = welcome.text.format(name=member.first_name, chat=chat_cfg.name)
        except (KeyError, IndexError, ValueError):
            text = welcome.text
        if welcome.rules.strip():
            text = f"{text}\n\n{welcome.rules.strip()}"

        keyboard = None
        if welcome.button_text.strip() and welcome.button_url.strip():
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton(welcome.button_text.strip(),
                                     url=welcome.button_url.strip())
            ]])

        try:
            await chat.send_message(text, reply_markup=keyboard)
        except Exception:
            log.exception("Failed to send welcome in chat %s", chat.id)
