"""Keyword alerts: watchlist per chat, hits forwarded to the admin chat.

Matching is case-insensitive substring matching on message text and
captions. One alert per message, listing every keyword that matched.
"""
from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from .config import AppConfig
from .database import Database

log = logging.getLogger(__name__)


def _message_link(chat, message_id: int) -> str | None:
    """Best-effort t.me link; only works for public supergroups/channels."""
    username = getattr(chat, "username", None)
    if username:
        return f"https://t.me/{username}/{message_id}"
    return None


async def check_keywords(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    chat = update.effective_chat
    user = update.effective_user
    if message is None or chat is None or user is None:
        return
    if user.is_bot:
        return

    config: AppConfig = context.application.bot_data["config"]
    chat_cfg = config.chats.get(chat.id)
    if chat_cfg is None or not chat_cfg.keywords:
        return

    text = message.text or message.caption or ""
    if not text:
        return
    lowered = text.lower()
    hits = [kw for kw in chat_cfg.keywords if kw.lower() in lowered]
    if not hits:
        return

    db: Database = context.application.bot_data["db"]
    for kw in hits:
        db.log_keyword_hit(chat.id, user.id, kw, text)

    snippet = text if len(text) <= 300 else text[:300] + "..."
    who = f"@{user.username}" if user.username else f"user {user.id}"
    alert = (
        f"Keyword alert [{chat_cfg.name}]\n"
        f"Matched: {', '.join(hits)}\n"
        f"From: {who}\n"
        f"Message: {snippet}"
    )
    link = _message_link(chat, message.message_id)
    if link:
        alert += f"\nLink: {link}"

    try:
        await context.bot.send_message(chat_id=config.admin_chat_id, text=alert)
    except Exception:
        log.exception("Could not send keyword alert to admin chat")
