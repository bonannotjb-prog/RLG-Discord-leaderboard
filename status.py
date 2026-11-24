"""Keep one Discord picture per server showing live Warcon status, replaced in place.

Reads the public status JSON for each server (no key needed), draws it with
rlgimage.py and posts it through a Discord webhook. Each server has its own
message. The first run posts them and saves their ids in state/; after that a
server's picture is swapped whenever something on it changes. The text under it
carries the live match clock.

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
  WARDOGSERVERLIST_API_KEY    optional secret. With it, each picture also shows the
                              server's rank among community servers and, when the
                              server is full, how many players are waiting to join
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
import serverlist

ORIGIN = os.environ.get("WARCON_ORIGIN", "https://console.warcon.app").rstrip("/")
SERVER_IDS = [s.strip() for s in os.environ.get("WARCON_SERVER_IDS", "").split(",") if s.strip()]
WEBHOOK = os.environ.get("DISCORD_STATUS_WEBHOOK_URL", "").strip()
INTERVAL = max(int(os.environ.get("STATUS_MIN_INTERVAL", "10")), int(os.environ.get("STATUS_INTERVAL", "20")))
RUN_MINUTES = float(os.environ.get("STATUS_RUN_MINUTES", "330"))
HEARTBEAT = 120  # edit at least this often even if nothing changed, so "checked" stays fresh
DRY_RUN = os.environ.get("DRY_RUN") == "1"
HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "state", "status_message_id.txt")     # older single message
IDS_FILE = os.path.join(HERE, "state", "status_messages.json")        # {server id: message id}
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
    # Rank and join queue come from WARDOGS Server List. Both are left out, and the
    # picture is drawn as before, when the key is missing or the site cannot be read.
    key = serverlist.server_key(s.get("joinCode"))
    rank = serverlist.standing_line(key)
    if state == "LIVE" and cap and players >= cap - (s.get("reservedSlots") or 0) - 1:
        queue = serverlist.waiting(key)
        if queue is not None:
            note = f"Full · {queue} waiting to join" if queue else "Full · queue to join"
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
    if rank:
        panel["rank"] = rank
    return panel, s, unix(s.get("observedAt"))


def caption(sid, panel, seen):
    """The text under one server's picture: live match clock, links and when it was
    checked. Discord time chips keep counting by themselves between updates."""
    page = f"{ORIGIN}/s/{sid}"
    started = match_started(sid) if panel.get("state") != "OFFLINE" else None
    clock = f"match started <t:{started}:R> · " if started else ""
    # <...> around an address stops Discord adding a link preview under the picture.
    line = f"**{panel['name']}** · {clock}[Server page](<{page}>) · [Leaderboard](<{page}/leaderboard>)"
    line += f" · Checked <t:{seen}:R>" if seen else ""
    if panel.get("rank") or "waiting" in str(panel.get("note")) or "queue" in str(panel.get("note")):
        line += "\n-# Rank and queue: " + serverlist.CREDIT_LINK   # the credit the data's licence asks for
    return line


def load_ids():
    """{server id: Discord message id}. The single message from the older combined
    picture is reused for the first server so it is replaced, not left behind."""
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


def save_ids(ids):
    os.makedirs(os.path.dirname(IDS_FILE), exist_ok=True)
    with open(IDS_FILE, "w", encoding="utf-8") as f:
        json.dump(ids, f, indent=1, sort_keys=True)


def draw(panel):
    return rlgimage.status_one(panel, footer_left="Redline Gaming · Wardogs",
                               footer_right="Updated " + rlgpost.stamp())


def update(last, alerts=False):
    """Check every server once. last holds, per server, (what its picture showed,
    when its message was last touched); the updated copy is returned."""
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

    last = dict(last)
    ids = None if DRY_RUN else load_ids()
    for sid, (panel, _, seen) in zip(SERVER_IDS, built):
        # A server's picture is only redrawn and uploaded when what it shows has changed.
        fingerprint = json.dumps(panel, sort_keys=True)
        old_fingerprint, sent = last.get(sid, (None, 0))
        changed = fingerprint != old_fingerprint
        if not changed and time.time() - sent < HEARTBEAT:
            continue
        text = caption(sid, panel, seen)
        filename = f"status-{sid[:8]}.png"
        if DRY_RUN:
            png = draw(panel)
            out = os.path.join(os.environ.get("DRY_RUN_DIR", "."), filename)
            with open(out, "wb") as f:
                f.write(png)
            print(f"{text}\n  picture saved to {out} ({len(png) // 1024} KB)")
            last[sid] = (fingerprint, time.time())
            continue
        message_id = ids.get(sid)
        if not changed and message_id and rlgpost.edit_text(WEBHOOK, message_id, text):
            last[sid] = (fingerprint, time.time())   # nothing new to draw: just refresh "Checked"
            continue
        ids[sid], new = rlgpost.put_image(WEBHOOK, message_id, draw(panel), filename, text)
        if new:
            save_ids(ids)
        last[sid] = (fingerprint, time.time())
        print(time.strftime("%H:%M:%S"), panel["name"], "posted new" if new else "picture updated", flush=True)
    return last


def main():
    if not SERVER_IDS:
        sys.exit("WARCON_SERVER_IDS is empty. Set it in the workflow file.")
    if not WEBHOOK and not DRY_RUN:
        sys.exit("DISCORD_STATUS_WEBHOOK_URL is not set. Add it as a repository secret.")

    if "--once" in sys.argv:
        update({})
        return

    deadline = time.time() + RUN_MINUTES * 60
    last, failures = {}, 0
    while time.time() < deadline:
        started = time.time()
        try:
            last = update(last, alerts=True)
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
