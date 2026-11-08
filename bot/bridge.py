"""Bot HQ web bridge — stdlib-only client for the Bot HQ dashboard API.

Adapted for Prism (bot_id="prism") from the trading-bot reference bridge.

Contract (Bot HQ, bot-key auth via the ``X-Bot-Key`` header):
  POST /api/heartbeat   {bot_id, name, status, detail, capabilities?}
  POST /api/events      {bot_id, kind, title, detail}
  GET  /api/messages/inbox?bot_id=<id>
  POST /api/messages    {bot_id, body, in_reply_to?}

Design rules:
  * ENABLED only when both BOT_HQ_URL and BOT_HQ_KEY are set; otherwise
    every method is a quiet no-op.
  * FAIL-SILENT: network errors are logged and swallowed — Bot HQ being
    down must never crash or block the bot.
  * Async wrappers (aheartbeat/alog_event) run the sync HTTP call in a
    worker thread so the Telegram event loop never blocks on HQ.
  * Prism advertises NO capabilities: it has no dashboard-chat handler
    (its handlers are Telegram group/channel commands), so chat is not
    wired and "chat" is never advertised.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)

BOT_ID = "prism"
BOT_NAME = "Prism"
USER_AGENT = "GlenrideBotHQ/1.0 (+https://bots.dvaughnhouse.com)"


class Bridge:
    def __init__(self, bot_id: str = BOT_ID, name: str = BOT_NAME, *,
                 url: str = "", key: str = "", timeout: float = 5.0,
                 capabilities: list[str] | None = None) -> None:
        self.bot_id = bot_id
        self.name = name
        self.url = (url or "").strip().rstrip("/")
        self.key = (key or "").strip()
        self.timeout = timeout
        self.capabilities = list(capabilities or [])

    @classmethod
    def from_env(cls) -> "Bridge":
        return cls(url=os.environ.get("BOT_HQ_URL", ""),
                   key=os.environ.get("BOT_HQ_KEY", ""))

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.key)

    def _request(self, method: str, path: str,
                 payload: dict | None = None) -> dict | None:
        if not self.enabled:
            return None
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self.url + path, data=data, method=method)
        req.add_header("X-Bot-Key", self.key)
        # Cloudflare rejects urllib's default User-Agent with 403.
        req.add_header("User-Agent", USER_AGENT)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
        except Exception as exc:  # noqa: BLE001 - fail-silent by design
            log.warning("Bot HQ %s %s failed: %s", method, path, exc)
            return None

    def heartbeat(self, status: str = "online", detail: str = "",
                  capabilities: list[str] | None = None) -> bool:
        caps = self.capabilities if capabilities is None else capabilities
        payload: dict = {"bot_id": self.bot_id, "name": self.name,
                         "status": status, "detail": detail}
        if caps:
            payload["capabilities"] = list(caps)
        return self._request("POST", "/api/heartbeat", payload) is not None

    def log_event(self, kind: str, title: str, detail: str = "") -> bool:
        return self._request("POST", "/api/events", {
            "bot_id": self.bot_id, "kind": kind, "title": title,
            "detail": detail,
        }) is not None

    def fetch_inbox(self) -> list[dict]:
        query = urllib.parse.urlencode({"bot_id": self.bot_id})
        resp = self._request("GET", f"/api/messages/inbox?{query}")
        if resp is None:
            return []
        items = resp if isinstance(resp, list) else resp.get("messages", [])
        out: list[dict] = []
        for item in items if isinstance(items, list) else []:
            if isinstance(item, dict) and "id" in item and "body" in item:
                out.append({"id": item["id"], "body": str(item["body"]),
                            "created_at": item.get("created_at", "")})
        return out

    def reply(self, body: str, in_reply_to=None) -> bool:
        payload: dict = {"bot_id": self.bot_id, "body": body}
        if in_reply_to is not None:
            payload["in_reply_to"] = in_reply_to
        return self._request("POST", "/api/messages", payload) is not None

    # ---- async wrappers: never block the event loop on HQ ----
    async def aheartbeat(self, status: str = "online", detail: str = "") -> bool:
        if not self.enabled:
            return False
        try:
            return await asyncio.to_thread(self.heartbeat, status, detail)
        except Exception:  # noqa: BLE001
            return False

    async def alog_event(self, kind: str, title: str, detail: str = "") -> bool:
        if not self.enabled:
            return False
        try:
            return await asyncio.to_thread(self.log_event, kind, title, detail)
        except Exception:  # noqa: BLE001
            return False


def get_bridge(app) -> Bridge | None:
    """Fetch the Bridge stored in app.bot_data, or None."""
    try:
        b = app.bot_data.get("bridge")  # type: ignore[union-attr]
        return b if isinstance(b, Bridge) else None
    except Exception:  # noqa: BLE001
        return None


async def emit(app, kind: str, title: str, detail: str = "") -> None:
    """Fire-and-forget event from async bot code; never raises."""
    b = get_bridge(app)
    if b is not None:
        await b.alog_event(kind, title, detail)
