"""
WC2026 Family Draw Bot
Tracks the 4-player draw: Ronan, Marc, Eoin, Ruaidhri
Auto-fetches scores from worldcup26.ir (free, no key)
Falls back to football-data.org free tier if needed
"""

import os
import json
import logging
import asyncio
import aiohttp
from datetime import datetime, timezone, timedelta
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.constants import ParseMode

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ─── CONFIGURATION ────────────────────────────────────────────────────────────

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID        = os.environ["CHAT_ID"]           # your group chat ID
FD_API_KEY     = os.environ.get("FD_API_KEY", "") # football-data.org free key (optional fallback)

# Irish Standard Time = UTC+1
IST = timezone(timedelta(hours=1))

# ─── DRAW: who owns which teams ───────────────────────────────────────────────

DRAW = {
    "Ronan":    ["Argentina","Morocco","Germany","Mexico","Iran","Australia","Qatar",
                 "Sweden","DR Congo","Iraq","Ghana","Cape Verde"],
    "Marc":     ["Spain","Brazil","Colombia","Senegal","Austria","South Korea","Canada",
                 "Tunisia","Bosnia & Herz.","Panama","Jordan","Curaçao"],
    "Eoin":     ["France","Netherlands","Croatia","Uruguay","Switzerland","Egypt",
                 "Ivory Coast","Türkiye","Scotland","South Africa","Uzbekistan","Haiti"],
    "Ruaidhri": ["England","Portugal","Belgium","United States","Japan","Ecuador",
                 "Algeria","Czechia","Norway","Saudi Arabia","Paraguay","New Zealand"],
}

# Build reverse lookup: team name → owner
TEAM_OWNER = {}
for player, teams in DRAW.items():
    for team in teams:
        TEAM_OWNER[team.lower()] = player

# Aliases for API name mismatches
TEAM_ALIASES = {
    "usa":                    "United States",
    "united states":          "United States",
    "czech republic":         "Czechia",
    "czechia":                "Czechia",
    "ivory coast":            "Ivory Coast",
    "côte d'ivoire":          "Ivory Coast",
    "cote d'ivoire":          "Ivory Coast",
    "south korea":            "South Korea",
    "korea republic":         "South Korea",
    "dr congo":               "DR Congo",
    "congo dr":               "DR Congo",
    "democratic republic of congo": "DR Congo",
    "bosnia and herzegovina": "Bosnia & Herz.",
    "bosnia & herzegovina":   "Bosnia & Herz.",
    "bosnia":                 "Bosnia & Herz.",
    "cabo verde":             "Cape Verde",
    "cape verde":             "Cape Verde",
    "curacao":                "Curaçao",
    "curaçao":                "Curaçao",
    "turkey":                 "Türkiye",
    "türkiye":                "Türkiye",
    "new zealand":            "New Zealand",
}

def normalise(name: str) -> str:
    """Normalise a team name from an API to match our draw."""
    n = name.strip().lower()
    if n in TEAM_ALIASES:
        return TEAM_ALIASES[n]
    # Try direct match in draw
    for team in TEAM_OWNER:
        if team == n:
            return team.title()
    return name.strip()

def owner_of(team_name: str) -> str | None:
    n = normalise(team_name).lower()
    return TEAM_OWNER.get(n)

# ─── SCORE FETCHING ───────────────────────────────────────────────────────────

PRIMARY_URL  = "https://worldcup26.ir/get/games"
FALLBACK_URL = "https://api.football-data.org/v4/competitions/WC/matches"

async def fetch_matches() -> list[dict]:
    """
    Fetch all WC2026 matches. Returns list of normalised match dicts:
    {
        "home": str, "away": str, "group": str,
        "home_score": int|None, "away_score": int|None,
        "status": "SCHEDULED"|"IN_PLAY"|"FINISHED",
        "utc_date": str  (ISO8601),
        "matchday": int
    }
    """
    async with aiohttp.ClientSession() as session:
        # Try primary API first
        try:
            async with session.get(PRIMARY_URL, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200:
                    data = await r.json(content_type=None)
                    return parse_primary(data)
        except Exception as e:
            logger.warning(f"Primary API failed: {e}")

        # Fallback to football-data.org
        try:
            headers = {"X-Auth-Token": FD_API_KEY} if FD_API_KEY else {}
            async with session.get(
                FALLBACK_URL,
                headers=headers,
                params={"stage": "GROUP_STAGE"},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    return parse_fallback(data)
        except Exception as e:
            logger.warning(f"Fallback API failed: {e}")

    return []

def parse_primary(data) -> list[dict]:
    """Parse worldcup26.ir response."""
    matches = []
    games = data if isinstance(data, list) else data.get("games", data.get("matches", []))
    for g in games:
        try:
            home = normalise(g.get("home_team", g.get("homeTeam", "")))
            away = normalise(g.get("away_team", g.get("awayTeam", "")))
            hs   = g.get("home_score", g.get("homeScore"))
            as_  = g.get("away_score", g.get("awayScore"))
            status = "FINISHED" if hs is not None else "SCHEDULED"
            if g.get("live") or g.get("status") in ("IN_PLAY","LIVE","live"):
                status = "IN_PLAY"
            group = g.get("group", g.get("stage", ""))
            date  = g.get("date", g.get("datetime", g.get("utcDate", "")))
            md    = g.get("matchday", g.get("round", 1))
            if isinstance(md, str):
                try: md = int("".join(filter(str.isdigit, md)) or 1)
                except: md = 1
            matches.append({
                "home": home, "away": away, "group": group,
                "home_score": int(hs) if hs is not None else None,
                "away_score": int(as_) if as_ is not None else None,
                "status": status, "utc_date": str(date), "matchday": md,
            })
        except Exception as e:
            logger.debug(f"Skipping game: {e}")
    return matches

def parse_fallback(data) -> list[dict]:
    """Parse football-data.org v4 response."""
    matches = []
    for m in data.get("matches", []):
        try:
            home   = normalise(m["homeTeam"]["name"])
            away   = normalise(m["awayTeam"]["name"])
            score  = m.get("score", {})
            ft     = score.get("fullTime", {})
            hs     = ft.get("home")
            as_    = ft.get("away")
            status = m.get("status", "SCHEDULED")
            group  = m.get("group", "")
            date   = m.get("utcDate", "")
            md     = m.get("matchday", 1)
            matches.append({
                "home": home, "away": away, "group": group,
                "home_score": hs, "away_score": as_,
                "status": status, "utc_date": date, "matchday": md,
            })
        except Exception as e:
            logger.debug(f"Skipping match: {e}")
    return matches

# ─── LEADERBOARD CALCULATION ──────────────────────────────────────────────────

def calc_leaderboard(matches: list[dict]) -> dict:
    """
    Points system:
    - Team wins group:        4 pts
    - Team finishes 2nd:      3 pts
    - Team finishes 3rd (Q):  2 pts  (best 8 3rd-place teams qualify)
    - Team finishes 3rd (out):1 pt
    - Team eliminated:        0 pts
    We approximate from current results: goals scored used as tiebreaker.
    Returns {player: {"points": int, "teams": {team: {"gp","gf","ga","pts"}}}}
    """
    # Build team records per group
    group_records: dict[str, dict[str, dict]] = {}
    for m in matches:
        if m["status"] != "FINISHED":
            continue
        grp = m.get("group", "?")
        if not grp:
            continue
        for team, opp_score, my_score in [
            (m["home"], m["away_score"], m["home_score"]),
            (m["away"], m["home_score"], m["away_score"]),
        ]:
            if team not in group_records.setdefault(grp, {}):
                group_records[grp][team] = {"gp":0,"gf":0,"ga":0,"pts":0}
            r = group_records[grp][team]
            r["gp"] += 1
            r["gf"] += my_score or 0
            r["ga"] += opp_score or 0
            if my_score > opp_score:   r["pts"] += 3
            elif my_score == opp_score: r["pts"] += 1

    # Rank teams within each group
    player_bonus: dict[str, int] = {p: 0 for p in DRAW}
    for grp, teams in group_records.items():
        ranked = sorted(
            teams.items(),
            key=lambda x: (x[1]["pts"], x[1]["gf"]-x[1]["ga"], x[1]["gf"]),
            reverse=True
        )
        # Only assign bonus if all 3 matchdays played (6 matches per group)
        finished_games = sum(1 for m in matches
                             if m.get("group") == grp and m["status"] == "FINISHED")
        if finished_games < 6:
            continue  # group not complete yet
        for pos, (team, _) in enumerate(ranked):
            owner = owner_of(team)
            if not owner:
                continue
            if pos == 0:   player_bonus[owner] += 4
            elif pos == 1: player_bonus[owner] += 3
            elif pos == 2: player_bonus[owner] += 1  # conservative: assume not top-8 3rd

    return {"bonus": player_bonus, "group_records": group_records}

# ─── FORMATTERS ───────────────────────────────────────────────────────────────

MEDALS = ["🥇","🥈","🥉","4️⃣"]

def fmt_leaderboard(matches: list[dict]) -> str:
    data = calc_leaderboard(matches)
    bonus = data["bonus"]

    # Count goals per player (fun metric even mid-tournament)
    goals: dict[str, int] = {p: 0 for p in DRAW}
    for m in matches:
        if m["status"] != "FINISHED":
            continue
        for team, score in [(m["home"], m["home_score"]), (m["away"], m["away_score"])]:
            owner = owner_of(team)
            if owner and score:
                goals[owner] += score

    ranked = sorted(DRAW.keys(), key=lambda p: (-bonus[p], -goals[p]))

    lines = ["🏆 *Family Leaderboard*\n"]
    for i, player in enumerate(ranked):
        medal = MEDALS[i] if i < len(MEDALS) else "  "
        b = bonus[player]
        g = goals[player]
        lines.append(f"{medal} *{player}* — {b} pts | ⚽ {g} goals")

    lines.append("\n_Points: 1st=4, 2nd=3, 3rd=1 (per group completed)_")
    lines.append("_Goals are your teams' combined goals scored_")
    return "\n".join(lines)


def fmt_group_tables(matches: list[dict]) -> str:
    data = calc_leaderboard(matches)
    grecs = data["group_records"]
    if not grecs:
        return "⏳ No completed matches yet — tables will appear once games kick off!"

    out = ["📊 *Group Tables*\n"]
    for grp in sorted(grecs.keys()):
        teams = grecs[grp]
        ranked = sorted(
            teams.items(),
            key=lambda x: (x[1]["pts"], x[1]["gf"]-x[1]["ga"], x[1]["gf"]),
            reverse=True
        )
        out.append(f"*Group {grp.replace('Group ','')}*")
        for pos, (team, r) in enumerate(ranked):
            owner = owner_of(team)
            owner_tag = f" ({owner})" if owner else ""
            qual = "✅" if pos < 2 else "  "
            gd = r['gf'] - r['ga']
            gd_str = f"+{gd}" if gd > 0 else str(gd)
            out.append(
                f"{qual} {team}{owner_tag} — "
                f"P{r['gp']} {r['gf']}:{r['ga']} ({gd_str}) {r['pts']}pts"
            )
        out.append("")
    return "\n".join(out)


def fmt_today(matches: list[dict]) -> str:
    now_ist = datetime.now(IST)
    today   = now_ist.date()
    todays  = []
    for m in matches:
        try:
            dt = datetime.fromisoformat(m["utc_date"].replace("Z","+00:00")).astimezone(IST)
            if dt.date() == today:
                todays.append((dt, m))
        except:
            pass

    if not todays:
        return f"📅 No matches today ({today.strftime('%a %d %b')} IST)"

    todays.sort(key=lambda x: x[0])
    lines = [f"📅 *Today's matches — {today.strftime('%a %d %b')} IST*\n"]
    for dt, m in todays:
        home_owner = owner_of(m["home"])
        away_owner = owner_of(m["away"])
        ht = f"{m['home']}" + (f" ({home_owner})" if home_owner else "")
        at = f"{m['away']}" + (f" ({away_owner})" if away_owner else "")
        if m["status"] == "FINISHED":
            score = f"*{m['home_score']} – {m['away_score']}* FT"
        elif m["status"] == "IN_PLAY":
            score = f"*{m['home_score']} – {m['away_score']}* 🔴 LIVE"
        else:
            score = dt.strftime("%H:%M")
        lines.append(f"• {ht} vs {at}  {score}")
    return "\n".join(lines)


def fmt_upcoming(matches: list[dict], days: int = 3) -> str:
    now_ist   = datetime.now(IST)
    cutoff    = now_ist + timedelta(days=days)
    upcoming  = []
    for m in matches:
        if m["status"] != "SCHEDULED":
            continue
        try:
            dt = datetime.fromisoformat(m["utc_date"].replace("Z","+00:00")).astimezone(IST)
            if now_ist <= dt <= cutoff:
                upcoming.append((dt, m))
        except:
            pass

    if not upcoming:
        return f"📆 No upcoming matches in the next {days} days."

    upcoming.sort(key=lambda x: x[0])
    lines = [f"📆 *Upcoming matches (next {days} days)*\n"]
    last_date = None
    for dt, m in upcoming:
        d = dt.date()
        if d != last_date:
            lines.append(f"*{d.strftime('%a %d %b')}*")
            last_date = d
        home_owner = owner_of(m["home"])
        away_owner = owner_of(m["away"])
        ht = m["home"] + (f" ({home_owner})" if home_owner else "")
        at = m["away"] + (f" ({away_owner})" if away_owner else "")
        lines.append(f"  {dt.strftime('%H:%M')}  {ht} vs {at}")
    return "\n".join(lines)


def fmt_results(matches: list[dict], n: int = 10) -> str:
    finished = [m for m in matches if m["status"] == "FINISHED"]
    finished.sort(key=lambda m: m.get("utc_date",""), reverse=True)
    recent = finished[:n]
    if not recent:
        return "No results yet."
    lines = [f"📋 *Latest results*\n"]
    for m in recent:
        ho = owner_of(m["home"])
        ao = owner_of(m["away"])
        ht = m["home"] + (f" ({ho})" if ho else "")
        at = m["away"] + (f" ({ao})" if ao else "")
        lines.append(f"• {ht} *{m['home_score']} – {m['away_score']}* {at}")
    return "\n".join(lines)


# ─── BOT COMMANDS ─────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🌍 *WC2026 Family Draw Bot*\n\n"
        "Commands:\n"
        "/today — today's matches & scores\n"
        "/results — latest results\n"
        "/upcoming — next 3 days of fixtures\n"
        "/leaderboard — family standings\n"
        "/groups — all group tables\n"
        "/draw — who owns which teams\n"
        "/help — this message"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

async def cmd_leaderboard(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Fetching scores…")
    matches = await fetch_matches()
    await update.message.reply_text(fmt_leaderboard(matches), parse_mode=ParseMode.MARKDOWN)

async def cmd_groups(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Fetching tables…")
    matches = await fetch_matches()
    await update.message.reply_text(fmt_group_tables(matches), parse_mode=ParseMode.MARKDOWN)

async def cmd_today(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    matches = await fetch_matches()
    await update.message.reply_text(fmt_today(matches), parse_mode=ParseMode.MARKDOWN)

async def cmd_upcoming(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    matches = await fetch_matches()
    await update.message.reply_text(fmt_upcoming(matches), parse_mode=ParseMode.MARKDOWN)

async def cmd_results(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    matches = await fetch_matches()
    await update.message.reply_text(fmt_results(matches), parse_mode=ParseMode.MARKDOWN)

async def cmd_draw(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    lines = ["🎲 *The Draw*\n"]
    for player, teams in DRAW.items():
        lines.append(f"*{player}*: {', '.join(teams)}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, ctx)

# ─── SCHEDULED DAILY UPDATE ───────────────────────────────────────────────────

async def daily_update(ctx: ContextTypes.DEFAULT_TYPE):
    """Sent at 08:00 IST every day during the tournament."""
    try:
        matches = await fetch_matches()
        bot: Bot = ctx.bot

        # Only post if tournament is active (has any finished matches OR today has matches)
        has_activity = any(m["status"] in ("FINISHED","IN_PLAY") for m in matches)
        today_has_matches = any(True for m in matches if _is_today(m))

        if not has_activity and not today_has_matches:
            return

        parts = []
        parts.append(fmt_today(matches))
        parts.append("\n" + fmt_leaderboard(matches))

        await bot.send_message(
            chat_id=CHAT_ID,
            text="\n".join(parts),
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        logger.error(f"daily_update failed: {e}")

async def evening_results(ctx: ContextTypes.DEFAULT_TYPE):
    """Sent at 23:59 IST — recap of day's results."""
    try:
        matches = await fetch_matches()
        today_finished = [m for m in matches if _is_today(m) and m["status"] == "FINISHED"]
        if not today_finished:
            return
        bot: Bot = ctx.bot
        text = fmt_results(today_finished, n=20)
        text = "🌙 *Day's results*\n\n" + text[text.find("\n")+1:]
        await bot.send_message(chat_id=CHAT_ID, text=text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"evening_results failed: {e}")

def _is_today(m: dict) -> bool:
    try:
        dt = datetime.fromisoformat(m["utc_date"].replace("Z","+00:00")).astimezone(IST)
        return dt.date() == datetime.now(IST).date()
    except:
        return False

# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start",       cmd_start))
    app.add_handler(CommandHandler("help",        cmd_help))
    app.add_handler(CommandHandler("leaderboard", cmd_leaderboard))
    app.add_handler(CommandHandler("groups",      cmd_groups))
    app.add_handler(CommandHandler("today",       cmd_today))
    app.add_handler(CommandHandler("upcoming",    cmd_upcoming))
    app.add_handler(CommandHandler("results",     cmd_results))
    app.add_handler(CommandHandler("draw",        cmd_draw))

    # Scheduled jobs (times are UTC; IST = UTC+1)
    jq = app.job_queue
    # 08:00 IST = 07:00 UTC
    jq.run_daily(daily_update,   time=datetime.now(timezone.utc).replace(
        hour=7, minute=0, second=0, microsecond=0).timetz())
    # 23:59 IST = 22:59 UTC
    jq.run_daily(evening_results, time=datetime.now(timezone.utc).replace(
        hour=22, minute=59, second=0, microsecond=0).timetz())

    logger.info("Bot starting…")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
