"""Match recaps: post a summary to Discord each time a match ends.

Used by status.py, which already reads each server's match list once a minute.
Nothing here runs unless DISCORD_RECAP_WEBHOOK_URL is set.

How it works:
  - The first time it sees a server it only notes the newest finished match, so
    it never floods the channel with old matches.
  - After that, every newly finished match gets one new picture, unless fewer
    than RECAP_MIN_PLAYERS people played in it (empty-server matches are skipped).
  - The id of the last match handled is kept in state/recaps.json.

Settings (environment variables, set in .github/workflows/status.yml):
  DISCORD_RECAP_WEBHOOK_URL  webhook on the recaps channel, kept as a GitHub secret
  RECAP_MIN_PLAYERS          fewest players in a match for it to get a recap (default 10)
"""
import json
import os
import re
import urllib.error
from datetime import datetime

import rlgimage
import rlgpost
import seed

WEBHOOK = os.environ.get("DISCORD_RECAP_WEBHOOK_URL", "").strip()
MIN_PLAYERS = int(os.environ.get("RECAP_MIN_PLAYERS", "10"))
HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "state", "recaps.json")

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
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(_state, f, indent=1, sort_keys=True)
    seed.commit_state("Update match recap state")


def _words(text):
    """Moscow_KOTH_01+KOTH_InfantryOnly -> Moscow KOTH 01, KOTH Infantry Only"""
    parts = re.split(r"[+,]", str(text or ""))
    return ", ".join(re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", p).replace("_", " ").strip() for p in parts if p)


def _length(start, end):
    try:
        a = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        b = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
    except ValueError:
        return None
    m = max(0, int((b - a).total_seconds() // 60))
    return f"{m // 60}h {m % 60:02d}m" if m >= 60 else f"{m}m"


def build(m, detail, server_name, page):
    """Returns (picture as PNG bytes, text shown under it, file name) for one match.
    page is the server's public Warcon page."""
    scores = sorted(((f.get("name"), f.get("score", 0)) for f in m.get("finalScores") or []),
                    key=lambda f: -f[1])
    facts = [x for x in (_length(m.get("startedAt"), m.get("endedAt")), f"{m.get('players', 0)} players") if x]
    rows = sorted((detail or {}).get("lines") or [], key=lambda r: (-r.get("kills", 0), r.get("deaths", 0)))
    awards = [(a.get("label") or "", a.get("name") or "", str(a.get("value") or ""))
              for a in (detail or {}).get("awards") or [] if a.get("name")]
    try:
        ended = datetime.fromisoformat(str(m.get("endedAt")).replace("Z", "+00:00"))
        when, chip = "Match ended " + rlgpost.stamp(ended), f" · Ended <t:{int(ended.timestamp())}:f>"
    except ValueError:
        when, chip = "", ""
    png = rlgimage.recap(server_name, _words(m.get("map")) or "Unknown map", _words(m.get("experiences")),
                         m.get("winner"), scores, facts, rows[:5], awards,
                         footer_left=when, footer_right=f"Peak {m.get('peakPlayers', 0)} players at once")
    winner = f"{m['winner']} wins" if m.get("winner") else "No winner"
    # <...> around the address stops Discord adding a link preview under the picture.
    text = (f"**{server_name}** · {_words(m.get('map')) or 'Unknown map'} · {winner}{chip} · "
            f"[All matches](<{page}/matches>)")
    return png, text, f"recap-{m.get('id')}.png"


def check(server_id, matches, server_name, origin, http):
    """Call with the server's newest matches, as Warcon returns them. origin is
    the Warcon address, for example https://console.warcon.app."""
    page = f"{origin}/s/{server_id}"
    if not enabled() or not matches:
        return
    ended = sorted((m for m in matches if m.get("endedAt") and m.get("id") is not None), key=lambda m: m["id"])
    if not ended:
        return
    state = _load()
    if server_id not in state:
        state[server_id] = ended[-1]["id"]   # first look: start from here, post nothing old
        _save()
        return
    fresh = [m for m in ended if m["id"] > state[server_id]]
    for m in fresh:
        if (m.get("players") or 0) >= MIN_PLAYERS:
            try:
                detail = http("GET", f"{origin}/api/public/servers/{server_id}/matches/{m['id']}")
            except (urllib.error.URLError, TimeoutError, ValueError):
                detail = None   # still post the result, just without the player list
            png, text, filename = build(m, detail, server_name, page)
            rlgpost.post_image(WEBHOOK, png, filename, text)
            print(f"Recap posted for match {m['id']} on {server_name}.", flush=True)
            # Remember what was posted live, so the one-time backfill never repeats it.
            posted = state.setdefault("_posted", {}).setdefault(server_id, [])
            posted.append(m["id"])
            del posted[:-300]
        state[server_id] = m["id"]
    if fresh:
        _save()
