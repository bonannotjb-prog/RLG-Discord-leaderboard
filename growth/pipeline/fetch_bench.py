# Read-only: BattleMetrics' game-wide daily player counts for Wardogs and each Redline server's daily rank.
import json, sys, time, urllib.request, urllib.parse
from datetime import datetime, timedelta, timezone
tok = open(sys.argv[1]).read().strip(); out = sys.argv[2]; API = "https://api.battlemetrics.com"
def get(url):
    for i in range(6):
        try: return json.load(urllib.request.urlopen(urllib.request.Request(url, headers={"Authorization": "Bearer " + tok}), timeout=40))
        except urllib.error.HTTPError as e:
            if e.code == 429: time.sleep(int(e.headers.get("Retry-After", "5")) + 1); continue
            raise
        except Exception: time.sleep(2 + i)
    raise SystemExit("gave up on " + url)
iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ"); start = datetime(2026, 9, 10, tzinfo=timezone.utc); now = datetime.now(timezone.utc).replace(microsecond=0)
game = {}; rank = {"41243237": {}, "41199298": {}}; a = start
while a < now:
    b = min(a + timedelta(days=60), now)
    d = get(API + "/metrics?" + urllib.parse.urlencode({"metrics[0][name]": "games.wardogs.players", "metrics[0][range]": iso(a) + ":" + iso(b), "metrics[0][resolution]": "1440"}))
    for p in d["data"]:
        at = p["attributes"]; game[at["timestamp"][:10]] = [at["timestamp"][:10], at.get("min"), at.get("value"), at.get("max")]
    for sid in rank:
        d = get(API + f"/servers/{sid}/rank-history?" + urllib.parse.urlencode({"start": iso(a), "stop": iso(b)}))
        for p in d["data"]: rank[sid][p["attributes"]["timestamp"][:10]] = [p["attributes"]["timestamp"][:10], p["attributes"]["value"]]
    a = b
today = now.strftime("%Y-%m-%d")
json.dump({"game": [v for k, v in sorted(game.items()) if k < today and v[2] is not None], "rank": {s: [v for k, v in sorted(r.items())] for s, r in rank.items()}}, open(out, "w"))
print("game days", len(game), "rank days", {s: len(r) for s, r in rank.items()})
