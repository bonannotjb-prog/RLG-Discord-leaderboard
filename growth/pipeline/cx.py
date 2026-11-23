# Context for the Redline Gaming dashboard: what was going on when a busy server emptied out.
# Inputs: WarCon API pull (matches with per-player lines, 30-day population with downtime; read-only key) and
#         BattleMetrics session history (read-only token). Output: one JSON file per dashboard source, {rows, meta}.
#   players at a moment   rebuilt from BattleMetrics join and leave records, minute by minute
#   match change          the end of one match and start of the next, counted only when 60 or more players were on
#                         2 minutes before the match ended (the game drops and re-adds everyone at the change itself)
#   lost a third          30 minutes into the next match the server had two thirds or fewer of those players
#   not live an hour on   under 20 players 60 minutes after the match ended
#   evening / late        the match ended between 6am and 11pm US Eastern / between 11pm and 6am
#   blowout / close       runner-up on 50 points or fewer / on 90 or more (first to 100 wins); the rest are "in between"
#   even teams            the largest faction had under 38% of all player-time in the match (an even split is 33%);
#                         a player on a team for under 5 minutes is left out
#   night ended           the server dropped under 20 players and stayed there for 90 minutes; "at a match change" = within 10 minutes of one
#   downtime              seconds WarCon could not reach the server, from its 20-second samples; a day counts as down at 2 hours or more
import json, sys, os, statistics, collections
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
RAW, API, AN_INF, AN_VAN, OUT = sys.argv[1:6]
TZ = ZoneInfo("America/New_York"); CODE = open(os.path.abspath(__file__)).read()
P = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")); r1 = lambda x: None if x is None else round(x, 1)
med = lambda xs: statistics.median(xs) if xs else None; pct = lambda a, b: None if not b else round(100 * a / b, 1)
raw = json.load(open(RAW)); api = json.load(open(API)); NOW = P(raw["fetchedAt"])
SRV = {"RLG Infantry": ("41243237", "2dd77c71-dbb6-49c4-95cf-78625467aba1", AN_INF), "RLG Vanilla": ("41199298", "230a2cb3-e2af-450d-a1e1-515b08139d3d", AN_VAN)}
T0 = datetime(2026, 9, 12, 0, tzinfo=NOW.tzinfo); N = int((NOW - T0).total_seconds() // 60)
hr = lambda h: f"{(h % 12) or 12}{'am' if h < 12 else 'pm'}"
out = collections.defaultdict(list)
for server, (bm, wc, anf) in SRV.items():
    diff = [0] * (N + 2)
    for a, b, first, nm, pid in raw["servers"][bm]["sessions"]:
        a = P(a); e = P(b) if b else NOW
        if e - a > timedelta(hours=4): e = a + timedelta(minutes=40)
        i = max(0, int((a - T0).total_seconds() // 60)); j = min(N, int((e - T0).total_seconds() // 60))
        if j > i: diff[i] += 1; diff[j] -= 1
    c = 0; cnt = []
    for x in diff[:N]: c += x; cnt.append(c)
    at = lambda t: cnt[min(N - 1, max(0, int((t - T0).total_seconds() // 60)))]
    ms = sorted([m for m in api["servers"][wc]["matches"] if m["endedAt"]], key=lambda m: m["startedAt"])
    rows = []; even = scored = 0
    for k, m in enumerate(ms[:-1]):
        s, e = P(m["startedAt"]), P(m["endedAt"])
        if e + timedelta(minutes=70) > NOW or (e - s) < timedelta(minutes=10): continue
        before = at(e - timedelta(minutes=2))
        if before < 60: continue
        sc = sorted((x["score"] for x in (m["finalScores"] or [])), reverse=True)
        kind = None if not m["winner"] else "Blowout" if sc[1] <= 50 else "Close finish" if sc[1] >= 90 else "In between"
        tm = collections.Counter()
        for l in api["servers"][wc]["lines"].get(str(m["id"]), []):
            if l["faction"] and l["seconds"] >= 300: tm[l["faction"]] += l["seconds"]
        if tm: scored += 1; even += max(tm.values()) / sum(tm.values()) < .38
        h = e.astimezone(TZ).hour
        rows.append(dict(keep=at(e + timedelta(minutes=30)) / before, dead=at(e + timedelta(minutes=60)) < 20, next=ms[k + 1]["map"], kind=kind, hour=h, late=(h >= 23 or h < 6)))
    stat = lambda v: {"changes": len(v), "lost_third_pct": pct(sum(1 for r in v if r["keep"] <= 2 / 3), len(v)), "not_live_pct": pct(sum(1 for r in v if r["dead"]), len(v))}
    for key, label, v in (("evening", "Match ended 6am to 11pm", [r for r in rows if not r["late"]]), ("late", "Match ended 11pm to 6am", [r for r in rows if r["late"]])):
        out["cx_when"].append({"server": server, "key": key, "when": label, **stat(v), "kept_median_pct": r1(100 * med([r["keep"] for r in v])) if v else None})
    for h in sorted({r["hour"] for r in rows}, key=lambda h: (h - 6) % 24):
        v = [r for r in rows if r["hour"] == h]
        if len(v) >= 5: out["cx_hours"].append({"server": server, "hour": h, "label": hr(h), **stat(v)})
    def split(name, field, groups):
        for i, g in enumerate(groups):
            ev = [r for r in rows if r[field] == g and not r["late"]]; la = [r for r in rows if r[field] == g and r["late"]]
            if len(ev) + len(la) < 10: continue
            out[name].append({"server": server, "order": i, "group": g, "evening_changes": len(ev), "evening_lost_pct": stat(ev)["lost_third_pct"],
                              "late_changes": len(la), "late_lost_pct": stat(la)["lost_third_pct"]})
    split("cx_next", "next", sorted({r["next"] for r in rows})); split("cx_score", "kind", ["Close finish", "In between", "Blowout"])
    ends = [P(m["endedAt"]) for m in ms]
    drops = [k for k in range(1, N) if cnt[k - 1] >= 20 and cnt[k] < 20 and all(cnt[j] < 20 for j in range(k, min(N, k + 90)))]
    near = sum(1 for k in drops if any(abs((T0 + timedelta(minutes=k) - e).total_seconds()) <= 600 for e in ends))
    latehrs = sum(1 for k in drops if (T0 + timedelta(minutes=k)).astimezone(TZ).hour in (23, 0, 1, 2, 3, 4, 5))
    out["cx_base"].append({"server": server, "changes": len(rows), "nights_ended": len(drops), "nights_ended_at_change": near, "nights_ended_late": latehrs,
                           "nights_ended_late_pct": pct(latehrs, len(drops)), "matches_with_teams": scored, "even_matches": even, "even_pct": pct(even, scored)})
    an = json.load(open(anf)); day = collections.defaultdict(lambda: [0.0, 0.0, 0])
    for p in an["population"]:
        d = P(p["ts"]).astimezone(TZ).date(); day[d][0] += p["up"]; day[d][1] += p["down"]; day[d][2] = max(day[d][2], p["max"])
    full = {d: v for d, v in day.items() if v[0] + v[1] >= 20 * 3600}
    dark = [d for d, v in full.items() if v[2] < 20]
    out["cx_down"].append({"server": server, "days": len(full), "uptime_pct": r1(100 * sum(v[0] for v in full.values()) / sum(v[0] + v[1] for v in full.values())),
                           "dark_days": len(dark), "dark_days_down": sum(1 for d in dark if full[d][1] >= 7200), "from": min(full).isoformat(), "to": max(full).isoformat()})
monday = lambda d: d - timedelta(days=d.weekday())
for n, (d, label) in enumerate(((date(2026, 10, 15), "Season 2 began and progress was wiped"), (date(2026, 10, 26), "Automatic seeding ping began in Discord"),
                                (date(2026, 11, 3), "Both servers sat mostly empty until November 13"), (date(2026, 11, 12), "Season 3 began and Limited mode arrived")), 1):
    out["cx_events"].append({"n": n, "date": d.isoformat(), "when": d.strftime("%b %-d"), "week": monday(d).isoformat(), "event": label})
meta = [{"label": "cx.py", "value": CODE, "type": "code", "lang": "python"},
        {"label": "Input", "value": "WarCon API (matches, scoreboards and 30-day population, pulled " + api["fetchedAt"] + ") and BattleMetrics session history (pulled " + raw["fetchedAt"] + "); both read-only"},
        {"label": "Season dates", "value": "Season 2 on October 15 from published patch news; Season 3 on November 12 from WarCon's season record"}]
os.makedirs(OUT, exist_ok=True)
for k, v in out.items():
    json.dump({"rows": v, "meta": meta}, open(os.path.join(OUT, k + ".json"), "w"), allow_nan=False)
    print("==", k)
    for r in v: print("  ", json.dumps(r))
