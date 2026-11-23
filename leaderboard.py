"""Keep one Discord message showing the Warcon public leaderboard, edited in place.

Reads the public leaderboard JSON (no key needed) and posts it through a Discord
webhook. The first run posts a new message and saves its id in state/; every
later run edits that same message.

Settings come from environment variables (set in .github/workflows/leaderboard.yml):
  DISCORD_WEBHOOK_URL  required, kept as a GitHub secret
  WARCON_SERVER_IDS    required, one or more server ids separated by commas
  WARCON_ORIGIN        default https://console.warcon.app
  LB_RANGE             current | 7d | 30d | 90d | all     (default current)
  LB_SORT              kills | deaths | kd | perHour | playtime | seeded |
                       matches | wins | winRate | cash    (default kills)
  LB_MIN_MINUTES       playtime floor to appear on the board (default 60)
  LB_TOP               players to show per server, 1-25    (default 10)
  DRY_RUN              set to 1 to print the message instead of sending it
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ORIGIN = os.environ.get("WARCON_ORIGIN", "https://console.warcon.app").rstrip("/")
SERVER_IDS = [s.strip() for s in os.environ.get("WARCON_SERVER_IDS", "").split(",") if s.strip()]
WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
RANGE = os.environ.get("LB_RANGE", "current")
SORT = os.environ.get("LB_SORT", "kills")
MIN_MINUTES = os.environ.get("LB_MIN_MINUTES", "60")
TOP = max(1, min(25, int(os.environ.get("LB_TOP", "10"))))
DRY_RUN = os.environ.get("DRY_RUN") == "1"
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state", "message_id.txt")
UA = "rlg-leaderboard-bot/1.0 (GitHub Actions; Discord webhook)"

RANGE_LABEL = {"current": "This season", "7d": "Last 7 days", "30d": "Last 30 days",
               "90d": "Last 90 days", "all": "All time"}
SORT_LABEL = {"kills": "kills", "deaths": "deaths", "kd": "K/D", "perHour": "kills per hour",
              "playtime": "playtime", "seeded": "seeding time", "matches": "matches",
              "wins": "wins", "winRate": "win rate", "cash": "cash"}


def http(method, url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"User-Agent": UA, "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else {}


def get_json(path, params=None):
    url = ORIGIN + path + ("?" + urllib.parse.urlencode(params) if params else "")
    return http("GET", url)


def cell(name, width):
    """A player name that is safe inside a code block and never wraps its column."""
    name = "".join(ch if ch.isprintable() else " " for ch in str(name or "unknown")).replace("`", "'").strip()
    return name if len(name) <= width else name[: width - 1] + "…"


def block(lines):
    return "```\n" + "\n".join(lines) + "\n```"


def build_embed(server_id):
    """One card per server, laid out as three columns that sit side by side:
    players, then kills and deaths, then K/D, wins and hours. Each column is kept
    narrow enough (16 characters) that Discord never wraps a row."""
    status = get_json(f"/api/public/servers/{server_id}").get("server", {})
    board = get_json(f"/api/public/servers/{server_id}/leaderboard",
                     {"range": RANGE, "sort": SORT, "minMinutes": MIN_MINUTES})
    rows = board.get("rows", [])[:TOP]
    players, combat, record = [], [], []
    for i, r in enumerate(rows, 1):
        kills, deaths = r.get("kills", 0), r.get("deaths", 0)
        kd = kills / deaths if deaths else float(kills)
        players.append(f"{r.get('rank', i):>2} {cell(r.get('name'), 13)}")
        combat.append(f"{kills:>6} {deaths:>7}")
        record.append(f"{(f'{kd:.2f}' if kd < 100 else f'{kd:.0f}'):>5} {r.get('wins', 0):>4} {r.get('minutes', 0) / 60:>5.1f}")

    page = f"{ORIGIN}/s/{server_id}/leaderboard"
    name = str(status.get("name") or "Leaderboard").split(" - ")[0].strip()
    online = f"{status.get('players', '?')}/{status.get('maxPlayers', '?')} online"
    where = f" on {status['map']}" if status.get("map") else ""
    embed = {
        "title": f"\u2694\ufe0f Top {len(rows) or TOP}: {name} \u2694\ufe0f"[:256],
        "url": page,
        "description": (f"{RANGE_LABEL.get(RANGE, RANGE)}, ranked by {SORT_LABEL.get(SORT, SORT)} "
                        f"(at least {MIN_MINUTES} minutes played)\n"
                        f"Right now: **{online}**{where}"),
        "color": 0x2ECC71,
    }
    if rows:
        embed["fields"] = [
            {"name": "\U0001F194 Player", "value": block(players), "inline": True},
            {"name": "\U0001F52A Kills \u2003\u26B0\ufe0f Deaths", "value": block(combat), "inline": True},
            {"name": "\u2696\ufe0f K/D \U0001F3C6 Wins \u23F1\ufe0f Hrs", "value": block(record), "inline": True},
            {"name": "\u200b", "inline": False,
             "value": f"{board.get('total', len(rows)):,} players ranked · [Full leaderboard]({page})\n"
                      f"Updated <t:{int(time.time())}:R>"},
        ]
    else:
        embed["description"] += "\n\nNo players on the board yet."
    return embed


def read_message_id():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return f.read().strip() or None
    except FileNotFoundError:
        return None


def save_message_id(message_id):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        f.write(str(message_id) + "\n")


def main():
    if not SERVER_IDS:
        sys.exit("WARCON_SERVER_IDS is empty. Set it in the workflow file.")
    payload = {
        "embeds": [build_embed(sid) for sid in SERVER_IDS[:10]],
        "allowed_mentions": {"parse": []},  # player names can never ping anyone
    }
    if DRY_RUN:
        total = 0
        for e in payload["embeds"]:
            print(e["title"] + "\n" + e["description"])
            total += len(e["title"]) + len(e["description"])
            cols = [f["value"].strip("`\n").split("\n") for f in e.get("fields", [])[:3]]
            if cols:
                print(" | ".join(f["name"] for f in e["fields"][:3]))
                for row in zip(*cols):
                    print(" | ".join(row))
                print(e["fields"][3]["value"])
                print("widest column:", max(len(x) for c in cols for x in c), "characters")
            total += sum(len(f["name"]) + len(f["value"]) for f in e.get("fields", []))
        print("total characters:", total)
        return
    if not WEBHOOK:
        sys.exit("DISCORD_WEBHOOK_URL is not set. Add it as a repository secret.")

    message_id = read_message_id()
    if message_id:
        try:
            http("PATCH", f"{WEBHOOK}/messages/{message_id}", payload)
            print("Edited the existing leaderboard message.")
            return
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
            print("The saved message is gone (deleted?). Posting a new one.")
    created = http("POST", WEBHOOK + "?wait=true", payload)
    save_message_id(created["id"])
    print("Posted a new leaderboard message and saved its id.")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as e:
        # Never print the webhook URL: it is a secret.
        host = urllib.parse.urlparse(e.url).netloc
        sys.exit(f"Request to {host} failed with HTTP {e.code}: {e.read()[:300].decode(errors='replace')}")
    except urllib.error.URLError as e:
        sys.exit(f"Network error: {e.reason}")
