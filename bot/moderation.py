"""Moderation: flood control, new-member link limits, banned words,
forwarded-message flagging, and warn -> mute -> ban escalation.

The bot needs admin rights in a group for delete/restrict/ban to work.
When it lacks rights, it logs the failure and notifies the admin chat
instead of crashing.
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from telegram import ChatPermissions, Update
from telegram.error import BadRequest, Forbidden
from telegram.ext import ApplicationHandlerStop, ContextTypes

from .admin import admin_only
from .bridge import emit
from .config import AppConfig, ChatConfig, ModerationConfig
from .database import Database

log = logging.getLogger(__name__)

URL_RE = re.compile(r"https?://|t\.me/|www\.", re.IGNORECASE)

# In-memory flood tracker: (chat_id, user_id) -> deque of message timestamps.
_flood: dict[tuple[int, int], deque] = defaultdict(deque)


def _has_link(text: str | None) -> bool:
    return bool(text) and bool(URL_RE.search(text))


def _is_forward(message) -> bool:
    return bool(getattr(message, "forward_origin", None)
                or getattr(message, "forward_date", None))


async def _notify_admin(context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    config: AppConfig = context.application.bot_data["config"]
    try:
        await context.bot.send_message(chat_id=config.admin_chat_id, text=text)
    except Exception:
        log.exception("Could not notify admin chat")


async def _delete(message) -> bool:
    try:
        await message.delete()
        return True
    except (Forbidden, BadRequest) as exc:
        log.warning("Cannot delete message in chat %s: %s", message.chat_id, exc)
        return False
    except Exception:
        log.exception("Unexpected error deleting message")
        return False


async def _mute_user(context: ContextTypes.DEFAULT_TYPE, chat_id: int,
                     user_id: int, minutes: int, reason: str) -> bool:
    """Mute via Telegram permissions. Returns True on success."""
    until = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    try:
        await context.bot.restrict_chat_member(
            chat_id=chat_id,
            user_id=user_id,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=until,
        )
        return True
    except (Forbidden, BadRequest) as exc:
        log.warning("Cannot mute user %s in chat %s: %s", user_id, chat_id, exc)
        await _notify_admin(
            context,
            f"Mod: tried to mute user {user_id} in chat {chat_id} ({reason}) "
            "but the bot lacks admin rights. Promote the bot to admin with "
            "'restrict members' permission.",
        )
        return False


async def _warn(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int,
                username: str | None, reason: str, mod: ModerationConfig,
                chat_name: str) -> None:
    """Issue a warn; auto-mute when the warn limit is reached."""
    db: Database = context.application.bot_data["db"]
    count = db.add_warning(chat_id, user_id)
    db.log_mod_action(chat_id, user_id, "warn", reason)
    log.info("Warn %d/%d for user %s in chat %s: %s",
             count, mod.warn_limit, user_id, chat_id, reason)
    await emit(context.application, "moderation",
               f"Moderation: warn {count}/{mod.warn_limit} in {chat_name}",
               f"user {user_id} in chat {chat_id}: {reason}")

    if count >= mod.warn_limit:
        ok = await _mute_user(context, chat_id, user_id, mod.mute_minutes,
                              f"{count} warns ({reason})")
        if ok:
            db.log_mod_action(chat_id, user_id, "mute",
                              f"auto-mute after {count} warns: {reason}")
            await emit(context.application, "moderation",
                       f"Moderation: auto-mute in {chat_name}",
                       f"user {user_id} muted {mod.mute_minutes} min "
                       f"after {count} warns: {reason}")
            db.reset_warnings(chat_id, user_id)
            await _notify_admin(
                context,
                f"Mod [{chat_name}]: user {user_id} auto-muted for "
                f"{mod.mute_minutes} min after {count} warns (last: {reason}).",
            )
    else:
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"Warning {count}/{mod.warn_limit} for "
                     f"@{username or user_id}: {reason}",
            )
        except Exception:
            log.exception("Could not send warn notice")


async def check_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Main moderation filter. Raises ApplicationHandlerStop when handled."""
    message = update.message
    chat = update.effective_chat
    user = update.effective_user
    if message is None or chat is None or user is None:
        return
    if user.is_bot or user.id == context.bot.id:
        return

    config: AppConfig = context.application.bot_data["config"]
    chat_cfg: ChatConfig | None = config.chats.get(chat.id)
    if chat_cfg is None:
        return
    mod: ModerationConfig = chat_cfg.moderation
    if not mod.enabled:
        return

    db: Database = context.application.bot_data["db"]
    text = message.text or message.caption or ""
    lowered = text.lower()
    key = (chat.id, user.id)
    now = datetime.now(timezone.utc)

    # 1) Flood control.
    window = timedelta(seconds=mod.flood.per_seconds)
    stamps = _flood[key]
    while stamps and now - stamps[0] > window:
        stamps.popleft()
    stamps.append(now)
    if len(stamps) > mod.flood.max_messages:
        await _delete(message)
        await _warn(context, chat.id, user.id, user.username,
                    f"flooding ({mod.flood.max_messages} msgs / "
                    f"{mod.flood.per_seconds}s)", mod, chat_cfg.name)
        raise ApplicationHandlerStop

    # 2) Banned words.
    hit = next((w for w in mod.banned_words if w.lower() in lowered), None)
    if hit:
        if mod.delete_banned:
            await _delete(message)
        await _warn(context, chat.id, user.id, user.username,
                    f"banned phrase: '{hit}'", mod, chat_cfg.name)
        raise ApplicationHandlerStop

    # 3) New-member link limit. (Telegram has no "no links" permission, so
    #    the bot deletes link messages from fresh members instead.)
    if (mod.new_member_link_hours > 0 and _has_link(text)
            and db.member_joined_within(chat.id, user.id, mod.new_member_link_hours)):
        await _delete(message)
        await _warn(context, chat.id, user.id, user.username,
                    f"links not allowed in the first {mod.new_member_link_hours}h",
                    mod, chat_cfg.name)
        raise ApplicationHandlerStop

    # 4) Forwarded-message flagging.
    if mod.flag_forwards and _is_forward(message):
        await _delete(message)
        db.log_mod_action(chat.id, user.id, "delete", "forwarded message")
        await _warn(context, chat.id, user.id, user.username,
                    "forwarded messages are not allowed here", mod, chat_cfg.name)
        raise ApplicationHandlerStop


def _target_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> tuple[int, str | None] | None:
    """Resolve the command target: replied-to user, or a numeric user ID arg."""
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        u = update.message.reply_to_message.from_user
        return u.id, u.username
    if context.args and context.args[0].lstrip("-").isdigit():
        return int(context.args[0]), None
    return None


@admin_only
async def cmd_warn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: AppConfig = context.application.bot_data["config"]
    chat = update.effective_chat
    target = _target_user(update, context)
    if target is None or chat is None:
        await update.message.reply_text("Reply to the user's message, or: /warn <user_id>")
        return
    user_id, username = target
    chat_cfg = config.chats.get(chat.id)
    mod = chat_cfg.moderation if chat_cfg else ModerationConfig(enabled=True)
    await _warn(context, chat.id, user_id, username, "manual warn by admin",
                mod, chat_cfg.name if chat_cfg else str(chat.id))
    await update.message.reply_text("Warned.")


@admin_only
async def cmd_mute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: AppConfig = context.application.bot_data["config"]
    db: Database = context.application.bot_data["db"]
    chat = update.effective_chat
    target = _target_user(update, context)
    if target is None or chat is None:
        await update.message.reply_text(
            "Reply to the user's message, or: /mute <user_id> [minutes]")
        return
    user_id, _ = target
    chat_cfg = config.chats.get(chat.id)
    minutes = mod.mute_minutes if (mod := chat_cfg.moderation if chat_cfg else None) else 60
    if context.args and context.args[-1].isdigit() and not (
            update.message.reply_to_message and len(context.args) == 1):
        # /mute <user_id> <minutes> form
        try:
            minutes = int(context.args[-1])
        except ValueError:
            pass
    elif context.args and context.args[0].isdigit() and update.message.reply_to_message:
        # /mute <minutes> as reply
        try:
            minutes = int(context.args[0])
        except ValueError:
            pass
    if await _mute_user(context, chat.id, user_id, minutes, "manual mute by admin"):
        db.log_mod_action(chat.id, user_id, "mute", f"manual, {minutes} min")
        await emit(context.application, "moderation",
                   "Moderation: manual mute",
                   f"user {user_id} in chat {chat.id} for {minutes} min")
        await update.message.reply_text(f"Muted for {minutes} min.")


@admin_only
async def cmd_unmute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.application.bot_data["db"]
    chat = update.effective_chat
    target = _target_user(update, context)
    if target is None or chat is None:
        await update.message.reply_text("Reply to the user's message, or: /unmute <user_id>")
        return
    user_id, _ = target
    try:
        await context.bot.restrict_chat_member(
            chat_id=chat.id,
            user_id=user_id,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_media_messages=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
            ),
        )
        db.log_mod_action(chat.id, user_id, "unmute", "manual by admin")
        await emit(context.application, "moderation",
                   "Moderation: manual unmute",
                   f"user {user_id} in chat {chat.id}")
        db.reset_warnings(chat.id, user_id)
        await update.message.reply_text("Unmuted.")
    except (Forbidden, BadRequest) as exc:
        await update.message.reply_text(f"Could not unmute (bot needs admin rights): {exc}")


@admin_only
async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.application.bot_data["db"]
    chat = update.effective_chat
    target = _target_user(update, context)
    if target is None or chat is None:
        await update.message.reply_text("Reply to the user's message, or: /ban <user_id>")
        return
    user_id, _ = target
    try:
        await context.bot.ban_chat_member(chat_id=chat.id, user_id=user_id)
        db.log_mod_action(chat.id, user_id, "ban", "manual by admin")
        await emit(context.application, "moderation",
                   "Moderation: manual ban",
                   f"user {user_id} in chat {chat.id}")
        await update.message.reply_text("Banned.")
    except (Forbidden, BadRequest) as exc:
        await update.message.reply_text(f"Could not ban (bot needs admin rights): {exc}")


@admin_only
async def cmd_unban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.application.bot_data["db"]
    chat = update.effective_chat
    target = _target_user(update, context)
    if target is None or chat is None:
        await update.message.reply_text("Usage: /unban <user_id>")
        return
    user_id, _ = target
    try:
        await context.bot.unban_chat_member(chat_id=chat.id, user_id=user_id,
                                            only_if_banned=True)
        db.log_mod_action(chat.id, user_id, "unban", "manual by admin")
        await emit(context.application, "moderation",
                   "Moderation: manual unban",
                   f"user {user_id} in chat {chat.id}")
        await update.message.reply_text("Unbanned.")
    except (Forbidden, BadRequest) as exc:
        await update.message.reply_text(f"Could not unban (bot needs admin rights): {exc}")
