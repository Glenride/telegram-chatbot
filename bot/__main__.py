"""Vapor Wire Bot — entry point.

Run with:  python -m bot   (from the project root)
"""
from __future__ import annotations

import logging
import sys

from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
)

from apscheduler.triggers.interval import IntervalTrigger

from . import admin, crosspost, digest, keywords, llm, moderation, scheduler, welcome
from . import bridge as bridge_mod
from .bridge import Bridge
from .config import AppConfig, load_config
from .database import Database

log = logging.getLogger(__name__)

# Text-ish updates the bot should see (moderation, keywords, crosspost, log).
TEXT_FILTER = (filters.TEXT | filters.CAPTION) & ~filters.COMMAND
ANY_CONTENT = filters.ALL & ~filters.StatusUpdate.ALL & ~filters.COMMAND


async def log_incoming(update: Update, context) -> None:
    """Persist every content message to SQLite for stats + digest."""
    message = update.message
    chat = update.effective_chat
    user = update.effective_user
    if message is None or chat is None or user is None:
        return
    db: Database = context.application.bot_data["db"]
    db.log_message(chat.id, user.id, user.username,
                   message.text or message.caption)


async def _hq_heartbeat(app: Application) -> None:
    """Bot HQ heartbeat job (APScheduler interval, every 5 minutes)."""
    b = bridge_mod.get_bridge(app)
    if b is not None:
        await b.aheartbeat(
            detail="Prism Telegram automation running "
                   "(scheduled posts, daily digest, moderation)")


async def post_init(app: Application) -> None:
    """Start the APScheduler once the event loop is running."""
    sched = app.bot_data[scheduler.SCHEDULER_KEY]
    sched.start()
    log.info("Scheduler started")
    b = bridge_mod.get_bridge(app)
    if b is not None and b.enabled:
        await b.aheartbeat(
            detail="Prism Telegram automation running "
                   "(scheduled posts, daily digest, moderation)")
        await b.alog_event(
            "info", "Connected to Bot HQ",
            "Prism started and registered with Bot HQ; "
            "heartbeat every 5 minutes.")
        sched.add_job(
            _hq_heartbeat, IntervalTrigger(minutes=5), args=[app],
            id="bot-hq-heartbeat", replace_existing=True,
            name="bot-hq-heartbeat")
        log.info("Bot HQ heartbeat scheduled every 5 minutes")


async def post_shutdown(app: Application) -> None:
    """Stop the APScheduler cleanly."""
    app.bot_data[scheduler.SCHEDULER_KEY].shutdown(wait=False)
    log.info("Scheduler stopped")


def build_application(config: AppConfig | None = None) -> Application:
    if config is None:
        config = load_config()
    db = Database(config.db_path)

    app = (
        ApplicationBuilder()
        .token(config.bot_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    app.bot_data["config"] = config
    app.bot_data["db"] = db

    # Bot HQ bridge (fail-silent; no-op unless BOT_HQ_URL+BOT_HQ_KEY set).
    hq_bridge = Bridge.from_env()
    app.bot_data["bridge"] = hq_bridge
    log.info("Bot HQ bridge %s",
             "enabled" if hq_bridge.enabled
             else "disabled (BOT_HQ_URL/BOT_HQ_KEY not set)")

    # LLM replies (fail-silent; no-op unless llm.enabled in chats.yaml).
    llm_client = llm.LlmClient.from_config(config)
    app.bot_data["llm"] = llm_client
    log.info("LLM replies %s (model %s)",
             "enabled" if config.llm.enabled else "disabled",
             llm_client.model)

    # Scheduler (config schedules + runtime schedules + daily digest).
    sched = scheduler.setup_scheduler(app, config, db)
    digest.schedule_digest(sched, app)

    # --- group 0: moderation runs first; it stops the chain on violations.
    app.add_handler(MessageHandler(TEXT_FILTER, moderation.check_message), group=0)

    # --- group 1: automation on surviving messages.
    app.add_handler(MessageHandler(TEXT_FILTER, keywords.check_keywords), group=1)
    app.add_handler(MessageHandler(TEXT_FILTER, llm.handle_llm_reply), group=1)
    app.add_handler(MessageHandler(ANY_CONTENT, crosspost.on_crosspost), group=1)

    # --- group 2: logging + onboarding.
    app.add_handler(MessageHandler(ANY_CONTENT, log_incoming), group=2)
    app.add_handler(
        MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome.on_new_members),
        group=2,
    )

    # --- admin commands (admin_only enforced inside each handler).
    app.add_handler(CommandHandler("help", admin.cmd_help))
    app.add_handler(CommandHandler("id", admin.cmd_id))
    app.add_handler(CommandHandler("stats", admin.cmd_stats))
    app.add_handler(CommandHandler("schedule_add", scheduler.schedule_add))
    app.add_handler(CommandHandler("schedule_list", scheduler.schedule_list))
    app.add_handler(CommandHandler("schedule_del", scheduler.schedule_del))
    app.add_handler(CommandHandler("warn", moderation.cmd_warn))
    app.add_handler(CommandHandler("mute", moderation.cmd_mute))
    app.add_handler(CommandHandler("unmute", moderation.cmd_unmute))
    app.add_handler(CommandHandler("ban", moderation.cmd_ban))
    app.add_handler(CommandHandler("unban", moderation.cmd_unban))

    return app


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    try:
        app = build_application()
    except RuntimeError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1
    log.info("Vapor Wire Bot starting (long polling)")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
