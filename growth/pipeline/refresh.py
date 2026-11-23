# Weekly refresh for the Redline Gaming growth page. Pulls from BattleMetrics, WarCon and Steam (read-only), rebuilds every dataset
# and writes them to WORK/upload/ with a manifest. Nothing is sent anywhere by this script; uploading is a separate step.
#   usage:  BM_TOKEN=... WC_KEY=... GROWTH_ADMINS_FILE=... SITE_DATA=docs/data python3 refresh.py WORK
#           (WORK is any scratch folder; WORK/prev_history.json is used if present)
# The run stops with a non-zero exit code, and writes nothing to WORK/upload, if any pull or check fails.
import json, os, sys, subprocess, time, shutil, urllib.request, collections
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE); STATIC = os.path.join(ROOT, "static")
WORK = os.path.abspath(sys.argv[1]); os.makedirs(WORK, exist_ok=True); TZ = ZoneInfo("America/New_York")
J = lambda *a: os.path.join(WORK, *a); P = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")); iso = lambda d: d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
def run(name, *args):
    t = time.time(); print(f"--- {name}", flush=True)
    r = subprocess.run([sys.executable, os.path.join(HERE, name)] + list(args), capture_output=True, text=True)
    open(J("log_" + name + ".txt"), "w").write(r.stdout + "\n" + r.stderr)
    if r.returncode: print(r.stdout[-1500:], r.stderr[-2500:]); raise SystemExit(f"FAILED: {name}")
    print(f"    ok in {round(time.time() - t)}s:", (r.stdout.strip().splitlines() or [""])[-1][:200], flush=True)
def fetch(url, path):
    for i in range(5):
        try:
            data = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=40).read(); json.loads(data); open(path, "wb").write(data); return
        except Exception as e: err = e; time.sleep(3 + 2 * i)
    raise SystemExit(f"FAILED: could not read {url}: {err}")
if not os.environ.get("SKIP_FETCH"):      # SKIP_FETCH=1 rebuilds from the files already pulled into WORK (for testing)
    tok, key = J(".t"), J(".k")
    try:
        for path, env in ((tok, "BM_TOKEN"), (key, "WC_KEY")):
            if not os.environ.get(env): raise SystemExit(f"FAILED: {env} is not set")
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600); os.write(fd, os.environ[env].strip().encode()); os.close(fd)
        # the cut-off is the most recent midnight US Eastern, so every day in the data is a full day
        now = datetime.now(TZ); cut = now.replace(hour=0, minute=0, second=0, microsecond=0)
        # 1. Redline join and leave records, merged with any copy kept from earlier runs so nothing BattleMetrics drops is lost
        run("fetch2.py", J("raw_new.json"), tok)
        new = json.load(open(J("raw_new.json"))); arch = J("archive", "raw.json"); os.makedirs(J("archive"), exist_ok=True)
        for sid, s in new["servers"].items():
            if s["errors"] or len(s["sessions"]) < 1000: raise SystemExit(f"FAILED: BattleMetrics pull for {sid} looks incomplete ({len(s['sessions'])} sessions, {len(s['errors'])} errors)")
        if os.path.exists(arch):
            old = json.load(open(arch))
            for sid, s in new["servers"].items():
                m = {(x[0], x[4]): x for x in old["servers"].get(sid, {}).get("sessions", [])}; m.update({(x[0], x[4]): x for x in s["sessions"]}); s["sessions"] = list(m.values())
        json.dump(new, open(arch, "w"))
        raw = {"fetchedAt": iso(cut - timedelta(minutes=1)), "servers": {}}
        for sid, s in new["servers"].items():
            keep = [[a, (b if b and P(b) <= cut else iso(cut)), f, nm, pid] for a, b, f, nm, pid in s["sessions"] if P(a) < cut]
            raw["servers"][sid] = dict(s, sessions=keep)
        json.dump(raw, open(J("raw.json"), "w")); earliest = min(x[0] for s in raw["servers"].values() for x in s["sessions"])
        # 2. the rest of the pulls
        run("fetch_bench.py", tok, J("bench.json"))
        run("wc_fetch.py", key, J("api.json"))
        run("modes_fetch.py", tok, J("servers.json"))
        run("modes_hist30.py", tok, os.path.join(STATIC, "ids30.json"), J("hist30.json"), iso(cut - timedelta(days=30)), iso(cut))
        fetch("https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/?appid=1867240&count=500&maxlength=200&feeds=steam_community_announcements", J("news.json"))
        fetch("https://steamcharts.com/app/1867240/chart-data.json", J("charts.json"))
        fetch("https://store.steampowered.com/appreviewhistogram/1867240?l=english", J("hist.json"))
    finally:
        for path in (tok, key):
            if os.path.exists(path): os.remove(path)
earliest = min(x[0] for s_ in json.load(open(J("raw.json")))["servers"].values() for x in s_["sessions"])
h30 = json.load(open(J("hist30.json"))); bad = sum(1 for v in h30.values() if isinstance(v, str))
if bad > 10: raise SystemExit(f"FAILED: hourly counts failed for {bad} of {len(h30)} other servers")
wc = json.load(open(J("api.json")))
if len(wc["servers"]) < 2 or any(len(s["matches"]) < 100 for s in wc["servers"].values()): raise SystemExit("FAILED: WarCon pull looks incomplete")
# 3. rebuild everything
INF, VAN = "2dd77c71-dbb6-49c4-95cf-78625467aba1", "230a2cb3-e2af-450d-a1e1-515b08139d3d"; A = os.environ.get("GROWTH_ADMINS_FILE") or os.path.join(STATIC, "admins.json")   # the admin list is kept out of the repository
run("analyze2.py", J("raw.json"), J("a2"), J("bench.json"))
run("cx.py", J("raw.json"), J("api.json"), J(f"an_{INF}.json"), J(f"an_{VAN}.json"), J("cx"))
run("simple.py", J("raw.json"), J("api.json"), J("a2"), J("cx"), os.path.join(STATIC, "lens"), J("simple"))
run("deep.py", J("raw.json"), J("a2"), J("servers.json"), J("hist30.json"), J("deep"), A)
run("corr.py", J("raw.json"), A, J("charts.json"), J("hist.json"), os.path.join(STATIC, "discord_days.json"), J("hist30.json"), J("servers.json"), J("corr"))
run("steam.py", J("raw.json"), J("bench.json"), J("news.json"), J("charts.json"), J("steam"), os.path.join(STATIC, "events_manual.json"))
run("history.py", J("corr", "days.json"), J("deep", "attempt_log.json"), J("prev_history.json"), J("hist"))
# 4. collect the files the dashboard reads: dataset id -> file
SETS = {"simple": ["summary", "by_server", "seeders", "start_times", "arrivals", "stays", "leaving_hours", "leaving_other", "first_match", "weeks", "modes_na", "modes_world"],
        "deep": ["deep_summary", "factors", "tipping", "stages", "stalls", "speed", "who", "funnel", "by_month=bench", "peers", "tactics", "admin_roles"],
        "corr": ["links", "links_ours", "links_not", "peer_links", "reviews", "share", "admin_days", "corr_summary"],
        "steam": ["events", "patches", "steam"], "hist": ["history", "history_now", "status"]}
up = J("upload"); shutil.rmtree(up, ignore_errors=True); os.makedirs(up); manifest = []
for folder, names in SETS.items():
    for n in names:
        ds, src = (n.split("=") + [n])[:2]; d = json.load(open(J(folder, src + ".json")))
        if not d["rows"]: raise SystemExit(f"FAILED: {ds} came out empty")
        if "7656119" in json.dumps(d["rows"]): raise SystemExit(f"FAILED: {ds} contains a game account number")
        shutil.copy(J(folder, src + ".json"), os.path.join(up, ds + ".json")); manifest.append({"dataset": ds, "file": os.path.join(up, ds + ".json"), "rows": len(d["rows"])})
st = json.load(open(J("hist", "status.json")))["rows"][0]; sm = json.load(open(J("simple", "summary.json")))["rows"][0]
report = {"ok": True, "data_through": st["data_through"], "earliest_session": earliest[:10], "history_weeks": st["weeks"], "last_week": st["last_week"], "seed_attempts": sm["attempts"], "days_live": sm["days_live"], "of_days": sm["days"],
          "other_servers_with_no_hourly_counts": bad, "datasets": len(manifest), "warnings": []}
if earliest[:10] > "2026-09-16": report["warnings"].append(f"BattleMetrics now only returns sessions from {earliest[:10]}; all-time tables start there, and older weeks in the history are kept from earlier runs")
json.dump({"report": report, "datasets": manifest}, open(J("manifest.json"), "w"), indent=1)
site = os.environ.get("SITE_DATA")      # the web page reads plain row files from this folder
if site:
    os.makedirs(site, exist_ok=True)
    for m in manifest: json.dump({"rows": json.load(open(m["file"]))["rows"]}, open(os.path.join(site, m["dataset"] + ".json"), "w"), separators=(",", ":"))
print("\nDONE", json.dumps(report))
