# Deploying Vapor Wire Bot on the iMac — click by click

You do Steps 1–3 on your phone (Telegram). Steps 4–8 are on the iMac
terminal. Nothing here creates accounts for you — the bot is yours, so
@BotFather only talks to you.

---

## Step 1 — Create the bot (on your phone, in Telegram)

1. Open Telegram. Tap the search bar at the top.
2. Type `BotFather` and open the result with the blue checkmark (@BotFather).
3. Tap **START** at the bottom (or send `/start`).
4. Send exactly: `/newbot`
5. BotFather asks for a display name. Reply: `Vapor Wire Bot`
6. BotFather asks for a username (must end in `bot`). Reply: `vaporwirebot`
   - If it says that name is taken, try `vapor_wire_bot` instead.
7. BotFather replies with a success message containing your token. It looks
   like this (yours will be different):
   `1234567890:AAHxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`
8. **Long-press the token → Copy.** Paste it into your Notes app for now.
   Treat it like a password — anyone with it controls your bot.

## Step 2 — Get your own Telegram user ID (on your phone)

1. In Telegram search, open `@userinfobot` (tap START).
2. It replies with a block of text including `Id: 123456789`.
3. Copy that number into your Notes app next to the token.

## Step 3 — Turn off privacy mode, add the bot to your chats (on your phone)

Privacy mode must be **off**, or the bot only sees commands and all the
moderation / keyword / logging features go blind.

1. Open @BotFather again. Send: `/setprivacy`
2. Tap your bot's name in the list it shows.
3. Tap **Disable**. It confirms privacy mode is off.

Now add the bot to each vapor wire group:

1. Open the group. Tap the group name at the top.
2. Tap **Add Members** (or the members list → Add). Search for your bot's
   username (`vaporwirebot`) and add it.
3. Promote it to admin: tap the group name → **Administrators** (or Members →
   find the bot) → toggle **Admin** on, and turn on:
   - Delete messages
   - Ban users
   - Restrict members
   (Pin messages is optional.)
4. For a **channel** (announcements): open the channel → channel name →
   **Administrators** → **Add Admin** → search the bot → give it **Post
   messages** permission.

---

## Step 4 — Install Docker on the iMac (terminal)

Open a terminal on the iMac (Omarchy). Run each line, one at a time:

```bash
omarchy-pkg-add docker docker-compose
```

```bash
sudo systemctl enable --now docker
```

```bash
sudo usermod -aG docker $USER
```

```bash
newgrp docker
```

Verify it works:

```bash
docker run --rm hello-world
```

You should see "Hello from Docker!" If instead you get a permission error,
log out of the iMac and back in, then run the `newgrp docker` line again.

Check the compose plugin too:

```bash
docker compose version
```

---

## Step 5 — Copy the bot folder to the iMac

On the machine that currently has the `telegram-chatbot` folder, run:

```bash
scp -r telegram-chatbot housedv@imac:~/projects/
```

Notes on that command:
- `housedv` is the example username — replace it with your iMac username if
  different.
- `imac` is the example hostname — replace it with your iMac's hostname or
  its LAN IP address (something like `192.168.1.50`). The iMac must be on
  the same network and reachable by SSH.
- `~/projects/` is where it lands. Create it first on the iMac if needed:
  `mkdir -p ~/projects`

After copying, on the iMac:

```bash
cd ~/projects/telegram-chatbot
ls
```

You should see `bot/`, `docker-compose.yml`, `.env.example`,
`chats.yaml.example`, `README.md`, and `docs/`.

## Step 6 — Fill in your secrets and chat IDs

```bash
cd ~/projects/telegram-chatbot
cp .env.example .env
cp chats.yaml.example chats.yaml
mkdir -p data
```

Edit `.env`:

```bash
nano .env
```

- Replace the `BOT_TOKEN=` value with the token from your Notes app.
- Replace `ADMIN_USER_ID=` with your user ID from @userinfobot.
- Leave `ADMIN_CHAT_ID` as your user ID (alerts come to your DM with the bot).
- Save: press `Ctrl+O`, then `Enter`, then `Ctrl+X` to exit nano.

Get your chat IDs:

1. Start the bot once (placeholders are fine for this): continue to Step 7.
2. In **each** vapor wire group/channel, send: `/id`
   - The bot must be a member (Step 3). This works even before the chat is
     configured — `/id` only needs you (the admin) to ask.
3. The bot replies with `chat_id: -1001234567890` (your real number).
4. Edit `chats.yaml` and replace every placeholder ID:

```bash
nano chats.yaml
```

Replace `-1001111111111`, `-1002222222222`, `-1003333333333` with the real
IDs. Adjust welcome text, schedules, banned words, and keywords while
you're in there — it's all commented. Save with `Ctrl+O`, `Enter`, `Ctrl+X`.

5. Restart the bot so it picks up the real config:

```bash
docker compose restart
```

## Step 7 — Start the bot

```bash
cd ~/projects/telegram-chatbot
docker compose up -d --build
```

Watch the logs to confirm it's alive:

```bash
docker compose logs -f
```

You should see lines like `Vapor Wire Bot starting (long polling)` and
`Scheduler ready with N job(s)`. Press `Ctrl+C` to stop watching logs —
the bot keeps running in the background.

Sanity check: in any configured group, send `/stats` (only you can run it).
The bot should reply with the activity summary.

Check it's set to survive reboots:

```bash
docker compose ps
```

The `STATUS` column should say `Up`.

## Step 8 — Updating later

To change welcome text, schedules, keywords, or any config:

```bash
cd ~/projects/telegram-chatbot
nano chats.yaml
docker compose restart
```

If the bot code itself changes (new files from me):

```bash
cd ~/projects/telegram-chatbot
docker compose up -d --build
```

Your data (message log, warns, runtime schedules) lives in `./data/bot.db`
and is **not** touched by rebuilds or restarts.

To stop everything: `docker compose down`

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Configuration error: BOT_TOKEN is missing...` | `.env` still has the placeholder — paste the real token from @BotFather |
| Logs show `Unauthorized` | Token is wrong or was revoked. Generate a new one: @BotFather → `/token` → pick the bot |
| Bot doesn't see normal messages | Privacy mode is still on: @BotFather → `/setprivacy` → your bot → **Disable** |
| Bot can't delete / mute / ban | It's not an admin, or admin rights are missing toggles — redo Step 3 |
| `/id` gets no reply in a group | Bot isn't a member of that group yet — redo Step 3 |
| `docker: permission denied` | Log out and back in (or reboot the iMac) after the `usermod` step |
| Scheduled post never arrives | Check the cron has 5 fields and the chat ID is right; `docker compose logs` shows scheduler errors |
| Digest never arrives | `digest.enabled` must be true and `ADMIN_CHAT_ID` must be a chat the bot can write to (start the bot in DM first: open it and tap START) |
