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
| `/leaderboard` | Family standings (group stage points) |
| `/groups` | All 12 group tables with owners shown |
| `/draw` | Who owns which teams |
| `/help` | Command list |

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

| Finish | Points |
|---|---|
| 1st in group | 4 pts |
| 2nd in group | 3 pts |
| 3rd in group | 1 pt |
| Eliminated | 0 pts |

Points are awarded per completed group (all 6 matches played).
Goal tally shown as a fun tiebreaker.

*(Knockout round scoring can be added later once the group stage ends!)*

---

## Troubleshooting

- **Bot not responding**: Check Railway/Render logs for errors
- **Wrong scores**: The primary API (worldcup26.ir) updates within minutes of goals — if it's slow, the football-data.org fallback kicks in
- **Chat ID wrong**: Make sure the bot is actually a member of the group before fetching updates
- **"Unauthorized" error**: Your TELEGRAM_TOKEN is wrong — copy it fresh from BotFather
