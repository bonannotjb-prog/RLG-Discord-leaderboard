import json, sys, time, urllib.request, urllib.parse, urllib.error, os, threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN = open(sys.argv[2]).read().strip()
OUT = sys.argv[1]
API = "https://api.battlemetrics.com"
SERVERS = ["41243237", "41199298"]
NOW = datetime.now(timezone.utc).replace(microsecond=0)
START = datetime(2026, 8, 1, tzinfo=timezone.utc)
lock = threading.Lock(); NREQ = [0]

def iso(d): return d.strftime("%Y-%m-%dT%H:%M:%SZ")
def log(*a): print(*a, flush=True)

def get(url):
    with lock:
        NREQ[0] += 1
        if NREQ[0] % 100 == 0: log("  req", NREQ[0])
    for attempt in range(8):
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + TOKEN})
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                ra = e.headers.get("Retry-After"); log("  429", ra)
                time.sleep(float(ra) + 1 if ra else 5 + attempt * 5); continue
            return {"errors": [{"status": e.code, "detail": e.read().decode("utf8", "replace")[:300]}]}
        except Exception as e:
            time.sleep(2 + attempt)
    return {"errors": [{"status": 0, "detail": "gave up"}]}

def chunks(start, stop, days):
    a = start
    while a < stop:
        b = min(a + timedelta(days=days), stop); yield a, b; a = b

def sess_chunk(sid, a, b):
    out, errs = {}, []
    q = urllib.parse.urlencode({"filter[servers]": sid, "filter[range]": f"{iso(a)}:{iso(b)}", "page[size]": 100})
    url = f"{API}/sessions?{q}"; seen = set()
    while url and url not in seen:
        seen.add(url); d = get(url)
        if "errors" in d: errs.append((iso(a), d["errors"])); break
        for s in d["data"]:
            at = s["attributes"]
            out[s["id"]] = [at["start"], at["stop"], 1 if at.get("firstTime") else 0, at.get("name"), s["relationships"]["player"]["data"]["id"]]
        url = (d.get("links") or {}).get("next")
    return sid, out, errs

result = {"fetchedAt": iso(NOW), "servers": {}}
jobs = []
for sid in SERVERS:
    info = get(f"{API}/servers/{sid}"); attrs = info["data"]["attributes"]
    created = datetime.fromisoformat(attrs["createdAt"].replace("Z", "+00:00"))
    s0 = max(START, created - timedelta(days=1))
    counts = []
    for a, b in chunks(s0, NOW, 5):
        q = urllib.parse.urlencode({"start": iso(a), "stop": iso(b), "resolution": "raw"})
        d = get(f"{API}/servers/{sid}/player-count-history?{q}")
        if "errors" not in d:
            counts += [[p["attributes"]["timestamp"], p["attributes"]["value"]] for p in d["data"]]
    result["servers"][sid] = {"name": attrs["name"], "game": info["data"]["relationships"]["game"]["data"]["id"],
        "createdAt": attrs["createdAt"], "maxPlayers": attrs.get("maxPlayers"), "rank": attrs.get("rank"),
        "counts": sorted({c[0]: c for c in counts}.values()), "sessions": {}, "errors": []}
    jobs += [(sid, a, b) for a, b in chunks(s0, NOW, 2)]
log("jobs", len(jobs))
with ThreadPoolExecutor(8) as ex:
    for sid, out, errs in ex.map(lambda j: sess_chunk(*j), jobs):
        result["servers"][sid]["sessions"].update(out); result["servers"][sid]["errors"] += errs
for sid, s in result["servers"].items():
    s["sessions"] = list(s["sessions"].values())
    log(sid, s["name"], "counts", len(s["counts"]), "sessions", len(s["sessions"]), "errors", len(s["errors"]), str(s["errors"][:1])[:300])
json.dump(result, open(OUT, "w")); log("DONE")
