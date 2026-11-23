# Read-only: list every Wardogs server on BattleMetrics with its current player count. Token read from argv file.
import json, sys, time, urllib.request, urllib.parse
tok = open(sys.argv[1]).read().strip(); out = sys.argv[2]
url = "https://api.battlemetrics.com/servers?" + urllib.parse.urlencode({"filter[game]": "wardogs", "page[size]": "100", "sort": "-players"})
rows = []; n = 0
while url and n < 60:
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + tok})
    try:
        d = json.load(urllib.request.urlopen(req, timeout=40))
    except urllib.error.HTTPError as e:
        if e.code == 429: time.sleep(int(e.headers.get("Retry-After", "5")) + 1); continue
        print("http", e.code, e.read()[:200]); break
    for s in d["data"]:
        a = s["attributes"]; rows.append({"id": s["id"], "name": a["name"], "players": a["players"], "max": a["maxPlayers"], "rank": a.get("rank"),
                                          "status": a.get("status"), "country": a.get("country"), "details": a.get("details")})
    url = d.get("links", {}).get("next"); n += 1
    if d["data"] and d["data"][-1]["attributes"]["players"] == 0 and n >= 3 and all(x["attributes"]["players"] == 0 for x in d["data"]): 
        # keep going a few pages to count empty servers too
        pass
json.dump({"fetchedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "rows": rows}, open(out, "w"))
print("pages", n, "servers", len(rows), "more", bool(url))
