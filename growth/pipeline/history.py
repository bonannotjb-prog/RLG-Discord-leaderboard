# The weekly history for the Redline Gaming dashboard: one row per full Monday-to-Sunday week (US Eastern), kept for good.
# Inputs: the day table from corr.py, the attempt log from deep.py, and the history saved by the last run (if any).
#   a week is recomputed whenever the session records still cover all of it; a week they no longer cover keeps the row saved earlier
#   days live            days that week with 20 or more players on at least one Redline server for an hour or more
#   typical minutes      the middle time from first join to 20 players, over that week's attempts that reached 20
#   per 10,000           Redline players a day for every 10,000 players in the game at its daily high on Steam
#   admins who seeded    admins on the team at the time who spent 10 minutes or more on a server before it reached 20
#   last 4 against 4     the last four full weeks beside the four before them
import json, sys, os, statistics, collections
from datetime import date, timedelta
DAYS, LOG, PREV, OUT = sys.argv[1:5]
CODE = open(os.path.abspath(__file__)).read(); r1 = lambda x: None if x is None else round(x, 1); med = lambda v: statistics.median(v) if v else None
days = {date.fromisoformat(r["date"]): r for r in json.load(open(DAYS))["rows"]}; log = json.load(open(LOG))["rows"]
first, last = min(days), max(days); monday = lambda d: d - timedelta(days=d.weekday())
rows = {}
if os.path.exists(PREV):
    for r in json.load(open(PREV))["rows"]: rows[r["week"]] = dict(r, kept=1)
w = monday(first) if monday(first) == first else monday(first) + timedelta(days=7)
while w + timedelta(days=6) <= last:
    ds = [days[w + timedelta(days=k)] for k in range(7)]; at = [a for a in log if w <= date.fromisoformat(a["date"]) <= w + timedelta(days=6)]; ok = [a for a in at if a["ok"]]
    st = [d["steam_players"] for d in ds if d.get("steam_players")]; pl = statistics.mean(d["players"] for d in ds); sh = sum(a["seeding_hours"] for a in at)
    rows[w.isoformat()] = {"week": w.isoformat(), "label": w.strftime("%b %-d"), "full_week": 1, "days_live": sum(1 for d in ds if d["live_hours"] >= 1), "live_hours_per_day": r1(statistics.mean(d["live_hours"] for d in ds)),
        "attempts": len(at), "reached_20": len(ok), "reached_20_pct": r1(100 * len(ok) / len(at)) if at else None, "typical_minutes_to_20": r1(med([a["tts"] for a in ok])),
        "players_per_day": round(pl), "steam_players": round(statistics.mean(st)) if st else None, "per_10000": r1(10000 * pl / statistics.mean(st)) if st else None,
        "admins_seeded": len({p for a in at for p in a["admins"]}), "admin_share_pct": r1(100 * sum(a["admin_hours"] for a in at) / sh) if sh else None, "kept": 0}
    w += timedelta(days=7)
hist = [rows[k] for k in sorted(rows)]
out = {"history": hist}
MET = [("days_live", "Days live a week", "of 7"), ("live_hours_per_day", "Live hours a day", "hours"), ("reached_20_pct", "Seed attempts that reached 20", "percent"), ("typical_minutes_to_20", "Typical minutes to reach 20", "minutes"),
       ("players_per_day", "Players a day", "players"), ("per_10000", "Redline players per 10,000 in the game", "per 10,000"), ("admins_seeded", "Admins who seeded", "admins a week")]
l4, p4 = hist[-4:], hist[-8:-4]; now = []
for i, (k, lab, unit) in enumerate(MET):
    a = [r[k] for r in l4 if r.get(k) is not None]; b = [r[k] for r in p4 if r.get(k) is not None]
    la, pb = (r1(statistics.mean(a)) if a else None), (r1(statistics.mean(b)) if b else None)
    now.append({"order": i, "key": k, "measure": lab, "unit": unit, "last_4_weeks": la, "the_4_before": pb, "change": r1(la - pb) if la is not None and pb is not None else None,
                "direction": None if la is None or pb is None else "up" if la > pb else "down" if la < pb else "level"})
out["history_now"] = now
out["status"] = [{"data_from": first.isoformat(), "data_through": last.isoformat(), "weeks": len(hist), "first_week": hist[0]["label"] if hist else None, "last_week": hist[-1]["label"] if hist else None,
                  "weeks_kept_from_earlier_runs": sum(r["kept"] for r in hist), "last4_from": l4[0]["label"] if l4 else None, "last4_to": l4[-1]["label"] if l4 else None}]
for r in now: out["status"][0][r["key"] + "_last4"] = r["last_4_weeks"]; out["status"][0][r["key"] + "_prev4"] = r["the_4_before"]
meta = [{"label": "history.py", "value": CODE, "type": "code", "lang": "python"}, {"label": "Input", "value": "Day table and attempt log built from BattleMetrics session records and Steam player counts, through " + last.isoformat()}]
os.makedirs(OUT, exist_ok=True)
for k, v in out.items(): json.dump({"rows": v, "meta": meta}, open(os.path.join(OUT, k + ".json"), "w"), allow_nan=False)
print("weeks", len(hist), "from", hist[0]["week"], "to", hist[-1]["week"]); print(json.dumps(hist[-1])); 
for r in now: print("  ", r["measure"], r["last_4_weeks"], "against", r["the_4_before"])
