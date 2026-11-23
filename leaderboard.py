"""Keep a Discord picture of the Warcon leaderboard for each server, replaced in place.

Reads the public leaderboard JSON (no key needed), draws it with rlgimage.py and
posts it through a Discord webhook: one message per server. The first run posts
the messages and saves their ids in state/; every later run swaps the picture.

Settings come from environment variables (set in .github/workflows/leaderboard.yml):
  DISCORD_WEBHOOK_URL  required, kept as a GitHub secret
  WARCON_SERVER_IDS    required, one or more server ids separated by commas
  WARCON_ORIGIN        default https://console.warcon.app
  LB_RANGE             current | 7d | 30d | 90d | all     (default current)
  LB_SORT              kills | deaths | kd | perHour | playtime | seeded |
                       matches | wins | winRate | cash    (default kills)
  LB_MIN_MINUTES       playtime floor to appear on the board (default 60)
  LB_TOP               players per server, 1-15            (default 10)
  DRY_RUN              set to 1 to save the pictures to disk instead of posting
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import rlgimage
import rlgpost

ORIGIN = os.environ.get("WARCON_ORIGIN", "https://console.warcon.app").rstrip("/")
SERVER_IDS = [s.strip() for s in os.environ.get("WARCON_SERVER_IDS", "").split(",") if s.strip()]
WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
RANGE = os.environ.get("LB_RANGE", "current")
SORT = os.environ.get("LB_SORT", "kills")
MIN_MINUTES = os.environ.get("LB_MIN_MINUTES", "60")
TOP = max(1, min(15, int(os.environ.get("LB_TOP", "10"))))
DRY_RUN = os.environ.get("DRY_RUN") == "1"
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state", "message_id.txt")   # older card version
IDS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state", "leaderboard_messages.json")
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


def short_name(full):
    """'[RLG] Redline Gaming [Vanilla] #1 - discord.gg/...' -> 'Vanilla #1'"""
    full = str(full or "").split(" - ")[0].strip()
    m = re.search(r"\[(?!RLG\])([^\]]+)\]\s*(#\d+)?", full)
    if m:
        return (m.group(1) + (" " + m.group(2) if m.group(2) else "")).strip()
    return full or "Server"


def build(server_id):
    """Returns (picture as PNG bytes, text shown under it, file name)."""
    status = get_json(f"/api/public/servers/{server_id}").get("server", {})
    board = get_json(f"/api/public/servers/{server_id}/leaderboard",
                     {"range": RANGE, "sort": SORT, "minMinutes": MIN_MINUTES})
    rows = board.get("rows", [])[:TOP]
    name = short_name(status.get("name"))
    online = f"{status.get('players', '?')}/{status.get('maxPlayers', '?')} online"
    if status.get("map"):
        online += f" · {status['map']}"
    png = rlgimage.leaderboard(
        f"{name} · Top {len(rows) or TOP}",
        f"{RANGE_LABEL.get(RANGE, RANGE)} · Ranked by {SORT_LABEL.get(SORT, SORT)} · {MIN_MINUTES}+ minutes played",
        rows, online=online,
        footer_left=f"{board.get('total', len(rows)):,} players ranked",
        footer_right="Updated " + rlgpost.stamp())
    page = f"{ORIGIN}/s/{server_id}/leaderboard"
    # <...> around the address stops Discord adding a link preview under the picture.
    text = f"**{name}** · [Full leaderboard](<{page}>) · Updated <t:{int(time.time())}:R>"
    return png, text, f"leaderboard-{server_id[:8]}.png"


def load_ids():
    """{server id: Discord message id}. The single message from the older card
    version is reused for the first server so it is replaced, not left behind."""
    try:
        with open(IDS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        pass
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            old = f.read().strip()
        return {SERVER_IDS[0]: old} if old and SERVER_IDS else {}
    except FileNotFoundError:
        return {}


def main():
    if not SERVER_IDS:
        sys.exit("WARCON_SERVER_IDS is empty. Set it in the workflow file.")
    if not WEBHOOK and not DRY_RUN:
        sys.exit("DISCORD_WEBHOOK_URL is not set. Add it as a repository secret.")
    ids = load_ids()
    for sid in SERVER_IDS[:10]:
        png, text, filename = build(sid)
        if DRY_RUN:
            out = os.path.join(os.environ.get("DRY_RUN_DIR", "."), filename)
            with open(out, "wb") as f:
                f.write(png)
            print(f"{text}\n  picture saved to {out} ({len(png) // 1024} KB)")
            continue
        ids[sid], new = rlgpost.put_image(WEBHOOK, ids.get(sid), png, filename, text)
        print(("Posted a new" if new else "Updated the") + f" leaderboard picture for {sid[:8]}.")
    if not DRY_RUN:
        os.makedirs(os.path.dirname(IDS_FILE), exist_ok=True)
        with open(IDS_FILE, "w", encoding="utf-8") as f:
            json.dump(ids, f, indent=1, sort_keys=True)


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as e:
        # Never print the webhook URL: it is a secret.
        host = urllib.parse.urlparse(e.url).netloc
        sys.exit(f"Request to {host} failed with HTTP {e.code}: {e.read()[:300].decode(errors='replace')}")
    except urllib.error.URLError as e:
        sys.exit(f"Network error: {e.reason}")
