"""Gold bar price: one Discord picture, replaced in place when the price changes.

The game sets one gold bar price a day (at midnight UTC, which is 7 PM Eastern
in winter and 8 PM in summer). This job reads it from WARDOGS Server List,
draws it with the last 30 days beside it and keeps one message up to date. The
picture is only uploaded again when the price day changes.

It stays switched off, without failing, until both secrets exist.

Settings (environment variables, set in .github/workflows/gold.yml):
  DISCORD_GOLD_WEBHOOK_URL   webhook on the channel for the gold price (secret)
  WARDOGSERVERLIST_API_KEY   read-only key from wardogserverlist.com (secret)
  GOLD_DAYS                  days of history drawn as bars (default 30)
  FORCE                      1 = redraw even if the price day has not changed
  DRY_RUN                    1 = save the picture to disk instead of posting
"""
import os
import sys
import urllib.error
from datetime import timedelta

import rlgimage
import rlgpost
import serverlist

WEBHOOK = os.environ.get("DISCORD_GOLD_WEBHOOK_URL", "").strip()
DAYS = max(7, min(60, int(os.environ.get("GOLD_DAYS", "30"))))
FORCE = os.environ.get("FORCE") in ("1", "true")
DRY_RUN = os.environ.get("DRY_RUN") == "1"
HERE = os.path.dirname(os.path.abspath(__file__))
ID_FILE = os.path.join(HERE, "state", "gold_message_id.txt")
LAST_FILE = os.path.join(HERE, "state", "gold_last.txt")


def read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


def write(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(str(value) + "\n")


def change(now, before):
    """The difference in words, so it never depends on colour."""
    if not before:
        return "no data"
    pct = 100 * (now - before) / before
    if abs(pct) < 0.5:
        return "Level"
    return f"{'Up' if pct > 0 else 'Down'} {abs(pct):.0f}%"


def day(d):
    return f"{d.strftime('%b')} {d.day}"


def off(message):
    """Stop without failing: a missing secret should not send a failure email every run."""
    print(message)
    print(f"::warning::{message}")
    sys.exit(0)


def main():
    if not WEBHOOK and not DRY_RUN:
        off("Gold price post is switched off: the DISCORD_GOLD_WEBHOOK_URL secret is not set.")
    if not serverlist.enabled():
        off("Gold price post is switched off: the WARDOGSERVERLIST_API_KEY secret is not set.")
    points = serverlist.gold()
    if not points:
        off("The gold bar price could not be read from WARDOGS Server List this time. Nothing changed.")

    today, price = points[-1]
    stamp = today.strftime("%Y-%m-%d") + f":{price}"
    if read(LAST_FILE) == stamp and read(ID_FILE) and not FORCE and not DRY_RUN:
        print(f"The price for {day(today)} ({price:,}) is already posted. Nothing to do.")
        return

    by_day = {d.date(): p for d, p in points}
    ago = lambda n: by_day.get((today - timedelta(days=n)).date())
    window = points[-DAYS:]
    low = min(window, key=lambda x: x[1])
    high = max(window, key=lambda x: x[1])
    rows = [
        ("Since yesterday", change(price, ago(1)), f"was {ago(1):,}" if ago(1) else ""),
        ("Against 7 days ago", change(price, ago(7)), f"was {ago(7):,}" if ago(7) else ""),
        (f"Last {len(window)} days", f"{low[1]:,} to {high[1]:,}", f"low {day(low[0])} · high {day(high[0])}"),
    ]
    png = rlgimage.gold(f"{price:,}", f"Price for {today.strftime('%A')}, {day(today)}", rows,
                        [(day(d), p) for d, p in window],
                        right=change(price, ago(1)) + " since yesterday" if ago(1) else "",
                        footer_left=serverlist.CREDIT, footer_right="Updated " + rlgpost.stamp())
    text = (f"**Gold bar price** · **{price:,}** cash · {change(price, ago(1))} since yesterday · "
            f"next price <t:{int(today.timestamp()) + 86400}:R> · "
            f"[Price history](<https://wardogserverlist.com/gold-bars>)\n-# {serverlist.CREDIT_LINK}")
    filename = f"gold-{today.strftime('%Y-%m-%d')}.png"
    if DRY_RUN:
        out = os.path.join(os.environ.get("DRY_RUN_DIR", "."), filename)
        with open(out, "wb") as f:
            f.write(png)
        print(f"{text}\n  picture saved to {out} ({len(png) // 1024} KB)")
        return
    try:
        message_id, new = rlgpost.put_image(WEBHOOK, read(ID_FILE), png, filename, text)
    except urllib.error.HTTPError as e:
        sys.exit(rlgpost.describe(e))
    write(ID_FILE, message_id)
    write(LAST_FILE, stamp)
    print(f"Gold price for {day(today)} ({price:,}) {'posted' if new else 'updated in place'}.")


if __name__ == "__main__":
    main()
