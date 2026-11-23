"""Sends pictures to Discord through a webhook: post, replace in place, delete.

Shared by the leaderboard, seederboard, recap, awards and status jobs. A webhook
can only touch messages it posted itself. The webhook URL is a secret and is
never printed.
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

UA = "rlg-discord-posts/1.0 (GitHub Actions; Discord webhook)"
EASTERN = ZoneInfo("America/New_York")   # Redline Gaming's time zone


def stamp(when=None, fmt="%b %-d, %Y · %-I:%M %p ET"):
    """A date and time in US Eastern, for the footer of a picture."""
    when = when or datetime.now(EASTERN)
    return when.astimezone(EASTERN).strftime(fmt)


def _send(method, url, data, headers):
    """One request to Discord, waiting and retrying when it says to slow down."""
    for attempt in range(6):
        req = urllib.request.Request(url, data=data, method=method, headers={"User-Agent": UA, **headers})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 5:
                raise
            try:
                wait = float(json.loads(e.read()).get("retry_after", 3))
            except (ValueError, AttributeError):
                wait = 3
            time.sleep(min(wait + 0.3, 60))


def _multipart(payload, png, filename):
    """payload_json plus one file, in the form Discord expects for uploads."""
    boundary = "rlg" + uuid.uuid4().hex
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="payload_json"\r\n'
        f"Content-Type: application/json\r\n\r\n".encode() + json.dumps(payload).encode() + b"\r\n",
        f'--{boundary}\r\nContent-Disposition: form-data; name="files[0]"; filename="{filename}"\r\n'
        f"Content-Type: image/png\r\n\r\n".encode() + png + b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ]
    return b"".join(parts), {"Content-Type": f"multipart/form-data; boundary={boundary}"}


def _payload(content, filename):
    return {
        "content": content[:2000],
        "embeds": [],                                    # clears a card left by an older version
        "attachments": [{"id": 0, "filename": filename}],
        "allowed_mentions": {"parse": []},               # nothing in a post can ping anyone
    }


def post_image(webhook, png, filename, content=""):
    """Post a new message with one picture. Returns the message id."""
    data, headers = _multipart(_payload(content, filename), png, filename)
    return _send("POST", webhook + "?wait=true", data, headers)["id"]


def edit_image(webhook, message_id, png, filename, content=""):
    """Swap the picture and text of a message this webhook posted earlier.
    Returns False if that message no longer exists."""
    data, headers = _multipart(_payload(content, filename), png, filename)
    try:
        _send("PATCH", f"{webhook}/messages/{message_id}", data, headers)
        return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        raise


def edit_text(webhook, message_id, content):
    """Change only the text under a picture. Returns False if the message is gone."""
    body = json.dumps({"content": content[:2000], "allowed_mentions": {"parse": []}}).encode()
    try:
        _send("PATCH", f"{webhook}/messages/{message_id}", body, {"Content-Type": "application/json"})
        return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        raise


def put_image(webhook, message_id, png, filename, content=""):
    """Replace the message if it exists, otherwise post a new one.
    Returns (message id, True if a new message was posted)."""
    if message_id and edit_image(webhook, message_id, png, filename, content):
        return message_id, False
    return post_image(webhook, png, filename, content), True


def describe(error):
    """A safe one-line description of an HTTP error (host and status, never the URL)."""
    host = urllib.parse.urlparse(getattr(error, "url", "") or "").netloc or "the server"
    try:
        detail = error.read()[:300].decode(errors="replace")
    except Exception:  # noqa: BLE001
        detail = ""
    return f"Request to {host} failed with HTTP {getattr(error, 'code', '?')}: {detail}"
