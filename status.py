"""Keep one Discord picture showing live Warcon server status, replaced in place.

Reads the public status JSON for each server (no key needed), draws it with
rlgimage.py and posts it through a Discord webhook. The first run posts a new
message and saves its id in state/; after that the picture is swapped whenever
something on it changes. The text under it carries the live match clocks.

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
  DRY_RUN                     set to 1 to save the picture to disk instead of sending it
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
import rlgimage
import rlgpost
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


def unix(iso):
    try:
        return int(datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


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


_names = {}   # server id -> last known short name, used while a server cannot be read


def build_server(server_id):
    """One server's panel for the picture. Returns (panel, status, seen): status is
    the raw Warcon status (None if unreadable), seen is when Warcon last looked."""
    try:
        s = http("GET", f"{ORIGIN}/api/public/servers/{server_id}").get("server", {})
    except (urllib.error.URLError, TimeoutError, ValueError):
        return {"name": _names.get(server_id, "Server"), "state": "OFFLINE"}, None, None
    name = _names[server_id] = short_name(s.get("name"))
    if not s.get("ok"):
        return {"name": name, "state": "OFFLINE"}, s, unix(s.get("observedAt"))
    players, cap = s.get("players") or 0, s.get("maxPlayers") or 0
    if players < seed.LOW:
        state, note = "EMPTY", "Jump on and start the seed"
    elif players <= seed.HIGH:
        state, note = "SEEDING", f"{seed.HIGH + 1 - players} more to go live"
    else:
        state, note = "LIVE", "Match in progress"
    per_side = {}
    for p in s.get("roster") or []:
        if isinstance(p, dict) and p.get("faction"):
            per_side[p["faction"]] = per_side.get(p["faction"], 0) + 1
    panel = {
        "name": name, "state": state, "note": note, "players": players, "max": cap,
        "map": words(s.get("map")), "weather": words(s.get("lighting")),
        "mode": ", ".join(words(x) for x in s.get("experiences") or []),
        "scores": [(f.get("name", "Faction"), f.get("score", 0), per_side.get(f.get("name"), 0))
                   for f in (s.get("scores") or [])[:3]],
    }
    return panel, s, unix(s.get("observedAt"))


def caption(built):
    """The text under the picture: live match clocks, links and when it was checked.
    Discord time chips keep counting by themselves between updates."""
    lines = []
    for sid, (panel, _, _) in zip(SERVER_IDS, built):
        page = f"{ORIGIN}/s/{sid}"
        started = match_started(sid) if panel.get("state") != "OFFLINE" else None
        clock = f"match started <t:{started}:R> · " if started else ""
        # <...> around an address stops Discord adding a link preview under the picture.
        lines.append(f"**{panel['name']}** · {clock}[Server page](<{page}>) · [Leaderboard](<{page}/leaderboard>)")
    seen = [t for _, _, t in built if t]
    if seen:
        lines.append(f"Checked <t:{min(seen)}:R>")
    return "\n".join(lines)


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


def update(last_fingerprint, last_sent, alerts=False):
    built = [build_server(sid) for sid in SERVER_IDS[:10]]
    if alerts and not DRY_RUN:
        for sid, (_, status, _) in zip(SERVER_IDS, built):
            # A problem in the alerts or recaps must never stop the status picture.
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

    panels = [p for p, _, _ in built]
    text = caption(built)
    # The picture is only redrawn and uploaded when what it shows has changed.
    fingerprint = json.dumps(panels, sort_keys=True)
    changed = fingerprint != last_fingerprint
    if not changed and time.time() - last_sent < HEARTBEAT:
        return last_fingerprint, last_sent
    total = sum(p.get("players", 0) for p in panels)
    if DRY_RUN:
        png = rlgimage.status(panels, right=f"{total} player{'' if total == 1 else 's'} online",
                              footer_left="Redline Gaming · Wardogs", footer_right="Updated " + rlgpost.stamp())
        out = os.path.join(os.environ.get("DRY_RUN_DIR", "."), "status.png")
        with open(out, "wb") as f:
            f.write(png)
        print(f"{text}\n  picture saved to {out} ({len(png) // 1024} KB)")
        return fingerprint, time.time()

    message_id = read_message_id()
    if not changed and message_id and rlgpost.edit_text(WEBHOOK, message_id, text):
        return fingerprint, time.time()   # nothing new to draw: just refresh "Checked"
    png = rlgimage.status(panels, right=f"{total} player{'' if total == 1 else 's'} online",
                          footer_left="Redline Gaming · Wardogs", footer_right="Updated " + rlgpost.stamp())
    message_id, new = rlgpost.put_image(WEBHOOK, message_id, png, "status.png", text)
    if new:
        save_message_id(message_id)
    print(time.strftime("%H:%M:%S"), "posted new" if new else "picture updated", flush=True)
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
