"""Vapor Wire Bot — Telegram chat-automation package.

Modules:
    config      Load .env secrets + chats.yaml into typed dataclasses.
    database    SQLite persistence (message log, warns, schedules, digest).
    welcome     New-member onboarding messages.
    scheduler   Cron-style scheduled posts + runtime /schedule_* commands.
    crosspost   Source chat -> destination chat(s) mirroring.
    moderation  Flood/link/forward filters + warn/mute/ban escalation.
    keywords    Keyword watchlist alerts to the admin chat.
    digest      Daily activity digest.
    admin       /help, /id, /stats.
"""

__version__ = "1.0.0"
