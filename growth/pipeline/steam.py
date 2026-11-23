# Game-wide context from Steam for the Redline Gaming dashboard.
# Sources: Steam news feed for WARDOGS (app 1867240, official announcements only), Steam player counts from steamcharts.com,
#          BattleMetrics game-wide daily players and Redline join and leave records. All public or read-only.
#   event            an official patch or season post on Steam; the date is the US Eastern day it was posted
#                    seasons are found from the posts; patches and outside events come from static/events_manual.json
#   week before      the 7 days ending the day before the event; week after: the 7 days starting on the event day
#   busiest day      the highest daily figure in the 10 days from the event; second week: days 8 to 14, shown once 3 or more of them have data
#   Wardogs players  the highest number in the game on Steam each day (steamcharts.com), averaged over the week
#   Redline players  different players seen on either Redline server per day, averaged over the same days
#   not on a server  the highest number in the game on Steam on the last full day beside BattleMetrics' highest count on servers that day;
#                    the two highs need not fall in the same hour, so this is a rough figure
import json, sys, os, collections, statistics, re
from datetime import datetime, timedelta, date, timezone
from zoneinfo import ZoneInfo
RAW, BENCH, NEWS, CHARTS, OUT, MANUAL = sys.argv[1:7]
TZ = ZoneInfo("America/New_York"); CODE = open(os.path.abspath(__file__)).read()
P = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")); r1 = lambda x: None if x is None else round(x, 1)
raw = json.load(open(RAW)); NOW = P(raw["fetchedAt"]); last = NOW.astimezone(TZ).date()
news = [i for i in json.load(open(NEWS))["appnews"]["newsitems"] if i["feedname"] == "steam_community_announcements"]
posted = {i["title"].strip(): datetime.fromtimestamp(i["date"], timezone.utc).astimezone(TZ).date() for i in news}
man = json.load(open(MANUAL)); EVENTS = []
for t, d in posted.items():       # a season starts with a post titled "SEASON 0N IS NOW LIVE"
    m = re.search(r"SEASON\s*0*(\d+)\s+IS NOW LIVE", t, re.I)
    if m: EVENTS.append((d, man["labels"].get(d.isoformat(), f"Season {m.group(1)} began"), f"Season {m.group(1)}"))
for e in man.get("seasons", []):      # seasons already known, in case their posts have dropped off the news feed
    if not any(x[2] == f"Season {e['n']}" for x in EVENTS): EVENTS.append((date.fromisoformat(e["date"]), man["labels"].get(e["date"], f"Season {e['n']} began"), f"Season {e['n']}"))
EVENTS += [(date.fromisoformat(e["date"]), e["event"], "Other") for e in man["extra"]]      # patches and outside events added by hand
EVENTS.sort()
monday = lambda d: d - timedelta(days=d.weekday())
out = {"events": [{"n": i, "date": d.isoformat(), "when": d.strftime("%b %-d"), "week": monday(d).isoformat(), "event": lab} for i, (d, lab, s) in enumerate(EVENTS, 1)]}
pts_ = [(datetime.fromtimestamp(t / 1000, timezone.utc), v) for t, v in json.load(open(CHARTS))]; per_utc = collections.Counter(t.date() for t, v in pts_); sd = collections.defaultdict(list)
for t, v in pts_: sd[t.date() if per_utc[t.date()] == 1 and t.hour == 0 else t.astimezone(TZ).date()].append(v)
game = {d: max(v) for d, v in sd.items()}      # players in the game on Steam: the highest count each day
bm_max = {date.fromisoformat(d): mx for d, mn, v, mx in json.load(open(BENCH))["game"]}
daily = collections.defaultdict(set); mins14 = collections.Counter()
for s in raw["servers"].values():
    for a, b, f, nm, pid in s["sessions"]:
        a = P(a); e = P(b) if b else NOW
        if e - a > timedelta(hours=4): e = a + timedelta(minutes=40)
        daily[a.astimezone(TZ).date()].add(pid)
        if a.astimezone(TZ).date() > last - timedelta(days=14): mins14[pid] += (e - a).total_seconds() / 60
avg = lambda xs: sum(xs) / len(xs) if xs else None; chg = lambda a, b: None if not a or b is None else r1(100 * (b - a) / a)
out["patches"] = []
for i, (d, lab, short) in enumerate(EVENTS, 1):
    before = [d - timedelta(days=k) for k in range(1, 8)]; after = [d + timedelta(days=k) for k in range(0, 7)]
    if not short.startswith("Season"): continue      # the table is about seasons; patches are only marked on the chart
    if after[-1] > last or min(sum(1 for x in before if x in game), sum(1 for x in after if x in game)) < 6: continue   # needs a full week after it and game-wide figures for both weeks
    gb, ga = avg([game[x] for x in before if x in game]), avg([game[x] for x in after if x in game])
    rb, ra = avg([len(daily[x]) for x in before]), avg([len(daily[x]) for x in after])
    top = max((x for x in [d + timedelta(days=k) for k in range(0, 10)] if x in game), key=lambda x: game[x])
    wk2 = [x for x in [d + timedelta(days=k) for k in range(7, 14)] if x in game and x <= last]; g2 = avg([game[x] for x in wk2]) if len(wk2) >= 3 else None
    r2 = avg([len(daily[x]) for x in wk2]) if len(wk2) >= 3 else None
    out["patches"].append({"n": i, "event": short, "when": d.strftime("%b %-d"), "game_before": round(gb), "game_after": round(ga), "game_change_pct": chg(gb, ga),
                           "game_busiest_day": round(game[top]), "game_busiest_when": top.strftime("%b %-d"), "game_second_week": None if g2 is None else round(g2), "game_second_week_change_pct": chg(gb, g2),
                           "second_week_days": len(wk2), "redline_before": round(rb), "redline_after": round(ra), "redline_second_week": None if r2 is None else round(r2),
                           "redline_change_pct": chg(rb, ra) if rb >= 50 else None})   # no percentage when Redline was all but empty the week before
pk = last   # the last full day
if pk not in game or pk not in bm_max: pk = max(d for d in game if d in bm_max)
out["steam"] = [{"day": pk.isoformat(), "steam_peak": game[pk], "servers_peak": bm_max[pk], "off_server_pct": r1(100 * (1 - bm_max[pk] / game[pk])), "posts": len(news)}]
meta = [{"label": "steam.py", "value": CODE, "type": "code", "lang": "python"},
        {"label": "Input", "value": "Steam news feed for WARDOGS (official announcements) and Steam player counts from steamcharts.com; BattleMetrics game-wide daily players and Redline join and leave records through " + raw["fetchedAt"]}]
os.makedirs(OUT, exist_ok=True)
for k, rows in out.items():
    json.dump({"rows": rows, "meta": meta}, open(os.path.join(OUT, k + ".json"), "w"), allow_nan=False); print("==", k)
    for r in rows: print("  ", json.dumps(r))
