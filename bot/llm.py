"""LLM chat replies — Prism talks back via the Mac Studio's local model.

Stdlib-only OpenAI-compatible client (urllib), same style as bridge.py.
No API key: the Studio endpoint is local and unauthenticated.

Triggers (checked in handle_llm_reply):
  * DM to the bot — any text gets a reply.
  * Group message that @-mentions the bot or replies to one of its messages.
Everything else is ignored, so the bot never jumps into conversations
uninvited. Runs in handler group 1, after moderation (group 0), so a
message the moderator deletes never reaches the model.

FAIL-SILENT: any model error is logged and the handler stays quiet.
Persona and limits live in chats.yaml under ``llm:`` — edit there and
restart, no rebuild.
"""
from __future__ import annotations

import asyncio
import functools
import json
import logging
import urllib.error
import urllib.request

from telegram import Update

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://192.168.1.187:1234/v1"
DEFAULT_MODEL = "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive"


class LlmClient:
    """Minimal OpenAI-compatible chat client (stdlib only)."""

    def __init__(self, base_url: str = DEFAULT_BASE_URL,
                 model: str = DEFAULT_MODEL, timeout: float = 120.0) -> None:
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.model = model or DEFAULT_MODEL
        self.timeout = timeout

    @classmethod
    def from_config(cls, config) -> "LlmClient":
        llm = config.llm
        return cls(base_url=llm.base_url, model=llm.model,
                   timeout=llm.timeout_seconds)

    def chat(self, messages: list[dict], temperature: float = 0.9,
             max_tokens: int = 2000) -> str | None:
        """One chat completion. Returns the reply text, or None on error."""
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "User-Agent": "PrismBot/1.0"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            text = (data["choices"][0]["message"]["content"] or "").strip()
            return text or None
        except Exception as exc:  # noqa: BLE001 — fail-silent by design
            log.warning("LLM chat failed: %s", exc)
            return None


def _is_trigger(update: Update, context) -> bool:
    message = update.message
    chat = update.effective_chat
    if message is None or chat is None:
        return False
    if chat.type == "private":
        return True
    bot = context.bot
    # Reply to one of the bot's own messages.
    replied = message.reply_to_message
    if (replied is not None and replied.from_user is not None
            and replied.from_user.id == bot.id):
        return True
    # @-mention of the bot.
    username = (bot.username or "").lower()
    text = (message.text or message.caption or "")
    if username and f"@{username}" in text.lower():
        return True
    return False


async def handle_llm_reply(update: Update, context) -> None:
    """Group-1 handler: answer mentions / replies / DMs with the local model."""
    config = context.application.bot_data.get("config")
    client: LlmClient | None = context.application.bot_data.get("llm")
    if config is None or client is None or not config.llm.enabled:
        return
    message = update.message
    chat = update.effective_chat
    if message is None or chat is None:
        return
    if config.llm.chats and chat.id not in config.llm.chats:
        return
    if not _is_trigger(update, context):
        return

    busy = context.application.bot_data.setdefault("llm_busy", set())
    if chat.id in busy:
        return
    busy.add(chat.id)
    try:
        text = (message.text or message.caption or "").strip()
        # Drop @-mention tokens so the model sees the clean message.
        text = " ".join(w for w in text.split() if not w.startswith("@")).strip()
        if not text:
            return
        user = update.effective_user
        who = (user.username or user.first_name or "someone") if user else "someone"
        user_text = f"{who}: {text}"
        replied = message.reply_to_message
        if replied is not None and (replied.text or replied.caption):
            quoted = (replied.text or replied.caption).strip()[:500]
            user_text = f"(replying to your message: '{quoted}')\n{user_text}"
        await context.bot.send_chat_action(chat_id=chat.id, action="typing")
        loop = asyncio.get_running_loop()
        reply = await loop.run_in_executor(
            None,
            functools.partial(
                client.chat,
                [{"role": "system", "content": config.llm.system_prompt},
                 {"role": "user", "content": user_text}],
                temperature=config.llm.temperature,
                max_tokens=config.llm.max_tokens,
            ),
        )
        if reply:
            await message.reply_text(reply[:4000])
    finally:
        busy.discard(chat.id)
