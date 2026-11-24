"""Reads figures from WARDOGS Server List (wardogserverlist.com): server rank, join
queue, seeding odds by hour, the gold bar price and game-wide player counts.

Shared by the status, seeding, gold price, weekly numbers and growth page jobs.
WarCon and BattleMetrics stay the source for everything about players; this
site only knows about servers and the game as a whole.

Nothing here can stop a job. Every call returns None when the key is missing,
the site is down or the answer is not what was expected, and the job carries on
without that figure.

Setting (environment variable):
  WARDOGSERVERLIST_API_KEY   a key made on wardogserverlist.com/account, kept as
                             a GitHub secret. It can only read.

The site's figures are free to reuse under CC BY 4.0 as long as the credit is
shown with a link back, so anything that shows one of these figures also shows
CREDIT (in a picture) or CREDIT_LINK (in Discord text or on a web page).
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

KEY = os.environ.get("WARDOGSERVERLIST_API_KEY", "").strip()
BASE = "https://wardogserverlist.com"
UA = "rlg-discord-posts/1.0 (GitHub Actions; Redline Gaming)"
CREDIT = "Server data: WARDOGS Server List · CC BY 4.0"
CREDIT_LINK = "Server data: [WARDOGS Server List](<https://wardogserverlist.com>) ([CC BY 4.0](<https://creativecommons.org/licenses/by/4.0/>))"
EASTERN = ZoneInfo("America/New_York")   # Redline Gaming's time zone

_cache = {}        # address -> (time read, answer)
_retry_at = {}     # address -> earliest time to try again after a failure
_refused = False   # the site said the key is wrong: stop asking for this run
_said = set()      # problems already printed, so the log is not flooded


def enabled():
    return bool(KEY) and not _refused


def _say(message):
    if message not in _said:
        _said.add(message)
        print("Server List: " + message, flush=True)


def get(path, ttl=600, **params):
    """One read from the site, kept for ttl seconds. Returns the parsed answer, the
    last good answer if the site cannot be reached right now, or None."""
    global _refused
    if not enabled():
        return None
    url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
    now = time.time()
    read_at, answer = _cache.get(url, (0, None))
    if answer is not None and now - read_at < ttl:
        return answer
    if now < _retry_at.get(url, 0):
        return answer
    req = urllib.request.Request(url, headers={"X-API-Key": KEY, "User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            fresh = json.loads(resp.read())
        _cache[url] = (now, fresh)
        return fresh
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            _refused = True
            _say(f"the key was refused (HTTP {e.code}). Check the WARDOGSERVERLIST_API_KEY secret.")
        elif e.code != 404:   # 404 = that server is not listed right now
            _say(f"{path} answered HTTP {e.code}; carrying on without it.")
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
        _say(f"{path} could not be read ({type(e).__name__}); carrying on without it.")
    _retry_at[url] = now + 300
    return answer


def server_key(join_code):
    """The site's lasting name for a community server, from its in-game join code
    (WarCon's public status gives the join code)."""
    join_code = str(join_code or "").strip()
    return "id|" + join_code if join_code else None


def region_name(region):
    """na-central -> NA Central"""
    parts = str(region or "").split("-")
    short = {"na": "NA", "eu": "EU"}
    return " ".join(short.get(p, p.capitalize()) for p in parts if p)


def standing(key):
    """Where a server stands on the site's 7-day community board, or None.
    {rank, of, region, region_rank, region_of}"""
    if not key:
        return None
    d = get("/api/server-insights", ttl=1800, key=key)
    r = (d or {}).get("rank") or {}
    if not r.get("current"):
        return None
    return {"rank": r["current"], "of": r.get("total"), "region": region_name(r.get("region")),
            "region_rank": r.get("regionRank"), "region_of": r.get("regionCount")}


def standing_line(key):
    """'Rank 221 of 1,000 · 8 of 122 in NA Central', or '' when not known."""
    s = standing(key)
    if not s:
        return ""
    line = f"Rank {s['rank']:,}" + (f" of {s['of']:,}" if s.get("of") else "")
    if s.get("region_rank") and s.get("region_of"):
        line += f" · {s['region_rank']} of {s['region_of']} in {s['region']}"
    return line


def waiting(key):
    """How many players are waiting to join a full server: a number, 0 when the
    site saw a queue but not its length, or None when there is no queue (or the
    site cannot tell). The site checks a full server about every 15 minutes."""
    if not key:
        return None
    q = (get("/api/server-queue", ttl=300, key=key) or {}).get("queue") or {}
    if not q.get("hasQueue"):
        return None
    return int(q.get("waiting") or 0)


def seed_hours(key):
    """How seeds started at each hour of the day went in this server's region over
    the last 14 days. Returns (region, time zone name, [(started, reached 20)] for
    hours 0 to 23 on that zone's clock), or None."""
    if not key:
        return None
    d = get("/api/seeding", ttl=3600, key=key)
    z = (d or {}).get("zoneStats") or {}
    starts, going = z.get("starts") or [], z.get("going") or []
    if not d or not d.get("ready") or len(starts) != 24 or len(going) != 24:
        return None
    return region_name(d.get("zone")), d.get("tz") or "UTC", list(zip(starts, going))


def seed_odds(key, when=None, min_runs=8):
    """Of the seeds started around this hour in the server's region, the share that
    reached 20 players. Counts the hour before and after too, so one odd hour does
    not swing it. Returns (percent, seeds counted, region) or None when there are
    fewer than min_runs to go on."""
    found = seed_hours(key)
    if not found:
        return None
    region, tz, hours = found
    try:
        hour = (when or datetime.now(ZoneInfo(tz))).astimezone(ZoneInfo(tz)).hour
    except Exception:  # noqa: BLE001 - an unknown zone name
        return None
    near = [hours[(hour + k) % 24] for k in (-1, 0, 1)]
    started, reached = sum(a for a, _ in near), sum(b for _, b in near)
    if started < min_runs:
        return None
    return round(100 * reached / started), started, region


def gold():
    """The gold bar price day by day: [(day as UTC datetime, cash for one bar)],
    oldest first, or None."""
    d = get("/api/gold-bars", ttl=300)
    points = (d or {}).get("points") or []
    out = []
    for p in points:
        try:
            out.append((datetime.fromtimestamp(p[0] / 1000, ZoneInfo("UTC")), int(p[1])))
        except (TypeError, ValueError, IndexError):
            continue
    return out or None


def hour_label(hour):
    """17 -> '5 PM'"""
    return f"{hour % 12 or 12} {'AM' if hour < 12 else 'PM'}"
