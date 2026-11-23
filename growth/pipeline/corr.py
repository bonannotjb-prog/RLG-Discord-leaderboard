# What moves Redline's numbers: one row per day, then how strongly each factor goes with Redline's live hours that day.
# Sources (all read-only or public): BattleMetrics join and leave records for the two Redline servers and hourly counts for the
#   busiest North America community servers; Steam player counts (steamcharts.com) and Steam's review history for WARDOGS;
#   the Redline Discord export of November 19 (message counts and times only, no message text); the admin roster.
#   day                 a US Eastern calendar day
#   live hours          hours that day with 20 or more players on at least one Redline server
#   link                Spearman rank correlation between the factor and live hours over the days both exist (-1 to 1)
#   after the game      the same link once the game's own daily player count is taken out of both sides (rank partial correlation)
#   strength            0.5 or more strong, 0.3 to 0.5 moderate, 0.15 to 0.3 weak, under 0.15 none
#   a link is not a cause: admins and chat are busier on days the server is live partly because it is live
import json, sys, os, collections, statistics, math, re
from datetime import datetime, timedelta, date, timezone
from zoneinfo import ZoneInfo
RAW, ADMINS, CHARTS, HIST, DISCORD, H30, SNAP, OUT = sys.argv[1:9]
TZ = ZoneInfo("America/New_York"); CODE = open(os.path.abspath(__file__)).read()
P = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")); r1 = lambda x: None if x is None else round(x, 1); r2 = lambda x: None if x is None else round(x, 2)
raw = json.load(open(RAW)); NOW = P(raw["fetchedAt"]); LAST = NOW.astimezone(TZ).date(); FIRST = date(2026, 9, 15)
DAYS = [FIRST + timedelta(days=k) for k in range((LAST - FIRST).days + 1)]
T0 = datetime(2026, 9, 12, tzinfo=timezone.utc); N = int((NOW - T0).total_seconds() // 60); mi = lambda t: min(N, max(0, int((t - T0).total_seconds() // 60)))
BM = {"RLG Infantry": "41243237", "RLG Vanilla": "41199298"}
cnt = {}; sess = []
for server, sid in BM.items():
    diff = [0] * (N + 2)
    for a, b, f, nm, pid in raw["servers"][sid]["sessions"]:
        a = P(a); e = P(b) if b else NOW
        if e - a > timedelta(hours=4): e = a + timedelta(minutes=40)
        i, j = mi(a), mi(e)
        if j > i: diff[i] += 1; diff[j] -= 1
        sess.append((server, a, e, pid, nm))
    c = 0; arr = []
    for x in diff[:N]: c += x; arr.append(c)
    cnt[server] = arr
D = {d: collections.defaultdict(float) for d in DAYS}
for k in range(N):
    d = (T0 + timedelta(minutes=k)).astimezone(TZ).date()
    if d in D and max(cnt["RLG Infantry"][k], cnt["RLG Vanilla"][k]) >= 20: D[d]["live_hours"] += 1 / 60
# the admin team, as BattleMetrics player numbers with the dates each person was on the team (static/admins.json, made from the roster sheet)
ADMIN = {a["player"]: (date.fromisoformat(a["start"]), date.fromisoformat(a["end"]) if a["end"] else None) for a in json.load(open(ADMINS))["admins"]}
admin_at = lambda p, d: p in ADMIN and ADMIN[p][0] <= d and (ADMIN[p][1] is None or d < ADMIN[p][1])
players = collections.defaultdict(set); adm = collections.defaultdict(set); adm_seed = collections.defaultdict(lambda: collections.Counter())
for server, a, e, pid, nm in sess:
    d = a.astimezone(TZ).date()
    if d not in D: continue
    players[d].add(pid)
    if admin_at(pid, d):
        adm[d].add(pid); adm_seed[d][pid] += sum(1 for k in range(mi(a), mi(e)) if cnt[server][k] < 20)
for d in DAYS:
    D[d]["players"] = len(players[d]); D[d]["admins_on"] = len(adm[d]); D[d]["admins_seeding"] = sum(1 for p, m in adm_seed[d].items() if m >= 10)
    D[d]["weekend"] = int(d.weekday() >= 5)
for i, d in enumerate(DAYS):
    if i: D[d]["live_yesterday"] = D[DAYS[i - 1]]["live_hours"]
# Steam: players in the game (daily high), and reviews
steam = collections.defaultdict(list)
pts_ = [(datetime.fromtimestamp(t / 1000, timezone.utc), v) for t, v in json.load(open(CHARTS))]; per_utc = collections.Counter(t.date() for t, v in pts_)
for t, v in pts_:     # older history is one figure per UTC day, stamped at midnight: it belongs to that date. The last 30 days are hourly and go by Eastern day.
    steam[t.date() if per_utc[t.date()] == 1 and t.hour == 0 else t.astimezone(TZ).date()].append(v)
for d in DAYS:
    if steam.get(d): D[d]["steam_players"] = max(steam[d])
h = json.load(open(HIST))["results"]; wk = {}
for r in h["rollups"]:
    w0 = datetime.fromtimestamp(r["date"], timezone.utc).date()
    for k in range(7): wk[w0 + timedelta(days=k)] = (r["recommendations_up"], r["recommendations_down"])
for d in DAYS:
    if d in wk and sum(wk[d]): D[d]["review_positive_pct"] = 100 * wk[d][0] / sum(wk[d]); D[d]["reviews_week"] = sum(wk[d])
# Discord: message counts per day from a saved export (static/discord_days.json); counts only
dd = json.load(open(DISCORD)); d0, d1, dc0 = date.fromisoformat(dd["first"]), date.fromisoformat(dd["last"]), date.fromisoformat(dd["seed_calls_from"])
for d in DAYS:
    if d0 <= d <= d1:
        v = dd["days"].get(d.isoformat(), {}); D[d]["discord_community"] = v.get("community", 0); D[d]["discord_staff"] = v.get("staff", 0)
        if d >= dc0: D[d]["seed_call_by_person"] = v.get("seed_call_by_person", 0)
# the busiest NA community servers: the typical live hours of the others, each day
snap = {r["id"]: r for r in json.load(open(SNAP))["rows"]}; h30 = json.load(open(H30)); peer = collections.defaultdict(dict)
for sid, pts in h30.items():
    if sid in BM.values() or isinstance(pts, str) or len(pts) < 24 * 20: continue
    for t, mn, v, mx in pts:
        d = P(t).astimezone(TZ).date()
        peer[d][sid] = peer[d].get(sid, 0) + (1 if (v or 0) >= 20 else 0)
for d in DAYS:
    if len(peer.get(d, {})) >= 30 and sum(1 for x in peer if x <= d) > 1: D[d]["peers_live_hours"] = statistics.median(peer[d].values())
# ---- links
def rank(xs):
    o = sorted(range(len(xs)), key=lambda i: xs[i]); r = [0.0] * len(xs); i = 0
    while i < len(o):
        j = i
        while j + 1 < len(o) and xs[o[j + 1]] == xs[o[i]]: j += 1
        for k in range(i, j + 1): r[o[k]] = (i + j) / 2
        i = j + 1
    return r
def pear(x, y):
    mx, my = sum(x) / len(x), sum(y) / len(y); sx = math.sqrt(sum((a - mx) ** 2 for a in x)); sy = math.sqrt(sum((b - my) ** 2 for b in y))
    return 0.0 if not sx or not sy else sum((a - mx) * (b - my) for a, b in zip(x, y)) / (sx * sy)
spear = lambda x, y: pear(rank(x), rank(y))
def partial(x, y, z):
    rxy, rxz, ryz = spear(x, y), spear(x, z), spear(y, z); den = math.sqrt(max(1e-9, (1 - rxz ** 2) * (1 - ryz ** 2))); return (rxy - rxz * ryz) / den
FACT = [("steam_players", "Players in the game that day (Steam)", "The game", "Not ours to change"),
        ("review_positive_pct", "Share of Steam reviews that were positive that week", "The game", "Not ours to change"),
        ("peers_live_hours", "Live hours on the other busy North America servers that day", "Other servers", "Not ours to change"),
        ("weekend", "It was a weekend", "The calendar", "Not ours to change"),
        ("admins_seeding", "Admins on a server before it reached 20", "The admin team", "Ours to change"),
        ("admins_on", "Admins who played that day", "The admin team", "Ours to change"),
        ("seed_call_by_person", "A person posted a seed call in Discord", "Discord", "Ours to change"),
        ("discord_community", "Messages in the community Discord channels", "Discord", "Ours to change"),
        ("discord_staff", "Messages in the admin Discord channels", "Discord", "Ours to change"),
        ("live_yesterday", "Live hours the day before", "Momentum", "Ours to change")]
SHORT = {"steam_players": "Players in the game (Steam)", "review_positive_pct": "Positive Steam reviews", "peers_live_hours": "Other busy servers' live hours", "weekend": "A weekend",
         "admins_seeding": "Admins seeding before 20", "admins_on": "Admins playing that day", "seed_call_by_person": "A person posted a seed call", "discord_community": "Community Discord messages",
         "discord_staff": "Admin Discord messages", "live_yesterday": "Live the day before"}
strength = lambda r: "Strong" if abs(r) >= .5 else "Moderate" if abs(r) >= .3 else "Weak" if abs(r) >= .15 else "None"
out = collections.defaultdict(list)
for key, label, group, control in FACT:
    ds = [d for d in DAYS if key in D[d]]; x = [D[d][key] for d in ds]; y = [D[d]["live_hours"] for d in ds]
    r = spear(x, y); dz = [d for d in ds if "steam_players" in D[d]]
    rp = None if key == "steam_players" else partial([D[d][key] for d in dz], [D[d]["live_hours"] for d in dz], [D[d]["steam_players"] for d in dz])
    hi = [D[d]["live_hours"] for d in ds if D[d][key] > statistics.median(x)] if key not in ("weekend", "seed_call_by_person") else [D[d]["live_hours"] for d in ds if D[d][key] == 1]
    lo = [D[d]["live_hours"] for d in ds if D[d][key] <= statistics.median(x)] if key not in ("weekend", "seed_call_by_person") else [D[d]["live_hours"] for d in ds if D[d][key] == 0]
    out["links"].append({"short": SHORT[key], "factor": label, "group": group, "control": control, "days": len(ds), "link": r2(r), "link_size": r2(abs(r)), "strength": strength(r), "direction": "more live hours" if r > 0 else "fewer live hours",
                         "link_after_game": r2(rp), "strength_after_game": None if rp is None else strength(rp),
                         "live_hours_when_high": r1(statistics.mean(hi)) if hi else None, "live_hours_when_low": r1(statistics.mean(lo)) if lo else None})
out["links"].sort(key=lambda r: -r["link_size"])
for i, r in enumerate(out["links"]): r["order"] = i
out["links_ours"] = [r for r in out["links"] if r["control"] == "Ours to change"]; out["links_not"] = [r for r in out["links"] if r["control"] != "Ours to change"]
# reviews by week, beside the game and Redline
for r in h["rollups"]:
    w0 = datetime.fromtimestamp(r["date"], timezone.utc).date(); ds = [w0 + timedelta(days=k) for k in range(7)]; ds = [d for d in ds if d in D]
    if len(ds) < 3: continue
    up, dn = r["recommendations_up"], r["recommendations_down"]
    out["reviews"].append({"week": w0.isoformat(), "label": w0.strftime("%b %-d"), "reviews": up + dn, "positive_pct": r1(100 * up / (up + dn)), "full_week": int(len(ds) == 7),
                           "steam_players": round(statistics.mean([D[d]["steam_players"] for d in ds if "steam_players" in D[d]])), "redline_live_hours_per_day": r1(statistics.mean([D[d]["live_hours"] for d in ds])),
                           "redline_players_per_day": round(statistics.mean([D[d]["players"] for d in ds]))})
# the day table
for d in DAYS:
    v = D[d]; out["days"].append({"date": d.isoformat(), "live_hours": r1(v["live_hours"]), "players": int(v["players"]), "steam_players": int(v["steam_players"]) if "steam_players" in v else None,
                                  "admins_on": int(v["admins_on"]), "admins_seeding": int(v["admins_seeding"]), "peers_live_hours": v.get("peers_live_hours"),
                                  "discord_community": int(v["discord_community"]) if "discord_community" in v else None})
# Redline's share of the game, by month, and the split by admins seeding
for m in sorted({(d.year, d.month) for d in DAYS}):
    ds = [d for d in DAYS if (d.year, d.month) == m and "steam_players" in D[d]]
    if len(ds) < 7: continue
    out["share"].append({"month": date(m[0], m[1], 1).strftime("%b %Y"), "order": m[0] * 12 + m[1], "steam_players": round(statistics.mean([D[d]["steam_players"] for d in ds])), "redline_players_per_day": round(statistics.mean([D[d]["players"] for d in ds])),
                         "redline_per_10000": r1(10000 * statistics.mean([D[d]["players"] for d in ds]) / statistics.mean([D[d]["steam_players"] for d in ds])), "days_live_pct": r1(100 * sum(1 for d in ds if D[d]["live_hours"] >= 1) / len(ds)),
                         "live_hours_per_day": r1(statistics.mean([D[d]["live_hours"] for d in ds]))})
for i, (lab, lo_, hi_) in enumerate((("No admin", 0, 0), ("1 admin", 1, 1), ("2 or 3 admins", 2, 3), ("4 or more admins", 4, 99))):
    ds = [d for d in DAYS if lo_ <= D[d]["admins_seeding"] <= hi_]
    if ds: out["admin_days"].append({"order": i, "admins_seeding": lab, "days": len(ds), "went_live_pct": r1(100 * sum(1 for d in ds if D[d]["live_hours"] >= 1) / len(ds)) if ds else None,
                              "live_hours": r1(statistics.mean([D[d]["live_hours"] for d in ds])) if ds else None})
L = {r["factor"]: r for r in out["links"]}; g = lambda k: next(r for r in out["links"] if r["factor"].startswith(k))
out["corr_summary"] = [{"days": len(DAYS), "strongest_ours": out["links_ours"][0]["factor"], "strongest_not": out["links_not"][0]["factor"],
                        "steam_link": g("Players in the game")["link"], "admins_link": g("Admins on a server")["link"], "admins_link_after_game": g("Admins on a server")["link_after_game"],
                        "peers_link": g("Live hours on the other")["link"], "reviews_link": g("Share of Steam reviews")["link"], "weekend_link": g("It was a weekend")["link"],
                        "review_low_pct": min(r["positive_pct"] for r in out["reviews"]), "review_low_week": min(out["reviews"], key=lambda r: r["positive_pct"])["label"],
                        "share_first": out["share"][0]["redline_per_10000"], "share_first_month": out["share"][0]["month"], "share_last": out["share"][-1]["redline_per_10000"], "share_last_month": out["share"][-1]["month"],
                        "live_no_admin_pct": out["admin_days"][0]["went_live_pct"], "live_4_admin_pct": out["admin_days"][-1]["went_live_pct"], "days_no_admin": out["admin_days"][0]["days"], "days_4_admin": out["admin_days"][-1]["days"],
                        "steam_first": out["share"][0]["steam_players"], "steam_last": out["share"][-1]["steam_players"], "momentum_link": g("Live hours the day before")["link"], "admins_on_link": g("Admins who played")["link"],
                        "discord_link": g("Messages in the community")["link"], "discord_link_after_game": g("Messages in the community")["link_after_game"], "call_link": g("A person posted")["link"]}]
# across the other busy NA community servers: what goes with more live hours a day (one row per server, 30 days)
PS = []
for sid, pts in h30.items():
    s_ = snap.get(sid)
    if not s_ or sid in BM.values() or isinstance(pts, str) or len(pts) < 24 * 20: continue
    day = collections.defaultdict(list)
    for t, mn, v, mx in pts:
        loc = P(t).astimezone(TZ); day[(loc - timedelta(hours=6)).date()].append(((loc.hour - 6) % 24, v or 0))
    first = [min(hh for hh, v in hv if v >= 20) for hv in day.values() if any(v >= 20 for hh, v in hv)]
    w = s_["details"]["wardogs"]; e = " ".join(w.get("experiences") or []) + " " + (w.get("experience") or ""); nmx = s_["name"].lower()
    PS.append({"hours": sum(1 for hv in day.values() for hh, v in hv if v >= 20) / len(day), "early": -statistics.median(first) if first else None,
               "boost": int(bool(re.search(r"boost|2x|3x|xp|\$", nmx))), "mode": int("limited" in nmx or "Limited" in e or "Infantry" in e or bool(re.search(r"\binf\b|infantry", nmx))),
               "reserved": w.get("reservedPlayerCapacity") or 0, "cap": int(0 < (w.get("maxWardogLevel") or 0) <= 100)})
for key, short, label in (("early", "Goes live earlier in the day", "The server usually reaches 20 earlier in the day"), ("mode", "Runs Limited or Infantry Only", "Runs Limited or Infantry Only, not Vanilla"),
                          ("boost", "Advertises boosted cash or XP", "Advertises boosted cash or XP in its name"), ("reserved", "More reserved slots", "Holds more reserved slots"), ("cap", "Has a low level cap", "Only admits players up to level 100 or below")):
    v = [p for p in PS if p[key] is not None]; r = spear([p[key] for p in v], [p["hours"] for p in v])
    out["peer_links"].append({"short": short, "factor": label, "servers": len(v), "link": r2(r), "link_size": r2(abs(r)), "strength": strength(r), "direction": "more live hours" if r > 0 else "fewer live hours"})
out["peer_links"].sort(key=lambda r: -r["link_size"])
meta = [{"label": "corr.py", "value": CODE, "type": "code", "lang": "python"},
        {"label": "Input", "value": "BattleMetrics (Redline sessions through " + raw["fetchedAt"] + "; hourly counts for the busiest NA community servers), Steam player counts from steamcharts.com and Steam's review history the Redline Discord export of November 19 (counts only), and the admin roster"}]
for k in ("links", "peer_links"):
    for r in out[k]: r["reading"] = "None" if r["strength"] == "None" else r["strength"] + (", more" if r["link"] > 0 else ", fewer")
os.makedirs(OUT, exist_ok=True)
for k, rows in out.items():
    json.dump({"rows": rows, "meta": meta}, open(os.path.join(OUT, k + ".json"), "w"), allow_nan=False)
    if k != "days":
        print("==", k)
        for r in rows: print("  ", json.dumps(r))
