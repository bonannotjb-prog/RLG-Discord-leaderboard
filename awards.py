"""Weekly awards: one Discord post a week naming the top players of the last 7 days.

Runs on its own schedule (.github/workflows/awards.yml). Kills, K/D, wins and
time played come from the Warcon leaderboard for the last 7 days, across every
RLG server. Seeding time comes from the seederboard's saved totals
(state/seed_time.json), so that award only appears once the seederboard job
has run.

It posts a new message each week and records the week in
state/awards_last.txt so the same week is never posted twice.

Settings (environment variables, set in the workflow file):
  DISCORD_AWARDS_WEBHOOK_URL  required, webhook on the awards channel (secret)
  WARCON_SERVER_IDS           any one RLG server id is enough; the board covers the whole org
  WARCON_ORIGIN               default https://console.warcon.app
  AWARDS_MIN_MINUTES          minutes played in the week to be eligible (default 120)
  FORCE                       1 = post even if this week was already posted
  DRY_RUN                     1 = print instead of posting
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

ORIGIN = os.environ.get("WARCON_ORIGIN", "https://console.warcon.app").rstrip("/")
SERVER_IDS = [s.strip() for s in os.environ.get("WARCON_SERVER_IDS", "").split(",") if s.strip()]
WEBHOOK = os.environ.get("DISCORD_AWARDS_WEBHOOK_URL", "").strip()
MIN_MINUTES = os.environ.get("AWARDS_MIN_MINUTES", "120")
FORCE = os.environ.get("FORCE") in ("1", "true")
DRY_RUN = os.environ.get("DRY_RUN") == "1"
HERE = os.path.dirname(os.path.abspath(__file__))
LAST_FILE = os.path.join(HERE, "state", "awards_last.txt")
SEED_FILE = os.path.join(HERE, "state", "seed_time.json")
UA = "rlg-weekly-awards/1.0 (GitHub Actions; Discord webhook)"
MEDALS = ["\U0001F947", "\U0001F948", "\U0001F949"]
NOW = datetime.now(timezone.utc)


def http(method, url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"User-Agent": UA, "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else {}


def plain(name, width=28):
    name = "".join(ch if ch.isprintable() else " " for ch in str(name)).strip() or "unknown"
    if len(name) > width:
        name = name[: width - 1] + "…"
    for ch in "\\*_~`|>[]()#":
        name = name.replace(ch, "\\" + ch)
    return name.replace("@", "@​")


def board(sort):
    """Top rows of the org-wide 7 day leaderboard for one ranking."""
    q = urllib.parse.urlencode({"range": "7d", "scope": "org", "sort": sort, "minMinutes": MIN_MINUTES})
    return http("GET", f"{ORIGIN}/api/public/servers/{SERVER_IDS[0]}/leaderboard?{q}").get("rows", [])


def podium(rows, describe):
    """Three lines, one per medal. describe(row) gives the text after the name."""
    out = [f"{MEDALS[i]} **{plain(r.get('name'))}** · {describe(r)}" for i, r in enumerate(rows[:3])]
    return "\n".join(out) or "Nobody qualified this week."


def top_seeders():
    """[(name, seconds)] for the last 7 full UTC days, from the seederboard's totals."""
    try:
        with open(SEED_FILE, encoding="utf-8") as f:
            d = json.load(f)
    except (FileNotFoundError, ValueError):
        return None
    first = (NOW.date() - timedelta(days=7)).isoformat()
    last = (NOW.date() - timedelta(days=1)).isoformat()
    totals = {}
    for day, players in d.get("days", {}).items():
        if first <= day <= last:
            for pid, secs in players.items():
                totals[pid] = totals.get(pid, 0) + secs
    names = d.get("names", {})
    ranked = sorted(totals.items(), key=lambda kv: -kv[1])[:3]
    # names are saved as [first seen, name]; older files may hold just the name
    return [((names.get(pid) or ["", "unknown"])[1] if isinstance(names.get(pid), list) else names.get(pid, "unknown"),
             secs) for pid, secs in ranked]


def main():
    if not SERVER_IDS:
        sys.exit("WARCON_SERVER_IDS is empty. Set it in the workflow file.")
    if not WEBHOOK and not DRY_RUN:
        sys.exit("DISCORD_AWARDS_WEBHOOK_URL is not set. Add it as a repository secret.")
    week = NOW.strftime("%G-W%V")
    try:
        with open(LAST_FILE, encoding="utf-8") as f:
            already = f.read().strip() == week
    except FileNotFoundError:
        already = False
    if already and not FORCE and not DRY_RUN:
        print(f"Awards for {week} were already posted. Nothing to do.")
        return

    kd = lambda r: r.get("kills", 0) / r["deaths"] if r.get("deaths") else float(r.get("kills", 0))
    fields = [
        {"name": "\U0001F52B Most Kills",
         "value": podium(board("kills"), lambda r: f"{r.get('kills', 0):,} kills")},
        {"name": "\U0001F3AF Best K/D",
         "value": podium(board("kd"), lambda r: f"{kd(r):.2f} K/D ({r.get('kills', 0):,} kills, {r.get('deaths', 0):,} deaths)")},
        {"name": "\U0001F3C6 Most Wins",
         "value": podium(board("wins"), lambda r: f"{r.get('wins', 0)} wins in {r.get('matches', 0)} matches")},
        {"name": "⏱️ Most Time Played",
         "value": podium(board("playtime"), lambda r: f"{r.get('minutes', 0) / 60:.1f} hours")},
    ]
    seeders = top_seeders()
    if seeders:
        fields.append({"name": "\U0001F331 Top Seeders", "value": "\n".join(
            f"{MEDALS[i]} **{plain(n)}** · {int(s // 3600)}h {int(s % 3600 // 60):02d}m seeding"
            for i, (n, s) in enumerate(seeders))})
    for f in fields:
        f["inline"] = False

    start, end = NOW - timedelta(days=7), NOW
    span = f"{start.strftime('%B')} {start.day} to {end.strftime('%B')} {end.day}"
    embed = {
        "title": "\U0001F3C5 RLG Weekly Awards",
        "description": (f"**{span}**\nThe top players across both RLG servers over the last 7 days. "
                        f"Kills, K/D, wins and time need at least {MIN_MINUTES} minutes played."),
        "color": 0x9B59B6,
        "fields": fields,
        "footer": {"text": "Sources: Warcon and BattleMetrics · Posted"},
        "timestamp": NOW.isoformat(),
    }
    if DRY_RUN:
        print(embed["title"] + "\n" + embed["description"] + "\n")
        for f in fields:
            print(f["name"] + "\n" + f["value"] + "\n")
        return
    try:
        http("POST", WEBHOOK + "?wait=true", {"embeds": [embed], "allowed_mentions": {"parse": []}})
    except urllib.error.HTTPError as e:
        # Never print the webhook URL: it is a secret.
        sys.exit(f"Discord answered HTTP {e.code}: {e.read()[:300].decode(errors='replace')}")
    os.makedirs(os.path.dirname(LAST_FILE), exist_ok=True)
    with open(LAST_FILE, "w", encoding="utf-8") as f:
        f.write(week + "\n")
    print(f"Posted the weekly awards for {week}.")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as e:
        host = urllib.parse.urlparse(e.url).netloc
        sys.exit(f"Request to {host} failed with HTTP {e.code}: {e.read()[:300].decode(errors='replace')}")
    except urllib.error.URLError as e:
        sys.exit(f"Network error: {e.reason}")
