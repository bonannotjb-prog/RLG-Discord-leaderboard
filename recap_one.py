"""Post the recap picture for one match, by hand, without touching any saved state.

Run from the Actions tab (.github/workflows/recap_one.yml). Useful for seeing what
a recap looks like, or for re-posting one match. It never changes which matches
the live recap job or the backfill think they have posted.

Settings (environment variables, set in the workflow file):
  DISCORD_RECAP_WEBHOOK_URL  required, the recaps channel webhook (secret)
  WARCON_SERVER_ID           the server the match was on
  MATCH_ID                   the match to post; empty = the newest finished match
  WARCON_ORIGIN              default https://console.warcon.app
  DRY_RUN                    1 = save the picture to disk instead of posting
"""
import os
import sys
import urllib.error

import recap
import recap_backfill as bf
import rlgpost

SERVER_ID = os.environ.get("WARCON_SERVER_ID", "").strip()
MATCH_ID = os.environ.get("MATCH_ID", "").strip()
DRY_RUN = os.environ.get("DRY_RUN") == "1"


def main():
    if not SERVER_ID:
        sys.exit("WARCON_SERVER_ID is empty. Set it in the workflow file.")
    if not recap.WEBHOOK and not DRY_RUN:
        sys.exit("DISCORD_RECAP_WEBHOOK_URL is not set. Add it as a repository secret.")
    status = bf.http("GET", f"{bf.ORIGIN}/api/public/servers/{SERVER_ID}").get("server", {})
    short = bf.short_name(status.get("name"))
    ended = [m for m in bf.all_matches(SERVER_ID) if m.get("endedAt") and m.get("id") is not None]
    if MATCH_ID:
        match = next((m for m in ended if str(m["id"]) == MATCH_ID), None)
        if not match:
            sys.exit(f"No finished match with id {MATCH_ID} was found on {short}.")
    else:
        if not ended:
            sys.exit(f"{short} has no finished matches yet.")
        match = max(ended, key=lambda m: m["endedAt"])
    try:
        detail = bf.http("GET", f"{bf.ORIGIN}/api/public/servers/{SERVER_ID}/matches/{match['id']}")
    except SystemExit:
        detail = None   # post the result without the player list
    png, text, filename = recap.build(match, detail, short, f"{bf.ORIGIN}/s/{SERVER_ID}")
    if DRY_RUN:
        out = os.path.join(os.environ.get("DRY_RUN_DIR", "."), filename)
        with open(out, "wb") as f:
            f.write(png)
        print(f"{text}\n  picture saved to {out} ({len(png) // 1024} KB)")
        return
    try:
        rlgpost.post_image(recap.WEBHOOK, png, filename, text)
    except urllib.error.HTTPError as e:
        sys.exit(rlgpost.describe(e))
    print(f"Posted the recap for match {match['id']} on {short}.")


if __name__ == "__main__":
    main()
