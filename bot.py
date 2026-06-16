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
import json
import random
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

# /bonus is restricted to a single user. Set BONUS_USER in Railway variables
# to EITHER their @username (e.g. "marc" or "@marc") OR their numeric Telegram
# ID (e.g. "123456789"). If unset, /bonus is disabled.
# Usernames are matched case-insensitively and the leading @ is optional.
BONUS_USER = os.environ.get("BONUS_USER", os.environ.get("BONUS_USER_ID", "")).strip().lstrip("@").lower()

# Persistent storage. If a Railway Volume is mounted at /data it survives
# redeploys; otherwise falls back to the working dir (survives restarts,
# resets on redeploy). Override with DATA_DIR if you like.
DATA_DIR  = os.environ.get("DATA_DIR", "/data" if os.path.isdir("/data") else ".")
STATE_FILE = os.path.join(DATA_DIR, "bot_state.json")

def load_state() -> dict:
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def save_state(state: dict) -> None:
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, STATE_FILE)   # atomic write
    except Exception as e:
        logger.error(f"save_state failed: {e}")

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

# ─── BBQ RECIPES ──────────────────────────────────────────────────────────────
# Short, crowd-pleasing recipes for match-day grilling. /bbq picks one at random.

BBQ_RECIPES = [
    {
        "name": "Classic Smashed Burgers",
        "time": "20 min",
        "serves": "4",
        "ingredients": [
            "500g beef mince (20% fat)", "4 brioche buns", "4 slices cheddar",
            "1 onion, finely sliced", "Salt & pepper", "Ketchup & mustard",
        ],
        "steps": [
            "Roll mince into 4 loose balls, don't overwork.",
            "Get the grill/plate screaming hot.",
            "Smash each ball flat with a spatula, season hard.",
            "2 min, flip, add cheese, 1 min more.",
            "Toast buns on the grill, build with onion & sauces.",
        ],
    },
    {
        "name": "Sticky Honey-Soy Chicken Thighs",
        "time": "30 min + marinate",
        "serves": "4–6",
        "ingredients": [
            "8 boneless chicken thighs", "4 tbsp honey", "4 tbsp soy sauce",
            "3 garlic cloves, crushed", "Thumb of ginger, grated", "1 tbsp sesame oil",
        ],
        "steps": [
            "Whisk honey, soy, garlic, ginger, sesame oil.",
            "Marinate thighs 30 min (or overnight).",
            "Grill medium 6–7 min per side, basting with leftover marinade.",
            "Watch the last few min — honey burns fast.",
            "Rest 5 min, scatter with sesame seeds & spring onion.",
        ],
    },
    {
        "name": "Garlic & Herb Butter Corn",
        "time": "15 min",
        "serves": "6",
        "ingredients": [
            "6 corn cobs", "100g butter, softened", "3 garlic cloves, crushed",
            "Small bunch parsley, chopped", "Salt", "Lime wedges",
        ],
        "steps": [
            "Boil corn 4 min to par-cook.",
            "Mix butter with garlic, parsley, salt.",
            "Grill corn direct heat, turning, until charred.",
            "Roll in the herb butter while hot.",
            "Serve with lime.",
        ],
    },
    {
        "name": "Chorizo & Halloumi Skewers",
        "time": "20 min",
        "serves": "4",
        "ingredients": [
            "250g cooking chorizo, chunked", "250g halloumi, cubed",
            "1 red pepper, chunked", "1 red onion, wedges", "Olive oil", "Smoked paprika",
        ],
        "steps": [
            "Thread chorizo, halloumi, pepper, onion onto skewers.",
            "Brush with oil, dust with smoked paprika.",
            "Grill medium-high 3–4 min per side.",
            "Halloumi should be golden, chorizo crisp at edges.",
            "Great with a squeeze of lemon.",
        ],
    },
    {
        "name": "Beer-Can Whole Chicken",
        "time": "1 hr 15 min",
        "serves": "4",
        "ingredients": [
            "1 whole chicken (~1.5kg)", "1 can of lager", "2 tbsp BBQ rub",
            "Olive oil", "Salt",
        ],
        "steps": [
            "Set up grill for indirect heat (~180°C).",
            "Open beer, drink half. Rub chicken with oil, salt, BBQ rub.",
            "Sit chicken upright over the half-full can.",
            "Cook indirect, lid down, ~60–75 min.",
            "Done at 75°C in the thigh. Rest 10 min before carving.",
        ],
    },
    {
        "name": "Grilled Veg Platter",
        "time": "25 min",
        "serves": "6",
        "ingredients": [
            "2 courgettes, sliced lengthways", "2 peppers, quartered",
            "1 aubergine, sliced", "1 red onion, wedges", "Olive oil",
            "Balsamic vinegar", "Salt & pepper",
        ],
        "steps": [
            "Toss all veg in oil, salt, pepper.",
            "Grill medium-high, turning, until char marks appear.",
            "Courgette & pepper ~3 min/side, aubergine a touch longer.",
            "Pile on a platter, drizzle with balsamic.",
            "Lovely warm or at room temp.",
        ],
    },
    {
        "name": "Lamb Koftas",
        "time": "25 min",
        "serves": "4",
        "ingredients": [
            "500g lamb mince", "1 onion, grated", "2 garlic cloves, crushed",
            "1 tsp cumin", "1 tsp coriander", "Handful mint, chopped", "Salt",
        ],
        "steps": [
            "Mix everything, knead 2 min so it binds.",
            "Shape around skewers into sausage shapes.",
            "Chill 10 min to firm up.",
            "Grill medium-high 8–10 min, turning, until charred.",
            "Serve with flatbread, yoghurt & lemon.",
        ],
    },
    {
        "name": "BBQ Pork Ribs (quick method)",
        "time": "1 hr",
        "serves": "4",
        "ingredients": [
            "2 racks pork ribs", "3 tbsp BBQ rub", "200ml BBQ sauce",
            "Apple juice (spray bottle)",
        ],
        "steps": [
            "Pre-boil ribs 20 min to tenderise (the shortcut).",
            "Rub all over, grill indirect medium ~30 min.",
            "Spritz with apple juice every 10 min.",
            "Brush with BBQ sauce last 10 min, turning.",
            "Rest 5 min, cut between bones.",
        ],
    },
    {
        "name": "Peri-Peri Spatchcock Chicken",
        "time": "45 min",
        "serves": "4",
        "ingredients": [
            "1 whole chicken, spatchcocked", "4 tbsp peri-peri sauce",
            "2 tbsp olive oil", "1 lemon, juiced", "2 garlic cloves", "Salt",
        ],
        "steps": [
            "Mix peri-peri, oil, lemon, crushed garlic, salt.",
            "Coat the flattened chicken, marinate 20 min.",
            "Grill indirect skin-side up ~20 min, lid down.",
            "Flip, grill direct 10–15 min until charred & 75°C.",
            "Rest 10 min, hit with extra peri-peri.",
        ],
    },
    {
        "name": "Grilled Prawn Skewers",
        "time": "15 min",
        "serves": "4",
        "ingredients": [
            "500g large prawns, peeled", "3 tbsp olive oil", "2 garlic cloves, crushed",
            "1 lemon, zest & juice", "Chilli flakes", "Parsley",
        ],
        "steps": [
            "Toss prawns in oil, garlic, lemon zest, chilli.",
            "Thread onto skewers.",
            "Grill hot 2 min per side until pink and opaque.",
            "Squeeze over lemon juice, scatter parsley.",
            "Don't overcook — they go rubbery fast.",
        ],
    },
    {
        "name": "Halloumi & Veg Burgers",
        "time": "20 min",
        "serves": "4",
        "ingredients": [
            "250g halloumi, thick slices", "4 burger buns", "1 courgette, sliced",
            "1 red pepper, sliced", "Olive oil", "Sweet chilli sauce", "Rocket",
        ],
        "steps": [
            "Brush halloumi & veg with oil.",
            "Grill veg until soft and charred, ~3 min/side.",
            "Grill halloumi 1–2 min per side until golden.",
            "Toast buns.",
            "Stack with rocket, veg, halloumi & sweet chilli.",
        ],
    },
    {
        "name": "Texas-Style Brisket Bites",
        "time": "1 hr",
        "serves": "6",
        "ingredients": [
            "1kg beef brisket, cubed", "3 tbsp BBQ rub", "200ml BBQ sauce",
            "1 tbsp brown sugar", "Foil tray",
        ],
        "steps": [
            "Toss brisket cubes in rub and sugar.",
            "Grill direct to sear all sides, ~5 min.",
            "Move to foil tray, add BBQ sauce, cover.",
            "Cook indirect, lid down, ~45 min until tender.",
            "Toss in the sticky sauce and serve.",
        ],
    },
    {
        "name": "Grilled Sea Bass in Foil",
        "time": "25 min",
        "serves": "2",
        "ingredients": [
            "2 whole sea bass, gutted", "1 lemon, sliced", "Fresh dill & parsley",
            "2 garlic cloves, sliced", "Olive oil", "Salt & pepper",
        ],
        "steps": [
            "Stuff each fish with lemon, herbs, garlic.",
            "Drizzle with oil, season inside and out.",
            "Wrap tightly in foil parcels.",
            "Grill medium 10–12 min, turning once.",
            "Open carefully — flesh should flake easily.",
        ],
    },
    {
        "name": "Korean Gochujang Pork Belly",
        "time": "30 min + marinate",
        "serves": "4",
        "ingredients": [
            "600g pork belly, thin slices", "2 tbsp gochujang", "1 tbsp soy sauce",
            "1 tbsp honey", "1 tbsp sesame oil", "2 garlic cloves", "Spring onions",
        ],
        "steps": [
            "Mix gochujang, soy, honey, sesame oil, garlic.",
            "Coat pork, marinate 30 min.",
            "Grill hot 2–3 min per side until caramelised.",
            "Watch for flare-ups from the fat.",
            "Scatter spring onions, serve with lettuce wraps.",
        ],
    },
    {
        "name": "Cajun Corn Ribs",
        "time": "20 min",
        "serves": "4",
        "ingredients": [
            "4 corn cobs, quartered lengthways", "3 tbsp melted butter",
            "1 tbsp Cajun seasoning", "Lime", "Coriander",
        ],
        "steps": [
            "Carefully cut cobs into long quarters (the 'ribs').",
            "Brush with butter, dust with Cajun seasoning.",
            "Grill medium-high, turning, until they curl & char ~8 min.",
            "Squeeze lime over.",
            "Finish with chopped coriander.",
        ],
    },
    {
        "name": "Lamb Chops with Rosemary",
        "time": "20 min",
        "serves": "4",
        "ingredients": [
            "8 lamb chops", "3 sprigs rosemary, chopped", "3 garlic cloves, crushed",
            "3 tbsp olive oil", "Salt & pepper", "Lemon",
        ],
        "steps": [
            "Rub chops with oil, rosemary, garlic, seasoning.",
            "Rest 15 min at room temp.",
            "Grill hot 3–4 min per side for medium.",
            "Stand the chops on their fat edge to crisp.",
            "Rest 5 min, finish with a squeeze of lemon.",
        ],
    },
    {
        "name": "Buffalo Grilled Wings",
        "time": "30 min",
        "serves": "4",
        "ingredients": [
            "1kg chicken wings", "2 tbsp oil", "1 tsp baking powder",
            "100ml hot sauce", "50g butter, melted", "Celery sticks",
        ],
        "steps": [
            "Pat wings dry, toss with oil and baking powder (for crispness).",
            "Grill indirect medium ~20 min, turning.",
            "Move to direct heat to crisp, ~5 min.",
            "Toss in hot sauce mixed with melted butter.",
            "Serve with celery and blue cheese dip.",
        ],
    },
    {
        "name": "Mediterranean Veg & Feta Parcels",
        "time": "25 min",
        "serves": "4",
        "ingredients": [
            "1 courgette, diced", "1 aubergine, diced", "1 pepper, diced",
            "Cherry tomatoes", "200g feta, cubed", "Olive oil", "Oregano",
        ],
        "steps": [
            "Toss veg with oil, oregano, salt.",
            "Divide between 4 foil parcels, top with feta.",
            "Seal parcels loosely.",
            "Grill medium 15–18 min until veg is soft.",
            "Open and finish with a drizzle of oil.",
        ],
    },
    {
        "name": "Pineapple & Teriyaki Chicken Skewers",
        "time": "25 min + marinate",
        "serves": "4",
        "ingredients": [
            "600g chicken breast, cubed", "4 tbsp teriyaki sauce", "1 tbsp honey",
            "Fresh pineapple, chunked", "1 red onion, wedges", "Sesame seeds",
        ],
        "steps": [
            "Marinate chicken in teriyaki and honey 20 min.",
            "Thread chicken, pineapple and onion onto skewers.",
            "Grill medium-high 10–12 min, turning and basting.",
            "Pineapple should caramelise at the edges.",
            "Sprinkle with sesame seeds to serve.",
        ],
    },
    {
        "name": "Smoky Aubergine & Chickpea Flatbreads",
        "time": "25 min",
        "serves": "4",
        "ingredients": [
            "2 aubergines, sliced", "1 tin chickpeas, drained", "Olive oil",
            "1 tsp smoked paprika", "4 flatbreads", "Greek yoghurt", "Lemon & mint",
        ],
        "steps": [
            "Brush aubergine with oil, dust with smoked paprika.",
            "Grill medium-high until soft and charred, ~3 min/side.",
            "Toss chickpeas in oil, grill in a foil tray until crisp.",
            "Warm flatbreads on the grill.",
            "Top with yoghurt, aubergine, chickpeas, mint and lemon.",
        ],
    },
]


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

KNOCKOUT_ROUNDS = {
    "Round of 32", "Round of 16", "Quarter-final",
    "Semi-final", "Match for third place", "Final",
}

def qualified_teams(matches: list[dict]) -> set[str]:
    """
    Real teams (not placeholders) that appear in any Round of 32 fixture.
    These are the teams that advanced from the group stage.
    Empty until the R32 bracket is populated with real names.
    """
    out = set()
    for m in matches:
        if m["round"] != "Round of 32":
            continue
        for t in (m["team1"], m["team2"]):
            if t and not is_placeholder(t):
                out.add(t)
    return out

def tournament_winner(matches: list[dict]) -> str | None:
    """Winner of the Final, once played."""
    for m in matches:
        if m["round"] == "Final" and finished(m):
            return m["team1"] if m["hs"] > m["as"] else m["team2"]
    return None

def calc_scores(matches: list[dict]) -> dict[str, dict]:
    """
    Per-player tally:
      goals   — combined goals scored by their teams (all finished games)
      wins    — combined wins (group + knockout)
      gpts    — group finish points: 1st=4, 2nd=3, 3rd=1, 4th=0
                 (awarded once each group's 6 games are complete)
      penalty — -1 for every one of their teams that does NOT reach the R32
                 (applied only once the R32 bracket is populated)
      kopts   — +2 per knockout win
      winpts  — +5 if they own the tournament winner
    """
    res = {p: {"goals":0, "wins":0, "gpts":0,
               "penalty":0, "kopts":0, "winpts":0} for p in DRAW}

    # Goals, wins, knockout-win points
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
                if m["round"] in KNOCKOUT_ROUNDS:   # knockout win
                    res[o]["kopts"] += 2

    # Group standings points: 4 / 3 / 1 / 0 by CURRENT table position.
    # Applied live (not gated on group completion) so the leaderboard reflects
    # where teams currently stand. Recalculates every time standings shift.
    tables = build_group_tables(matches)
    for grp, ranked in tables.items():
        for pos, (team, _) in enumerate(ranked):
            o = owner_of(team)
            if not o:
                continue
            if pos == 0:   res[o]["gpts"] += 4
            elif pos == 1: res[o]["gpts"] += 3
            elif pos == 2: res[o]["gpts"] += 1
            # 4th place: 0

    # Non-qualification penalty: -1 per owned team not in the R32.
    # Only applied once the bracket is known (otherwise nobody has "failed" yet).
    advanced = qualified_teams(matches)
    if advanced:
        for p, teams in DRAW.items():
            for team in teams:
                if team not in advanced:
                    res[p]["penalty"] -= 1

    # Overall winner bonus
    champ = tournament_winner(matches)
    if champ:
        o = owner_of(champ)
        if o:
            res[o]["winpts"] += 5

    return res

def total_points(v: dict) -> int:
    return v["gpts"] + v["penalty"] + v["kopts"] + v["winpts"]

# ─── MESSAGE FORMATTERS ───────────────────────────────────────────────────────

MEDALS = ["🥇", "🥈", "🥉", "🧹"]

def fmt_leaderboard(matches: list[dict]) -> str:
    s = calc_scores(matches)
    total = {p: total_points(s[p]) for p in DRAW}
    ranked = sorted(DRAW, key=lambda p: (-total[p], -s[p]["wins"], -s[p]["goals"]))
    bracket_set = bool(qualified_teams(matches))

    lines = ["🏆 *Family Leaderboard*",
             "_Live — shifts as games finish_", ""]
    for i, p in enumerate(ranked):
        v = s[p]
        lines.append(f"{MEDALS[i]} *{p}* — *{total[p]} pts*  (⚽{v['goals']} ✅{v['wins']}W)")
        bits = [f"group {v['gpts']:+d}"]
        if bracket_set:
            bits.append(f"missed-cut {v['penalty']:+d}")
        if v["kopts"]:  bits.append(f"KO {v['kopts']:+d}")
        if v["winpts"]: bits.append(f"champion {v['winpts']:+d}")
        lines.append("    " + " · ".join(bits))
    lines += ["",
              "_Group (by current position): 1st=4 · 2nd=3 · 3rd=1 · 4th=0_",
              "_❌ -1 per team that doesn't reach the R32_",
              "_🔪 +2 per knockout win · 👑 +5 for the champion_"]
    if not bracket_set:
        lines += ["", "_Group points reflect current standings & will shift. "
                  "Missed-cut penalties (-1/team) apply once the R32 is drawn._"]
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
    "/bbq — a random BBQ recipe 🍖\n"
    "/refresh — force-refresh the data"
)

async def cmd_start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text(HELP, parse_mode=ParseMode.MARKDOWN)

async def cmd_bbq(u: Update, c: ContextTypes.DEFAULT_TYPE):
    r = random.choice(BBQ_RECIPES)
    lines = [f"🍖 *{r['name']}*",
             f"⏱ {r['time']}  ·  🍽 Serves {r['serves']}", "",
             "*Ingredients*"]
    lines += [f"• {ing}" for ing in r["ingredients"]]
    lines += ["", "*Method*"]
    lines += [f"{i}. {step}" for i, step in enumerate(r["steps"], 1)]
    lines += ["", "_Fire up the grill — match's on soon! ⚽_"]
    await u.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)

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

async def cmd_bonus(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """
    Restricted to a single user (BONUS_USER_ID). Adds points to that user's
    rolling, persistent bonus tally and shows the new total.
    Usage: /bonus 10   (or just /bonus to see the current tally)
    These points are purely cosmetic and don't affect the leaderboard.
    """
    user = u.effective_user
    if not BONUS_USER:
        return  # feature not configured; stay silent

    # Match against numeric ID OR @username (case-insensitive, @ optional)
    uname = (user.username or "").lower()
    is_allowed = (str(user.id) == BONUS_USER) or (uname and uname == BONUS_USER)
    if not is_allowed:
        await u.message.reply_text("🚫 Only Ruaidhri may claim bonus points.")
        return

    state = load_state()
    current = int(state.get("bonus_points", 0))

    # Parse the amount; default to viewing the tally if none/invalid
    args = c.args if c.args else []
    if not args:
        await u.message.reply_text(
            f"✨ Hello Ruaidhri, your bonus tally stands at *{current}* points.",
            parse_mode=ParseMode.MARKDOWN)
        return

    try:
        amount = int(args[0])
    except (ValueError, IndexError):
        await u.message.reply_text(
            "Usage: `/bonus 10` to add 10 points (whole numbers only).",
            parse_mode=ParseMode.MARKDOWN)
        return

    new_total = current + amount
    state["bonus_points"] = new_total
    save_state(state)

    verb = "added" if amount >= 0 else "removed"
    await u.message.reply_text(
        f"✨ *{abs(amount)}* bonus points {verb}!\n"
        f"🏅 Hello Ruaidhri, your all-time bonus tally is now *{new_total}* points.\n"
        parse_mode=ParseMode.MARKDOWN)

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
                    ("groups", cmd_groups),
                    ("draw", cmd_draw), ("bbq", cmd_bbq),
                    ("bonus", cmd_bonus),
                    ("refresh", cmd_refresh)]:
        app.add_handler(CommandHandler(cmd, fn))

    # Jobs run in UTC: 08:00 IST = 07:00 UTC, 23:30 IST = 22:30 UTC
    app.job_queue.run_daily(morning_update, time=dtime(7, 0, tzinfo=timezone.utc))
    app.job_queue.run_daily(night_recap,    time=dtime(22, 30, tzinfo=timezone.utc))

    logger.info("Bot v2 starting…")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
