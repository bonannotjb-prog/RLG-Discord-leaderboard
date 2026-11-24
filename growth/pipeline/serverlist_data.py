# Server List figures for the growth page: where each Redline server ranks among community servers, week by week,
# Redline's share of North American community players, when North America plays, when seeds work, and Infantry Only
# against the other rule sets. Read from WARDOGS Server List (wardogserverlist.com, CC BY 4.0) with a read-only key.
#   usage:  WARDOGSERVERLIST_API_KEY=... python3 serverlist_data.py docs/data
# This script never fails the build. Without the key, or if the site cannot be read, it leaves the files already in
# docs/data as they are. sl_weeks.json is a kept history: each run adds the newest full week and keeps earlier ones,
# so the page still has them after the site stops listing them.
import json, os, sys, time, urllib.request, statistics, collections
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
import serverlist as sl
OUT = os.path.abspath(sys.argv[1]); ET = ZoneInfo("America/New_York"); NA = ["na-west", "na-east", "na-central", "na-north"]
WARCON = os.environ.get("WARCON_ORIGIN", "https://console.warcon.app").rstrip("/")
# WarCon ids, in the order the page shows them. The Server List key is built from the join code WarCon reports.
SERVERS = [("Vanilla", "van", "230a2cb3-e2af-450d-a1e1-515b08139d3d"), ("Infantry", "inf", "2dd77c71-dbb6-49c4-95cf-78625467aba1")]
r1 = lambda x: None if x is None else round(x, 1); utc = lambda ms: datetime.fromtimestamp(ms / 1000, timezone.utc)
def save(name, rows):
    if not rows: print(f"  {name}: nothing to write, keeping the file already there"); return
    json.dump({"rows": rows}, open(os.path.join(OUT, name + ".json"), "w"), separators=(",", ":"), allow_nan=False); print(f"  {name}: {len(rows)} rows")
def prev(name):
    try: return json.load(open(os.path.join(OUT, name + ".json")))["rows"]
    except Exception: return []
def join_code(wid):
    try:
        req = urllib.request.Request(f"{WARCON}/api/public/servers/{wid}", headers={"User-Agent": sl.UA, "Accept": "application/json"})
        return json.loads(urllib.request.urlopen(req, timeout=20).read()).get("server", {}).get("joinCode")
    except Exception: return None
def main():
    if not sl.enabled(): print("WARDOGSERVERLIST_API_KEY is not set: Server List figures left as they are."); return
    os.makedirs(OUT, exist_ok=True); now_ms = time.time() * 1000
    old_keys = {r["id"]: r.get("key") for r in prev("sl_rank")}     # used if WarCon cannot be read this time
    keys = {sid: (sl.server_key(join_code(wid)) or old_keys.get(sid)) for _, sid, wid in SERVERS}
    # 1. where each server stands now: the 7-day board and the season so far
    rank = []
    for name, sid, _ in SERVERS:
        k = keys[sid]
        if not k: continue
        st = sl.standing(k) or {}; se = sl.get("/api/season-server", key=k) or {}; sv = se.get("server") or {}
        if not st and not sv: continue
        rank.append({"id": sid, "key": k, "server": name, "rank_7d": st.get("rank"), "of_7d": st.get("of"), "region": st.get("region") or sl.region_name(sv.get("region")),
                     "region_rank_7d": st.get("region_rank"), "region_of_7d": st.get("region_of"), "season": se.get("name"), "season_rank": sv.get("rank"), "season_of": se.get("of"),
                     "season_region_rank": sv.get("zoneRank"), "season_avg_players": sv.get("avgPlayers"), "season_online_pct": sv.get("uptimePct"), "season_full_pct": sv.get("fullPct")})
    save("sl_rank", rank)
    # 2. week by week (Server List weeks run Monday to Sunday, UTC); only full weeks, merged into the kept history
    weeks = {r["week"]: dict(r, kept=1) for r in prev("sl_weeks")}
    periods = {utc(p["start"]).date().isoformat(): p for p in (sl.get("/api/region-periods", by="week") or {}).get("periods") or [] if p.get("hours", 0) >= 160}
    fresh = collections.defaultdict(dict)
    for _, sid, _ in SERVERS:
        det = (sl.get("/api/server-detail", key=keys[sid], window="30d") or {}) if keys[sid] else {}
        for w in det.get("weeks") or []:
            # full weeks only, and not the week the site first saw the server part way through
            if w.get("week") and w["week"] + 7 * 86400000 <= now_ms and w.get("rank") and (det.get("firstSeen") or 0) <= w["week"]:
                fresh[utc(w["week"]).date().isoformat()][sid] = w
    for wk, got in fresh.items():
        if len(got) < len([1 for _, sid, _ in SERVERS if keys[sid]]): continue   # a week only counts once every server has a rank for it
        d = datetime.fromisoformat(wk); row = {"week": wk, "label": d.strftime("%b %-d"), "full_week": 1, "kept": 0}
        for _, sid, _ in SERVERS:
            w = got.get(sid) or {}
            row.update({f"{sid}_rank": w.get("rank"), f"{sid}_of": w.get("of"), f"{sid}_top_pct": r1(100 * w["rank"] / w["of"]) if w.get("rank") and w.get("of") else None,
                        f"{sid}_avg_players": w.get("avgPlayers"), f"{sid}_online_pct": w.get("windowSharePct")})
        both = [got[sid].get("avgPlayers") or 0 for _, sid, _ in SERVERS if sid in got]; row["redline_avg_players"] = r1(sum(both)) if both else None
        p = periods.get(wk)
        if p:
            com = [(p["zones"].get(z) or {}).get("community") for z in NA]; com = sum(c for c in com if c)
            row["na_community_players"] = com or None; row["world_community_players"] = (p.get("world") or {}).get("community")
            row["per_1000_na"] = r1(1000 * sum(both) / com) if com and both else None
        elif wk in weeks:   # the site no longer lists this week's region totals: keep the ones saved earlier
            for f in ("na_community_players", "world_community_players", "per_1000_na"): row[f] = weeks[wk].get(f)
        weeks[wk] = row
    hist = [weeks[k] for k in sorted(weeks)]; save("sl_weeks", hist)
    # 3. hour of the day, US Eastern: players on North American community servers (30 days) and how seeds started then went (14 days)
    rh = sl.get("/api/region-hours", window="30d") or {}; by_hour = collections.defaultdict(list)
    for i, ts in enumerate(rh.get("hours") or []):
        vals = [(rh["zones"].get(z) or {}).get("community") for z in NA]; vals = [v[i] for v in vals if v and i < len(v) and v[i] is not None]
        if vals: by_hour[utc(ts).astimezone(ET).hour].append(sum(vals))
    seeds = {}
    found = next((f for f in (sl.seed_hours(keys[sid]) for _, sid, _ in SERVERS if keys[sid]) if f), None)
    if found:
        region, tz, hrs = found; base = datetime.now(ZoneInfo(tz)).replace(minute=0, second=0, microsecond=0)
        for h, (a, b) in enumerate(hrs): seeds[base.replace(hour=h).astimezone(ET).hour] = (a, b, region)
    hours = []
    for h in range(24):
        a, b, region = seeds.get(h, (None, None, None))
        if not by_hour.get(h) and a is None: continue
        hours.append({"hour": f"{sl.hour_label(h)} to {sl.hour_label((h + 1) % 24)}", "order": h, "na_community_players": round(statistics.mean(by_hour[h])) if by_hour.get(h) else None,
                      "seeds_started": a, "seeds_reached_20": b, "reached_20_pct": round(100 * b / a) if a and a >= 8 else None, "seed_region": region})
    save("sl_hours", hours)
    # 4. Infantry Only against every other rule set, North American community servers, average over 30 days
    inf = sl.get("/api/infantry", window="30d", zones=",".join(NA)) or {}; cols = inf.get("cols") or []; rows = inf.get("rows") or []
    modes = []
    if rows and {"infPlayers", "comPlayers", "infServers", "comServers"} <= set(cols):
        avg = lambda c: statistics.mean(r[cols.index(c)] or 0 for r in rows)
        ip, cp, isv, csv_ = avg("infPlayers"), avg("comPlayers"), avg("infServers"), avg("comServers")
        for name, pl, sv in (("Infantry Only", ip, isv), ("Every other rule set", cp - ip, csv_ - isv)):
            modes.append({"mode": name, "servers": round(sv), "players": round(pl), "players_per_server": r1(pl / sv) if sv else None,
                          "share_of_servers_pct": r1(100 * sv / csv_) if csv_ else None, "share_of_players_pct": r1(100 * pl / cp) if cp else None})
    save("sl_modes", modes)
    # 5. one row saying how fresh all of this is
    if rank or fresh:
        save("sl_status", [{"updated": datetime.now(ET).date().isoformat(), "weeks": len(hist), "first_week": hist[0]["label"] if hist else None, "last_week": hist[-1]["label"] if hist else None,
                            "weeks_kept_from_earlier_runs": sum(r.get("kept", 0) for r in hist), "seed_region": (found or [None])[0]}])
    print("DONE Server List figures")
if __name__ == "__main__":
    try: main()
    except Exception as e: print(f"Server List figures skipped ({type(e).__name__}: {e}); the files already in place are kept.")
