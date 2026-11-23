# Player metrics for the Redline Gaming dashboard, worked out for every date window the page offers.
# Input: BattleMetrics session history (GET /sessions?filter[servers]=..&filter[range]=..), read-only.
# Output: one JSON file per dashboard source, each {rows, meta}. Most rows carry `server` and `win`.
#
#   win               "all", "60", "30", "14", "7" (last N days up to the data pull) or one date "YYYY-MM-DD"
#   seeded / live     20 or more players online at once
#   seed attempt      a stretch where the server goes from empty to at least 3 players
#                     (empty gaps of 10 minutes or less do not end a stretch); it belongs to the day it started
#   time to seed      first join after empty -> first moment 20 players are online
#   seeder            a player online for at least 10 minutes during a seed attempt before it reached 20
#   seeder lists      hours count all time online before a stretch reached 20, seeded or not;
#                     windows longer than 7 days leave out players who joined only one attempt on only one day
#   helping both      seeded one server, then joined the other within 2 hours of the first reaching 20,
#                     while the second still had fewer than 20
#   visit             one player's sessions joined together when the gap is under 5 minutes
#   full              100 players online; time to fill runs from 20 online to 100
#   core seeders      the five players on each server who have been part of the most seeds
#   last 4 weeks      the last four complete Monday-to-Sunday weeks, against the four before them
#   ghost session     no end recorded, or longer than 4 hours: counts as 40 minutes for player counts,
#                     left out of visit lengths
import json, sys, os, bisect, statistics
from datetime import datetime, timedelta
from collections import defaultdict
from zoneinfo import ZoneInfo

RAW, OUTDIR = sys.argv[1], sys.argv[2]
BENCH = json.load(open(sys.argv[3])) if len(sys.argv) > 3 else {"game": [], "rank": {}}
TZ = ZoneInfo("America/New_York")
SEEDED, MIN_PEAK, FULL = 20, 3, 100
MIN_SEED_MIN = 10
BRIDGE, VISIT_GAP = timedelta(minutes=10), timedelta(minutes=5)
GHOST, GHOST_NOMINAL, MOVE_WINDOW = timedelta(minutes=240), timedelta(minutes=40), timedelta(hours=2)
SHORT = {"41243237": "RLG Infantry", "41199298": "RLG Vanilla"}
DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
BUCKETS = [("under 5 min", 0, 5), ("5 to 30 min", 5, 30), ("30 to 60 min", 30, 60), ("1 to 2 hours", 60, 120), ("over 2 hours", 120, 1e9)]

raw = json.load(open(RAW))
P = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00"))
NOW = P(raw["fetchedAt"])
CODE = open(os.path.abspath(__file__)).read()
mins = lambda td: td.total_seconds() / 60
r1 = lambda x: None if x is None else round(x, 1)
med = lambda xs: statistics.median(xs) if xs else None
avg = lambda xs: sum(xs) / len(xs) if xs else None
pct = lambda a, b: None if not b else round(100 * a / b, 1)
ldate = lambda t: t.astimezone(TZ).date()
out = defaultdict(list)
S = {}

# ------------------------------------------------------------------ per-server groundwork
for sid, name in SHORT.items():
    srv = raw["servers"][sid]
    names, per_player, ghosts = {}, defaultdict(list), 0
    for a, b, first, nm, pid in srv["sessions"]:
        a = P(a); e = P(b) if b else NOW
        g = (e - a) > GHOST
        if g: ghosts += 1; e = a + GHOST_NOMINAL
        per_player[pid].append((a, e, g))
        if pid not in names or a >= names[pid][0]: names[pid] = (a, nm)
    pname = {k: v[1] for k, v in names.items()}
    ev = sorted([(a, 1) for iv in per_player.values() for a, e, g in iv] + [(e, -1) for iv in per_player.values() for a, e, g in iv], key=lambda x: (x[0], x[1]))
    times, counts, c = [], [], 0
    for t, k in ev:
        c += k; times.append(t); counts.append(c)
    count_at = (lambda T, C: (lambda t: C[bisect.bisect_right(T, t) - 1] if t >= T[0] else 0))(times, counts)
    t1 = min(times[-1], NOW)

    # hour grid, daily peak, stretches under 20
    grid = defaultdict(lambda: [0.0, 0.0, 0.0]); day_peak = defaultdict(int); under = []
    for i in range(len(times) - 1):
        a, b, c = times[i], times[i + 1], counts[i]
        if a == b: continue
        if c < SEEDED:
            if under and under[-1][1] == a: under[-1][1] = b
            else: under.append([a, b])
        cur = a
        while cur < b:
            loc = cur.astimezone(TZ)
            nxt = min(b, (loc.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)).astimezone(cur.tzinfo))
            h = (nxt - cur).total_seconds() / 3600; gk = grid[(loc.date(), loc.hour)]
            gk[0] += c * h; gk[2] += h
            if c >= SEEDED: gk[1] += h
            day_peak[loc.date()] = max(day_peak[loc.date()], c); cur = nxt
    ustarts = [u[0] for u in under]

    # minutes each player spent online while the server had fewer than 20, by day
    under20 = defaultdict(float)
    for pid, iv in per_player.items():
        for a, e, g in iv:
            j = max(bisect.bisect_right(ustarts, a) - 1, 0)
            while j < len(under) and under[j][0] < e:
                lo, hi = max(a, under[j][0]), min(e, under[j][1])
                if hi > lo: under20[(pid, ldate(lo))] += mins(hi - lo)
                j += 1

    # visits
    visits = []
    for pid, iv in per_player.items():
        iv.sort(); cs, ce, cg = iv[0]
        for a, e, g in iv[1:]:
            if a - ce <= VISIT_GAP: ce = max(ce, e); cg = cg or g
            else: visits.append((pid, cs, ce, cg)); cs, ce, cg = a, e, g
        visits.append((pid, cs, ce, cg))
    active = defaultdict(set); hours_played = defaultdict(float)
    for pid, a, e, g in visits:
        active[pid].add(ldate(a))
        if not g: hours_played[pid] += mins(e - a) / 60
    first_day = {p: min(ds) for p, ds in active.items()}
    fv = {}
    for pid, a, e, g in visits:
        if pid not in fv or a < fv[pid]: fv[pid] = a
    first_state = {p: ("live" if count_at(a) >= SEEDED else "unseeded") for p, a in fv.items()}
    day_players = defaultdict(set); day_new = defaultdict(int)
    for p, ds in active.items():
        for d in ds: day_players[d].add(p)
        day_new[first_day[p]] += 1
    vrows = [(ldate(a), mins(e - a), "seeded" if count_at(a) >= SEEDED else "seeding", pid, ldate(a) == first_day[pid])
             for pid, a, e, g in visits if not g and e < NOW]

    # runs and seed attempts
    runs, cur, prev = [], None, 0
    for i, t in enumerate(times):
        c = counts[i]
        if prev == 0 and c > 0:
            if cur and cur["empty"] is not None and t - cur["empty"] <= BRIDGE: cur["empty"] = None
            else:
                if cur: cur["end"] = cur["empty"]; runs.append(cur)
                cur = {"start": t, "peak": 0, "seeded_at": None, "ge20": timedelta(0), "empty": None, "full_at": None, "live_end": None}
        if cur:
            if prev >= SEEDED and i > 0: cur["ge20"] += t - times[i - 1]
            cur["peak"] = max(cur["peak"], c)
            if c >= SEEDED and cur["seeded_at"] is None: cur["seeded_at"] = t
            if c >= FULL and cur["full_at"] is None: cur["full_at"] = t
            if prev >= SEEDED and c < SEEDED: cur["live_end"] = t
            if c == 0 and prev > 0: cur["empty"] = t
        prev = c
    if cur: cur["end"] = cur["empty"] or t1; runs.append(cur)
    attempts = [r for r in runs if r["peak"] >= MIN_PEAK]
    flat = sorted((a, e, pid) for pid, iv in per_player.items() for a, e, g in iv); starts = [x[0] for x in flat]
    for r in runs:
        w0, w1 = r["start"], (r["seeded_at"] or r["end"]); present = defaultdict(float); order = []
        for a, e, pid in flat[bisect.bisect_left(starts, w0 - GHOST):bisect.bisect_right(starts, w1)]:
            ov = mins(min(e, w1) - max(a, w0))
            if ov > 0 or w0 <= a <= w1:
                if pid not in present: order.append((a, pid))
                present[pid] += max(ov, 0)
        # a player gets credit for an attempt only after at least 10 minutes on the server before it reached 20
        order.sort(); r["joined"] = len(present)
        r["present"] = {pid: m for pid, m in present.items() if m >= MIN_SEED_MIN}; r["first5"] = [p for a, p in order if p in r["present"]][:5]
        loc = r["start"].astimezone(TZ); r["date"] = loc.date(); r["hour"] = loc.hour; r["dow"] = loc.strftime("%a")
        if r["peak"] < MIN_PEAK: continue
        out["seeds"].append({"server": name, "start": r["start"].isoformat(), "date": r["date"].isoformat(), "hour": r["hour"], "dow": r["dow"],
                             "seeded": 1 if r["seeded_at"] else 0, "tts_min": r1(mins(r["seeded_at"] - r["start"])) if r["seeded_at"] else None,
                             "peak": r["peak"], "seed_players": r["joined"], "seeded_hours": r1(mins(r["ge20"]) / 60),
                             "run_hours": r1(mins(r["end"] - r["start"]) / 60)})
    S[name] = dict(pname=pname, per_player=per_player, count_at=count_at, grid=grid, day_peak=day_peak, under20=under20, vrows=vrows,
                   active=active, hours=hours_played, first_day=first_day, day_players=day_players, day_new=day_new, attempts=attempts, runs=runs, first_state=first_state, sid=sid,
                   first=ldate(times[0]), last=ldate(t1), ghosts=ghosts)

END = max(s["last"] for s in S.values()); FIRST = min(s["first"] for s in S.values())
ALLDAYS = [FIRST + timedelta(days=i) for i in range((END - FIRST).days + 1)]

# seeders of one server who went on to help seed the other
for A, B in (("RLG Infantry", "RLG Vanilla"), ("RLG Vanilla", "RLG Infantry")):
    for r in S[A]["attempts"]:
        r["helped_other"] = []
        if not r["seeded_at"]: continue
        for pid in r["present"]:
            joins = [a0 for a0, e0, g in S[B]["per_player"].get(pid, []) if r["seeded_at"] <= a0 <= r["seeded_at"] + MOVE_WINDOW]
            if joins and S[B]["count_at"](min(joins)) - 1 < SEEDED: r["helped_other"].append(pid)

# ------------------------------------------------------------------ windows
WINDOWS = [("all", FIRST, END, "All time")] + [(str(n), END - timedelta(days=n - 1), END, f"Last {n} days") for n in (60, 30, 14, 7)] \
    + [(d.isoformat(), d, d, d.strftime("%b %-d")) for d in ALLDAYS]

for win, lo, hi, label in WINDOWS:
    days = [d for d in ALLDAYS if lo <= d <= hi]; inwin = lambda d: lo <= d <= hi
    long_window = len(days) > 7
    for name, s in S.items():
        own_days = [d for d in days if s["first"] <= d <= s["last"]]
        at = [r for r in s["attempts"] if inwin(r["date"])]; ok = [r for r in at if r["seeded_at"]]
        tts = [mins(r["seeded_at"] - r["start"]) for r in ok]
        vs = [v for v in s["vrows"] if inwin(v[0])]
        by = {st: [v[1] for v in vs if v[2] == st] for st in ("seeded", "seeding")}
        players = set().union(*[s["day_players"].get(d, set()) for d in days]) if days else set()
        newp = [p for p in players if inwin(s["first_day"][p])]
        can7 = [p for p in newp if (s["last"] - s["first_day"][p]).days >= 7]
        back7 = sum(1 for p in can7 if any(1 <= (x - s["first_day"][p]).days <= 7 for x in s["active"][p]))
        firstv = [v[1] for v in vs if v[4]]
        live = {d: sum(s["grid"][(d, h)][1] for h in range(24) if (d, h) in s["grid"]) for d in own_days}

        # seeders in this window: one list. Hours and days count all time online on a stretch before it reached 20,
        # whether or not it got there, including stretches too small to count as an attempt.
        st = defaultdict(lambda: {"ok": 0, "short": 0, "min": 0.0, "f5": 0, "days": set()})
        for r in s["runs"]:
            if not inwin(r["date"]): continue
            big = r["peak"] >= MIN_PEAK
            for pid, m in r["present"].items():
                v = st[pid]; v["min"] += m
                if m > 0: v["days"].add(r["date"])
                if big: v["ok" if r["seeded_at"] else "short"] += 1
            if r["seeded_at"]:
                for pid in r["first5"]: st[pid]["f5"] += 1
        keep = lambda v: (v["ok"] + v["short"] >= 2 or len(v["days"]) >= 2) if long_window else (v["ok"] + v["short"] >= 1 or v["min"] >= 10)
        seeders = sorted([kv for kv in st.items() if keep(kv[1])], key=lambda kv: (-kv[1]["ok"], -kv[1]["min"]))
        for rank, (pid, v) in enumerate(seeders, 1):
            out["seeders"].append({"server": name, "win": win, "rank": rank, "key": pid, "player": s["pname"][pid], "seeds_helped": v["ok"],
                                   "attempts_fell_short": v["short"], "first_five": v["f5"], "days_seeding": len(v["days"]),
                                   "seeding_hours": r1(v["min"] / 60)})

        # seed success by weekday and start time
        for dn in ["All"] + (DOW if len(days) > 1 else []):
            for blk in range(0, 24, 3):
                rs = [r for r in at if blk <= r["hour"] < blk + 3 and dn in ("All", r["dow"])]
                if not rs: continue
                okb = [r for r in rs if r["seeded_at"]]; lab = lambda h: f"{(h % 12) or 12}{'am' if h < 12 else 'pm'}"
                out["hours"].append({"server": name, "win": win, "day": dn, "block_n": blk, "block": f"{lab(blk)} to {lab((blk + 3) % 24)}",
                                     "attempts": len(rs), "seeded": len(okb), "success_pct": pct(len(okb), len(rs)),
                                     "median_tts_min": r1(med([mins(r["seeded_at"] - r["start"]) for r in okb]))})

        # visit lengths
        for state, xs in by.items():
            for i, (lab_, a0, b0) in enumerate(BUCKETS):
                n = sum(1 for x in xs if a0 <= x < b0)
                if xs: out["visits"].append({"server": name, "win": win, "state": state, "order": i, "bucket": lab_, "visits": n, "share": pct(n, len(xs))})

        # players online by weekday and hour
        hs = defaultdict(lambda: [0.0, 0.0, 0.0])
        for d in own_days:
            for h in range(24):
                g = s["grid"].get((d, h))
                if g:
                    k = hs[(d.weekday(), h)]; k[0] += g[0]; k[1] += g[1]; k[2] += g[2]
        for (wd, h), k in sorted(hs.items()):
            out["heat"].append({"server": name, "win": win, "dow": DOW[wd], "dow_n": wd, "hour": h, "avg_players": r1(k[0] / k[2]),
                                "seeded_share": pct(k[1], k[2]) or 0})

        att_days = {r["date"] for r in at}
        status = {d: ("live" if live[d] > 0 else "failed" if d in att_days else "solo" if s["day_players"].get(d) else "empty") for d in own_days}
        fills = [mins(r["full_at"] - r["seeded_at"]) for r in ok if r["full_at"]]
        split = {k: [p for p in can7 if s["first_state"][p] == k] for k in ("live", "unseeded")}
        backk = {k: sum(1 for p in v if any(1 <= (x - s["first_day"][p]).days <= 7 for x in s["active"][p])) for k, v in split.items()}
        ends = defaultdict(int)
        for r in ok:
            if r["live_end"]: ends[r["live_end"].astimezone(TZ).hour] += 1
        for h in sorted(ends, key=lambda h: (h - 6) % 24):
            out["ends"].append({"server": name, "win": win, "hour": h, "label": f"{(h % 12) or 12}{'am' if h < 12 else 'pm'}", "seeds": ends[h]})
        out["servers"].append({
            "days_failed": sum(1 for v in status.values() if v == "failed"), "days_solo": sum(1 for v in status.values() if v == "solo"),
            "days_empty": sum(1 for v in status.values() if v == "empty"),
            "fill_median_min": r1(med(fills)), "seeds_filled": len(fills),
            "new_live_n": len(split["live"]), "new_live_back7_pct": pct(backk["live"], len(split["live"])),
            "new_unseeded_n": len(split["unseeded"]), "new_unseeded_back7_pct": pct(backk["unseeded"], len(split["unseeded"])),
            "server": name, "win": win, "window": label, "win_lo": lo.isoformat(), "win_hi": hi.isoformat(),
            "days_in_window": len(own_days), "attempts": len(at), "seeds_ok": len(ok), "seed_success_pct": pct(len(ok), len(at)),
            "tts_median_min": r1(med(tts)), "tts_fastest_min": r1(min(tts)) if tts else None, "tts_slowest_min": r1(max(tts)) if tts else None,
            "visit_avg_min_seeded": r1(avg(by["seeded"])), "visit_median_min_seeded": r1(med(by["seeded"])),
            "visit_median_min_seeding": r1(med(by["seeding"])),
            "stay_live_median_hours": r1(med([mins(r["ge20"]) / 60 for r in ok])),
            "days_live": sum(1 for v in live.values() if v > 0), "live_hours": r1(sum(live.values())),
            "players": len(players), "new_players": len(newp), "back_within_7d_pct": pct(back7, len(can7)),
            "first_visit_median_min": r1(med(firstv)), "first_visit_under_5_pct": pct(sum(1 for x in firstv if x < 5), len(firstv)),
            "seeders_total": len(seeders), "tracked_since": s["first"].isoformat(), "data_through": s["last"].isoformat()})

    # helping both servers seed, this window
    both_ways = set(); per = defaultdict(lambda: defaultdict(int)); tot_moves = 0
    for A, B in (("RLG Infantry", "RLG Vanilla"), ("RLG Vanilla", "RLG Infantry")):
        ok = [r for r in S[A]["attempts"] if inwin(r["date"]) and r["seeded_at"]]
        who = set(); moves = 0; withm = 0
        for r in ok:
            moves += len(r["helped_other"]); withm += bool(r["helped_other"])
            for pid in r["helped_other"]: who.add(pid); per[pid][A] += 1
        both_ways |= who; tot_moves += moves
        out["moves"].append({"win": win, "key": f"{A.split()[-1]} to {B.split()[-1]}", "seeds": len(ok), "seeds_with_move": withm,
                             "seeds_with_move_pct": pct(withm, len(ok)), "moves": moves, "players": len(who)})
    out["moves"].append({"win": win, "key": "Either direction", "moves": tot_moves, "players": len(both_ways)})
    nm = lambda pid: S["RLG Infantry"]["pname"].get(pid) or S["RLG Vanilla"]["pname"].get(pid)
    for rank, (pid, d) in enumerate(sorted(per.items(), key=lambda kv: -sum(kv[1].values())), 1):
        out["movers"].append({"win": win, "rank": rank, "key": pid, "player": nm(pid), "infantry_to_vanilla": d.get("RLG Infantry", 0),
                              "vanilla_to_infantry": d.get("RLG Vanilla", 0), "total_moves": sum(d.values())})
    sets = [set().union(*[s["day_players"].get(d, set()) for d in days]) for s in S.values()]
    out["overlap"].append({"win": win, "players_either": len(sets[0] | sets[1]), "players_both": len(sets[0] & sets[1]),
                           "both_pct": pct(len(sets[0] & sets[1]), len(sets[0] | sets[1]))})

# ------------------------------------------------------------------ not windowed
for name, s in S.items():
    for d in ALLDAYS:
        if not (s["first"] <= d <= s["last"]): continue
        n = len(s["day_players"].get(d, ())); live = sum(s["grid"][(d, h)][1] for h in range(24) if (d, h) in s["grid"])
        out["daily"].append({"server": name, "date": d.isoformat(), "players": n, "new_players": s["day_new"].get(d, 0),
                             "returning_players": n - s["day_new"].get(d, 0), "peak": s["day_peak"].get(d, 0), "seeded_hours": r1(live)})
    cohorts = defaultdict(list)
    for p, fd in s["first_day"].items(): cohorts[fd - timedelta(days=fd.weekday())].append(p)
    for wk in sorted(cohorts):
        ps = cohorts[wk]; fd = s["first_day"]
        c7 = [p for p in ps if (s["last"] - fd[p]).days >= 7]; c30 = [p for p in ps if (s["last"] - fd[p]).days >= 30]
        b7 = sum(1 for p in c7 if any(1 <= (x - fd[p]).days <= 7 for x in s["active"][p]))
        b30 = sum(1 for p in c30 if any(8 <= (x - fd[p]).days <= 30 for x in s["active"][p]))
        out["cohorts"].append({"server": name, "week": wk.isoformat(), "week_end": (wk + timedelta(days=6)).isoformat(), "new_players": len(ps),
                               "back_within_7d_pct": pct(b7, len(c7)) if len(c7) == len(ps) else None,
                               "back_day_8_to_30_pct": pct(b30, len(c30)) if len(c30) == len(ps) else None})
    # regulars away from every server for 14 days or more, as of the data pull
    last_seen = {p: max(ds) for p, ds in s["active"].items()}
    anywhere = lambda p: max(max(o["active"][p]) for o in S.values() if p in o["active"])
    regulars = [p for p, ds in s["active"].items() if len(ds) >= 5]
    away = [p for p in regulars if (END - last_seen[p]).days >= 14]; gone = [p for p in away if (END - anywhere(p)).days >= 14]
    out["regulars"].append({"server": name, "regulars": len(regulars), "regulars_lapsed": len(gone), "regulars_moved": len(away) - len(gone)})
    for rank, p in enumerate(sorted(gone, key=lambda p: (-len(s["active"][p]), -s["hours"][p]))[:25], 1):
        out["lapsed"].append({"server": name, "rank": rank, "key": p, "player": s["pname"][p], "active_days": len(s["active"][p]),
                              "hours_played": r1(s["hours"][p]), "last_seen": anywhere(p).isoformat(), "days_away": (END - anywhere(p)).days})

# ------------------------------------------------------------------ weekly trend, seeder bench, game-wide benchmark
monday = lambda d: d - timedelta(days=d.weekday())
WEEKS = sorted({monday(d) for d in ALLDAYS})
FULLW = [w for w in WEEKS if w >= FIRST and w + timedelta(days=6) <= END]
last4, prev4 = FULLW[-4:], FULLW[-8:-4]
chg = lambda a, b: None if not b else round(100 * (a - b) / b, 1)
gday = {x[0]: x[2] for x in BENCH["game"] if x[2] is not None}
wk_rlg = defaultdict(float)
for name, s in S.items():
    rank = dict(BENCH["rank"].get(s["sid"], []))
    core = [pid for pid, n in sorted(((pid, sum(1 for r in s["attempts"] if r["seeded_at"] and pid in r["present"])) for pid in
            {p for r in s["attempts"] if r["seeded_at"] for p in r["present"]}), key=lambda kv: -kv[1])[:5]]
    wrows = {}
    for w in WEEKS:
        ds = [w + timedelta(days=i) for i in range(7)]; own = [d for d in ds if s["first"] <= d <= s["last"]]
        if not own: continue
        ps = set().union(*[s["day_players"].get(d, set()) for d in own])
        g = [s["grid"][(d, h)] for d in own for h in range(24) if (d, h) in s["grid"]]
        at = [r for r in s["attempts"] if w <= r["date"] <= ds[-1]]
        ranks = [rank[d.isoformat()] for d in own if d.isoformat() in rank]
        row = {"server": name, "week": w.isoformat(), "week_end": ds[-1].isoformat(), "days_tracked": len(own), "full_week": 1 if w in FULLW and len(own) == 7 else 0,
               "players": len(ps), "new_players": sum(s["day_new"].get(d, 0) for d in own), "live_hours": r1(sum(x[1] for x in g)),
               "avg_players": r1(sum(x[0] for x in g) / (len(own) * 24)), "attempts": len(at), "seeds_ok": sum(1 for r in at if r["seeded_at"]),
               "seeders": len({p for r in at for p, m in r["present"].items() if m > 0}),
               "core_seeders": len({p for r in at for p in r["present"] if p in core}), "rank_median": med(ranks)}
        wrows[w] = row; out["weeks"].append(row); wk_rlg[w] += row["avg_players"]
    def span(ws):
        ds = [w + timedelta(days=i) for w in ws for i in range(7)]
        ps = set().union(*[s["day_players"].get(d, set()) for d in ds]) if ds else set()
        return {"players": len(ps), "new": sum(s["day_new"].get(d, 0) for d in ds), "live": round(sum(wrows[w]["live_hours"] for w in ws if w in wrows), 1),
                "avg": round(avg([wrows[w]["avg_players"] for w in ws if w in wrows] or [0]), 1)}
    a4, b4 = span(last4), span(prev4)
    out["trend"].append({"server": name, "last4_from": last4[0].isoformat(), "last4_to": (last4[-1] + timedelta(days=6)).isoformat(),
                         "players_4w": a4["players"], "players_prev4w": b4["players"], "players_change_pct": chg(a4["players"], b4["players"]),
                         "new_4w": a4["new"], "new_prev4w": b4["new"], "new_change_pct": chg(a4["new"], b4["new"]),
                         "live_4w": a4["live"], "live_prev4w": b4["live"], "live_change_pct": chg(a4["live"], b4["live"]),
                         "avg_4w": a4["avg"], "avg_prev4w": b4["avg"], "avg_change_pct": chg(a4["avg"], b4["avg"])})
    for key, test in (("with", lambda r: any(p in core for p in r["present"])), ("without", lambda r: not any(p in core for p in r["present"]))):
        rs = [r for r in s["attempts"] if test(r)]; okc = [r for r in rs if r["seeded_at"]]
        out["core"].append({"server": name, "key": key, "attempts": len(rs), "seeded": len(okc), "success_pct": pct(len(okc), len(rs)),
                            "core_players": ", ".join(s["pname"][p] for p in core)})
gw = {}
for w in WEEKS:
    vals = [gday[(w + timedelta(days=i)).isoformat()] for i in range(7) if (w + timedelta(days=i)).isoformat() in gday]
    if not vals: continue
    gw[w] = avg(vals)
    out["game"].append({"week": w.isoformat(), "week_end": (w + timedelta(days=6)).isoformat(), "days_with_data": len(vals), "game_avg_players": round(gw[w]),
                        "rlg_avg_players": r1(wk_rlg.get(w, 0)), "rlg_share_per_10k": r1(10000 * wk_rlg.get(w, 0) / gw[w]) if gw[w] else None})
ga, gb = avg([gw[w] for w in last4 if w in gw]), avg([gw[w] for w in prev4 if w in gw])
if ga and gb:
    out["trend"].append({"server": "Wardogs, all servers", "last4_from": last4[0].isoformat(), "last4_to": (last4[-1] + timedelta(days=6)).isoformat(),
                         "avg_4w": round(ga), "avg_prev4w": round(gb), "avg_change_pct": chg(ga, gb)})

meta = [{"label": "analyze2.py", "value": CODE, "type": "code", "lang": "python"},
        {"label": "Input", "value": "BattleMetrics API: session history and rank history for servers 41243237 and 41199298, and game-wide Wardogs player counts; read-only token"},
        {"label": "Pulled", "value": raw["fetchedAt"]},
        {"label": "Time zone", "value": "US Eastern (the Redline Gaming organization's setting)"}]
os.makedirs(OUTDIR, exist_ok=True)
for k, rows in out.items():
    json.dump({"rows": rows, "meta": meta}, open(os.path.join(OUTDIR, k + ".json"), "w"), allow_nan=False)
    print(k, len(rows), os.path.getsize(os.path.join(OUTDIR, k + ".json")))
