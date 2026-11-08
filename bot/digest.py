"""Daily digest: plain-text activity summary sent to the admin chat.

Covers the last 24 hours: message volume per chat, new members,
moderation actions, and keyword hits. Scheduled once daily via
APScheduler (time + timezone from the digest config).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram.ext import Application

from .bridge import emit
from .config import AppConfig
from .database import Database

log = logging.getLogger(__name__)


def build_digest_text(config: AppConfig, db: Database) -> str:
    """Compose the digest for the trailing 24 hours. Pure function of the DB."""
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    lines = ["Vapor Wire Bot — daily digest (last 24h)", ""]

    lines.append("Messages per chat:")
    counts = db.message_counts_since(since)
    if not counts:
        lines.append("- none logged")
    for row in counts:
        chat_cfg = config.chats.get(int(row["chat_id"]))
        name = chat_cfg.name if chat_cfg else str(row["chat_id"])
        lines.append(f"- {name}: {row['n']}")

    lines.append("")
    members = db.new_members_since(since)
    lines.append(f"New members: {len(members)}")
    for row in members[:20]:
        chat_cfg = config.chats.get(int(row["chat_id"]))
        name = chat_cfg.name if chat_cfg else str(row["chat_id"])
        who = f"@{row['username']}" if row["username"] else f"user {row['user_id']}"
        lines.append(f"- {who} joined {name}")

    lines.append("")
    actions = db.mod_actions_since(since)
    lines.append(f"Moderation actions: {len(actions)}")
    by_action: dict[str, int] = {}
    for row in actions:
        by_action[row["action"]] = by_action.get(row["action"], 0) + 1
    for action, n in sorted(by_action.items()):
        lines.append(f"- {action}: {n}")

    lines.append("")
    hits = db.keyword_hits_since(since)
    total_hits = sum(r["n"] for r in hits)
    lines.append(f"Keyword hits: {total_hits}")
    for row in hits[:20]:
        chat_cfg = config.chats.get(int(row["chat_id"]))
        name = chat_cfg.name if chat_cfg else str(row["chat_id"])
        lines.append(f"- '{row['keyword']}' x{row['n']} in {name}")

    return "\n".join(lines)


async def send_digest(app: Application) -> None:
    """APScheduler job body."""
    config: AppConfig = app.bot_data["config"]
    db: Database = app.bot_data["db"]
    try:
        text = build_digest_text(config, db)
        await app.bot.send_message(chat_id=config.admin_chat_id, text=text)
        log.info("Daily digest sent")
        await emit(app, "digest", "Daily digest sent",
                   f"admin chat {config.admin_chat_id}")
    except Exception:
        log.exception("Failed to send daily digest")


def schedule_digest(scheduler: AsyncIOScheduler, app: Application) -> None:
    """Register the daily digest job. No-op when disabled."""
    config: AppConfig = app.bot_data["config"]
    if not config.digest.enabled:
        log.info("Digest disabled in config")
        return
    try:
        hour, minute = (int(p) for p in config.digest.time.split(":"))
    except ValueError:
        log.error("Bad digest time '%s', expected HH:MM", config.digest.time)
        return
    scheduler.add_job(
        send_digest,
        CronTrigger(hour=hour, minute=minute, timezone=config.digest.timezone),
        args=[app],
        id="daily-digest",
        replace_existing=True,
        name="daily-digest",
    )
    log.info("Digest scheduled daily at %s %s",
             config.digest.time, config.digest.timezone)
