# Deep dive on seeding for the Redline Gaming dashboard: what goes with a seed attempt working, where the time goes,
# who does the seeding, and how the busiest North America community servers compare.
# Sources: BattleMetrics API (Redline join and leave records; server list and 30 days of hourly counts for the 60 busiest
#          NA community servers) and the seed attempts worked out by analyze2.py. Read-only.
#   seed attempt        a stretch where a server goes from empty to 3 or more players; it worked if it reached 20
#   before 20           from the first join to the minute 20 were on, or to the end of the attempt if it never got there
#   seeder              on the server for 10 minutes or more before 20
#   top seeder          one of the 25 players who have helped most seeds overall
#   RLG tag             a player whose name carries the [RLG] tag
#   admin               a player on the admin roster, counted as an admin only for attempts between the day they joined the team and the day they left it
#   other server busy   the other Redline server had 80 or more on when the attempt began
#   with / without      every comparison splits the same attempts in two; "difference" is in percentage points
#   link strength       the correlation between the factor and reaching 20 (0 = no link, 1 = perfect link); under 0.15 is treated as no link
#   these are links, not proof of cause: for example a seeder may join because an attempt already looks promising
import json, sys, os, statistics, collections, re, math
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
RAW, A2, SNAP, H30, OUT, ADMINS = sys.argv[1:7]
TZ = ZoneInfo("America/New_York"); CODE = open(os.path.abspath(__file__)).read()
P = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")); r1 = lambda x: None if x is None else round(x, 1)
med = lambda xs: statistics.median(xs) if xs else None; pct = lambda a, b: None if not b else round(100 * a / b, 1)
L = lambda f: json.load(open(os.path.join(A2, f + ".json")))["rows"]
raw = json.load(open(RAW)); NOW = P(raw["fetchedAt"])
BM = {"RLG Infantry": "41243237", "RLG Vanilla": "41199298"}; OTHER = {"RLG Infantry": "RLG Vanilla", "RLG Vanilla": "RLG Infantry"}
T0 = datetime(2026, 9, 12, 0, tzinfo=NOW.tzinfo); N = int((NOW - T0).total_seconds() // 60); mi = lambda t: min(N - 1, max(0, int((t - T0).total_seconds() // 60)))
cnt = {}; sess = {}
for server, sid in BM.items():
    diff = [0] * (N + 2); v = []
    for a, b, f, nm, pid in raw["servers"][sid]["sessions"]:
        a = P(a); e = P(b) if b else NOW
        if e - a > timedelta(hours=4): e = a + timedelta(minutes=40)
        i, j = mi(a), min(N, int((e - T0).total_seconds() // 60))
        if j > i: diff[i] += 1; diff[j] -= 1
        v.append((a, e, pid, nm))
    c = 0; arr = []
    for x in diff[:N]: c += x; arr.append(c)
    cnt[server] = arr; sess[server] = sorted(v)
# the admin team, as BattleMetrics player numbers with the dates each person was on the team (static/admins.json, made from the roster sheet)
ADMIN = {a["player"]: dict(role=a["role"], start=date.fromisoformat(a["start"]), end=date.fromisoformat(a["end"]) if a["end"] else None) for a in json.load(open(ADMINS))["admins"]}
admin_at = lambda p, d: p in ADMIN and ADMIN[p]["start"] <= d and (ADMIN[p]["end"] is None or d < ADMIN[p]["end"])
top25 = set()
sd = collections.Counter()
for r in L("seeders"):
    if r["win"] == "all": sd[r["key"]] += r["seeds_helped"]
top25 = {k for k, v in sd.most_common(25)}
live_day = collections.defaultdict(bool)
for server in BM:
    for k in range(0, N, 5):
        if cnt[server][k] >= 20: live_day[(T0 + timedelta(minutes=k)).astimezone(TZ).date()] = True
A = []
for s in L("seeds"):
    server = s["server"]; t0 = P(s["start"]); c = cnt[server]; k0 = mi(t0)
    kend = k0 + int(round(s["tts_min"])) if s["seeded"] else k0 + int(round(s["run_hours"] * 60))
    kend = min(N - 1, max(kend, k0 + 1))
    def first(n):
        for k in range(k0, kend + 1):
            if c[k] >= n: return k - k0
        return None
    pres = collections.defaultdict(float); names = {}; joined = set()
    for a, e, pid, nm in sess[server]:
        if e <= t0 or mi(a) > kend: continue
        lo = max(mi(a), k0); hi = min(min(N, int((e - T0).total_seconds() // 60)), kend)
        if hi > lo: pres[pid] += hi - lo; names[pid] = nm
        if k0 <= mi(a) <= kend: joined.add(pid)
    early = {pid for a, e, pid, nm in sess[server] if mi(a) <= k0 + 30 and e > t0 and mi(a) >= k0 - 1}
    seeders = {p for p, m in pres.items() if m >= 10}
    d = t0.astimezone(TZ)
    A.append(dict(server=server, ok=bool(s["seeded"]), tts=s["tts_min"] if s["seeded"] else None, peak=max(c[k0:kend + 1]), dur=kend - k0,
                  t5=first(5), t10=first(10), t15=first(15), at15=c[min(N - 1, k0 + 15)], at30=c[min(N - 1, k0 + 30)],
                  top_early=len(early & top25), top_any=len(seeders & top25), rlg_early=sum(1 for p in early if "[RLG]" in (names.get(p) or "") or "RLG" in (names.get(p) or "").upper().split("]")[0]),
                  adm_early=sum(1 for p in early if admin_at(p, d.date())), adm_seeders={p for p in seeders if admin_at(p, d.date())},
                  seeders=len(seeders), joined=len(joined), stuck=pct(len(seeders & joined), len(joined)),
                  other_busy=cnt[OTHER[server]][k0] >= 80, other_live=cnt[OTHER[server]][k0] >= 20, yesterday=live_day[d.date() - timedelta(days=1)],
                  weekend=d.weekday() >= 5, hour=d.hour, month=d.strftime("%Y-%m"), date=d.date(), names=names, pres=pres))
n = len(A); ok = [a for a in A if a["ok"]]
print("attempts", n, "ok", len(ok))
def corr(xs, ys):
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys); sx = math.sqrt(sum((x - mx) ** 2 for x in xs)); sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return 0 if not sx or not sy else sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)
out = collections.defaultdict(list)
FACT = [("Two or more top seeders on in the first 30 minutes", lambda a: a["top_early"] >= 2), ("At least one top seeder on in the first 30 minutes", lambda a: a["top_early"] >= 1),
        ("An [RLG] tagged player on in the first 30 minutes", lambda a: a["rlg_early"] >= 1),
        ("An admin on in the first 30 minutes", lambda a: a["adm_early"] >= 1), ("Two or more admins on in the first 30 minutes", lambda a: a["adm_early"] >= 2),
        ("5 or more players on within 15 minutes", lambda a: a["at15"] >= 5), ("10 or more players on within 30 minutes", lambda a: a["at30"] >= 10),
        ("The other Redline server was busy (80 or more on)", lambda a: a["other_busy"]), ("A Redline server was live the day before", lambda a: a["yesterday"]),
        ("Started on a weekend", lambda a: a["weekend"]), ("Started before noon Eastern", lambda a: a["hour"] < 12), ("Started 6pm Eastern or later", lambda a: a["hour"] >= 18)]
for i, (name, f) in enumerate(FACT):
    y = [a for a in A if f(a)]; no = [a for a in A if not f(a)]
    py, pn = pct(sum(a["ok"] for a in y), len(y)), pct(sum(a["ok"] for a in no), len(no))
    r = corr([1.0 if f(a) else 0.0 for a in A], [1.0 if a["ok"] else 0.0 for a in A])
    out["factors"].append({"factor": name, "attempts_with": len(y), "reached_20_with_pct": py, "attempts_without": len(no), "reached_20_without_pct": pn,
                           "difference_points": r1((py or 0) - (pn or 0)) if py is not None and pn is not None else None, "link_strength": round(abs(r), 2),
                           "typical_minutes_with": r1(med([a["tts"] for a in y if a["ok"]])), "typical_minutes_without": r1(med([a["tts"] for a in no if a["ok"]])),
                           "reading": "Strong link" if abs(r) >= .35 else "Some link" if abs(r) >= .15 else "No real link"})
FSHORT = {"At least one top seeder": "A top seeder on early", "10 or more players": "10 players within 30 minutes", "An [RLG] tagged": "An [RLG] tag on early", "Two or more top seeders": "Two top seeders on early",
          "An admin on": "An admin on early", "Two or more admins": "Two admins on early", "5 or more players": "5 players within 15 minutes", "Started before noon": "Started before noon",
          "A Redline server was live": "Live the day before", "Started on a weekend": "Started on a weekend", "The other Redline server": "Other Redline server busy", "Started 6pm": "Started 6pm or later"}
for r in out["factors"]: r["short"] = next(v for k, v in FSHORT.items() if r["factor"].startswith(k)); r["strength"] = "Strong" if r["link_strength"] >= .35 else "Some" if r["link_strength"] >= .15 else "None"
out["factors"].sort(key=lambda r: -r["link_strength"])
for i, r in enumerate(out["factors"]): r["order"] = i
# critical mass: once an attempt has N on, how often does it go on to 20
for k in (3, 5, 8, 10, 12, 15):
    v = [a for a in A if a["peak"] >= k]
    out["tipping"].append({"reached": f"{k} players", "n": k, "attempts": len(v), "went_on_to_20": sum(a["ok"] for a in v), "went_on_to_20_pct": pct(sum(a["ok"] for a in v), len(v))})
# where the time goes, for attempts that worked
st = [("First join to 5 players", [a["t5"] for a in ok if a["t5"] is not None]), ("5 to 10 players", [a["t10"] - a["t5"] for a in ok if a["t10"] is not None and a["t5"] is not None]),
      ("10 to 20 players", [a["tts"] - a["t10"] for a in ok if a["t10"] is not None])]
tot = sum(med(v) for n_, v in st)
out["stages"] = [{"order": i, "stage": n_, "typical_minutes": r1(med(v)), "share_of_time_pct": pct(med(v), tot), "slowest_quarter_minutes": r1(sorted(v)[int(len(v) * .75)])} for i, (n_, v) in enumerate(st)]
bad = [a for a in A if not a["ok"]]
for i, (lab, lo, hi) in enumerate((("Never got past 4", 0, 4), ("Stalled at 5 to 9", 5, 9), ("Stalled at 10 to 14", 10, 14), ("Stalled at 15 to 19", 15, 19))):
    v = [a for a in bad if lo <= a["peak"] <= hi]
    out["stalls"].append({"order": i, "where": lab, "attempts": len(v), "share_pct": pct(len(v), len(bad)), "typical_minutes_before_it_ended": r1(med([a["dur"] for a in v]))})
# speed: what goes with a fast seed (attempts that worked)
for name, f in (("Two or more admins on in the first 30 minutes", lambda a: a["adm_early"] >= 2), ("Two or more top seeders on in the first 30 minutes", lambda a: a["top_early"] >= 2), ("5 or more players on within 15 minutes", lambda a: a["at15"] >= 5),
                ("The other Redline server was busy (80 or more on)", lambda a: a["other_busy"]), ("Started 6pm Eastern or later", lambda a: a["hour"] >= 18), ("Started on a weekend", lambda a: a["weekend"])):
    y = [a["tts"] for a in ok if f(a)]; no = [a["tts"] for a in ok if not f(a)]
    out["speed"].append({"factor": name, "seeds_with": len(y), "typical_minutes_with": r1(med(y)), "seeds_without": len(no), "typical_minutes_without": r1(med(no)),
                         "minutes_saved": r1(med(no) - med(y)) if y and no else None})
out["speed"].sort(key=lambda r: -(r["minutes_saved"] or 0))
# who seeds: tagged members against everyone else, the funnel, the untapped regulars
hours = collections.Counter(); part = collections.Counter(); nm = {}; bymonth = collections.defaultdict(set)
for a in A:
    for p, m in a["pres"].items():
        if m >= 10: hours[p] += m / 60; part[p] += 1; nm[p] = a["names"].get(p) or nm.get(p); bymonth[a["month"]].add(p)
tag = lambda p: "[RLG]" in (nm.get(p) or "")
th = sum(hours.values())
ahours = collections.Counter(); a30 = set(); cut = NOW.astimezone(TZ).date() - timedelta(days=30)
for a in A:
    for p in a["adm_seeders"]:
        ahours[p] += a["pres"][p] / 60
        if a["date"] >= cut: a30.add(p)
grp3 = [("The admin team", lambda p: False), ("Other players with the [RLG] tag", lambda p: tag(p) and p not in ahours), ("Everyone else", lambda p: not tag(p) and p not in ahours)]
out["who"] = [{"order": 0, "group": "The admin team", "seeders": len(ahours), "seeding_hours": r1(sum(ahours.values())), "share_of_hours_pct": pct(sum(ahours.values()), th)}]
rest = {p: h - ahours.get(p, 0) for p, h in hours.items()}          # an admin's seeding from before they joined the team counts with the players
for i, (lab, f) in enumerate((("Other players with the [RLG] tag", lambda p: tag(p)), ("Everyone else", lambda p: not tag(p))), 1):
    v = {p: h for p, h in rest.items() if h > 0.001 and p not in ahours and f(p)}
    out["who"].append({"order": i, "group": lab, "seeders": len(v), "seeding_hours": r1(sum(v.values())), "share_of_hours_pct": pct(sum(v.values()), th)})
out["who"][2]["seeding_hours"] = r1(th - out["who"][0]["seeding_hours"] - out["who"][1]["seeding_hours"]); out["who"][2]["share_of_hours_pct"] = r1(100 - out["who"][0]["share_of_hours_pct"] - out["who"][1]["share_of_hours_pct"])
team = {p: v for p, v in ADMIN.items() if v["role"] != "Removed"}
for i, role in enumerate(("Director", "Senior", "Admin", "Trial")):
    ps = [p for p, v in team.items() if v["role"] == role]
    out["admin_roles"].append({"order": i, "role": role, "people": len(ps), "have_seeded": sum(1 for p in ps if p in ahours), "seeded_last_30_days": sum(1 for p in ps if p in a30),
                               "seeding_hours": r1(sum(ahours.get(p, 0) for p in ps)), "share_of_all_seeding_pct": pct(sum(ahours.get(p, 0) for p in ps), th)})
for i, (lab, lo, hi) in enumerate((("Seeded once", 1, 1), ("2 to 4 times", 2, 4), ("5 to 9 times", 5, 9), ("10 times or more", 10, 999))):
    v = [p for p, c in part.items() if lo <= c <= hi]
    out["funnel"].append({"order": i, "group": lab, "players": len(v), "share_of_seeding_hours_pct": pct(sum(hours[p] for p in v), th)})
active = collections.defaultdict(set)
for server in BM:
    for a, e, pid, n_ in sess[server]: active[pid].add(a.astimezone(TZ).date())
regs = {p for p, d in active.items() if len(d) >= 5}; recent = {p for p, d in active.items() if max(d) >= NOW.astimezone(TZ).date() - timedelta(days=30)}
months = sorted(bymonth); mlab = lambda m: datetime.strptime(m, "%Y-%m").strftime("%b %Y")
out["bench"] = [{"month": mlab(m), "order": i, "attempts": sum(1 for a in A if a["month"] == m), "reached_20_pct": pct(sum(a["ok"] for a in A if a["month"] == m), sum(1 for a in A if a["month"] == m)),
                 "different_seeders": len(bymonth[m]), "seeders_per_attempt": r1(med([a["seeders"] for a in A if a["month"] == m]))} for i, m in enumerate(months)]
prev = set().union(*[bymonth[m] for m in months[:-1]]) if len(months) > 1 else set(); latest = bymonth[months[-1]]   # earlier months against the latest month
# ---- the busiest NA community servers over 30 days
snap = {r["id"]: r for r in json.load(open(SNAP))["rows"]}; h30 = json.load(open(H30)); peers = []
def mode(r):
    w = r["details"]["wardogs"]; e = " ".join(w.get("experiences") or []) + " " + (w.get("experience") or ""); nmx = r["name"].lower()
    if "limited" in nmx or "Limited" in e: return "Limited"
    return "Infantry Only" if ("Infantry" in e or re.search(r"\binf\b|infantry", nmx)) else "Vanilla"
for sid, pts in h30.items():
    s = snap.get(sid)
    if not s or isinstance(pts, str) or len(pts) < 24 * 20: continue
    day = collections.defaultdict(list)
    for t, mn, v, mx in pts:
        loc = P(t).astimezone(TZ); day[(loc - timedelta(hours=6)).date()].append((loc.hour, v or 0))   # a "day" runs 6am to 6am Eastern
    live = {d: [h for h, v in hv if v >= 20] for d, hv in day.items()}; ld = [d for d, hs in live.items() if hs]
    first = [next(h for h, v in sorted(day[d], key=lambda x: (x[0] - 6) % 24) if v >= 20) for d in ld]
    w = s["details"]["wardogs"]; name = s["name"].strip()
    peers.append({"key": sid, "server": name, "mode": mode(s), "days": len(day), "days_live": len(ld), "days_live_pct": pct(len(ld), len(day)),
                  "live_hours_per_day": r1(sum(len(h) for h in live.values()) / len(day)), "typical_hour_live": med([(h - 6) % 24 for h in first]) if first else None,
                  "reserved_slots": w.get("reservedPlayerCapacity") or 0, "level_cap": 0 < (w.get("maxWardogLevel") or 0) <= 100,
                  "boost_in_name": bool(re.search(r"boost|2x|3x|xp|\$", name.lower())), "redline": sid in BM.values()})
hrl = lambda x: None if x is None else (lambda h: f"{(h % 12) or 12}{'am' if h % 24 < 12 else 'pm'}")(int(round(x + 6)) % 24)
for p in peers: p["goes_live_around"] = hrl(p["typical_hour_live"])
peers.sort(key=lambda p: (-p["live_hours_per_day"]))
for i, p in enumerate(peers, 1): p["rank"] = i
out["peers"] = [{"key": p["key"], "rank": p["rank"], "server": ("REDLINE: " if p["redline"] else "") + p["server"][:60], "mode": p["mode"], "days_live_pct": p["days_live_pct"],
                 "live_hours_per_day": p["live_hours_per_day"], "goes_live_around": p["goes_live_around"], "redline": int(p["redline"])} for p in peers]
others = [p for p in peers if not p["redline"]]; red = [p for p in peers if p["redline"]]
def grp(label, f, pool):
    y = [p for p in pool if f(p)]; no = [p for p in pool if not f(p)]
    return {"tactic": label, "servers_with": len(y), "days_live_with_pct": r1(med([p["days_live_pct"] for p in y])) if y else None, "hours_per_day_with": r1(med([p["live_hours_per_day"] for p in y])) if y else None,
            "servers_without": len(no), "days_live_without_pct": r1(med([p["days_live_pct"] for p in no])) if no else None, "hours_per_day_without": r1(med([p["live_hours_per_day"] for p in no])) if no else None}
out["tactics"] = [grp("Holds reserved slots for members or seeders (4 or more)", lambda p: p["reserved_slots"] >= 4, others), grp("Advertises boosted cash or XP in its name", lambda p: p["boost_in_name"], others),
                  grp("Runs Limited or Infantry Only", lambda p: p["mode"] != "Vanilla", others)]
top10 = sorted(others, key=lambda p: -p["live_hours_per_day"])[:10]
out["deep_summary"] = [{
    "attempts": n, "reached_20": len(ok), "failed": len(bad),
    "tip_10_pct": next(r["went_on_to_20_pct"] for r in out["tipping"] if r["n"] == 10), "tip_5_pct": next(r["went_on_to_20_pct"] for r in out["tipping"] if r["n"] == 5),
    "first5_minutes": out["stages"][0]["typical_minutes"], "first5_share_pct": out["stages"][0]["share_of_time_pct"], "last_stage_minutes": out["stages"][2]["typical_minutes"],
    "failed_under_10": sum(1 for a in bad if a["peak"] < 10), "failed_under_10_pct": pct(sum(1 for a in bad if a["peak"] < 10), len(bad)),
    "admins": len(team), "admins_seeded": sum(1 for p in team if p in ahours), "admins_seeded_30": sum(1 for p in team if p in a30), "admins_not_30": len(team) - sum(1 for p in team if p in a30),
    "admin_share_pct": out["who"][0]["share_of_hours_pct"], "admin_hours": out["who"][0]["seeding_hours"],
    "adm_early_pct": pct(sum(a["ok"] for a in A if a["adm_early"]), sum(1 for a in A if a["adm_early"])), "adm_early_attempts": sum(1 for a in A if a["adm_early"]),
    "no_adm_early_pct": pct(sum(a["ok"] for a in A if not a["adm_early"]), sum(1 for a in A if not a["adm_early"])), "no_adm_early_attempts": sum(1 for a in A if not a["adm_early"]),
    "adm2_minutes": r1(med([a["tts"] for a in ok if a["adm_early"] >= 2])), "adm_under2_minutes": r1(med([a["tts"] for a in ok if a["adm_early"] < 2])),
    "ten_plus_players": out["funnel"][3]["players"], "ten_plus_share_pct": out["funnel"][3]["share_of_seeding_hours_pct"], "once_players": out["funnel"][0]["players"],
    "regulars": len(regs), "regulars_never_seeded": len(regs - set(part)), "regulars_never_seeded_recent": len((regs & recent) - set(part)),
    "seeders_sep_oct": len(prev), "seeders_sep_oct_still_in_nov": len(prev & latest), "seeders_new_in_nov": len(latest - prev),
    "joined_typical": med([a["joined"] for a in A]), "stuck_typical_pct": r1(med([a["stuck"] for a in A if a["stuck"] is not None])),
    "peers": len(others), "peer_days_live_pct": r1(med([p["days_live_pct"] for p in top10])), "peer_hours_per_day": r1(med([p["live_hours_per_day"] for p in top10])),
    "peer_goes_live": hrl(med([p["typical_hour_live"] for p in top10 if p["typical_hour_live"] is not None])),
    "inf_days_live_pct": next(p["days_live_pct"] for p in red if p["key"] == "41243237"), "van_days_live_pct": next(p["days_live_pct"] for p in red if p["key"] == "41199298"),
    "inf_hours_per_day": next(p["live_hours_per_day"] for p in red if p["key"] == "41243237"), "van_hours_per_day": next(p["live_hours_per_day"] for p in red if p["key"] == "41199298"),
    "inf_goes_live": next(p["goes_live_around"] for p in red if p["key"] == "41243237"), "van_goes_live": next(p["goes_live_around"] for p in red if p["key"] == "41199298"),
    "inf_rank": next(p["rank"] for p in red if p["key"] == "41243237"), "van_rank": next(p["rank"] for p in red if p["key"] == "41199298"), "ranked": len(peers)}]
meta = [{"label": "deep.py", "value": CODE, "type": "code", "lang": "python"},
        {"label": "Input", "value": "BattleMetrics API, read-only: Redline join and leave records through " + raw["fetchedAt"] + ", and 30 days of hourly counts for 60 busy North America community servers"}]
# one row per attempt for the weekly history: when, whether it worked, and how much of the seeding the admin team did
out["attempt_log"] = [{"date": a["date"].isoformat(), "ok": int(a["ok"]), "tts": a["tts"], "seeding_hours": round(sum(m for m in a["pres"].values() if m >= 10) / 60, 2),
                       "admin_hours": round(sum(a["pres"][p] for p in a["adm_seeders"]) / 60, 2), "admins": sorted(a["adm_seeders"])} for a in A]
os.makedirs(OUT, exist_ok=True)
for k, v in out.items():
    json.dump({"rows": v, "meta": meta}, open(os.path.join(OUT, k + ".json"), "w"), allow_nan=False)
    print("==", k)
    if k in ("stalls", "tactics", "factors", "who", "admin_roles", "speed"):
        for r in v: print("  ", json.dumps(r))
