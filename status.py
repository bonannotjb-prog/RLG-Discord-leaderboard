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

import recap
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


def short_name(full):
    """'[RLG] Redline Gaming [Vanilla] #1 - discord.gg/...' -> 'Vanilla #1'"""
    full = str(full or "").split(" - ")[0].strip()
    m = re.search(r"\[(?!RLG\])([^\]]+)\]\s*(#\d+)?", full)
    if m:
        return (m.group(1) + (" " + m.group(2) if m.group(2) else "")).strip()
    return full or "Server"


_match_cache = {}   # server id -> (time checked, newest matches from Warcon)


def get_matches(server_id):
    """The server's newest matches from Warcon, looked up at most once a minute.
    Returns the last list it got if Warcon cannot be read."""
    checked, matches = _match_cache.get(server_id, (0, []))
    if time.time() - checked < 60:
        return matches
    try:
        matches = http("GET", f"{ORIGIN}/api/public/servers/{server_id}/matches").get("matches") or []
    except (urllib.error.URLError, TimeoutError, ValueError, AttributeError):
        pass  # keep the last known list
    _match_cache[server_id] = (time.time(), matches)
    return matches


def match_started(server_id):
    """When the match now running began (the entry with no end time), or None."""
    current = next((m for m in get_matches(server_id) if not m.get("endedAt")), None)
    return unix(current.get("startedAt")) if current else None


def build_column(server_id):
    """One server as one column of the card. Returns (field, rank, status, seen):
    rank orders the states (3 live, 2 seeding, 1 empty, 0 offline or unknown) and
    seen is when Warcon last looked at the server."""
    page = f"{ORIGIN}/s/{server_id}"
    links = f"[Server page]({page}) · [Leaderboard]({page}/leaderboard)"
    try:
        s = http("GET", f"{ORIGIN}/api/public/servers/{server_id}").get("server", {})
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        code = getattr(e, "code", None)
        why = f"HTTP {code}" if code else "no answer"
        return ({"name": "\u26A0\ufe0f Status unavailable", "inline": True,
                 "value": f"Warcon gave {why}. Will retry.\n{links}"}, 0, None, None)

    name = short_name(s.get("name"))
    players, cap = s.get("players") or 0, s.get("maxPlayers") or 0
    if not s.get("ok"):
        return ({"name": f"{OFFLINE[0]} {name} · Offline", "inline": True,
                 "value": f"Offline or unreachable.\n{links}"}, 0, s, unix(s.get("observedAt")))
    if players < seed.LOW:
        state, rank, note = EMPTY, 1, "Jump on and start the seed"
    elif players <= seed.HIGH:
        state, rank, note = SEEDING, 2, f"{seed.HIGH + 1 - players} more to go live"
    else:
        state, rank, note = LIVE, 3, "Match in progress"

    lines = [f"**{players}/{cap} players** · {note}", meter(players, cap, 12)]
    if s.get("map"):
        lines.append(f"\U0001F5FA\ufe0f **Map:** {words(s['map'])}")
    if s.get("lighting"):
        lines.append(f"\U0001F326\ufe0f **Weather:** {words(s['lighting'])}")
    if s.get("experiences"):
        lines.append("\U0001F3AE **Mode:** " + ", ".join(words(x) for x in s["experiences"]))
    started = match_started(server_id)
    if started:
        # A Discord time chip: it reads "12 minutes ago" and keeps counting by itself.
        lines.append(f"\u23F1\ufe0f **Match started:** <t:{started}:R>")
    scores = s.get("scores") or []
    if scores:
        per_side = {}
        for p in s.get("roster") or []:
            if isinstance(p, dict) and p.get("faction"):
                per_side[p["faction"]] = per_side.get(p["faction"], 0) + 1
        lead = max(f.get("score", 0) for f in scores)
        lines.append("")
        for f in scores[:3]:
            pts, on = f.get("score", 0), per_side.get(f.get("name"), 0)
            crown = " \U0001F451" if lead and pts == lead else ""
            lines.append(f"\u2694\ufe0f **{f.get('name', 'Faction')}:** {pts} pts · {on} on{crown}")
    lines += ["", links]
    field = {"name": f"{state[0]} {name} · {state[1]}", "value": "\n".join(lines)[:1024], "inline": True}
    return field, rank, s, unix(s.get("observedAt"))


def build_card(columns):
    """All servers on one card, side by side. Returns (embed, fingerprint); the
    fingerprint leaves out timestamps so it only changes when the content does."""
    best = max((rank for _, rank, _, _ in columns), default=0)
    color = {3: LIVE[2], 2: SEEDING[2], 1: EMPTY[2], 0: OFFLINE[2]}[best]
    total = sum((s or {}).get("players") or 0 for _, _, s, _ in columns)
    embed = {
        "title": "RLG Live Server Status",
        "description": f"**{total}** player{'' if total == 1 else 's'} online across {len(columns)} server"
                       f"{'' if len(columns) == 1 else 's'}",
        "color": color,
        "fields": [f for f, _, _, _ in columns],
    }
    fingerprint = json.dumps(embed, sort_keys=True)
    seen = [t for _, _, _, t in columns if t]
    if seen:
        embed["fields"].append({"name": "\u200b", "inline": False, "value": f"Checked <t:{min(seen)}:R>"})
    return embed, fingerprint


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
    columns = [build_column(sid) for sid in SERVER_IDS[:10]]
    if alerts and not DRY_RUN:
        for sid, (_, _, status, _) in zip(SERVER_IDS, columns):
            # A problem in the alerts or recaps must never stop the status card.
            for label, job in (
                ("Seeding alert", lambda: seed.check(sid, status, f"{ORIGIN}/s/{sid}", http)),
                ("Match recap", lambda: recap.check(sid, get_matches(sid),
                                                    short_name((status or {}).get("name")), ORIGIN, http)),
            ):
                try:
                    job()
                except Exception as e:  # noqa: BLE001 - report it and keep going
                    detail = f"HTTP {e.code}" if isinstance(e, urllib.error.HTTPError) else f"{type(e).__name__}: {e}"
                    print(f"{label} problem on {sid[:8]} ({detail}); carrying on.", flush=True)
    embed, fingerprint = build_card(columns)
    if fingerprint == last_fingerprint and time.time() - last_sent < HEARTBEAT:
        return last_fingerprint, last_sent  # nothing changed, skip the edit
    payload = {"embeds": [embed], "allowed_mentions": {"parse": []}}
    if DRY_RUN:
        print(embed["title"] + "\n" + embed["description"] + "\n")
        for f in embed["fields"]:
            print(f"[{'column' if f['inline'] else 'full row'}] {f['name']}\n{f['value']}\n")
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
