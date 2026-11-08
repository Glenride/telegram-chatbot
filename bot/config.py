"""Configuration loading.

Secrets come from ``.env`` (BOT_TOKEN, ADMIN_USER_ID, ...).  Everything
behavioral comes from ``chats.yaml`` so behavior changes never touch code.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

import yaml
from dotenv import load_dotenv

log = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(BASE_DIR, ".env")
CHATS_PATH = os.path.join(BASE_DIR, "chats.yaml")


# ---------------------------------------------------------------- dataclasses

@dataclass
class WelcomeConfig:
    enabled: bool = False
    text: str = ""
    rules: str = ""
    button_text: str = ""
    button_url: str = ""


@dataclass
class ScheduleConfig:
    name: str = ""
    cron: str = ""
    text: str = ""
    # dynamic: instead of posting `text`, generate a topical opener from
    # the chat's recent messages (and post nothing when the room is quiet).
    dynamic: bool = False


@dataclass
class CrosspostConfig:
    to: list[int] = field(default_factory=list)
    header: str = ""
    footer: str = ""


@dataclass
class FloodConfig:
    max_messages: int = 5
    per_seconds: int = 10


@dataclass
class ModerationConfig:
    enabled: bool = False
    flood: FloodConfig = field(default_factory=FloodConfig)
    new_member_link_hours: int = 24
    banned_words: list[str] = field(default_factory=list)
    warn_limit: int = 3
    mute_minutes: int = 60
    delete_banned: bool = True
    flag_forwards: bool = True


@dataclass
class ChatConfig:
    chat_id: int = 0
    name: str = ""
    welcome: WelcomeConfig = field(default_factory=WelcomeConfig)
    schedules: list[ScheduleConfig] = field(default_factory=list)
    crosspost: CrosspostConfig = field(default_factory=CrosspostConfig)
    moderation: ModerationConfig = field(default_factory=ModerationConfig)
    keywords: list[str] = field(default_factory=list)


@dataclass
class DigestConfig:
    enabled: bool = True
    time: str = "21:00"
    timezone: str = "America/New_York"


@dataclass
class LlmConfig:
    enabled: bool = False
    base_url: str = "http://192.168.1.187:1234/v1"
    model: str = "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive"
    system_prompt: str = ""
    temperature: float = 0.9
    max_tokens: int = 2000
    timeout_seconds: float = 120.0
    chats: list[int] = field(default_factory=list)




@dataclass
class AppConfig:
    bot_token: str = ""
    admin_user_id: int = 0
    admin_chat_id: int = 0
    timezone: str = "America/New_York"
    db_path: str = ""
    digest: DigestConfig = field(default_factory=DigestConfig)
    llm: LlmConfig = field(default_factory=LlmConfig)
    chats: dict[int, ChatConfig] = field(default_factory=dict)


# ------------------------------------------------------------------ parsing

def _parse_welcome(raw: dict | None) -> WelcomeConfig:
    raw = raw or {}
    return WelcomeConfig(
        enabled=bool(raw.get("enabled", False)),
        text=str(raw.get("text", "")),
        rules=str(raw.get("rules", "")),
        button_text=str(raw.get("button_text", "")),
        button_url=str(raw.get("button_url", "")),
    )


def _parse_schedules(raw: list | None) -> list[ScheduleConfig]:
    out: list[ScheduleConfig] = []
    for item in raw or []:
        out.append(ScheduleConfig(
            name=str(item.get("name", "")),
            cron=str(item.get("cron", "")),
            text=str(item.get("text", "")),
            dynamic=bool(item.get("dynamic", False)),
        ))
    return out


def _parse_crosspost(raw: dict | None) -> CrosspostConfig:
    raw = raw or {}
    return CrosspostConfig(
        to=[int(c) for c in (raw.get("to") or [])],
        header=str(raw.get("header", "")),
        footer=str(raw.get("footer", "")),
    )


def _parse_moderation(raw: dict | None) -> ModerationConfig:
    raw = raw or {}
    flood_raw = raw.get("flood") or {}
    return ModerationConfig(
        enabled=bool(raw.get("enabled", False)),
        flood=FloodConfig(
            max_messages=int(flood_raw.get("max_messages", 5)),
            per_seconds=int(flood_raw.get("per_seconds", 10)),
        ),
        new_member_link_hours=int(raw.get("new_member_link_hours", 24)),
        banned_words=[str(w) for w in (raw.get("banned_words") or [])],
        warn_limit=int(raw.get("warn_limit", 3)),
        mute_minutes=int(raw.get("mute_minutes", 60)),
        delete_banned=bool(raw.get("delete_banned", True)),
        flag_forwards=bool(raw.get("flag_forwards", True)),
    )


def _parse_chat(raw: dict) -> ChatConfig:
    chat_id = int(raw.get("chat_id", 0))
    return ChatConfig(
        chat_id=chat_id,
        name=str(raw.get("name", f"chat {chat_id}")),
        welcome=_parse_welcome(raw.get("welcome")),
        schedules=_parse_schedules(raw.get("schedules")),
        crosspost=_parse_crosspost(raw.get("crosspost")),
        moderation=_parse_moderation(raw.get("moderation")),
        keywords=[str(k) for k in (raw.get("keywords") or [])],
    )


def _parse_llm(raw: dict | None) -> LlmConfig:
    raw = raw or {}
    return LlmConfig(
        enabled=bool(raw.get("enabled", False)),
        base_url=str(raw.get("base_url", "http://192.168.1.187:1234/v1")),
        model=str(raw.get("model", "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive")),
        system_prompt=str(raw.get("system_prompt", "")),
        temperature=float(raw.get("temperature", 0.9)),
        max_tokens=int(raw.get("max_tokens", 2000)),
        timeout_seconds=float(raw.get("timeout_seconds", 120)),
        chats=[int(c) for c in (raw.get("chats") or [])],
    )


def load_config(env_path: str = ENV_PATH, chats_path: str = CHATS_PATH) -> AppConfig:
    """Load and validate configuration. Raises RuntimeError on bad config."""
    load_dotenv(env_path)

    token = os.environ.get("BOT_TOKEN", "").strip()
    if not token or "replace_me" in token.lower() or "example" in token.lower():
        raise RuntimeError(
            f"BOT_TOKEN is missing or still the placeholder in {env_path}. "
            "Copy .env.example to .env and paste the token from @BotFather."
        )

    try:
        admin_user_id = int(os.environ.get("ADMIN_USER_ID", "0"))
    except ValueError:
        raise RuntimeError("ADMIN_USER_ID must be a number (your Telegram user ID).")
    if not admin_user_id:
        raise RuntimeError("ADMIN_USER_ID is not set in .env.")

    try:
        admin_chat_id = int(os.environ.get("ADMIN_CHAT_ID", "") or admin_user_id)
    except ValueError:
        raise RuntimeError("ADMIN_CHAT_ID must be a number.")

    timezone = os.environ.get("TIMEZONE", "America/New_York").strip() or "America/New_York"
    db_path = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "data", "bot.db")).strip()

    if not os.path.exists(chats_path):
        raise RuntimeError(
            f"chats.yaml not found at {chats_path}. "
            "Copy chats.yaml.example to chats.yaml and fill in your chat IDs."
        )
    with open(chats_path, "r", encoding="utf-8") as fh:
        raw_yaml = yaml.safe_load(fh) or {}

    global_raw = raw_yaml.get("global") or {}
    digest_raw = global_raw.get("digest") or {}
    digest = DigestConfig(
        enabled=bool(digest_raw.get("enabled", True)),
        time=str(digest_raw.get("time", "21:00")),
        timezone=str(digest_raw.get("timezone", global_raw.get("timezone", timezone))),
    )
    llm = _parse_llm(raw_yaml.get("llm"))

    chats: dict[int, ChatConfig] = {}
    for item in raw_yaml.get("chats") or []:
        chat_cfg = _parse_chat(item)
        if chat_cfg.chat_id == 0:
            raise RuntimeError("A chat entry in chats.yaml is missing chat_id.")
        if chat_cfg.chat_id in chats:
            raise RuntimeError(f"Duplicate chat_id {chat_cfg.chat_id} in chats.yaml.")
        chats[chat_cfg.chat_id] = chat_cfg

    cfg = AppConfig(
        bot_token=token,
        admin_user_id=admin_user_id,
        admin_chat_id=admin_chat_id,
        timezone=str(global_raw.get("timezone", timezone)),
        db_path=db_path,
        digest=digest,
        llm=llm,
        chats=chats,
    )
    log.info("Loaded config: %d chat(s), digest=%s", len(chats), digest.enabled)
    return cfg
