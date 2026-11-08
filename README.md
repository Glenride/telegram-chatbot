# Vapor Wire Bot

Telegram chat-automation bot for the vapor wire channels and groups.
Self-hosted with Docker on the iMac. Long polling (no webhook, no open
ports), SQLite for state, everything config-driven.

## Modules

| Module | File | What it does |
|---|---|---|
| Welcome / onboarding | `bot/welcome.py` | Per-chat welcome message for new members, optional rules text and button |
| Scheduled posts | `bot/scheduler.py` | Cron-style posts per chat; runtime `/schedule_add`, `/schedule_list`, `/schedule_del` |
| Cross-posting | `bot/crosspost.py` | Source chat → destination chat(s), optional header/footer templates |
| Moderation | `bot/moderation.py` | Flood control, new-member link limits, banned words, forwarded-message flagging, warn → mute → ban escalation; `/warn /mute /unmute /ban /unban` |
| Keyword alerts | `bot/keywords.py` | Per-chat watchlist; hits forwarded to the admin chat with context |
| Daily digest | `bot/digest.py` | Once-daily plain-text summary: top chats, new members, mod actions, keyword hits |
| Admin / help | `bot/admin.py` | `/help`, `/id` (chat IDs for config), `/stats` |
| Message log | `bot/__main__.py` | Every content message logged to SQLite (feeds stats + digest) |

## Quick start (on the iMac)

Full click-by-click: [docs/deploy-imac.md](docs/deploy-imac.md). Short version:

```bash
cd ~/projects/telegram-chatbot
cp .env.example .env          # then paste your BotFather token + user ID
cp chats.yaml.example chats.yaml   # then fill in your chat IDs (get them with /id)
mkdir -p data
docker compose up -d --build
docker compose logs -f
```

## Configuration

**`.env`** — secrets only:

| Variable | Required | Purpose |
|---|---|---|
| `BOT_TOKEN` | yes | Token from @BotFather |
| `ADMIN_USER_ID` | yes | Your Telegram user ID; only this user runs admin commands |
| `ADMIN_CHAT_ID` | no | Where alerts + digest go (defaults to your DM with the bot) |
| `TIMEZONE` | no | For schedules and digest (default `America/New_York`) |
| `DB_PATH` | no | SQLite path (default `/app/data/bot.db`) |

**`chats.yaml`** — one entry per managed chat. All behavior lives here:

- `welcome` — `enabled`, `text` (`{name}` / `{chat}` templating), `rules`, optional button
- `schedules` — list of `name` / `cron` (5-field) / `text`
- `crosspost` — `to:` destination chat IDs, `header`, `footer`
- `moderation` — `enabled`, `flood` (max_messages/per_seconds), `new_member_link_hours`, `banned_words`, `warn_limit`, `mute_minutes`, `delete_banned`, `flag_forwards`
- `keywords` — watchlist phrases for admin alerts

See `chats.yaml.example` (fully commented, vapor wire sections) for the exact shape.

## Admin commands

`/help` `/id` `/stats` `/schedule_add <name> <m h dom mon dow> <text>`
`/schedule_list` `/schedule_del <id>` `/warn` `/mute [minutes]` `/unmute`
`/ban` `/unban <user_id>` — moderation commands target the replied-to message,
or take a numeric user ID.

## Requirements for the bot to work in a group

1. **Privacy mode OFF** — @BotFather → `/setprivacy` → Disable. Otherwise the
   bot only sees commands, and moderation/keywords/logging go blind.
2. **Bot is an admin** with *Delete messages*, *Restrict members*, *Ban users* —
   otherwise delete/mute/ban fail (the bot logs it and tells you).
3. **Chat IDs in chats.yaml** — send `/id` in each chat after adding the bot.

## Project layout

```
telegram-chatbot/
├── bot/                  # the package (python -m bot)
│   ├── __main__.py       # entry point, handler wiring
│   ├── config.py         # .env + chats.yaml -> dataclasses
│   ├── database.py       # SQLite: messages, warns, schedules, digest
│   ├── welcome.py        # onboarding
│   ├── scheduler.py      # cron posts + /schedule_* commands
│   ├── crosspost.py      # source -> destination mirroring
│   ├── moderation.py     # filters + warn/mute/ban
│   ├── keywords.py       # watchlist alerts
│   ├── digest.py         # daily summary
│   └── admin.py          # /help /id /stats (+ admin_only guard)
├── chats.yaml.example    # fully commented, vapor wire sections
├── .env.example
├── docker-compose.yml    # one container, restart: unless-stopped
├── Dockerfile
├── requirements.txt
└── docs/deploy-imac.md   # click-by-click iMac setup
```

## Notes

- Long polling: the iMac sits behind NAT, so the bot pulls updates from
  Telegram — nothing to expose, no webhook URL needed.
- State is one SQLite file (`./data/bot.db`, Docker volume) — survives
  restarts and updates.
- Never put `.env` (real token) in git or share it.
