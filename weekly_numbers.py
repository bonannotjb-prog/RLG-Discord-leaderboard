"""Weekly numbers: once a week, two Discord pictures showing how the servers did.

Picture 1 is last week (Monday to Sunday, US Eastern): days live, seed attempts,
how many reached 20, the typical time to reach 20 and players a day.
Picture 2 puts the last four full weeks beside the four before.

Everything is counted from BattleMetrics session records for both servers, read
with the same token as the seederboard. No player names are shown.

With the WARDOGSERVERLIST_API_KEY secret set, two more pictures follow, both from
WARDOGS Server List: where each server ranked among community servers last week,
and the hours of the day when seeds in the region most often reach 20. When a
season ends it also posts each server's season card, once. Without the key, or
if that site cannot be read, the first two pictures post as usual.

How things are counted:
  - Live: 20 or more players on a server. A day is "live" if the servers were
    live for at least one hour of it in total.
  - Seed attempt: a server goes from empty to at least 3 players. Gaps of 10
    minutes or less with nobody on do not end it. It belongs to the day it started.
  - Time to reach 20: minutes from the first join to 20 online, for attempts
    that got there. "Typical" is the middle value (median).
  - Players a day: different players seen each day, both servers together.
  - Sessions BattleMetrics never saw close, or that run past 4 hours, count as 40 minutes.

It posts new messages each week and records the week in state/numbers_last.txt
so the same week is never posted twice.

Settings (environment variables, set in the workflow file):
  DISCORD_NUMBERS_WEBHOOK_URL  required, webhook on the channel for the numbers (secret)
  BATTLEMETRICS_TOKEN          required (secret, the same one the seederboard uses)
  BM_SERVER_IDS                BattleMetrics server ids, separated by commas
  WARDOGSERVERLIST_API_KEY     optional (secret): adds the rank and seeding-hours pictures
  WARCON_SERVER_IDS            WarCon server ids, used to find each server on Server List
  FORCE                        1 = post even if this week was already posted
  DRY_RUN                      1 = save the pictures to disk instead of posting
"""
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import rlgimage
import rlgpost
import seedboard as bm   # reuses its BattleMetrics reader (retries, day-by-day fallback)
import serverlist

WEBHOOK = os.environ.get("DISCORD_NUMBERS_WEBHOOK_URL", "").strip()
FORCE = os.environ.get("FORCE") in ("1", "true")
DRY_RUN = os.environ.get("DRY_RUN") == "1"
HERE = os.path.dirname(os.path.abspath(__file__))
LAST_FILE = os.path.join(HERE, "state", "numbers_last.txt")
SEASON_FILE = os.path.join(HERE, "state", "season_posted.txt")
WARCON = os.environ.get("WARCON_ORIGIN", "https://console.warcon.app").rstrip("/")
WARCON_IDS = [x.strip() for x in os.environ.get("WARCON_SERVER_IDS", "").split(",") if x.strip()]
EASTERN = ZoneInfo("America/New_York")
LIVE = 20          # players on for a server to count as live
ATTEMPT = 3        # players on for a stretch to count as a seed attempt
GAP = 10           # minutes of nobody on that still belong to the same stretch
WEEKS = 8


def eastern_midnight(day):
    return datetime(day.year, day.month, day.day, tzinfo=EASTERN).astimezone(timezone.utc)


def online_per_minute(sessions, origin, total):
    """Players on at each minute after origin, plus each session as (first minute, last minute, player)."""
    diff, spans = [0] * (total + 2), []
    idx = lambda t: max(0, min(total, int((t - origin).total_seconds() // 60)))
    for start, stop, _, pid in sessions.values():
        a = bm.parse(start)
        e = bm.parse(stop) if stop else bm.NOW
        if e - a > bm.LONG:
            e = a + bm.LONG_AS
        i, j = idx(a), idx(e)
        if j > i:
            diff[i] += 1
            diff[j] -= 1
            spans.append((i, j, pid))
    online, on = [0] * total, 0
    for m in range(total):
        on += diff[m]
        online[m] = on
    return online, spans


def attempts(online):
    """[(first minute, minutes to reach 20 or None)] for each seed attempt."""
    out, m, n = [], 0, len(online)
    empty_before = False   # a stretch only counts if we saw the server empty before it
    while m < n:
        if online[m] == 0:
            empty_before = True
            m += 1
            continue
        start, peak, reached, gap = m, 0, None, 0
        while m < n:
            if online[m] == 0:
                gap += 1
                if gap > GAP:
                    break
            else:
                gap = 0
                peak = max(peak, online[m])
                if reached is None and online[m] >= LIVE:
                    reached = m - start
            m += 1
        if empty_before and peak >= ATTEMPT:
            out.append((start, reached))
    return out


def measure(weeks_back=WEEKS):
    """One dict per full week, oldest first."""
    today = datetime.now(EASTERN).date()
    this_monday = today - timedelta(days=today.weekday())
    first_monday = this_monday - timedelta(days=7 * weeks_back)
    origin = eastern_midnight(first_monday - timedelta(days=1))   # one spare day to see the server empty first
    end = min(eastern_midnight(this_monday), bm.NOW)
    total = int((end - origin).total_seconds() // 60)
    days = [first_monday + timedelta(days=i) for i in range(7 * weeks_back)]
    bounds = [(int((eastern_midnight(d) - origin).total_seconds() // 60),
               int((eastern_midnight(d + timedelta(days=1)) - origin).total_seconds() // 60)) for d in days]

    live_any = [False] * total
    players = [set() for _ in days]
    per_day_attempts = [[] for _ in days]
    for server_id in bm.SERVERS:
        sessions = bm.fetch_sessions(server_id, origin, end)
        print(f"Server {server_id}: {len(sessions)} sessions since {bm.iso(origin)}", flush=True)
        online, spans = online_per_minute(sessions, origin, total)
        for m, n in enumerate(online):
            if n >= LIVE:
                live_any[m] = True
        for i, j, pid in spans:
            for d, (a, b) in enumerate(bounds):
                if i < b and j > a:
                    players[d].add(pid)
        for start, reached in attempts(online):
            for d, (a, b) in enumerate(bounds):
                if a <= start < b:
                    per_day_attempts[d].append(reached)

    weeks = []
    for w in range(weeks_back):
        ds = range(w * 7, w * 7 + 7)
        live_hours = [sum(live_any[bounds[d][0]:bounds[d][1]]) / 60 for d in ds]
        tried = [r for d in ds for r in per_day_attempts[d]]
        made = [r for r in tried if r is not None]
        weeks.append({
            "start": days[w * 7], "end": days[w * 7 + 6],
            "days_live": sum(1 for h in live_hours if h >= 1),
            "live_hours_per_day": statistics.mean(live_hours),
            "attempts": len(tried), "reached": len(made),
            "reached_pct": 100 * len(made) / len(tried) if tried else None,
            "minutes_to_20": statistics.median(made) if made else None,
            "players_per_day": statistics.mean(len(players[d]) for d in ds),
        })
    return weeks


def num(v, places=0):
    if v is None:
        return "no data"
    return f"{v:,.{places}f}"


def change(now, before, points=False):
    """The difference in words, so it never depends on colour."""
    if now is None or before is None:
        return "no data"
    if points:
        d = now - before
        return "Level" if abs(d) < 0.5 else f"{'Up' if d > 0 else 'Down'} {abs(d):.0f} points"
    if before == 0:
        return "Level" if now == 0 else "Up"
    pct = 100 * (now - before) / before
    return "Level" if abs(pct) < 0.5 else f"{'Up' if pct > 0 else 'Down'} {abs(pct):.0f}%"


def average(weeks, key):
    values = [w[key] for w in weeks if w[key] is not None]
    return statistics.mean(values) if values else None


def day(d):
    return f"{d.strftime('%B')} {d.day}"


def listed_servers():
    """[(short name, Server List key)] for each RLG server. The key is built from
    the join code in WarCon's public status, so nothing extra has to be set up."""
    out = []
    for sid in WARCON_IDS:
        try:
            req = urllib.request.Request(f"{WARCON}/api/public/servers/{sid}",
                                         headers={"User-Agent": rlgpost.UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                s = json.loads(resp.read()).get("server", {})
        except Exception:  # noqa: BLE001 - leave this server out of the extra pictures
            continue
        key = serverlist.server_key(s.get("joinCode"))
        if key:
            name = str(s.get("name") or "").split(" - ")[0]
            tag = next((part.split("]")[0] for part in name.split("[")[1:] if not part.startswith("RLG]")), "")
            out.append((tag or name or "Server", key))
    return out


def places(now, before):
    """Change in rank in words. A smaller rank number is a better place."""
    if now is None or before is None:
        return "no data"
    d = before - now
    return "Level" if d == 0 else f"{'Up' if d > 0 else 'Down'} {abs(d)} place{'' if abs(d) == 1 else 's'}"


def rank_post(servers, week):
    """Picture: where each server ranked among community servers in the last two
    full weeks (Server List weeks run Monday to Sunday, UTC). None if not known."""
    rows, label = [], ""
    for name, key in servers:
        d = serverlist.get("/api/server-detail", ttl=600, key=key, window="30d") or {}
        cut = (time.time() - 7 * 86400) * 1000   # a week is full once it started 7 or more days ago
        full = sorted((w for w in d.get("weeks") or [] if w.get("week", 0) <= cut and w.get("rank")),
                      key=lambda w: -w["week"])
        if not full:
            continue
        a, b = full[0], (full[1] if len(full) > 1 else None)
        start = datetime.fromtimestamp(a["week"] / 1000, timezone.utc)
        label = f"Week of {day(start)}"
        show = lambda w: f"{w['rank']:,} of {w['of']:,}" if w and w.get("of") else (f"{w['rank']:,}" if w else "no data")
        rows.append((f"{name} · rank", show(a), show(b), places(a["rank"], b["rank"] if b else None)))
        rows.append((f"{name} · average players on", num(a.get("avgPlayers"), 1),
                     num(b.get("avgPlayers"), 1) if b else "no data",
                     change(a.get("avgPlayers"), b.get("avgPlayers") if b else None)))
    if not rows:
        return None
    png = rlgimage.numbers_trend(
        "Where we rank", f"{label} · Among every community server in the game", rows,
        footer_left="Rank 1 is the busiest · " + serverlist.CREDIT, footer_right="Updates every Monday",
        columns=("Last week", "Week before", "Change"), first="Server")
    return png, "**Where we rank among community servers**\n-# " + serverlist.CREDIT_LINK, f"numbers-{week}-rank.png"


def seed_hours_post(servers, week):
    """Picture: the hours of the day when seeds started in the region most often
    reached 20, over the last 14 days, shown in US Eastern. None if not known."""
    found = next((f for f in (serverlist.seed_hours(key) for _, key in servers) if f), None)
    if not found:
        return None
    region, tz, hours = found
    today = datetime.now(ZoneInfo(tz)).replace(minute=0, second=0, microsecond=0)
    ranked = []
    for h, (started, reached) in enumerate(hours):
        if started >= 8:   # too few seeds at an hour says nothing
            east = today.replace(hour=h).astimezone(EASTERN).hour
            ranked.append((100 * reached / started, started, reached, east))
    if not ranked:
        return None
    ranked.sort(key=lambda r: (-r[0], -r[1]))
    rows = [(f"{serverlist.hour_label(east)} to {serverlist.hour_label((east + 1) % 24)} ET",
             f"{pct:.0f}%", f"{reached} of {started}", "") for pct, started, reached, east in ranked[:6]]
    png = rlgimage.numbers_trend(
        "Best hours to seed", f"When seeds in {region} most often reach 20 · Last 14 days · Times are US Eastern", rows,
        footer_left=serverlist.CREDIT,
        footer_right="Updates every Monday", columns=("Reached 20", "Seeds", ""), first="Seed started")
    return (png, f"**Best hours to start a seed** · every community server in {region}, hours with fewer than "
                 "8 seeds left out\n-# " + serverlist.CREDIT_LINK, f"numbers-{week}-seed-hours.png")


def season_cards(servers):
    """When a season has just finished, post each server's season card from Server
    List, once. The first time this runs it only notes the season already over."""
    if not servers:
        return
    d = serverlist.get("/api/season-server", ttl=600, key=servers[0][1], season="final")
    season = (d or {}).get("season")
    if not season or not d.get("final"):
        return
    try:
        with open(SEASON_FILE, encoding="utf-8") as f:
            seen = f.read().strip()
    except FileNotFoundError:
        seen = None
    if seen == str(season):
        return
    os.makedirs(os.path.dirname(SEASON_FILE), exist_ok=True)
    with open(SEASON_FILE, "w", encoding="utf-8") as f:
        f.write(f"{season}\n")
    if seen is None:
        return   # first run: that season ended before this job existed
    for name, key in servers:
        url = f"{serverlist.BASE}/season-card.png?" + urllib.parse.urlencode({"season": season, "key": key})
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": rlgpost.UA}), timeout=40) as r:
                png = r.read()
            rlgpost.post_image(WEBHOOK, png, f"season-{season}-{name.lower().replace(' ', '-')}.png",
                               f"**{d.get('name') or 'Season ' + str(season)} is over: how {name} did**\n-# "
                               + serverlist.CREDIT_LINK)
            time.sleep(1)
        except Exception as e:  # noqa: BLE001 - a missing card must not fail the job
            print(f"Season card for {name} was not posted ({type(e).__name__}).", flush=True)


def extra_posts(week):
    """The Server List pictures. Any problem here leaves the list empty."""
    if not serverlist.enabled():
        print("WARDOGSERVERLIST_API_KEY is not set: skipping the rank and seeding-hours pictures.")
        return [], []
    try:
        servers = listed_servers()
        return [p for p in (rank_post(servers, week), seed_hours_post(servers, week)) if p], servers
    except Exception as e:  # noqa: BLE001 - never let the extras stop the weekly numbers
        print(f"Server List pictures skipped ({type(e).__name__}: {e}).", flush=True)
        return [], []


def main():
    if not bm.SERVERS:
        sys.exit("BM_SERVER_IDS is empty. Set it in the workflow file.")
    if not bm.TOKEN:
        sys.exit("BATTLEMETRICS_TOKEN is not set. Add it as a repository secret.")
    if not WEBHOOK and not DRY_RUN:
        sys.exit("DISCORD_NUMBERS_WEBHOOK_URL is not set. Add it as a repository secret.")
    week = datetime.now(EASTERN).strftime("%G-W%V")
    try:
        with open(LAST_FILE, encoding="utf-8") as f:
            already = f.read().strip() == week
    except FileNotFoundError:
        already = False
    if already and not FORCE and not DRY_RUN:
        print(f"The numbers for {week} were already posted. Nothing to do.")
        return

    weeks = measure()
    last, l4, p4 = weeks[-1], weeks[-4:], weeks[-8:-4]
    through = f"Through {day(last['end'])}"
    stats = [
        ("Days live", f"{last['days_live']} of 7", ""),
        ("Seed attempts", num(last["attempts"]), ""),
        ("Attempts that reached 20", num(last["reached"]), ""),
        ("Typical time to reach 20", num(last["minutes_to_20"]), "minutes" if last["minutes_to_20"] is not None else ""),
        ("Players a day", num(last["players_per_day"]), ""),
    ]
    rows = []
    for label, key, places, unit, points in (
        ("Days live a week", "days_live", 1, "", False),
        ("Live hours a day", "live_hours_per_day", 1, "", False),
        ("Seed attempts that reached 20", "reached_pct", 0, "%", True),
        ("Typical minutes to reach 20", "minutes_to_20", 0, "", False),
        ("Players a day", "players_per_day", 0, "", False),
    ):
        a, b = average(l4, key), average(p4, key)
        show = lambda v: num(v, places) + (unit if v is not None else "")
        rows.append((label, show(a), show(b), change(a, b, points)))

    posts = [
        (rlgimage.numbers_week(
            "Last week", f"Week of {day(last['start'])} · Both servers", stats, right=through,
            footer_left="Live means 20 or more players on · Source: BattleMetrics",
            footer_right="Updates every Monday"),
         f"**RLG Weekly Numbers** · week of {day(last['start'])}", f"numbers-{week}-last-week.png"),
        (rlgimage.numbers_trend(
            "Last 4 weeks", f"{day(l4[0]['start'])} to {day(l4[-1]['end'])}, beside the 4 weeks before · Both servers",
            rows, right=through, footer_left="For minutes to reach 20, lower is better · Source: BattleMetrics",
            footer_right="Updates every Monday"),
         "**Last 4 weeks beside the 4 before**", f"numbers-{week}-last-4-weeks.png"),
    ]
    extras, servers = extra_posts(week)
    posts += extras
    if DRY_RUN:
        for png, text, filename in posts:
            out = os.path.join(os.environ.get("DRY_RUN_DIR", "."), filename)
            with open(out, "wb") as f:
                f.write(png)
            print(f"{text}\n  picture saved to {out} ({len(png) // 1024} KB)")
        return
    # The week is recorded as soon as the first picture is up, so a problem part
    # way through can never lead to the set being posted twice.
    for i, (png, text, filename) in enumerate(posts):
        try:
            rlgpost.post_image(WEBHOOK, png, filename, text)
        except urllib.error.HTTPError as e:
            sys.exit(rlgpost.describe(e) + f" ({i} of {len(posts)} pictures were posted.)")
        if i == 0:
            os.makedirs(os.path.dirname(LAST_FILE), exist_ok=True)
            with open(LAST_FILE, "w", encoding="utf-8") as f:
                f.write(week + "\n")
        time.sleep(1)
    try:
        season_cards(servers)
    except Exception as e:  # noqa: BLE001
        print(f"Season cards skipped ({type(e).__name__}: {e}).", flush=True)
    print(f"Posted the weekly numbers for {week}.")


if __name__ == "__main__":
    main()
