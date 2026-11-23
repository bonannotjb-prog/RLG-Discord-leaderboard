"""Seederboard from BattleMetrics: who has spent the most time seeding the RLG servers.

Runs on its own schedule (.github/workflows/seedboard.yml). It reads join and
leave records from the BattleMetrics API, works out how many players were on
each server minute by minute, and credits every player for the minutes they
were on while the server had between SEED_MIN and SEED_MAX players.

Because BattleMetrics keeps the history, the first run fills in everything
since SEEDBOARD_START. Later runs only redo the last few days.

How time is counted:
  - A server is "seeding" in any minute it has SEED_MIN to SEED_MAX players on.
  - Each player on during that minute gets one minute.
  - Time on both servers is added together.
  - A session BattleMetrics shows as longer than 4 hours is treated as 40
    minutes, the same rule the RLG growth report uses for sessions that were
    never closed properly.
  - Days are UTC days.

Settings (environment variables, set in the workflow file):
  BATTLEMETRICS_TOKEN            required, a read-only BattleMetrics API token (secret)
  DISCORD_SEEDBOARD_WEBHOOK_URL  required, webhook on the board's channel (secret)
  BM_SERVER_IDS                  BattleMetrics server ids, separated by commas
  SEEDBOARD_START                first day to count, YYYY-MM-DD
  SEED_MIN / SEED_MAX            the seeding band (default 1 and 20)
  SEEDBOARD_TOP                  players per board, 1-15 (default 10)
  DRY_RUN                        1 = save the picture to disk instead of posting
"""
import http.client
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import rlgimage
import rlgpost

API = os.environ.get("BM_API", "https://api.battlemetrics.com").rstrip("/")
TOKEN = os.environ.get("BATTLEMETRICS_TOKEN", "").strip()
WEBHOOK = os.environ.get("DISCORD_SEEDBOARD_WEBHOOK_URL", "").strip()
SERVERS = [s.strip() for s in os.environ.get("BM_SERVER_IDS", "").split(",") if s.strip()]
START = datetime.strptime(os.environ.get("SEEDBOARD_START", "2026-09-01"), "%Y-%m-%d").replace(tzinfo=timezone.utc)
LOW = int(os.environ.get("SEED_MIN", "1"))
HIGH = int(os.environ.get("SEED_MAX", "20"))
TOP = max(1, min(15, int(os.environ.get("SEEDBOARD_TOP", "10"))))
DRY_RUN = os.environ.get("DRY_RUN") == "1"
REDO_DAYS = 3          # later runs recount today and the two days before it
LONG = timedelta(hours=4)
LONG_AS = timedelta(minutes=40)
HERE = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(HERE, "state", "seed_time.json")
ID_FILE = os.path.join(HERE, "state", "seedboard_message_id.txt")
UA = "rlg-seederboard/1.0 (GitHub Actions)"
WAIT_SCALE = float(os.environ.get("BM_WAIT_SCALE", "1"))   # only lowered in tests
NOW = datetime.now(timezone.utc).replace(second=0, microsecond=0)


def iso(d):
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


class BMError(Exception):
    """BattleMetrics kept failing on one request."""


RETRY_WAITS = [3, 8, 15, 30, 60, 60]   # seconds to wait after each failed try


def bm_get(url, waits=RETRY_WAITS):
    """GET from BattleMetrics. Waits when it asks us to slow down, and retries
    when it has a hiccup (HTTP 500s and timeouts are common on big pulls)."""
    last = "no answer"
    for attempt in range(len(waits) + 1):
        req = urllib.request.Request(url, headers={
            "Authorization": "Bearer " + TOKEN, "User-Agent": UA, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            detail = e.read()[:300].decode(errors="replace")
            if e.code in (401, 403):
                sys.exit(f"BattleMetrics refused the token (HTTP {e.code}): {detail}")
            if e.code == 429:
                wait = e.headers.get("Retry-After")
                time.sleep(float(wait) + 1 if wait else 10)
                continue
            if e.code < 500:
                sys.exit(f"BattleMetrics answered HTTP {e.code}: {detail}")
            last = f"HTTP {e.code}"
        except (OSError, http.client.HTTPException, ValueError) as e:
            # dropped connections, timeouts and half-finished answers
            last = str(getattr(e, "reason", e)) or type(e).__name__
        if attempt < len(waits):
            time.sleep(waits[attempt] * WAIT_SCALE)
    raise BMError(last)


def server_start(server_id):
    """Do not ask for days before BattleMetrics first saw the server."""
    try:
        created = bm_get(f"{API}/servers/{server_id}", waits=RETRY_WAITS[:3])["data"]["attributes"]["createdAt"]
        return max(START, parse(created).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1))
    except (BMError, KeyError, TypeError, ValueError):
        return START


def fetch_range(server_id, a, b, out):
    q = urllib.parse.urlencode({"filter[servers]": server_id,
                                "filter[range]": f"{iso(a)}:{iso(b)}", "page[size]": 100})
    url, seen = f"{API}/sessions?{q}", set()
    while url and url not in seen:
        seen.add(url)
        d = bm_get(url)
        for s in d.get("data", []):
            at = s["attributes"]
            out[s["id"]] = (at["start"], at.get("stop"), at.get("name"),
                            s["relationships"]["player"]["data"]["id"])
        url = (d.get("links") or {}).get("next")


def fetch_sessions(server_id, start, stop):
    """Every session on the server that overlaps start..stop, as
    {session id: (start, stop or None, name, player id)}."""
    out = {}
    a = max(start, server_start(server_id)) if start == START else start
    while a < stop:
        b = min(a + timedelta(days=5), stop)
        try:
            fetch_range(server_id, a, b, out)
        except BMError as first:
            # The 5-day pull keeps failing: go through it one day at a time instead.
            print(f"  {iso(a)} to {iso(b)} failed ({first}); retrying day by day", flush=True)
            c = a
            while c < b:
                e = min(c + timedelta(days=1), b)
                try:
                    fetch_range(server_id, c, e, out)
                except BMError as err:
                    sys.exit(f"BattleMetrics kept failing for server {server_id}, {iso(c)} to {iso(e)} "
                             f"({err}). Nothing was saved. Run the job again in a few minutes.")
                c = e
        if start == START:
            print(f"  server {server_id}: read up to {b.strftime('%Y-%m-%d')}, {len(out)} sessions so far", flush=True)
        a = b
    return out


def count_window(window_start, days, names):
    """Fill days[YYYY-MM-DD][player id] = seeding seconds for every day from
    window_start to now, across all servers."""
    total_min = int((NOW - window_start).total_seconds() // 60)
    if total_min <= 0:
        return
    idx = lambda t: max(0, min(total_min, int((t - window_start).total_seconds() // 60)))
    day = window_start
    while day <= NOW:
        days[day.strftime("%Y-%m-%d")] = {}
        day += timedelta(days=1)
    for server_id in SERVERS:
        sessions = fetch_sessions(server_id, window_start, NOW)
        print(f"Server {server_id}: {len(sessions)} sessions since {iso(window_start)}", flush=True)
        spans, diff = [], [0] * (total_min + 2)
        for start, stop, name, pid in sessions.values():
            a = parse(start)
            e = parse(stop) if stop else NOW
            if e - a > LONG:
                e = a + LONG_AS
            i, j = idx(a), idx(e)
            if j > i:
                diff[i] += 1
                diff[j] -= 1
                spans.append((i, j, pid))
            if name:
                names[pid] = (start, name) if pid not in names or start >= names[pid][0] else names[pid]
        # seeding[m] = number of seeding minutes before minute m
        seeding, on, run = [0] * (total_min + 1), 0, 0
        for m in range(total_min):
            on += diff[m]
            run += 1 if LOW <= on <= HIGH else 0
            seeding[m + 1] = run
        for i, j, pid in spans:
            m = i
            while m < j:  # split the session at each UTC midnight
                day_end = (m // 1440 + 1) * 1440
                k = min(j, day_end)
                secs = (seeding[k] - seeding[m]) * 60
                if secs:
                    key = (window_start + timedelta(minutes=m)).strftime("%Y-%m-%d")
                    days[key][pid] = days[key].get(pid, 0) + secs
                m = k


def load():
    try:
        with open(DATA_FILE, encoding="utf-8") as f:
            d = json.load(f)
    except (FileNotFoundError, ValueError):
        d = {}
    if d.get("source") != "battlemetrics" or d.get("band") != [LOW, HIGH] \
            or d.get("servers") != SERVERS or d.get("start") != iso(START):
        return {"names": {}, "days": {}}   # first run, or the settings changed: count from scratch
    return d


def totals(days, since=None):
    """[(player id, seconds, days seeded, last day seeded)], most time first."""
    t = {}
    for day, players in days.items():
        if since and day < since:
            continue
        for pid, secs in players.items():
            if secs <= 0:
                continue
            cur = t.get(pid, (0, 0, ""))
            t[pid] = (cur[0] + secs, cur[1] + 1, max(cur[2], day))
    return sorted(((pid, *v) for pid, v in t.items()), key=lambda r: (-r[1], r[0]))


def main():
    if not SERVERS:
        sys.exit("BM_SERVER_IDS is empty. Set it in the workflow file.")
    if not TOKEN:
        sys.exit("BATTLEMETRICS_TOKEN is not set. Add it as a repository secret.")
    if not WEBHOOK and not DRY_RUN:
        sys.exit("DISCORD_SEEDBOARD_WEBHOOK_URL is not set. Add it as a repository secret.")

    data = load()
    names = {pid: tuple(v) for pid, v in data["names"].items()}
    first_run = not data["days"]
    window = START if first_run else max(START, (NOW - timedelta(days=REDO_DAYS - 1)).replace(hour=0, minute=0))
    print("Counting everything since " + iso(window) if first_run else "Recounting since " + iso(window), flush=True)
    count_window(window, data["days"], names)

    data.update(source="battlemetrics", band=[LOW, HIGH], servers=SERVERS, start=iso(START),
                names={pid: list(v) for pid, v in names.items()})
    data["days"] = {d: p for d, p in sorted(data["days"].items()) if p}
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"), sort_keys=True)

    shown = {pid: v[1] for pid, v in names.items()}
    since30 = (NOW.date() - timedelta(days=29)).isoformat()
    last30, alltime = totals(data["days"], since30), totals(data["days"])
    rows = lambda ranked: [{"name": shown.get(pid, "unknown"), "seconds": secs, "days": ndays}
                           for pid, secs, ndays, _ in ranked[:TOP]]
    png = rlgimage.seederboard(
        f"Time on a server with {LOW} to {HIGH} players · Both servers",
        [("Last 30 days", rows(last30)), ("All time", rows(alltime))],
        right=f"{len(last30):,} seeders this month",
        footer_left=f"Counted since {START.strftime('%B')} {START.day}, {START.year} · Source: BattleMetrics",
        footer_right="Updated " + rlgpost.stamp())
    text = (f"**Seederboard** · {len(alltime):,} players have helped seed · "
            f"Updated <t:{int(time.time())}:R>")
    if DRY_RUN:
        out = os.path.join(os.environ.get("DRY_RUN_DIR", "."), "seederboard.png")
        with open(out, "wb") as f:
            f.write(png)
        print(f"{text}\n  picture saved to {out} ({len(png) // 1024} KB)")
        return
    try:
        with open(ID_FILE, encoding="utf-8") as f:
            message_id = f.read().strip()
    except FileNotFoundError:
        message_id = ""
    try:
        message_id, new = rlgpost.put_image(WEBHOOK, message_id, png, "seederboard.png", text)
    except urllib.error.HTTPError as e:
        sys.exit(rlgpost.describe(e))
    if new:
        with open(ID_FILE, "w", encoding="utf-8") as f:
            f.write(str(message_id) + "\n")
    print(("Posted a new" if new else "Updated the") + " seederboard picture.")


if __name__ == "__main__":
    main()
