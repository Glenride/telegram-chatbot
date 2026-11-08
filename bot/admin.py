"""Admin-only commands: /help, /id, /stats.

Every command here is restricted to ADMIN_USER_ID from .env via the
``admin_only`` decorator. Regular members get a silent ignore.
"""
from __future__ import annotations

import functools
import logging
from datetime import datetime, timedelta, timezone

from telegram import Update
from telegram.ext import ContextTypes

from .config import AppConfig
from .database import Database

log = logging.getLogger(__name__)


def admin_only(func):
    """Decorator: only ADMIN_USER_ID may run the wrapped command."""
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        config: AppConfig = context.application.bot_data["config"]
        user = update.effective_user
        if user is None or user.id != config.admin_user_id:
            return  # silently ignore non-admins
        return await func(update, context, *args, **kwargs)
    return wrapper


@admin_only
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Vapor Wire Bot — admin commands\n\n"
        "/id — show this chat's ID and your user ID\n"
        "/stats — today's activity across managed chats\n"
        "/schedule_add <name> <m h dom mon dow> <text> — add a scheduled post\n"
        "/schedule_list — list config + runtime schedules\n"
        "/schedule_del <id> — delete a runtime schedule\n"
        "/warn — reply to a user to warn them\n"
        "/mute [minutes] — reply to a user to mute (default from config)\n"
        "/unmute — reply to a user to lift a mute\n"
        "/ban — reply to a user to ban\n"
        "/unban <user_id> — unban by user ID\n\n"
        "Moderation also runs automatically: flood control, new-member link "
        "limits, banned words, and forwarded-message flagging per chats.yaml."
    )


@admin_only
async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    user = update.effective_user
    await update.message.reply_text(
        f"chat_id: {chat.id}\n"
        f"chat type: {chat.type}\n"
        f"your user_id: {user.id}"
    )


@admin_only
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: AppConfig = context.application.bot_data["config"]
    db: Database = context.application.bot_data["db"]
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()

    lines = ["Activity (last 24h):"]
    counts = db.message_counts_since(since)
    if not counts:
        lines.append("- no messages logged")
    for row in counts:
        chat_cfg = config.chats.get(int(row["chat_id"]))
        name = chat_cfg.name if chat_cfg else str(row["chat_id"])
        lines.append(f"- {name}: {row['n']} messages")
    members = db.new_members_since(since)
    lines.append(f"New members: {len(members)}")
    actions = db.mod_actions_since(since)
    lines.append(f"Moderation actions: {len(actions)}")
    hits = db.keyword_hits_since(since)
    lines.append(f"Keyword hits: {sum(r['n'] for r in hits)}")
    await update.message.reply_text("\n".join(lines))
