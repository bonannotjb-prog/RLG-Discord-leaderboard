# Read-only pull from WarCon's API: every match on both Redline servers with its per-player lines. GET requests only.
import json, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
key = open(sys.argv[1]).read().strip(); out = sys.argv[2]; O = "https://console.warcon.app"
def get(path):
    for i in range(6):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(O + path, headers={"Authorization": "Bearer " + key, "Accept": "application/json", "User-Agent": "curl/8.5.0"}), timeout=40))
        except urllib.error.HTTPError as e:
            if e.code in (429, 502, 503): time.sleep(int(e.headers.get("Retry-After", "3")) + 1 + i); continue
            return {"ok": False, "http": e.code}
        except Exception as e:
            time.sleep(2 + i)
    return {"ok": False, "http": "failed"}
first = get("/api/servers"); print("servers call:", {k: v for k, v in first.items() if k != "servers"}); servers = first["servers"]; res = {"fetchedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "servers": {}}
for s in servers:
    ms = []; page = 1
    while True:
        d = get(f"/api/servers/{s['id']}/matches?page={page}"); ms += d.get("matches", [])
        if page >= d.get("pages", 1): break
        page += 1
    with ThreadPoolExecutor(3) as ex: det = list(ex.map(lambda m: get(f"/api/servers/{s['id']}/matches/{m['id']}"), ms))
    bad = sum(1 for d in det if not d.get("ok"))
    res["servers"][s["id"]] = {"name": s["name"], "matches": ms, "lines": {str(m["id"]): d.get("lines", []) for m, d in zip(ms, det) if d.get("ok")}}
    print(s["name"], "matches", len(ms), "details failed", bad)
json.dump(res, open(out, "w"))
# the last 30 days of population and downtime for each server
import os
for s in servers:
    json.dump(get(f"/api/servers/{s['id']}/analytics?range=30d"), open(os.path.join(os.path.dirname(os.path.abspath(out)), f"an_{s['id']}.json"), "w"))
