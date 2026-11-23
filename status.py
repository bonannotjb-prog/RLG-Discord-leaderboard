"""Keep one Discord message showing live Warcon server status, edited in place.

Reads the public status JSON for each server (no key needed) and posts it through
a Discord webhook. The first run posts a new message and saves its id in state/;
after that the same message is edited.

Modes:
  python status.py --once   update the message one time and exit
  python status.py          keep updating every STATUS_INTERVAL seconds until
                            STATUS_RUN_MINUTES have passed, then exit

Settings come from environment variables (set in .github/workflows/status.yml):
  DISCORD_STATUS_WEBHOOK_URL  required, kept as a GitHub secret
  WARCON_SERVER_IDS           required, one or more server ids separated by commas
  WARCON_ORIGIN               default https://console.warcon.app
  STATUS_INTERVAL             seconds between checks, minimum 10 (default 20)
  STATUS_RUN_MINUTES          how long the loop runs before exiting (default 330)
  DRY_RUN                     set to 1 to print the message instead of sending it
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

import seed

ORIGIN = os.environ.get("WARCON_ORIGIN", "https://console.warcon.app").rstrip("/")
SERVER_IDS = [s.strip() for s in os.environ.get("WARCON_SERVER_IDS", "").split(",") if s.strip()]
WEBHOOK = os.environ.get("DISCORD_STATUS_WEBHOOK_URL", "").strip()
INTERVAL = max(int(os.environ.get("STATUS_MIN_INTERVAL", "10")), int(os.environ.get("STATUS_INTERVAL", "20")))
RUN_MINUTES = float(os.environ.get("STATUS_RUN_MINUTES", "330"))
HEARTBEAT = 120  # edit at least this often even if nothing changed, so "checked" stays fresh
DRY_RUN = os.environ.get("DRY_RUN") == "1"
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state", "status_message_id.txt")
UA = "rlg-status-bot/1.0 (GitHub Actions; Discord webhook)"


def http(method, url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"User-Agent": UA, "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else {}


def words(camel):
    """DayLateRain -> Day Late Rain"""
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(camel or "")).replace("_", " ").strip()


def meter(value, top, width=10):
    """A short progress bar made of solid and hollow blocks."""
    filled = 0 if not top else round(width * min(value, top) / top)
    if value and not filled:
        filled = 1  # one player still shows one block
    return "\u25B0" * filled + "\u25B1" * (width - filled)


def unix(iso):
    try:
        return int(datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


# State shown at the top of each card. Every state has its own icon and its own
# words, so it never depends on telling the stripe colours apart.
OFFLINE = ("\u26D4", "Offline", 0xE74C3C)
EMPTY = ("\U0001F4A4", "Empty", 0x95A5A6)
SEEDING = ("\U0001F331", "Seeding", 0xF1C40F)
LIVE = ("\U0001F525", "Live", 0x2ECC71)


def build_embed(server_id):
    """Returns (embed, fingerprint, status). The fingerprint leaves out timestamps, so
    it only changes when something a player would care about changes. status is the
    raw Warcon status, or None if it could not be read."""
    page = f"{ORIGIN}/s/{server_id}"
    links = f"[Server page]({page}) · [Leaderboard]({page}/leaderboard)"
    try:
        s = http("GET", f"{ORIGIN}/api/public/servers/{server_id}").get("server", {})
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        code = getattr(e, "code", None)
        body = f"\u26A0\ufe0f **Status unavailable** (Warcon gave {'HTTP ' + str(code) if code else 'no answer'}). Will retry."
        return {"title": "Server status", "url": page, "description": body, "color": 0x95A5A6}, body, None

    name = str(s.get("name") or "Server status").split(" - ")[0].strip()
    players, cap = s.get("players") or 0, s.get("maxPlayers") or 0
    if not s.get("ok"):
        state = OFFLINE
    elif players < seed.LOW:
        state = EMPTY
    elif players <= seed.HIGH:
        state = SEEDING
    else:
        state = LIVE
    icon, label, color = state

    if state is OFFLINE:
        headline = f"{icon} **Offline or unreachable**"
    elif state is EMPTY:
        headline = f"{icon} **Empty** · jump on and start the seed"
    elif state is SEEDING:
        headline = f"{icon} **Seeding** · {seed.HIGH + 1 - players} more to go live"
    else:
        headline = f"{icon} **Live**"
    embed = {"title": name[:256], "url": page, "color": color, "description": headline, "fields": []}

    if state is not OFFLINE:
        embed["fields"] += [
            {"name": "\U0001F465 Players", "value": f"**{players}/{cap}**\n{meter(players, cap)}", "inline": True},
            {"name": "\U0001F5FA\ufe0f Map", "value": words(s.get("map")) or "Unknown", "inline": True},
            {"name": "\U0001F326\ufe0f Weather", "value": words(s.get("lighting")) or "Unknown", "inline": True},
        ]
        if s.get("experiences"):
            embed["fields"].append({"name": "\U0001F3AE Mode", "inline": False,
                                    "value": ", ".join(words(x) for x in s["experiences"])})
        scores = s.get("scores") or []
        per_side = {}
        for p in s.get("roster") or []:
            if isinstance(p, dict) and p.get("faction"):
                per_side[p["faction"]] = per_side.get(p["faction"], 0) + 1
        # Faction scores only mean something while a match is being played.
        if players and scores:
            lead = max(f.get("score", 0) for f in scores)
            for f in scores[:3]:
                pts, on = f.get("score", 0), per_side.get(f.get("name"), 0)
                crown = " \U0001F451" if lead and pts == lead else ""
                embed["fields"].append({
                    "name": f"{f.get('name', 'Faction')}{crown}", "inline": True,
                    "value": f"**{pts}** pts\n{on} player{'' if on == 1 else 's'}"})

    fingerprint = json.dumps(embed, sort_keys=True)
    seen = unix(s.get("observedAt"))
    embed["fields"].append({"name": "\u200b", "inline": False,
                            "value": links + (f"\nChecked <t:{seen}:R>" if seen else "")})
    return embed, fingerprint, s


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


def send(payload):
    """Edit the saved message, or post a new one if there is none."""
    message_id = read_message_id()
    if message_id:
        try:
            http("PATCH", f"{WEBHOOK}/messages/{message_id}", payload)
            return "edited"
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
    created = http("POST", WEBHOOK + "?wait=true", payload)
    save_message_id(created["id"])
    return "posted new"


def update(last_fingerprint, last_sent, alerts=False):
    built = [build_embed(sid) for sid in SERVER_IDS[:10]]
    if alerts and not DRY_RUN:
        for sid, (_, _, status) in zip(SERVER_IDS, built):
            seed.check(sid, status, f"{ORIGIN}/s/{sid}", http)
    fingerprint = "\n---\n".join(fp for _, fp, _ in built)
    if fingerprint == last_fingerprint and time.time() - last_sent < HEARTBEAT:
        return last_fingerprint, last_sent  # nothing changed, skip the edit
    payload = {"embeds": [e for e, _, _ in built], "allowed_mentions": {"parse": []}}
    if DRY_RUN:
        for e in payload["embeds"]:
            print(e["title"] + "\n" + e["description"])
            for f in e.get("fields", []):
                print(f"  [{'inline' if f['inline'] else 'full row'}] {f['name']}: " + f["value"].replace("\n", " / "))
            print()
    else:
        print(time.strftime("%H:%M:%S"), send(payload), flush=True)
    return fingerprint, time.time()


def main():
    if not SERVER_IDS:
        sys.exit("WARCON_SERVER_IDS is empty. Set it in the workflow file.")
    if not WEBHOOK and not DRY_RUN:
        sys.exit("DISCORD_STATUS_WEBHOOK_URL is not set. Add it as a repository secret.")

    if "--once" in sys.argv:
        update(None, 0)
        return

    deadline = time.time() + RUN_MINUTES * 60
    fingerprint, sent, failures = None, 0, 0
    while time.time() < deadline:
        started = time.time()
        try:
            fingerprint, sent = update(fingerprint, sent, alerts=True)
            failures = 0
        except urllib.error.HTTPError as e:
            failures += 1
            wait = 0
            if e.code == 429:  # Discord asked us to slow down
                try:
                    wait = float(json.loads(e.read()).get("retry_after", 5))
                except (ValueError, AttributeError):
                    wait = 5
            print(f"Discord answered HTTP {e.code}; retrying.", flush=True)
            time.sleep(min(wait, 60))
        except (urllib.error.URLError, TimeoutError) as e:
            failures += 1
            print(f"Network error ({getattr(e, 'reason', e)}); retrying.", flush=True)
        if failures >= 30:
            sys.exit("Giving up after 30 failed updates in a row.")
        time.sleep(max(1, INTERVAL - (time.time() - started)))


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as e:
        # Never print the webhook URL: it is a secret.
        host = urllib.parse.urlparse(e.url).netloc
        sys.exit(f"Request to {host} failed with HTTP {e.code}: {e.read()[:300].decode(errors='replace')}")
    except urllib.error.URLError as e:
        sys.exit(f"Network error: {e.reason}")
