# WC2026 Family Draw Bot — Setup Guide

Your Telegram bot for the 4-player World Cup draw:
**Ronan · Marc · Eoin · Ruaidhri**

Auto-fetches live scores from worldcup26.ir (free, no API key needed).
Posts a daily morning update at 08:00 IST and a results recap at midnight.

---

## Commands

| Command | What it does |
|---|---|
| `/today` | Today's matches with scores (live & final) |
| `/results` | Last 10 finished results |
| `/upcoming` | Next 3 days of fixtures |
| `/leaderboard` | Family standings with full points breakdown |
| `/groups` | All 12 group tables with owners shown |
| `/draw` | Who owns which teams |
| `/bbq` | A random match-day BBQ recipe (20 to choose from) |
| `/help` | Command list |

### Secret `/bonus` command (one user only)

There's a hidden `/bonus` command restricted to a single person. They can type
`/bonus 10` to add 10 to their personal rolling tally (or just `/bonus` to view
it). The points are purely cosmetic — they don't affect the leaderboard — and
the tally persists between restarts.

**To enable it**, add one Railway variable — use whichever you prefer:

| Variable | Value |
|---|---|
| `BONUS_USER` | The chosen user's @username (e.g. `marc` or `@marc`) **or** their numeric Telegram ID |

The username is matched case-insensitively and the leading `@` is optional, so
`Marc`, `marc` and `@marc` all work.

**Finding someone's numeric ID without their help** — the bot quietly notes the
name and ID of anyone who posts in the group. To look them up:

1. **Disable the bot's privacy mode** so it can see normal group messages:
   message @BotFather → `/setprivacy` → pick your bot → **Disable**.
   (Without this, Telegram only sends the bot commands, not chat messages.)
2. Wait for the person to post anything in the group (or look back — once
   privacy is off, new messages are captured).
3. **DM the bot privately** (tap its name → Message) and send `/seen`.
   It replies with everyone it has observed and their numeric IDs.
4. Copy the ID you want into the `BONUS_USER` variable.

`/seen` only reveals IDs in a private chat, never in the group.

If `BONUS_USER` is left unset, the command silently does nothing.

### Making the bonus tally permanent (optional)

The tally is saved to `bot_state.json`. On Railway's free tier the filesystem
resets on each **redeploy** (restarts are fine). To make it survive redeploys,
add a **Volume** in Railway:

1. In your service, go to **Settings → Volumes → New Volume**
2. Set the mount path to `/data`
3. Redeploy — the bot auto-detects `/data` and stores state there permanently.

---

## Step 1 — Create your Telegram Bot

1. Open Telegram and search for **@BotFather**
2. Send `/newbot`
3. Give it a name, e.g. `WC2026 Family Draw`
4. Give it a username, e.g. `wc2026familydraw_bot`
5. BotFather sends you a **token** — copy it, you'll need it shortly
   (looks like: `7123456789:AAFxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`)

---

## Step 2 — Get your group chat ID

1. Create a Telegram group (or use your existing family group)
2. Add your new bot to the group
3. Send any message in the group
4. Visit this URL in your browser (replace TOKEN with your bot token):
   ```
   https://api.telegram.org/bot<TOKEN>/getUpdates
   ```
5. Look for `"chat":{"id":` — the number after it is your **Chat ID**
   (group chats are negative numbers, e.g. `-1001234567890`)

---

## Step 3 — Deploy to Railway (free)

Railway gives you a free hobby plan — no credit card needed to start.

1. Go to **https://railway.app** and sign up with GitHub
2. Click **New Project → Deploy from GitHub repo**
3. Push this folder to a GitHub repo first:
   ```bash
   git init
   git add .
   git commit -m "WC2026 bot"
   git branch -M main
   git remote add origin https://github.com/YOUR_USERNAME/wc2026bot.git
   git push -u origin main
   ```
4. In Railway, select your repo
5. Railway detects the Procfile automatically

---

## Step 4 — Set environment variables in Railway

In your Railway project, go to **Variables** and add:

| Variable | Value |
|---|---|
| `TELEGRAM_TOKEN` | Your bot token from BotFather |
| `CHAT_ID` | Your group chat ID (with the minus sign if negative) |
| `FD_API_KEY` | *(optional)* Your football-data.org free key — sign up at football-data.org for a free key as a backup score source |

Click **Deploy** — the bot starts automatically.

---

## Step 5 — Deploy to Render (alternative free option)

1. Go to **https://render.com** and sign up
2. Click **New → Background Worker**
3. Connect your GitHub repo
4. Set:
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python bot.py`
5. Add the same environment variables as above under **Environment**
6. Click **Create Background Worker**

---

## Points system

| Event | Points |
|---|---|
| 1st in group | +4 |
| 2nd in group | +3 |
| 3rd in group | +1 |
| 4th in group | 0 |
| Team fails to reach Round of 32 | −1 per team |
| Knockout win | +2 per win |
| Owns the tournament winner | +5 |

Group points and the −1 penalties settle once each group completes and the
Round of 32 bracket is drawn. The bot reads the actual R32 fixtures to decide
which teams qualified — no guesswork. The leaderboard shows a live breakdown
(group / missed-cut / KO / champion) so it's never stuck on zero.

---

## Troubleshooting

- **Bot not responding**: Check Railway/Render logs for errors
- **Wrong scores**: The primary API (worldcup26.ir) updates within minutes of goals — if it's slow, the football-data.org fallback kicks in
- **Chat ID wrong**: Make sure the bot is actually a member of the group before fetching updates
- **"Unauthorized" error**: Your TELEGRAM_TOKEN is wrong — copy it fresh from BotFather
