"""Cron-style scheduled posts via APScheduler.

Two kinds of schedules:
  * config schedules  -- from chats.yaml, re-created on every startup.
  * runtime schedules -- added with /schedule_add, stored in SQLite so they
                         survive restarts, managed with /schedule_list and
                         /schedule_del.
"""
from __future__ import annotations

import asyncio
import functools
import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram import Update
from telegram.ext import Application, ContextTypes

from .admin import admin_only
from .bridge import emit
from .config import AppConfig, ChatConfig
from .database import Database

log = logging.getLogger(__name__)

SCHEDULER_KEY = "scheduler"


def _validate_cron(cron: str, timezone: str) -> CronTrigger:
    """Parse a 5-field cron string; raises ValueError when invalid."""
    parts = cron.split()
    if len(parts) != 5:
        raise ValueError("cron must have exactly 5 fields: 'minute hour day month weekday'")
    return CronTrigger.from_crontab(cron, timezone=timezone)


async def _post_job(app: Application, chat_id: int, text: str, name: str) -> None:
    """APScheduler job body: send one scheduled message."""
    try:
        await app.bot.send_message(chat_id=chat_id, text=text)
        log.info("Scheduled post sent: %s -> chat %s", name, chat_id)
        await emit(app, "post", f"Scheduled post published: {name}",
                   f"chat {chat_id}")
    except Exception:
        log.exception("Scheduled post failed: %s -> chat %s", name, chat_id)


async def _post_dynamic_job(app: Application, chat_id: int, name: str) -> None:
    """Topical morning post: opener from the last 24h of room messages.

    Posts nothing when the room has been quiet (< 3 messages) or when the
    local model returns nothing -- a skipped post beats an irrelevant one.
    """
    config = app.bot_data.get("config")
    db = app.bot_data.get("db")
    client = app.bot_data.get("llm")
    if config is None or db is None or client is None or not config.llm.enabled:
        log.info("Dynamic post skipped: %s (LLM unavailable)", name)
        return
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    rows = db.recent_messages(chat_id, since, limit=30)
    bot_id = app.bot.id if app.bot is not None else None
    texts: list[str] = []
    for row in reversed(rows):
        if bot_id is not None and row["user_id"] == bot_id:
            continue
        text = (row["text"] or "").strip()
        if text:
            texts.append(text[:300])
    if len(texts) < 3:
        log.info("Dynamic post skipped: %s (only %d recent messages)",
                 name, len(texts))
        return
    excerpt = "\n".join(f"- {t}" for t in texts[-20:])
    theme_ctx = ""
    try:
        with open("/app/fleet-wiki/prism-theme-local-money-in-practice.md", encoding="utf-8") as _tf:
            theme_ctx = _tf.read().strip()
    except OSError:
        theme_ctx = ""
    weekly_ctx = ""
    try:
        with open("/app/fleet-wiki/prism-weekly-prevention-over-cure.md", encoding="utf-8") as _wf:
            weekly_ctx = _wf.read().strip()
    except OSError:
        weekly_ctx = ""
    system = (
        "You write the Prism Telegram group's morning opener. The room is a "
        "crypto, AI, and building crew. Below are the room's actual messages "
        "from the last 24 hours. Write ONE short opener (1-2 sentences) that "
        "picks up the room's real thread and asks one follow-up question "
        "about it. Keep it plain, warm, and clean. Do NOT open with a generic "
        "good morning, do NOT invent topics, people, prices, or news that "
        "are not in the messages, and do NOT mention that you are a bot."
    )
    user_content = f"Recent messages:\n{excerpt}\n\nWrite the morning opener."
    if theme_ctx or weekly_ctx:
        user_content = f"Recent messages:\n{excerpt}\n\n"
        if theme_ctx:
            user_content += (
                "Weekly theme background (use only if it connects to the room's real thread; "
                "never invent topics, prices, or news):\n"
                + theme_ctx[:1500]
                + "\n\n"
            )
        if weekly_ctx:
            user_content += (
                "Week 1 weekly theme — Prevention over cure (background, use only if it connects to the room's real thread; "
                "never invent topics, prices, or news):\n"
                + weekly_ctx[:1200]
                + "\n\n"
            )
        user_content += "Write the morning opener."
    loop = asyncio.get_running_loop()
    reply = await loop.run_in_executor(
        None,
        functools.partial(
            client.chat,
            [{"role": "system", "content": system},
             {"role": "user",
              "content": user_content}],
            temperature=0.7,
            max_tokens=2000,
        ),
    )
    if not reply:
        log.info("Dynamic post skipped: %s (model returned nothing)", name)
        return
    try:
        await app.bot.send_message(chat_id=chat_id, text=reply[:1000])
        log.info("Dynamic post sent: %s -> chat %s", name, chat_id)
        await emit(app, "post", f"Dynamic post published: {name}",
                   f"chat {chat_id}")
    except Exception:
        log.exception("Dynamic post failed: %s -> chat %s", name, chat_id)


def _add_job(scheduler: AsyncIOScheduler, app: Application, job_id: str,
             chat_id: int, cron: str, text: str, name: str, timezone: str) -> None:
    trigger = _validate_cron(cron, timezone)
    scheduler.add_job(
        _post_job, trigger,
        args=[app, chat_id, text, name],
        id=job_id, replace_existing=True, name=name,
    )


def setup_scheduler(app: Application, config: AppConfig, db: Database) -> AsyncIOScheduler:
    """Create the scheduler and register every configured + runtime schedule.

    Call ``scheduler.start()`` from ``post_init`` (the event loop is running
    there); call ``scheduler.shutdown()`` from ``post_shutdown``.
    """
    scheduler = AsyncIOScheduler(timezone=config.timezone)

    for chat_cfg in config.chats.values():
        for sched in chat_cfg.schedules:
            if not sched.name or not sched.cron or (not sched.text and not sched.dynamic):
                log.warning("Skipping incomplete schedule in chat %s", chat_cfg.chat_id)
                continue
            try:
                trigger = _validate_cron(sched.cron, config.timezone)
                if sched.dynamic:
                    scheduler.add_job(
                        _post_dynamic_job, trigger,
                        args=[app, chat_cfg.chat_id,
                              f"{chat_cfg.name}/{sched.name}"],
                        id=f"cfg-{chat_cfg.chat_id}-{sched.name}",
                        replace_existing=True, name=sched.name,
                    )
                else:
                    _add_job(scheduler, app, f"cfg-{chat_cfg.chat_id}-{sched.name}",
                             chat_cfg.chat_id, sched.cron, sched.text,
                             f"{chat_cfg.name}/{sched.name}", config.timezone)
            except ValueError as exc:
                log.error("Bad cron '%s' for schedule '%s': %s",
                          sched.cron, sched.name, exc)

    for row in db.list_schedules():
        if not row["enabled"]:
            continue
        try:
            _add_job(scheduler, app, f"rt-{row['id']}", int(row["chat_id"]),
                     row["cron"], row["text"], row["name"], config.timezone)
        except ValueError as exc:
            log.error("Bad cron in runtime schedule %s: %s", row["name"], exc)

    app.bot_data[SCHEDULER_KEY] = scheduler
    log.info("Scheduler ready with %d job(s)", len(scheduler.get_jobs()))
    return scheduler


# ------------------------------------------------------------ admin commands

@admin_only
async def schedule_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Add a runtime schedule: /schedule_add <name> <m h dom mon dow> <text...>"""
    config: AppConfig = context.application.bot_data["config"]
    db: Database = context.application.bot_data["db"]
    scheduler: AsyncIOScheduler = context.application.bot_data[SCHEDULER_KEY]
    chat = update.effective_chat

    args = context.args or []
    if len(args) < 7 or chat is None:
        await update.message.reply_text(
            "Usage: /schedule_add <name> <minute hour day month weekday> <message>\n"
            "Example: /schedule_add morning 0 9 * * * Good morning, wire."
        )
        return

    name = args[0]
    cron = " ".join(args[1:6])
    text = " ".join(args[6:])
    try:
        _validate_cron(cron, config.timezone)
    except ValueError as exc:
        await update.message.reply_text(f"Bad schedule: {exc}")
        return

    schedule_id = db.add_schedule(name, chat.id, cron, text)
    _add_job(scheduler, context.application, f"rt-{schedule_id}",
             chat.id, cron, text, name, config.timezone)
    await update.message.reply_text(
        f"Scheduled '{name}' in this chat ({cron}, {config.timezone})."
    )


@admin_only
async def schedule_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List config + runtime schedules visible to the admin."""
    config: AppConfig = context.application.bot_data["config"]
    db: Database = context.application.bot_data["db"]

    lines = ["*Config schedules (chats.yaml):*"]
    for chat_cfg in config.chats.values():
        for sched in chat_cfg.schedules:
            lines.append(f"- {chat_cfg.name}: `{sched.name}` ({sched.cron})")
    lines.append("\n*Runtime schedules (/schedule_add):*")
    rows = db.list_schedules()
    if not rows:
        lines.append("- none")
    for row in rows:
        chat_cfg: ChatConfig | None = config.chats.get(int(row["chat_id"]))
        chat_name = chat_cfg.name if chat_cfg else str(row["chat_id"])
        lines.append(f"- #{row['id']} {chat_name}: `{row['name']}` ({row['cron']})")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


@admin_only
async def schedule_del(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Delete a runtime schedule: /schedule_del <id> (see /schedule_list)."""
    db: Database = context.application.bot_data["db"]
    scheduler: AsyncIOScheduler = context.application.bot_data[SCHEDULER_KEY]

    args = context.args or []
    if not args or not args[0].isdigit():
        await update.message.reply_text("Usage: /schedule_del <id>  (see /schedule_list)")
        return
    schedule_id = int(args[0])
    if db.delete_schedule(schedule_id):
        try:
            scheduler.remove_job(f"rt-{schedule_id}")
        except Exception:
            log.warning("Schedule #%d removed from DB but had no live job", schedule_id)
        await update.message.reply_text(f"Deleted schedule #{schedule_id}.")
    else:
        await update.message.reply_text(f"No runtime schedule #{schedule_id} found.")
