# Read-only: hourly player counts since Season 3 launch for each North America community server.
import json, sys, time, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor
tok = open(sys.argv[1]).read().strip(); srv = json.load(open(sys.argv[2])); out = sys.argv[3]
def get(s):
    url = f"https://api.battlemetrics.com/servers/{s['id']}/player-count-history?" + urllib.parse.urlencode({"start": sys.argv[4], "stop": sys.argv[5], "resolution": "60"})
    for _ in range(6):
        try:
            d = json.load(urllib.request.urlopen(urllib.request.Request(url, headers={"Authorization": "Bearer " + tok}), timeout=40))
            return s["id"], [[p["attributes"]["timestamp"], p["attributes"].get("min"), p["attributes"].get("value"), p["attributes"].get("max")] for p in d["data"]]
        except urllib.error.HTTPError as e:
            if e.code == 429: time.sleep(int(e.headers.get("Retry-After", "5")) + 1); continue
            return s["id"], "http %d" % e.code
        except Exception as e:
            time.sleep(2)
    return s["id"], "failed"
with ThreadPoolExecutor(4) as ex: res = dict(ex.map(get, srv))
json.dump(res, open(out, "w"))
print("servers", len(res), "errors", sum(1 for v in res.values() if isinstance(v, str)), "points", sum(len(v) for v in res.values() if not isinstance(v, str)))
