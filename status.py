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

ORIGIN = os.environ.get("WARCON_ORIGIN", "https://console.warcon.app").rstrip("/")
SERVER_IDS = [s.strip() for s in os.environ.get("WARCON_SERVER_IDS", "").split(",") if s.strip()]
WEBHOOK = os.environ.get("DISCORD_STATUS_WEBHOOK_URL", "").strip()
INTERVAL = max(10, int(os.environ.get("STATUS_INTERVAL", "20")))
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


def bar(value, top, width=20):
    filled = 0 if not top else round(width * min(value, top) / top)
    return "█" * filled + "░" * (width - filled)


def unix(iso):
    try:
        return int(datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def build_embed(server_id):
    """Returns (embed, fingerprint). The fingerprint leaves out timestamps, so it only
    changes when something a player would care about changes."""
    page = f"{ORIGIN}/s/{server_id}"
    try:
        s = http("GET", f"{ORIGIN}/api/public/servers/{server_id}").get("server", {})
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        code = getattr(e, "code", None)
        why = f"HTTP {code}" if code else "no answer"
        body = f"**Status unavailable** (Warcon gave {why}). Will retry."
        return {"title": "Server status", "url": page, "description": body}, body

    up = bool(s.get("ok"))
    players, cap = s.get("players") or 0, s.get("maxPlayers") or 0
    lines = [f"**{'ONLINE' if up else 'OFFLINE or unreachable'}**"]
    if up:
        lines.append(f"**Players:** {players}/{cap}")
        lines.append(f"`{bar(players, cap)}`")
        if s.get("map"):
            lines.append(f"**Map:** {words(s['map'])}")
        if s.get("experiences"):
            lines.append("**Mode:** " + ", ".join(words(x) for x in s["experiences"]))
        if s.get("lighting"):
            lines.append(f"**Weather:** {words(s['lighting'])}")

        scores = s.get("scores") or []
        roster = s.get("roster") or []
        per_side = {}
        for p in roster:
            if isinstance(p, dict) and p.get("faction"):
                per_side[p["faction"]] = per_side.get(p["faction"], 0) + 1
        if scores:
            top = max([s.get("scoreCap") or 0] + [f.get("score", 0) for f in scores]) or 1
            name_w = max(len(str(f.get("name", ""))) for f in scores)
            table = []
            for f in scores:
                row = f"{str(f.get('name', '')):<{name_w}}  {f.get('score', 0):>4}  {bar(f.get('score', 0), top, 12)}"
                if per_side:
                    row += f"  {per_side.get(f.get('name'), 0):>3} playing"
                table.append(row)
            lines.append("**Score:**\n```\n" + "\n".join(table) + "\n```")

    fingerprint = "\n".join(lines)
    seen = unix(s.get("observedAt"))
    lines.append(f"[Server page]({page}) · [Leaderboard]({page}/leaderboard)")
    if seen:
        lines.append(f"Checked <t:{seen}:R>")
    embed = {
        "title": (s.get("name") or "Server status")[:256],
        "url": page,
        "description": "\n".join(lines)[:4096],
    }
    return embed, embed["title"] + "\n" + fingerprint


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


def update(last_fingerprint, last_sent):
    built = [build_embed(sid) for sid in SERVER_IDS[:10]]
    fingerprint = "\n---\n".join(fp for _, fp in built)
    if fingerprint == last_fingerprint and time.time() - last_sent < HEARTBEAT:
        return last_fingerprint, last_sent  # nothing changed, skip the edit
    payload = {"embeds": [e for e, _ in built], "allowed_mentions": {"parse": []}}
    if DRY_RUN:
        for e in payload["embeds"]:
            print(e["title"] + "\n" + e["description"] + "\n")
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
            fingerprint, sent = update(fingerprint, sent)
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
