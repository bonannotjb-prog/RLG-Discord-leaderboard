"""Seeding alerts: ping a Discord role when a real seeding attempt is under way.

Used by status.py, which already checks each server every few seconds. Nothing
here runs unless DISCORD_SEED_WEBHOOK_URL is set.

What it does:
  - Start. A server that was quiet reaches SEED_START_PLAYERS players and keeps
    at least that many for SEED_START_MINUTES. Only then is the role pinged, so
    one person popping in and out never pings anyone.
  - One server at a time. While one server has a seeding call running, no call
    is started for another. If two are ready together, the busier one is called.
  - While seeding. The same message is edited with the live count, how many
    more are needed, and who is on. Edits never ping.
  - Final push. When the count first reaches SEED_PUSH_AT, one short extra
    message pings the role: "5 more and it is live". It is deleted when the
    attempt ends, so the channel keeps one message per attempt.
  - Finish. Past SEED_MAX the message says the server is live. If it empties
    first, it says how close it got. Either way it thanks every helper by name
    with the minutes they put in, and names the longest-serving one Seed MVP.
  - No new call for the same server until SEED_COOLDOWN_MINUTES have passed.

Settings (environment variables, set in .github/workflows/status.yml):
  DISCORD_SEED_WEBHOOK_URL   webhook on the seeding channel, kept as a GitHub secret
  SEED_ROLE_ID               id of the role to ping (not secret)
  SEED_MIN / SEED_MAX        the seeding band, default 1 and 20
  SEED_START_PLAYERS         players needed before the first ping (default 3)
  SEED_START_MINUTES         how long they must stay before the ping (default 3)
  SEED_PUSH_AT               player count for the final-push ping; 0 turns it off (default 15)
  SEED_COOLDOWN_MINUTES      default 90
  SEED_ALERT_ON_DROP         1 = also call when a full server falls back into the
                             band; default 0 (only call when filling from quiet)
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
START = max(LOW, int(os.environ.get("SEED_START_PLAYERS", "3")))
START_WAIT = float(os.environ.get("SEED_START_MINUTES", "3")) * 60
PUSH_AT = int(os.environ.get("SEED_PUSH_AT", "15"))
COOLDOWN = float(os.environ.get("SEED_COOLDOWN_MINUTES", "90")) * 60
ON_DROP = os.environ.get("SEED_ALERT_ON_DROP", "0") == "1"
HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "state", "seed_state.json")

_state = None
# Everything below is kept in memory only, so player names are never written to
# the repository.
_helpers = {}    # server id -> {steamId: [name, seconds on during this attempt]}
_counts = {}     # server id -> latest player count
_last_look = {}  # server id -> time of the previous look


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
    the cooldown and which message to edit. Keys starting with "_" stay in memory."""
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    saved = {sid: {k: v for k, v in st.items() if not k.startswith("_")}
             for sid, st in _state.items()}
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(saved, f, indent=1, sort_keys=True)
    commit_state("Update seeding alert state")


def commit_state(message):
    """On GitHub, commit everything in state/ back to the repository so the next
    job starts from it. Does nothing when run anywhere else."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    for cmd in (["git", "add", "state"],
                ["git", "commit", "-q", "-m", message],
                ["git", "pull", "-q", "--rebase"],
                ["git", "push", "-q"]):
        if subprocess.run(cmd, cwd=HERE, capture_output=True).returncode != 0:
            break  # not fatal: it is tried again at the next save


def _short(name):
    return str(name or "the server").split(" - ")[0].strip()


def _plain(name):
    """Show a player name as plain text: no Discord formatting, no mentions."""
    name = "".join(ch if ch.isprintable() else " " for ch in str(name)).strip() or "unknown"
    for ch in "\\*_~`|>[]()#":
        name = name.replace(ch, "\\" + ch)
    return name.replace("@", "@​")[:40]


def _join(items, limit=1300):
    """Comma separated list, cut off before Discord's message size limit."""
    shown, used = [], 0
    for item in items:
        if used + len(item) + 2 > limit:
            break
        shown.append(item)
        used += len(item) + 2
    more = len(items) - len(shown)
    return ", ".join(shown) + (f" and {more} more" if more > 0 else "")


def _roster(s):
    """{steamId: name} for everyone on the server right now."""
    out = {}
    for p in s.get("roster") or []:
        if isinstance(p, dict) and p.get("name"):
            out[str(p.get("steamId") or p["name"])] = p["name"]
    return out


def _mins(secs):
    return f"{max(1, round(secs / 60))}m"


def _thanks(helpers):
    """Thank-you line with each helper's minutes, longest first, plus the MVP."""
    ranked = sorted(helpers.values(), key=lambda v: (-v[1], str(v[0]).lower()))
    if not ranked:
        return "Thank you to everyone who helped."
    text = "Thank you to: " + _join([f"{_plain(nm)} ({_mins(secs)})" for nm, secs in ranked])
    if len(ranked) > 1:
        text += f"\n⭐ **Seed MVP:** {_plain(ranked[0][0])}, on for {_mins(ranked[0][1])}"
    return text


def _text(kind, s, page, helpers, peak=0):
    name, n, cap = _short(s.get("name")), s.get("players") or 0, s.get("maxPlayers") or 0
    ping = f"<@&{ROLE_ID}> " if ROLE_ID else ""
    need = HIGH + 1 - n
    if kind == "seeding":
        on_now = sorted({_plain(x) for x in _roster(s).values()}, key=str.lower)
        who = f"So far these people are in: {_join(on_now)}\n" if on_now else ""
        return (f"{ping}**We are trying to seed {name}. Come and join!**\n"
                f"Players on right now: **{n}/{cap}** · {need} more to go live\n"
                f"{who}[Server page]({page})")
    if kind == "push":
        return (f"{ping}**Final push: {need} more and {name} is live!**\n"
                f"**{n}/{cap}** on right now. [Server page]({page})")
    if kind == "seeded":
        return (f"**{name} is live: it passed {HIGH} players.**\n{_thanks(helpers)}\n"
                f"Players on right now: **{n}/{cap}**")
    reached = f" It reached {peak} of the {HIGH + 1} needed." if peak else ""
    return (f"**Seeding on {name} stopped before it went live.**{reached}\n"
            f"{_thanks(helpers)}\nWe will try again next time.")


def check(server_id, s, page, http):
    """Call once per poll with the server's public status dict."""
    if not enabled() or not s or not s.get("ok"):
        return
    state = _load()
    st = state.setdefault(server_id, {})
    n = s.get("players") or 0
    now = time.time()
    in_band = LOW <= n <= HIGH
    seeding = st.get("phase") == "seeding"
    mentions = {"parse": [], "roles": [ROLE_ID]} if ROLE_ID else {"parse": []}
    silent = {"parse": []}
    _counts[server_id] = n

    # "Armed" means the server has been quiet since it was last busy, so a rise
    # into the band is a fresh seed and not a busy server winding down.
    if "_armed" not in st:
        st["_armed"] = not (st.get("last_count") or 0) > HIGH
    if n > HIGH:
        st["_armed"] = False
    elif n < START:
        st["_armed"] = True

    # Credit everyone on the server for the time since the last look.
    last = _last_look.get(server_id)
    _last_look[server_id] = now
    elapsed = min(now - last, 90) if last else 0
    helpers = _helpers.setdefault(server_id, {})
    if not seeding and not in_band:
        helpers.clear()   # nothing running: the next attempt starts with an empty list
    on_now = _roster(s)
    if in_band or seeding:
        for sid, name in on_now.items():
            entry = helpers.setdefault(sid, [name, 0])
            entry[0] = name
            entry[1] += elapsed
    roster_changed = set(on_now) != set(st.get("_on", []))
    st["_on"] = sorted(on_now)

    def edit(kind):
        try:
            http("PATCH", f"{WEBHOOK}/messages/{st['message_id']}",
                 {"content": _text(kind, s, page, helpers, st.get("peak", 0))[:2000],
                  "allowed_mentions": silent})
        except urllib.error.HTTPError as e:
            if e.code != 404:  # 404 = someone deleted the alert; nothing to update
                raise

    def drop_push():
        """Remove the final-push message so each attempt leaves one message."""
        if st.get("push_message_id"):
            try:
                http("DELETE", f"{WEBHOOK}/messages/{st['push_message_id']}")
            except urllib.error.HTTPError as e:
                if e.code != 404:
                    raise
            st.pop("push_message_id", None)

    if seeding:
        st["peak"] = max(st.get("peak", 0), n if n <= HIGH else st.get("peak", 0))
        if in_band:
            if PUSH_AT and n >= PUSH_AT and not st.get("pushed"):
                made = http("POST", WEBHOOK + "?wait=true",
                            {"content": _text("push", s, page, helpers)[:2000], "allowed_mentions": mentions})
                st.update(pushed=True, push_message_id=made["id"])
                _save()
                print(f"Final push sent for {server_id[:8]} at {n} players.", flush=True)
            if n != st.get("last_count") or roster_changed:
                edit("seeding")
        else:
            edit("seeded" if n > HIGH else "ended")
            drop_push()
            st.update(phase="done", last_count=n)
            st.pop("_since", None)
            helpers.clear()
            _save()
            print(f"Seeding on {server_id[:8]} finished at {n} players.", flush=True)
    else:
        ready = in_band and n >= START and (st["_armed"] or ON_DROP)
        if not ready:
            st.pop("_since", None)
        else:
            st.setdefault("_since", now)
            # One call at a time: another server already being seeded, or a
            # busier server that is also ready, goes first.
            blocked = any(
                other != server_id and (
                    ost.get("phase") == "seeding"
                    or ("_since" in ost and _counts.get(other, 0) > n))
                for other, ost in state.items() if isinstance(ost, dict))
            waited = now - st["_since"] >= START_WAIT
            rested = now - st.get("last_alert", 0) >= COOLDOWN
            if waited and rested and not blocked:
                made = http("POST", WEBHOOK + "?wait=true",
                            {"content": _text("seeding", s, page, helpers)[:2000], "allowed_mentions": mentions})
                st.update(phase="seeding", last_alert=now, message_id=made["id"], last_count=n,
                          peak=n, pushed=bool(PUSH_AT and n >= PUSH_AT))
                st.pop("_since", None)
                _save()
                print(f"Seeding call sent for {server_id[:8]} at {n} players.", flush=True)
    st["last_count"] = n
