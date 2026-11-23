"""One-time backfill: post a recap for every past match Warcon still has.

Run by hand from the Actions tab (.github/workflows/recap_backfill.yml). It
reads each server's full match list, keeps finished matches with at least
RECAP_MIN_PLAYERS players, and posts them oldest first so the channel reads
in date order. Each recap carries the date the match ended.

It will not repeat itself: matches the live recap job already posted are
skipped, and once it finishes it writes state/recaps_backfilled.txt and
refuses to run again unless FORCE=1.

Settings (environment variables, set in the workflow file):
  DISCORD_RECAP_WEBHOOK_URL  required, the recaps channel webhook (secret)
  WARCON_SERVER_IDS          server ids separated by commas
  WARCON_ORIGIN              default https://console.warcon.app
  RECAP_MIN_PLAYERS          fewest players for a match to get a recap (default 10)
  COUNT_ONLY                 1 = say how many recaps would be posted, post nothing
  FORCE                      1 = run even if a backfill was already done
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import recap
import rlgpost

ORIGIN = os.environ.get("WARCON_ORIGIN", "https://console.warcon.app").rstrip("/")
SERVER_IDS = [s.strip() for s in os.environ.get("WARCON_SERVER_IDS", "").split(",") if s.strip()]
WEBHOOK = recap.WEBHOOK
COUNT_ONLY = os.environ.get("COUNT_ONLY") in ("1", "true")
FORCE = os.environ.get("FORCE") in ("1", "true")
PAUSE = float(os.environ.get("BACKFILL_PAUSE", "2.4"))   # seconds between posts (Discord allows 30 a minute)
HERE = os.path.dirname(os.path.abspath(__file__))
DONE_FILE = os.path.join(HERE, "state", "recaps_backfilled.txt")
UA = "rlg-recap-backfill/1.0 (GitHub Actions; Discord webhook)"


def http(method, url, body=None):
    """Request with patience: waits out Discord's and Warcon's rate limits."""
    data = json.dumps(body).encode() if body is not None else None
    headers = {"User-Agent": UA, "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    for attempt in range(8):
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            text = e.read()[:300].decode(errors="replace")
            if e.code == 429 or e.code >= 500:
                wait = 5 + attempt * 5
                try:
                    wait = float(json.loads(text).get("retry_after", wait))
                except (ValueError, AttributeError):
                    found = re.search(r"(\d+)\s*s", text)
                    wait = float(found.group(1)) if found else wait
                time.sleep(min(wait + 0.5, 120))
                continue
            host = urllib.parse.urlparse(url).netloc   # never print the webhook URL
            sys.exit(f"Request to {host} failed with HTTP {e.code}: {text}")
        except (OSError, ValueError):
            time.sleep(3 + attempt * 3)
    sys.exit(f"Gave up on {urllib.parse.urlparse(url).netloc} after 8 tries.")


def all_matches(server_id):
    out, page = [], 1
    while True:
        d = http("GET", f"{ORIGIN}/api/public/servers/{server_id}/matches?page={page}")
        out += d.get("matches") or []
        if page >= (d.get("pages") or 1) or not d.get("matches"):
            return out
        page += 1
        time.sleep(0.4)


def main():
    if not SERVER_IDS:
        sys.exit("WARCON_SERVER_IDS is empty. Set it in the workflow file.")
    if not WEBHOOK and not COUNT_ONLY:
        sys.exit("DISCORD_RECAP_WEBHOOK_URL is not set. Add it as a repository secret.")
    if os.path.exists(DONE_FILE) and not FORCE and not COUNT_ONLY:
        sys.exit("A backfill was already done (state/recaps_backfilled.txt exists). "
                 "Tick the force box to run it again; that would post every recap a second time.")

    state = recap._load()
    already = state.get("_posted", {})
    todo, new_marks = [], {}
    for sid in SERVER_IDS:
        status = http("GET", f"{ORIGIN}/api/public/servers/{sid}").get("server", {})
        name = str(status.get("name") or "Server").split(" - ")[0].strip()
        found = re.search(r"\[(?!RLG\])([^\]]+)\]\s*(#\d+)?", name)
        short = (found.group(1) + (" " + found.group(2) if found.group(2) else "")).strip() if found else name
        ended = [m for m in all_matches(sid) if m.get("endedAt") and m.get("id") is not None]
        if not ended:
            continue
        newest = max(m["id"] for m in ended)
        if sid not in state:
            new_marks[sid] = newest   # live recaps have not looked yet: they will start after this point
        cutoff = state.get(sid, newest)
        skip = set(already.get(sid, []))
        picked = [m for m in ended if m["id"] <= cutoff and m["id"] not in skip
                  and (m.get("players") or 0) >= recap.MIN_PLAYERS]
        print(f"{short}: {len(ended)} finished matches, {len(picked)} to recap", flush=True)
        todo += [(m["endedAt"], sid, short, m) for m in picked]
    todo.sort(key=lambda t: t[0])
    minutes = len(todo) * (PAUSE + 1.6) / 60
    print(f"{len(todo)} recaps in total, oldest {todo[0][0][:10] if todo else 'n/a'}. "
          f"About {minutes:.0f} minutes to post.", flush=True)
    if COUNT_ONLY:
        print("Count only: nothing was posted.")
        return

    for n, (_, sid, short, m) in enumerate(todo, 1):
        try:
            detail = urllib.request.urlopen(urllib.request.Request(
                f"{ORIGIN}/api/public/servers/{sid}/matches/{m['id']}",
                headers={"User-Agent": UA, "Accept": "application/json"}), timeout=30)
            detail = json.load(detail)
        except (OSError, ValueError):
            detail = None   # post the result without the player list
        png, text, filename = recap.build(m, detail, short, f"{ORIGIN}/s/{sid}")
        try:
            rlgpost.post_image(WEBHOOK, png, filename, text)
        except urllib.error.HTTPError as e:
            sys.exit(rlgpost.describe(e))
        if n % 25 == 0 or n == len(todo):
            print(f"  posted {n} of {len(todo)}", flush=True)
        time.sleep(PAUSE)

    os.makedirs(os.path.dirname(DONE_FILE), exist_ok=True)
    with open(DONE_FILE, "w", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {len(todo)} recaps\n")
    if new_marks:
        state.update(new_marks)
        with open(recap.STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=1, sort_keys=True)
    print("Backfill finished.")


if __name__ == "__main__":
    main()
