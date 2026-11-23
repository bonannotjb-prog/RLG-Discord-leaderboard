"""Seeding alerts: ping a Discord role when a server starts filling from empty.

Used by status.py, which already checks each server every few seconds. Nothing
here runs unless DISCORD_SEED_WEBHOOK_URL is set.

What it does, per server:
  - The count rises from below SEED_MIN into the SEED_MIN..SEED_MAX band
    -> posts one message that pings the role.
  - While the count stays in the band -> edits that message with the live count
    (edits never ping again).
  - The count passes SEED_MAX -> edits the message to say the server is seeded.
  - The count falls below SEED_MIN -> edits the message to say the attempt ended.
  - No new ping for the same server until SEED_COOLDOWN_MINUTES have passed.

Settings (environment variables, set in .github/workflows/status.yml):
  DISCORD_SEED_WEBHOOK_URL   webhook on the seeding channel, kept as a GitHub secret
  SEED_ROLE_ID               id of the role to ping (not secret)
  SEED_MIN / SEED_MAX        the band, default 1 and 20
  SEED_COOLDOWN_MINUTES      default 90
  SEED_ALERT_ON_DROP         1 = also ping when a full server falls back into the
                             band; default 0 (only ping when filling from empty)
"""
import json
import os
import subprocess
import time
import urllib.error

WEBHOOK = os.environ.get("DISCORD_SEED_WEBHOOK_URL", "").strip()
ROLE_ID = "".join(ch for ch in os.environ.get("SEED_ROLE_ID", "") if ch.isdigit())
LOW = int(os.environ.get("SEED_MIN", "1"))
HIGH = int(os.environ.get("SEED_MAX", "20"))
COOLDOWN = float(os.environ.get("SEED_COOLDOWN_MINUTES", "90")) * 60
ON_DROP = os.environ.get("SEED_ALERT_ON_DROP", "0") == "1"
HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "state", "seed_state.json")

_state = None


def enabled():
    return bool(WEBHOOK)


def _load():
    global _state
    if _state is None:
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                _state = json.load(f)
        except (FileNotFoundError, ValueError):
            _state = {}
    return _state


def _save():
    """Write the state file and, on GitHub, commit it so the next job remembers
    the cooldown and which message to edit."""
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(_state, f, indent=1, sort_keys=True)
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    for cmd in (["git", "add", "state"],
                ["git", "commit", "-q", "-m", "Update seeding alert state"],
                ["git", "pull", "-q", "--rebase"],
                ["git", "push", "-q"]):
        if subprocess.run(cmd, cwd=HERE, capture_output=True).returncode != 0:
            break  # not fatal: worst case is one repeated ping after a job handover


def _short(name):
    return str(name or "the server").split(" - ")[0].strip()


def _text(kind, s, page):
    name, n, cap = _short(s.get("name")), s.get("players") or 0, s.get("maxPlayers") or 0
    if kind == "seeding":
        ping = f"<@&{ROLE_ID}> " if ROLE_ID else ""
        return (f"{ping}**We are trying to seed {name}. Come and join!**\n"
                f"Players on right now: **{n}/{cap}**\n[Server page]({page})")
    if kind == "seeded":
        return (f"**Seeded: {name} passed {HIGH} players.** Thanks to everyone who jumped on.\n"
                f"Players on right now: **{n}/{cap}**")
    return f"**Seeding attempt on {name} ended.** The server emptied out."


def check(server_id, s, page, http):
    """Call once per poll with the server's public status dict."""
    if not enabled() or not s or not s.get("ok"):
        return
    st = _load().setdefault(server_id, {})
    n = s.get("players") or 0
    prev = st.get("last_count")
    in_band = LOW <= n <= HIGH
    now = time.time()
    mentions = {"parse": [], "roles": [ROLE_ID]} if ROLE_ID else {"parse": []}

    def edit(kind):
        try:
            http("PATCH", f"{WEBHOOK}/messages/{st['message_id']}",
                 {"content": _text(kind, s, page), "allowed_mentions": {"parse": []}})
        except urllib.error.HTTPError as e:
            if e.code != 404:  # 404 = someone deleted the alert; nothing to update
                raise

    if st.get("phase") == "seeding":
        if in_band:
            if n != prev:
                edit("seeding")
        else:
            edit("seeded" if n > HIGH else "ended")
            st["phase"] = "done"
            st["last_count"] = n
            _save()
            print(f"Seeding on {server_id[:8]} finished at {n} players.", flush=True)
    elif in_band:
        rising = prev is None or prev < LOW
        dropping = prev is not None and prev > HIGH
        if (rising or (dropping and ON_DROP)) and now - st.get("last_alert", 0) >= COOLDOWN:
            created = http("POST", WEBHOOK + "?wait=true",
                           {"content": _text("seeding", s, page), "allowed_mentions": mentions})
            st.update(phase="seeding", last_alert=now, message_id=created["id"], last_count=n)
            _save()
            print(f"Seeding alert sent for {server_id[:8]} at {n} players.", flush=True)
    st["last_count"] = n
