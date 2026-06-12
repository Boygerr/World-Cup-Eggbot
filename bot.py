"""
WC2026 Family Draw Bot  v2
Tracks the 4-player draw: Ronan, Marc, Eoin, Ruaidhri

Data source: openfootball/worldcup.json (GitHub, public domain, updated daily)
  https://raw.githubusercontent.com/openfootball/worldcup.json/master/2026/worldcup.json

Verified structure (June 2026):
  match = {
    "round": "Matchday 1",
    "date":  "2026-06-11",
    "time":  "13:00 UTC-6",
    "team1": "Mexico",
    "team2": "South Africa",
    "score": {"ft": [2, 0], "ht": [1, 0]},   # only present when finished
    "group": "Group A",                        # group stage only
    "ground": "Mexico City"
  }
Knockout placeholders ("1A", "W73", "3A/B/C/D/F"…) are shown as TBD until resolved.
"""

import os
import re
import logging
from datetime import datetime, timezone, timedelta, time as dtime

import aiohttp
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.constants import ParseMode

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID        = os.environ["CHAT_ID"]

DATA_URL = "https://raw.githubusercontent.com/openfootball/worldcup.json/master/2026/worldcup.json"

IST = timezone(timedelta(hours=1))  # Irish Standard Time (summer) = UTC+1

# ─── THE DRAW ─────────────────────────────────────────────────────────────────
# Names below use the EXACT spellings from the openfootball dataset.

DRAW = {
    "Ronan":    ["Argentina", "Morocco", "Germany", "Mexico", "Iran", "Australia",
                 "Qatar", "Sweden", "DR Congo", "Iraq", "Ghana", "Cape Verde"],
    "Marc":     ["Spain", "Brazil", "Colombia", "Senegal", "Austria", "South Korea",
                 "Canada", "Tunisia", "Bosnia & Herzegovina", "Panama", "Jordan", "Curaçao"],
    "Eoin":     ["France", "Netherlands", "Croatia", "Uruguay", "Switzerland", "Egypt",
                 "Ivory Coast", "Turkey", "Scotland", "South Africa", "Uzbekistan", "Haiti"],
    "Ruaidhri": ["England", "Portugal", "Belgium", "USA", "Japan", "Ecuador",
                 "Algeria", "Czech Republic", "Norway", "Saudi Arabia", "Paraguay", "New Zealand"],
}

TEAM_OWNER = {t: p for p, teams in DRAW.items() for t in teams}

# Knockout placeholder pattern: "1A", "2L", "3A/B/C/D/F", "W73", "L101"
PLACEHOLDER_RE = re.compile(r"^([123][A-L](/.*)?|[WL]\d+)$")

def is_placeholder(name: str) -> bool:
    return bool(PLACEHOLDER_RE.match(name.strip()))

def owner_of(team: str) -> str | None:
    return TEAM_OWNER.get(team)

def tag(team: str) -> str:
    """Team name with owner in brackets, e.g. 'Spain (Marc)'."""
    o = owner_of(team)
    return f"{team} ({o})" if o else team

# ─── DATA FETCH + CACHE ───────────────────────────────────────────────────────

_cache: dict = {"matches": [], "fetched_at": None}
CACHE_TTL = timedelta(minutes=10)

async def fetch_matches(force: bool = False) -> list[dict]:
    """Fetch + parse matches, with a 10-minute cache to stay fast and polite."""
    now = datetime.now(timezone.utc)
    if (not force and _cache["fetched_at"]
            and now - _cache["fetched_at"] < CACHE_TTL
            and _cache["matches"]):
        return _cache["matches"]

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(DATA_URL, timeout=aiohttp.ClientTimeout(total=15)) as r:
                r.raise_for_status()
                data = await r.json(content_type=None)
    except Exception as e:
        logger.error(f"Fetch failed: {e}")
        return _cache["matches"]  # serve stale data rather than nothing

    matches = []
    for m in data.get("matches", []):
        team1 = m.get("team1", "")
        team2 = m.get("team2", "")
        if isinstance(team1, dict): team1 = team1.get("name", "")
        if isinstance(team2, dict): team2 = team2.get("name", "")

        score = m.get("score") or {}
        ft = score.get("ft")
        hs, as_ = (ft[0], ft[1]) if ft else (None, None)

        matches.append({
            "round":   m.get("round", ""),
            "date":    m.get("date", ""),        # "2026-06-11"
            "time":    m.get("time", ""),        # "13:00 UTC-6"
            "team1":   team1,
            "team2":   team2,
            "hs":      hs,
            "as":      as_,
            "group":   m.get("group", ""),       # "" for knockouts
            "ground":  m.get("ground", ""),
            "goals1":  m.get("goals1", []),
            "goals2":  m.get("goals2", []),
        })

    _cache["matches"] = matches
    _cache["fetched_at"] = now
    logger.info(f"Fetched {len(matches)} matches "
                f"({sum(1 for x in matches if x['hs'] is not None)} finished)")
    return matches

def kickoff_ist(m: dict) -> datetime | None:
    """Parse '13:00 UTC-6' + '2026-06-11' → aware datetime in Irish time."""
    try:
        tm = re.match(r"(\d{1,2}):(\d{2})\s*UTC([+-]\d{1,2})", m["time"])
        if not tm:
            return None
        hh, mm, offset = int(tm.group(1)), int(tm.group(2)), int(tm.group(3))
        local_tz = timezone(timedelta(hours=offset))
        y, mo, d = (int(x) for x in m["date"].split("-"))
        return datetime(y, mo, d, hh, mm, tzinfo=local_tz).astimezone(IST)
    except Exception:
        return None

def finished(m: dict) -> bool:
    return m["hs"] is not None

# ─── GROUP TABLES + LEADERBOARD ───────────────────────────────────────────────

def build_group_tables(matches: list[dict]) -> dict[str, list]:
    """{ 'Group A': [(team, {gp,w,d,l,gf,ga,pts}), ...sorted...] }"""
    tables: dict[str, dict[str, dict]] = {}
    for m in matches:
        if not m["group"] or not finished(m):
            continue
        grp = m["group"]
        for team, gf, ga in [(m["team1"], m["hs"], m["as"]),
                             (m["team2"], m["as"], m["hs"])]:
            r = tables.setdefault(grp, {}).setdefault(
                team, {"gp":0,"w":0,"d":0,"l":0,"gf":0,"ga":0,"pts":0})
            r["gp"] += 1; r["gf"] += gf; r["ga"] += ga
            if gf > ga:   r["w"] += 1; r["pts"] += 3
            elif gf == ga: r["d"] += 1; r["pts"] += 1
            else:          r["l"] += 1

    out = {}
    for grp, teams in tables.items():
        out[grp] = sorted(
            teams.items(),
            key=lambda x: (x[1]["pts"], x[1]["gf"] - x[1]["ga"], x[1]["gf"]),
            reverse=True)
    return out

def group_complete(matches: list[dict], grp: str) -> bool:
    games = [m for m in matches if m["group"] == grp]
    return len(games) >= 6 and all(finished(m) for m in games)

def calc_scores(matches: list[dict]) -> dict[str, dict]:
    """
    Per-player tally:
      goals   — combined goals scored by their teams (all finished games)
      wins    — combined wins
      gpts    — group points: 1st=4, 2nd=3, 3rd=1 (only for completed groups)
      kopts   — knockout points: 2 per knockout win (R32 → Final)
    """
    res = {p: {"goals":0, "wins":0, "gpts":0, "kopts":0} for p in DRAW}

    for m in matches:
        if not finished(m):
            continue
        for team, gf, ga in [(m["team1"], m["hs"], m["as"]),
                             (m["team2"], m["as"], m["hs"])]:
            o = owner_of(team)
            if not o:
                continue
            res[o]["goals"] += gf
            if gf > ga:
                res[o]["wins"] += 1
                if not m["group"]:           # knockout win
                    res[o]["kopts"] += 2

    tables = build_group_tables(matches)
    for grp, ranked in tables.items():
        if not group_complete(matches, grp):
            continue
        for pos, (team, _) in enumerate(ranked):
            o = owner_of(team)
            if not o:
                continue
            if pos == 0:   res[o]["gpts"] += 4
            elif pos == 1: res[o]["gpts"] += 3
            elif pos == 2: res[o]["gpts"] += 1
    return res

# ─── MESSAGE FORMATTERS ───────────────────────────────────────────────────────

MEDALS = ["🥇", "🥈", "🥉", "4️⃣"]

def fmt_leaderboard(matches: list[dict]) -> str:
    s = calc_scores(matches)
    total = {p: v["gpts"] + v["kopts"] for p, v in s.items()}
    ranked = sorted(DRAW, key=lambda p: (-total[p], -s[p]["wins"], -s[p]["goals"]))

    lines = ["🏆 *Family Leaderboard*", ""]
    for i, p in enumerate(ranked):
        v = s[p]
        lines.append(
            f"{MEDALS[i]} *{p}* — {total[p]} pts  "
            f"(⚽{v['goals']}  ✅{v['wins']}W)")
    lines += ["",
              "_Group pts: 1st=4 · 2nd=3 · 3rd=1 (when group completes)_",
              "_Knockout: +2 per win · ⚽ goals · ✅ wins by your teams_"]
    return "\n".join(lines)

def fmt_groups(matches: list[dict]) -> str:
    tables = build_group_tables(matches)
    if not tables:
        return "⏳ No completed group games yet."
    lines = ["📊 *Group Tables*", ""]
    for grp in sorted(tables):
        lines.append(f"*{grp}*")
        for pos, (team, r) in enumerate(tables[grp]):
            gd = r["gf"] - r["ga"]
            mark = "✅" if pos < 2 else "▫️"
            lines.append(
                f"{mark} {tag(team)} — {r['pts']}pts "
                f"(P{r['gp']} {r['gf']}:{r['ga']})")
        lines.append("")
    return "\n".join(lines).rstrip()

def fmt_match_line(m: dict, show_time: bool = True) -> str:
    t1 = "TBD" if is_placeholder(m["team1"]) else tag(m["team1"])
    t2 = "TBD" if is_placeholder(m["team2"]) else tag(m["team2"])
    if finished(m):
        mid = f"*{m['hs']} – {m['as']}*"
    else:
        ko = kickoff_ist(m)
        mid = ko.strftime("%H:%M") if (ko and show_time) else "vs"
    return f"• {t1}  {mid}  {t2}"

def fmt_today(matches: list[dict]) -> str:
    today = datetime.now(IST).date()
    todays = [(kickoff_ist(m), m) for m in matches]
    todays = [(k, m) for k, m in todays if k and k.date() == today]
    if not todays:
        return f"📅 No matches today ({today.strftime('%a %d %b')})."
    todays.sort(key=lambda x: x[0])
    lines = [f"📅 *Matches today — {today.strftime('%a %d %b')}* (Irish time)", ""]
    lines += [fmt_match_line(m) for _, m in todays]
    return "\n".join(lines)

def fmt_results(matches: list[dict], n: int = 12) -> str:
    done = [m for m in matches if finished(m)]
    if not done:
        return "No finished matches yet."
    done.sort(key=lambda m: (m["date"], m["time"]), reverse=True)
    lines = ["📋 *Latest results*", ""]
    cur_date = None
    for m in done[:n]:
        if m["date"] != cur_date:
            cur_date = m["date"]
            d = datetime.strptime(cur_date, "%Y-%m-%d")
            lines.append(f"*{d.strftime('%a %d %b')}*")
        lines.append(fmt_match_line(m))
    return "\n".join(lines)

def fmt_upcoming(matches: list[dict], days: int = 3) -> str:
    now = datetime.now(IST)
    cutoff = now + timedelta(days=days)
    ups = []
    for m in matches:
        if finished(m):
            continue
        k = kickoff_ist(m)
        if k and now <= k <= cutoff:
            ups.append((k, m))
    if not ups:
        return f"📆 No upcoming matches in the next {days} days."
    ups.sort(key=lambda x: x[0])
    lines = [f"📆 *Next {days} days* (Irish time)", ""]
    cur = None
    for k, m in ups:
        if k.date() != cur:
            cur = k.date()
            lines.append(f"*{cur.strftime('%a %d %b')}*")
        lines.append(fmt_match_line(m))
    return "\n".join(lines)

def fmt_draw() -> str:
    lines = ["🎲 *The Draw*", ""]
    for p, teams in DRAW.items():
        lines.append(f"*{p}*")
        lines.append(", ".join(teams))
        lines.append("")
    return "\n".join(lines).rstrip()

# ─── COMMANDS ─────────────────────────────────────────────────────────────────

HELP = (
    "🌍 *WC2026 Family Draw Bot*\n\n"
    "/today — today's fixtures & scores\n"
    "/results — latest results\n"
    "/upcoming — next 3 days\n"
    "/leaderboard — family standings\n"
    "/groups — group tables\n"
    "/draw — who owns which teams\n"
    "/refresh — force-refresh the data"
)

async def cmd_start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text(HELP, parse_mode=ParseMode.MARKDOWN)

async def cmd_today(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text(fmt_today(await fetch_matches()),
                               parse_mode=ParseMode.MARKDOWN)

async def cmd_results(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text(fmt_results(await fetch_matches()),
                               parse_mode=ParseMode.MARKDOWN)

async def cmd_upcoming(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text(fmt_upcoming(await fetch_matches()),
                               parse_mode=ParseMode.MARKDOWN)

async def cmd_leaderboard(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text(fmt_leaderboard(await fetch_matches()),
                               parse_mode=ParseMode.MARKDOWN)

async def cmd_groups(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text(fmt_groups(await fetch_matches()),
                               parse_mode=ParseMode.MARKDOWN)

async def cmd_draw(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text(fmt_draw(), parse_mode=ParseMode.MARKDOWN)

async def cmd_refresh(u: Update, c: ContextTypes.DEFAULT_TYPE):
    matches = await fetch_matches(force=True)
    n_done = sum(1 for m in matches if finished(m))
    await u.message.reply_text(
        f"🔄 Refreshed — {len(matches)} matches loaded, {n_done} finished.")

# ─── DAILY JOBS ───────────────────────────────────────────────────────────────

async def morning_update(ctx: ContextTypes.DEFAULT_TYPE):
    """08:00 IST — today's fixtures + current leaderboard."""
    try:
        matches = await fetch_matches(force=True)
        text = fmt_today(matches)
        if "No matches today" not in text:
            text += "\n\n" + fmt_leaderboard(matches)
        await ctx.bot.send_message(chat_id=CHAT_ID, text=text,
                                   parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"morning_update: {e}")

async def night_recap(ctx: ContextTypes.DEFAULT_TYPE):
    """23:30 IST — today's results, if any finished."""
    try:
        matches = await fetch_matches(force=True)
        today = datetime.now(IST).date().isoformat()
        done_today = [m for m in matches if finished(m) and m["date"] == today]
        if not done_today:
            return
        lines = ["🌙 *Today's results*", ""]
        lines += [fmt_match_line(m) for m in done_today]
        await ctx.bot.send_message(chat_id=CHAT_ID, text="\n".join(lines),
                                   parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"night_recap: {e}")

# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    for cmd, fn in [("start", cmd_start), ("help", cmd_start),
                    ("today", cmd_today), ("results", cmd_results),
                    ("upcoming", cmd_upcoming), ("leaderboard", cmd_leaderboard),
                    ("groups", cmd_groups), ("draw", cmd_draw),
                    ("refresh", cmd_refresh)]:
        app.add_handler(CommandHandler(cmd, fn))

    # Jobs run in UTC: 08:00 IST = 07:00 UTC, 23:30 IST = 22:30 UTC
    app.job_queue.run_daily(morning_update, time=dtime(7, 0, tzinfo=timezone.utc))
    app.job_queue.run_daily(night_recap,    time=dtime(22, 30, tzinfo=timezone.utc))

    logger.info("Bot v2 starting…")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
