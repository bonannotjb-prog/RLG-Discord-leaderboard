# The simple Redline Gaming dashboard: who gets the servers live, how long it takes, what brings players in and what drives them away.
# Sources: BattleMetrics API (join and leave records for both Redline servers; hourly counts for every public Wardogs server)
#          and WarCon API (matches, scoreboards, seasons). Both read-only. Both Redline servers are added together unless a row names one.
#   live / seeded        20 or more players on
#   seed attempt         a stretch where a server goes from empty to 3 or more players; it worked if it reached 20
#   time to reach 20     first player joining the empty server to 20 on
#   seeder               spent at least 10 minutes on a server before it reached 20
#   new player           a player's first ever join on that server
#   players already on   the count one minute before the join
#   visit                one sitting; sessions under 5 minutes apart are joined; sessions with no recorded end or over 4 hours are left out
#   match change         end of one match and start of the next with 60 or more on; "lost a third" = two thirds or fewer left 30 minutes later
#   came back            played another match on a later day within 7 days (WarCon game accounts; on a team for 5 minutes or more)
#   typical              the median
import json, sys, os, statistics, collections
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
RAW, API, A2, CX, LENS, OUT = sys.argv[1:7]
TZ = ZoneInfo("America/New_York"); CODE = open(os.path.abspath(__file__)).read()
P = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")); r1 = lambda x: None if x is None else round(x, 1)
med = lambda xs: statistics.median(xs) if xs else None; pct = lambda a, b: None if not b else round(100 * a / b, 1)
L = lambda d, f: json.load(open(os.path.join(d, f + ".json")))["rows"]
raw = json.load(open(RAW)); api = json.load(open(API)); NOW = P(raw["fetchedAt"])
BM = {"RLG Infantry": "41243237", "RLG Vanilla": "41199298"}; WC = {"RLG Infantry": "2dd77c71-dbb6-49c4-95cf-78625467aba1", "RLG Vanilla": "230a2cb3-e2af-450d-a1e1-515b08139d3d"}
T0 = datetime(2026, 9, 15, 4, tzinfo=NOW.tzinfo); N = int((NOW - T0).total_seconds() // 60)
out = {}

# ---- arrivals and stays, by how many players were already on
BANDS = [("Empty (0 to 4 on)", 0, 4), ("Seeding (5 to 19 on)", 5, 19), ("Live (20 to 59 on)", 20, 59), ("Busy (60 to 99 on)", 60, 99), ("Full (100 on)", 100, 999)]
band = lambda c: next(i for i, (n, lo, hi) in enumerate(BANDS) if lo <= c <= hi)
mins = [0] * 5; newp = [0] * 5; stays = collections.defaultdict(list); week_players = collections.defaultdict(set); live_min = collections.defaultdict(int); cnts = {}
for server, sid in BM.items():
    diff = [0] * (N + 2); ss = raw["servers"][sid]["sessions"]
    for a, b, f, nm, pid in ss:
        a = P(a); e = P(b) if b else NOW
        if e - a > timedelta(hours=4): e = a + timedelta(minutes=40)
        i = max(0, int((a - T0).total_seconds() // 60)); j = min(N, int((e - T0).total_seconds() // 60))
        if j > i: diff[i] += 1; diff[j] -= 1
    c = 0; cnt = []
    for x in diff[:N]: c += x; cnt.append(c)
    cnts[server] = cnt
    for c in cnt: mins[band(c)] += 1
    first = {}; by = collections.defaultdict(list)
    for a, b, f, nm, pid in ss:
        a = P(a)
        if pid not in first or a < first[pid]: first[pid] = a
        d = a.astimezone(TZ).date(); week_players[d - timedelta(days=d.weekday())].add(pid)
        if b and P(b) - a <= timedelta(hours=4): by[pid].append((a, P(b)))
    at = lambda t: cnt[min(N - 1, max(0, int((t - T0).total_seconds() // 60) - 1))]
    for pid, t in first.items():
        if t >= T0: newp[band(at(t))] += 1
    for pid, v in by.items():
        v.sort(); s, e = v[0]
        for a, b in v[1:] + [(None, None)]:
            if a is not None and a - e < timedelta(minutes=5): e = max(e, b); continue
            if s >= T0: stays[min(2, band(at(s)))].append((e - s).total_seconds() / 60)
            s, e = a, b
tot_new = sum(newp)
out["arrivals"] = [{"order": i, "players_on": n, "hours": r1(mins[i] / 60), "new_players": newp[i], "new_players_per_hour": r1(newp[i] / (mins[i] / 60)) if mins[i] else None,
                    "share_of_new_pct": pct(newp[i], tot_new)} for i, (n, lo, hi) in enumerate(BANDS)]
out["stays"] = [{"order": i, "players_on": n, "visits": len(stays[i]), "typical_stay_min": r1(med(stays[i])), "left_within_5_min_pct": pct(sum(1 for x in stays[i] if x < 5), len(stays[i]))}
                for i, n in enumerate(("Nearly empty (0 to 4 on)", "Seeding (5 to 19 on)", "Live (20 or more on)"))]

# ---- seeding: how long, when, who
seeds = L(A2, "seeds"); ok = [s for s in seeds if s["seeded"]]; srv = [r for r in L(A2, "servers") if r["win"] == "all"]
blk = lambda h: h // 3 * 3; hr = lambda h: f"{(h % 12) or 12}{'am' if h % 24 < 12 else 'pm'}"
out["start_times"] = []
for b in range(0, 24, 3):
    v = [s for s in seeds if blk(s["hour"]) == b]
    if len(v) >= 3: out["start_times"].append({"order": (b - 6) % 24, "started": hr(b) + " to " + hr(b + 3), "attempts": len(v), "reached_20": sum(s["seeded"] for s in v),
                                               "reached_20_pct": pct(sum(s["seeded"] for s in v), len(v)), "typical_minutes": r1(med([s["tts_min"] for s in v if s["seeded"]]))})
out["start_times"].sort(key=lambda r: r["order"])
out["by_server"] = [{"server": r["server"], "attempts": r["attempts"], "reached_20": r["seeds_ok"], "reached_20_pct": r["seed_success_pct"], "typical_minutes": r["tts_median_min"],
                     "minutes_20_to_full": r["fill_median_min"], "typical_hours_live": r["stay_live_median_hours"], "days_live": r["days_live"], "days": r["days_in_window"]} for r in srv]
sd = collections.defaultdict(lambda: dict(seeds=0, short=0, hours=0.0, seeds_30=0, hours_30=0.0, name=None))
for r in L(A2, "seeders"):
    x = sd[r["key"]]
    if r["win"] == "all": x["seeds"] += r["seeds_helped"]; x["short"] += r["attempts_fell_short"]; x["hours"] += r["seeding_hours"]; x["name"] = r["player"]
    if r["win"] == "30": x["seeds_30"] += r["seeds_helped"]; x["hours_30"] += r["seeding_hours"]; x["name"] = x["name"] or r["player"]
allt = {k: v for k, v in sd.items() if v["seeds"] + v["short"] > 0}
top = sorted(allt.items(), key=lambda kv: (-kv[1]["seeds"], -kv[1]["hours"]))
out["seeders"] = [{"key": k, "rank": i, "player": v["name"], "seeds_helped": v["seeds"], "attempts_fell_short": v["short"], "hours_seeding": r1(v["hours"]),
                   "seeds_helped_last_30_days": v["seeds_30"]} for i, (k, v) in enumerate(top[:25], 1)]
core = L(A2, "core"); cw = [r for r in core if r["key"] == "with"]; cn = [r for r in core if r["key"] == "without"]
days_all = sorted({(T0 + timedelta(minutes=m)).astimezone(TZ).date() for m in range(0, N, 720)})
live_days = sum(1 for d in days_all if any(cnts[s][k] >= 20 for s in cnts for k in range(max(0, int((datetime(d.year, d.month, d.day, tzinfo=TZ) - T0).total_seconds() // 60)),
                                                                                           min(N, int((datetime(d.year, d.month, d.day, tzinfo=TZ) - T0).total_seconds() // 60) + 1440), 5)))
tr = {r["server"]: r for r in L(A2, "trend")}
red4 = tr["RLG Infantry"]["avg_4w"] + tr["RLG Vanilla"]["avg_4w"]; redp = tr["RLG Infantry"]["avg_prev4w"] + tr["RLG Vanilla"]["avg_prev4w"]

# ---- what drives players away: time of night, not the match
hours = collections.defaultdict(lambda: [0, 0, 0]); when = collections.defaultdict(lambda: [0, 0, 0])
for r in L(CX, "cx_hours"):
    h = hours[(r["hour"], r["label"])]; h[0] += r["changes"]; h[1] += round(r["lost_third_pct"] * r["changes"] / 100); h[2] += round(r["not_live_pct"] * r["changes"] / 100)
for r in L(CX, "cx_when"):
    w = when[r["key"]]; w[0] += r["changes"]; w[1] += round(r["lost_third_pct"] * r["changes"] / 100); w[2] += round(r["not_live_pct"] * r["changes"] / 100)
out["leaving_hours"] = [{"order": (h - 6) % 24, "match_ended": lab, "match_changes": v[0], "lost_a_third_pct": pct(v[1], v[0]), "night_over_within_hour_pct": pct(v[2], v[0])}
                        for (h, lab), v in sorted(hours.items(), key=lambda kv: (kv[0][0] - 6) % 24) if v[0] >= 8]
factors = []
for name, f, what in (("cx_next", "Which map came next", None), ("cx_score", "How the last match finished", None)):
    rows = L(CX, name); g = collections.defaultdict(lambda: [0, 0])
    for r in rows: g[r["group"]][0] += r["evening_changes"]; g[r["group"]][1] += round((r["evening_lost_pct"] or 0) * r["evening_changes"] / 100)
    ps = {k: pct(v[1], v[0]) for k, v in g.items() if v[0] >= 10}
    factors.append({"factor": f, "compared": ", ".join(ps), "lowest_pct": min(ps.values()), "highest_pct": max(ps.values()), "match_changes": sum(v[0] for v in g.values())})
out["leaving_other"] = factors
base = L(CX, "cx_base")

# ---- do new players come back, by how their first match went (WarCon accounts)
apps = collections.defaultdict(list)
for server, wid in WC.items():
    for m in api["servers"][wid]["matches"]:
        if not m["endedAt"]: continue
        sc = {x["name"]: x["score"] for x in (m["finalScores"] or [])}
        for l in api["servers"][wid]["lines"].get(str(m["id"]), []):
            if l["faction"] and l["seconds"] >= 300:
                res = None if not m["winner"] else "Won" if l["faction"] == m["winner"] else "Lost a close one" if sc.get(l["faction"], 0) >= 50 else "Lost heavily"
                apps[l["steamId"]].append((P(m["startedAt"]), res))
last = max(t for v in apps.values() for t, r in v); lo, hi = datetime(2026, 10, 11, tzinfo=NOW.tzinfo), last - timedelta(days=8)
fm = collections.defaultdict(lambda: [0, 0])
for sid, v in apps.items():
    v.sort(key=lambda x: x[0]); t, res = v[0]
    if not (lo <= t <= hi) or res is None: continue
    back = any(t2.date() > t.date() and t2 - t <= timedelta(days=7) for t2, r2 in v[1:])
    fm[res][0] += 1; fm[res][1] += back
out["first_match"] = [{"order": i, "first_match": k, "new_players": fm[k][0], "came_back": fm[k][1], "came_back_pct": pct(fm[k][1], fm[k][0])} for i, k in enumerate(("Won", "Lost a close one", "Lost heavily")) if fm[k][0]]
fa, fb = sum(v[0] for v in fm.values()), sum(v[1] for v in fm.values())

# ---- modes and the weekly picture
out["modes"] = [{"key": sc + "|" + r["mode"], "where": sc, "mode": r["mode"], "label": r["mode"], "servers": r["servers"], "reached_20_pct": r["went_live_pct"], "live_hours_per_server": r["live_hours_per_server"]}
                for sc, f in (("North America", "md_modes"), ("Worldwide", "md_world")) for r in L(LENS, f)]
out["modes_na"] = [r for r in out["modes"] if r["where"] == "North America"]; out["modes_world"] = [r for r in out["modes"] if r["where"] == "Worldwide"]
seasons = [("2026-10-15", "Season 2 began"), ("2026-11-12", "Season 3 began, Limited mode arrived")]
weeks = []
for w in sorted(week_players):
    if w < date(2026, 9, 14): continue
    lm = 0
    for k in range(N):
        pass
    weeks.append({"week": w.isoformat(), "label": w.strftime("%b %-d"), "players": len(week_players[w]), "full_week": int(w + timedelta(days=6) <= NOW.astimezone(TZ).date()),
                  "event": next((lab for d, lab in seasons if w <= date.fromisoformat(d) <= w + timedelta(days=6)), None)})
out["weeks"] = weeks
out["events"] = [{"n": i, "date": d, "when": date.fromisoformat(d).strftime("%b %-d"), "week": (date.fromisoformat(d) - timedelta(days=date.fromisoformat(d).weekday())).isoformat(), "event": lab} for i, (d, lab) in enumerate(seasons, 1)]

nm = {r["mode"]: r for r in out["modes_na"]}
out["summary"] = [{
    "data_from": "2026-09-15", "data_through": NOW.astimezone(TZ).date().isoformat(), "days": len(days_all), "days_live": live_days, "days_dark": len(days_all) - live_days,
    "attempts": len(seeds), "reached_20": len(ok), "reached_20_pct": pct(len(ok), len(seeds)), "typical_minutes_to_20": r1(med([s["tts_min"] for s in ok])),
    "fastest_minutes": r1(min(s["tts_min"] for s in ok)), "typical_hours_live": r1(med([s["seeded_hours"] for s in ok])), "minutes_20_to_full": r1(med([r["fill_median_min"] for r in srv])),
    "seeders": len(allt), "top5": ", ".join(v["name"] for k, v in top[:5]), "attempts_with_core": sum(r["attempts"] for r in cw), "reached_20_with_core_pct": pct(sum(r["seeded"] for r in cw), sum(r["attempts"] for r in cw)),
    "attempts_without_core": sum(r["attempts"] for r in cn), "reached_20_without_core_pct": pct(sum(r["seeded"] for r in cn), sum(r["attempts"] for r in cn)),
    "best_start": max((r for r in out["start_times"] if r["attempts"] >= 8), key=lambda r: r["reached_20_pct"])["started"],
    "best_start_pct": max((r for r in out["start_times"] if r["attempts"] >= 8), key=lambda r: r["reached_20_pct"])["reached_20_pct"],
    "new_players": tot_new, "new_on_live_pct": pct(sum(newp[2:]), tot_new), "new_per_hour_live": r1(sum(newp[2:]) / (sum(mins[2:]) / 60)), "new_per_hour_not_live": r1(sum(newp[:2]) / (sum(mins[:2]) / 60)),
    "left_within_5_empty_pct": out["stays"][0]["left_within_5_min_pct"], "left_within_5_live_pct": out["stays"][2]["left_within_5_min_pct"],
    "typical_stay_empty_min": out["stays"][0]["typical_stay_min"], "typical_stay_live_min": out["stays"][2]["typical_stay_min"],
    "lost_third_before_11pm_pct": pct(when["evening"][1], when["evening"][0]), "lost_third_after_11pm_pct": pct(when["late"][1], when["late"][0]),
    "nights_ended": sum(r["nights_ended"] for r in base), "nights_ended_late": sum(r["nights_ended_late"] for r in base), "nights_ended_late_pct": pct(sum(r["nights_ended_late"] for r in base), sum(r["nights_ended"] for r in base)),
    "even_teams_pct": pct(sum(r["even_matches"] for r in base), sum(r["matches_with_teams"] for r in base)),
    "came_back_pct": pct(fb, fa), "first_match_players": fa,
    "limited_na_hours": nm["Limited"]["live_hours_per_server"], "infantry_na_hours": nm["Infantry Only"]["live_hours_per_server"], "vanilla_na_hours": nm["Vanilla"]["live_hours_per_server"],
    "redline_change_pct": r1(100 * (red4 - redp) / redp), "game_change_pct": tr["Wardogs, all servers"]["avg_change_pct"], "trend_from": tr["RLG Infantry"]["last4_from"], "trend_to": tr["RLG Infantry"]["last4_to"]}]
meta = [{"label": "simple.py", "value": CODE, "type": "code", "lang": "python"},
        {"label": "Input", "value": "BattleMetrics API (Redline join and leave records through " + raw["fetchedAt"] + "; server list and hourly counts for every public Wardogs server, November 13 to 20) and WarCon API (matches and scoreboards pulled " + api["fetchedAt"] + "). Both keys are read-only."},
        {"label": "Built from", "value": "analyze2.py (seed attempts and seeders), cx.py (match changes) and lens.py (game modes), whose outputs this script condenses"}]
os.makedirs(OUT, exist_ok=True)
for k, v in out.items():
    json.dump({"rows": v, "meta": meta}, open(os.path.join(OUT, k + ".json"), "w"), allow_nan=False)
for k in ("summary", "arrivals", "stays", "start_times", "by_server", "leaving_hours", "leaving_other", "first_match", "modes_na", "modes_world", "weeks", "events"):
    print("==", k)
    for r in out[k]: print("  ", json.dumps(r))
print("== seeders"); [print("  ", json.dumps(r)) for r in out["seeders"][:6]]
