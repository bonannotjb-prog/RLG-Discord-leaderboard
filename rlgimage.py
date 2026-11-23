"""Draws RLG's Discord posts as pictures, in Redline Gaming's colours.

One function per post, each returning PNG bytes:
  leaderboard()   kills leaderboard for one server
  seederboard()   seeding time, last 30 days beside all time
  recap()         one finished match
  awards()        the weekly awards
  status()        live status of every server

Every picture is 1650 pixels wide and wider than it is tall, because Discord
shows a posted image at most 550 wide and 350 tall: a wide shape fills that
box, so the text is readable without clicking.

Needs Pillow and fontTools (installed by the workflows). Fonts and artwork are
in assets/.
"""
import io
import os
import subprocess
import sys

try:
    from fontTools.ttLib import TTFont
    from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont
except ImportError:
    # The workflow normally installs these first. If that step is missing (an
    # older workflow file), install them here so the job still runs.
    print("Picture tools not found; installing pillow and fonttools.", flush=True)
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "pillow", "fonttools"])
    from fontTools.ttLib import TTFont
    from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
# Where artwork and fonts are looked for, in order. The intended place is
# assets/ and assets/fonts/, but files uploaded loose beside the scripts work too.
SEARCH = [os.path.join(HERE, "assets", "fonts"), os.path.join(HERE, "assets"),
          os.path.join(HERE, "fonts"), HERE]


def _find(name):
    """Full path of an artwork or font file, or None if it is not in the repository."""
    for folder in SEARCH:
        path = os.path.join(folder, name)
        if os.path.isfile(path):
            return path
    return None


def _need(name):
    path = _find(name)
    if not path:
        raise SystemExit(
            f"Font file {name} is missing from the repository. Upload the whole 'assets' folder "
            "from the zip (it holds a 'fonts' folder with ten .ttf files, banner.jpg and icon.png).")
    return path

# Colours taken from the RLG banner and icon.
BG = (11, 11, 13)
ROW_A = (20, 20, 23)
ROW_B = (26, 26, 30)
LINE = (46, 46, 52)
RED = (214, 32, 39)
WHITE = (240, 238, 234)
SOFT = (176, 174, 170)
DIM = (120, 118, 116)
MEDAL = [(212, 175, 55), (190, 194, 203), (176, 112, 62)]   # gold, silver, bronze

W = 1650
PAD = 54
HEADER_H = 250
COLS_H = 56
ROW_H = 66
FOOT_H = 64
COMPACT = False


def set_style(compact):
    """compact=True draws a narrower picture with a slim header. Discord fits every
    picture into the same box, so a narrower drawing shows about 20% larger text."""
    global W, PAD, HEADER_H, COMPACT
    COMPACT = bool(compact)
    W, PAD, HEADER_H = (1380, 40, 108) if COMPACT else (1650, 54, 250)


set_style(True)   # every post uses the slim header; set_style(False) brings back the tall banner

# Each style is a list of fonts tried in order, so a name with unusual
# characters still draws instead of showing empty boxes.
STYLES = {
    "title": ["Teko-SemiBold.ttf", "Teko-SemiBold-Ext.ttf", "DejaVuSans-Bold.ttf"],
    "bold": ["BarlowCondensed-Bold.ttf", "BarlowCondensed-Bold-Ext.ttf", "DejaVuSans-Bold.ttf"],
    "semi": ["BarlowCondensed-SemiBold.ttf", "BarlowCondensed-SemiBold-Ext.ttf", "DejaVuSans-Bold.ttf"],
    "text": ["BarlowCondensed-Medium.ttf", "BarlowCondensed-Medium-Ext.ttf", "DejaVuSans.ttf"],
}
_cmaps, _fonts = {}, {}


def _cmap(name):
    if name not in _cmaps:
        _cmaps[name] = set(TTFont(_need(name), lazy=True).getBestCmap())
    return _cmaps[name]


def _font(name, size):
    if (name, size) not in _fonts:
        # DejaVu looks larger than the condensed faces at the same size.
        scale = 0.80 if name.startswith("DejaVu") else 1.0
        _fonts[(name, size)] = ImageFont.truetype(_need(name), round(size * scale))
    return _fonts[(name, size)]


def _runs(text, style, size):
    """Split text into (font, piece) runs, picking the first font that has each character."""
    runs = []
    for ch in " ".join(str(text).split()):
        if not ch.isprintable():
            continue
        name = next((n for n in STYLES[style] if ord(ch) in _cmap(n)), None)
        if name is None:
            continue   # no font has it (an emoji, say): leave it out
        if ch == " " and runs and runs[-1][1].endswith(" "):
            continue
        if runs and runs[-1][0] == name:
            runs[-1][1] += ch
        else:
            runs.append([name, ch])
    return [(_font(n, size), piece) for n, piece in runs]


def width(text, style, size, spacing=0):
    runs = _runs(text, style, size)
    total = sum(f.getlength(p) for f, p in runs)
    return total + spacing * max(0, sum(len(p) for _, p in runs) - 1)


def text(draw, x, y, value, style, size, fill, align="left", spacing=0, max_width=None, shrink_to=None):
    """Draw value with its vertical middle at y. align is left, right or center.
    A value wider than max_width is first drawn smaller (down to shrink_to), and
    only cut off with an ellipsis if it still does not fit."""
    value = str(value)
    if max_width and shrink_to:
        while size > shrink_to and width(value, style, size, spacing) > max_width:
            size -= 1
    if max_width and width(value, style, size, spacing) > max_width:
        while value and width(value + "…", style, size, spacing) > max_width:
            value = value[:-1]
        value = value.rstrip() + "…"
    total = width(value, style, size, spacing)
    if align == "right":
        x -= total
    elif align == "center":
        x -= total / 2
    for font, piece in _runs(value, style, size):
        if spacing:
            for ch in piece:
                draw.text((x, y), ch, font=font, fill=fill, anchor="lm")
                x += font.getlength(ch) + spacing
        else:
            draw.text((x, y), piece, font=font, fill=fill, anchor="lm")
            x += font.getlength(piece)
    return total


def _header(canvas, title, subtitle, online, kicker="REDLINE GAMING"):
    """Banner artwork across the top, darkened on the left so the title reads clearly."""
    banner_path = _find("banner.jpg")
    if banner_path:
        banner = Image.open(banner_path).convert("RGB")
        bw, bh = banner.size
        # Use the right-hand part of the banner (control tower, hangar and the REDLINE
        # flag on the wall), not the centre logo, which is already shown as the icon.
        region = banner.crop((int(bw * 0.56), 0, bw, bh))
        scale = max(W / region.width, HEADER_H / region.height)
        region = region.resize((round(region.width * scale), round(region.height * scale)), Image.LANCZOS)
        top = int((region.height - HEADER_H) * (0.12 if COMPACT else 0.07))
        region = region.crop((0, top, W, top + HEADER_H))
        region = ImageEnhance.Brightness(region.filter(ImageFilter.GaussianBlur(0.8))).enhance(0.9)
        # Fade to near-black toward the left and the bottom.
        fade = Image.new("L", (W, HEADER_H), 0)
        fd = ImageDraw.Draw(fade)
        for x in range(W):
            fd.line([(x, 0), (x, HEADER_H)], fill=int(240 * max(0.0, 1 - x / (W * 0.74)) ** 0.9))
        canvas.paste(Image.composite(Image.new("RGB", (W, HEADER_H), BG), region, fade), (0, 0))
        bottom = Image.new("L", (W, HEADER_H), 0)
        bd = ImageDraw.Draw(bottom)
        for y in range(HEADER_H):
            bd.line([(0, y), (W, y)], fill=int(215 * max(0.0, (y - HEADER_H * 0.5) / (HEADER_H * 0.5)) ** 1.4))
        canvas.paste(Image.new("RGB", (W, HEADER_H), BG), (0, 0), bottom)
    else:
        print("banner.jpg is missing from the repository; drawing a plain header.", flush=True)

    draw = ImageDraw.Draw(canvas)
    icon_size = 78 if COMPACT else 170
    ix, iy = PAD, (HEADER_H - 5 - icon_size) // 2 if COMPACT else (HEADER_H - icon_size) // 2
    icon_path = _find("icon.png")
    if icon_path:
        icon = Image.open(icon_path).convert("RGB").resize((icon_size, icon_size), Image.LANCZOS)
        canvas.paste(icon, (ix, iy))
        draw.rectangle([ix - 2, iy - 2, ix + icon_size + 1, iy + icon_size + 1], outline=(70, 70, 76), width=2)
        tx = ix + icon_size + (22 if COMPACT else 36)
    else:
        print("icon.png is missing from the repository; leaving the icon out.", flush=True)
        tx = PAD
    if COMPACT:
        # One slim strip: title on top, the rule under it, the live figure on the right.
        rw = width(online.upper(), "semi", 26, 3) if online else 0
        text(draw, tx, 36, title.upper(), "title", 62, WHITE, max_width=W - tx - PAD - 20)
        text(draw, tx, 78, subtitle.upper(), "semi", 24, SOFT, spacing=2, max_width=W - tx - PAD - rw - 40)
        if online:
            text(draw, W - PAD, 78, online.upper(), "semi", 26, WHITE, align="right", spacing=3)
    else:
        text(draw, tx, 62, kicker.upper(), "semi", 30, RED, spacing=7)
        text(draw, tx, 124, title.upper(), "title", 98, WHITE, max_width=W - tx - PAD - 250)
        text(draw, tx, 190, subtitle.upper(), "semi", 30, SOFT, spacing=3)
        if online:
            text(draw, W - PAD, 190, online.upper(), "semi", 30, WHITE, align="right", spacing=3)
    draw.rectangle([0, HEADER_H - 5, W, HEADER_H], fill=RED)


def _canvas(height, title, subtitle, right="", kicker="REDLINE GAMING"):
    canvas = Image.new("RGB", (W, height), BG)
    _header(canvas, title, subtitle, right, kicker)
    return canvas, ImageDraw.Draw(canvas)


def _footer(draw, y, left, right):
    draw.line([(PAD, y + 4), (W - PAD, y + 4)], fill=LINE, width=2)
    text(draw, PAD, y + FOOT_H / 2 + 2, left.upper(), "semi", 26, DIM, spacing=2)
    text(draw, W - PAD, y + FOOT_H / 2 + 2, right.upper(), "semi", 26, DIM, align="right", spacing=2)


def _png(canvas):
    out = io.BytesIO()
    canvas.save(out, format="PNG", optimize=True)
    return out.getvalue()


def _rank(draw, cx, cy, rank, r=22, size=32):
    """Rank marker. The top three get a filled disc; the number is always shown,
    so the place never depends on telling the disc colours apart."""
    if 1 <= rank <= 3:
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=MEDAL[rank - 1])
        text(draw, cx, cy + 1, rank, "bold", size, (18, 18, 20), align="center")
    else:
        text(draw, cx, cy + 1, rank, "semi", size, DIM, align="center")


def _bar(draw, x, y, w, h, fraction, fill=WHITE):
    draw.rectangle([x, y, x + w, y + h], fill=(40, 40, 46))
    if fraction > 0:
        draw.rectangle([x, y, x + max(h, round(w * min(1.0, fraction))), y + h], fill=fill)


def _clock(seconds):
    m = int(seconds // 60)
    return f"{m // 60}h {m % 60:02d}m"


# ----------------------------------------------------------------- leaderboard
def leaderboard(title, subtitle, rows, online="", footer_left="", footer_right=""):
    """rows: dicts with rank, name, kills, deaths, wins, minutes."""
    n = max(1, len(rows))
    canvas, draw = _canvas(HEADER_H + COLS_H + ROW_H * n + FOOT_H, title, subtitle, online)
    right = W - PAD
    step = [0, 160, 300, 455, 630] if COMPACT else [0, 190, 360, 550, 760]
    cols = [(label, right - off) for label, off in zip(["HOURS", "WINS", "K/D", "DEATHS", "KILLS"], step)]
    name_x = PAD + (96 if COMPACT else 110)
    name_max = cols[-1][1] - 130 - name_x

    y = HEADER_H + COLS_H / 2 + 2
    text(draw, PAD + 34, y, "#", "semi", 28, DIM, align="center")
    text(draw, name_x, y, "PLAYER", "semi", 28, DIM, spacing=3)
    for label, x in cols:
        text(draw, x, y, label, "semi", 28, DIM, align="right", spacing=3)
    top = HEADER_H + COLS_H
    draw.line([(PAD, top - 1), (W - PAD, top - 1)], fill=LINE, width=2)

    for i, r in enumerate(rows):
        y0 = top + i * ROW_H
        mid = y0 + ROW_H / 2
        draw.rectangle([PAD, y0, W - PAD, y0 + ROW_H - 3], fill=ROW_A if i % 2 == 0 else ROW_B)
        rank = r.get("rank", i + 1)
        podium = 1 <= rank <= 3
        if podium:
            draw.rectangle([PAD, y0, PAD + 6, y0 + ROW_H - 3], fill=MEDAL[rank - 1])
        _rank(draw, PAD + 34, mid, rank)
        text(draw, name_x, mid + 1, r.get("name") or "unknown", "bold" if podium else "semi", 38,
             WHITE if podium else (222, 220, 216), max_width=name_max, shrink_to=29)
        kills, deaths = r.get("kills", 0), r.get("deaths", 0)
        kd = kills / deaths if deaths else float(kills)
        values = [f"{r.get('minutes', 0) / 60:,.1f}", f"{r.get('wins', 0):,}",
                  f"{kd:.2f}" if kd < 100 else f"{kd:.0f}", f"{deaths:,}", f"{kills:,}"]
        for (label, x), v in zip(cols, values):
            main = label == "KILLS"
            text(draw, x, mid + 1, v, "bold" if main else "text", 40 if main else 36,
                 WHITE if main else SOFT, align="right")
    _footer(draw, top + ROW_H * n, footer_left, footer_right)
    return _png(canvas)


# ----------------------------------------------------------------- seederboard
def seederboard(subtitle, boards, right="", footer_left="", footer_right=""):
    """boards: two (heading, rows) pairs shown side by side. rows: dicts with
    name, seconds, days."""
    n = max([len(rows) for _, rows in boards] + [1])
    canvas, draw = _canvas(HEADER_H + COLS_H + ROW_H * n + FOOT_H, "Seederboard", subtitle, right)
    gap = 42
    col_w = (W - 2 * PAD - gap) // 2
    top = HEADER_H + COLS_H
    for b, (heading, rows) in enumerate(boards[:2]):
        x0 = PAD + b * (col_w + gap)
        x1 = x0 + col_w
        y = HEADER_H + COLS_H / 2 + 2
        text(draw, x0 + 4, y, heading.upper(), "semi", 30, RED, spacing=4)
        text(draw, x1 - 4, y, "TIME · DAYS", "semi", 26, DIM, align="right", spacing=3)
        draw.line([(x0, top - 1), (x1, top - 1)], fill=LINE, width=2)
        for i in range(n):
            y0 = top + i * ROW_H
            mid = y0 + ROW_H / 2
            draw.rectangle([x0, y0, x1, y0 + ROW_H - 3], fill=ROW_A if i % 2 == 0 else ROW_B)
            if i >= len(rows):
                continue
            r, rank = rows[i], i + 1
            podium = rank <= 3
            if podium:
                draw.rectangle([x0, y0, x0 + 6, y0 + ROW_H - 3], fill=MEDAL[rank - 1])
            _rank(draw, x0 + 38, mid, rank)
            days = r.get("days", 0)
            dw = text(draw, x1 - 18, mid + 1, f"{days} day{'' if days == 1 else 's'}", "text", 30, DIM, align="right")
            tw = text(draw, x1 - 18 - max(dw, 96) - 22, mid + 1, _clock(r.get("seconds", 0)), "bold", 38, WHITE, align="right")
            text(draw, x0 + 78, mid + 1, r.get("name") or "unknown", "bold" if podium else "semi", 36,
                 WHITE if podium else (222, 220, 216), max_width=col_w - 78 - 18 - max(dw, 96) - 22 - tw - 24,
                 shrink_to=27)
        if not rows:
            text(draw, x0 + col_w / 2, top + ROW_H / 2, "No seeding time recorded yet", "semi", 32, DIM, align="center")
    _footer(draw, top + ROW_H * n, footer_left, footer_right)
    return _png(canvas)


# ------------------------------------------------------------------- recap
def recap(server, map_name, mode, winner, scores, facts, top, awards_list, footer_left="", footer_right=""):
    """scores: [(faction, points)] highest first. facts: short strings for the header.
    top: dicts with name, faction, kills, deaths. awards_list: [(label, name, value)]."""
    body_h = 5 * ROW_H + COLS_H + 26 + 150
    canvas, draw = _canvas(HEADER_H + body_h + FOOT_H, f"Match recap · {server}",
                           " · ".join(x for x in (map_name, mode) if x), " · ".join(facts))
    top_y = HEADER_H + 26
    left_w = 420 if COMPACT else 500
    # Result panel on the left.
    draw.rectangle([PAD, top_y, PAD + left_w, top_y + COLS_H + 5 * ROW_H - 3], fill=ROW_A)
    draw.rectangle([PAD, top_y, PAD + 6, top_y + COLS_H + 5 * ROW_H - 3], fill=RED)
    text(draw, PAD + 34, top_y + 44, "RESULT", "semi", 28, DIM, spacing=4)
    headline = (f"{winner} wins" if winner else "No winner").upper()
    size = 92
    while size > 54 and width(headline, "title", size) > left_w - 68:
        size -= 2   # shrink long faction names to fit the panel instead of cutting them off
    text(draw, PAD + 34, top_y + 112, headline, "title", size, WHITE, max_width=left_w - 68)
    best = max([p for _, p in scores] + [1])
    for i, (faction, pts) in enumerate(scores[:3]):
        y = top_y + 196 + i * 62
        lead = i == 0 and winner
        text(draw, PAD + 34, y, faction, "bold" if lead else "semi", 36, WHITE if lead else SOFT)
        text(draw, PAD + left_w - 34, y, pts, "bold", 40, WHITE if lead else SOFT, align="right")
        _bar(draw, PAD + 34, y + 24, left_w - 68, 8, pts / best, WHITE if lead else (120, 120, 126))
    # Top players on the right.
    x0 = PAD + left_w + 42
    x1 = W - PAD
    y = top_y + COLS_H / 2
    text(draw, x0 + 4, y, "TOP PLAYERS", "semi", 30, RED, spacing=4)
    cols = [("K/D", x1 - 18), ("DEATHS", x1 - (128 if COMPACT else 150)), ("KILLS", x1 - (262 if COMPACT else 320))]
    for label, x in cols:
        text(draw, x, y, label, "semi", 26, DIM, align="right", spacing=3)
    t0 = top_y + COLS_H
    draw.line([(x0, t0 - 1), (x1, t0 - 1)], fill=LINE, width=2)
    for i in range(5):
        y0 = t0 + i * ROW_H
        mid = y0 + ROW_H / 2
        draw.rectangle([x0, y0, x1, y0 + ROW_H - 3], fill=ROW_A if i % 2 == 0 else ROW_B)
        if i >= len(top):
            continue
        r, rank = top[i], i + 1
        if rank <= 3:
            draw.rectangle([x0, y0, x0 + 6, y0 + ROW_H - 3], fill=MEDAL[rank - 1])
        _rank(draw, x0 + 38, mid, rank)
        kills, deaths = r.get("kills", 0), r.get("deaths", 0)
        kd = kills / deaths if deaths else float(kills)
        for (label, x), v in zip(cols, [f"{kd:.2f}" if kd < 100 else f"{kd:.0f}", deaths, kills]):
            text(draw, x, mid + 1, v, "bold" if label == "KILLS" else "text", 38 if label == "KILLS" else 34,
                 WHITE if label == "KILLS" else SOFT, align="right")
        fx = cols[-1][1] - (92 if COMPACT else 110)
        fw = text(draw, fx, mid + 1, r.get("faction") or "", "text", 24 if COMPACT else 26, DIM, align="right")
        text(draw, x0 + 78, mid + 1, r.get("name") or "unknown", "bold" if rank <= 3 else "semi", 36,
             WHITE if rank <= 3 else (222, 220, 216), max_width=fx - fw - 18 - (x0 + 78), shrink_to=27)
    # Awards strip.
    ay = t0 + 5 * ROW_H + 22
    tiles = awards_list[:3]
    if tiles:
        gap = 24
        tw = (W - 2 * PAD - gap * (len(tiles) - 1)) // len(tiles)
        for i, (label, name, value) in enumerate(tiles):
            tx = PAD + i * (tw + gap)
            draw.rectangle([tx, ay, tx + tw, ay + 124], fill=ROW_B)
            text(draw, tx + 24, ay + 32, label.upper(), "semi", 26, RED, spacing=3)
            vw = text(draw, tx + tw - 24, ay + 84, value, "bold", 40, WHITE, align="right")
            text(draw, tx + 24, ay + 84, name, "semi", 38, (222, 220, 216), max_width=tw - 48 - vw - 20, shrink_to=28)
    _footer(draw, HEADER_H + body_h, footer_left, footer_right)
    return _png(canvas)


# ------------------------------------------------------------------- awards
def awards(subtitle, categories, right="", footer_left="", footer_right=""):
    """categories: up to five (heading, [(name, value), ...]) pairs, three names each.
    The first three sit on the top row, the rest share the bottom row."""
    panel_h = 318
    gap = 24
    rows = [categories[:3], categories[3:5]]
    rows = [r for r in rows if r]
    canvas, draw = _canvas(HEADER_H + 26 + len(rows) * (panel_h + gap) - gap + 26 + FOOT_H,
                           "Weekly awards", subtitle, right)
    y = HEADER_H + 26
    for line in rows:
        pw = (W - 2 * PAD - gap * (len(line) - 1)) // len(line)
        for i, (heading, winners) in enumerate(line):
            x = PAD + i * (pw + gap)
            draw.rectangle([x, y, x + pw, y + panel_h], fill=ROW_A)
            draw.rectangle([x, y, x + pw, y + 5], fill=RED)
            text(draw, x + 28, y + 50, heading.upper(), "title", 54, WHITE)
            for j in range(3):
                ry = y + 96 + j * 72
                draw.rectangle([x + 16, ry, x + pw - 16, ry + 64], fill=ROW_B)
                if j >= len(winners):
                    continue
                name, value = winners[j]
                _rank(draw, x + 52, ry + 32, j + 1, r=21, size=30)
                vw = text(draw, x + pw - 34, ry + 33, value, "bold" if j == 0 else "semi", 36,
                          WHITE if j == 0 else SOFT, align="right")
                text(draw, x + 90, ry + 33, name, "bold" if j == 0 else "semi", 36,
                     WHITE if j == 0 else (222, 220, 216), max_width=pw - 90 - 34 - vw - 18, shrink_to=27)
            if not winners:
                text(draw, x + pw / 2, y + 96 + 104, "Nobody qualified this week", "semi", 30, DIM, align="center")
        y += panel_h + gap
    _footer(draw, y - gap + 26, footer_left, footer_right)
    return _png(canvas)


# ------------------------------------------------------------------- status
def status(servers, right="", footer_left="", footer_right=""):
    """servers: dicts with name, state (LIVE, SEEDING, EMPTY or OFFLINE), note,
    players, max, map, mode, weather, scores [(faction, points, players on)]."""
    panel_h = 600
    gap = 34
    shown = servers[:2] or [{}]
    canvas, draw = _canvas(HEADER_H + 26 + panel_h + 20 + FOOT_H, "Live server status",
                           "Updates as players join and leave", right)
    pw = (W - 2 * PAD - gap * (len(shown) - 1)) // len(shown)
    y = HEADER_H + 26
    for i, s in enumerate(shown):
        x = PAD + i * (pw + gap)
        state = str(s.get("state") or "OFFLINE").upper()
        active = state in ("LIVE", "SEEDING")
        draw.rectangle([x, y, x + pw, y + panel_h], fill=ROW_A)
        draw.rectangle([x, y, x + pw, y + 5], fill=RED if active else (70, 70, 76))
        text(draw, x + 30, y + 62, (s.get("name") or "Server").upper(), "title", 76, WHITE, max_width=pw - 280)
        # The state is written out in words inside the tag: filled when players are on.
        tag_w = width(state, "bold", 30, 4) + 44
        tx = x + pw - 30 - tag_w
        if active:
            draw.rectangle([tx, y + 34, tx + tag_w, y + 86], fill=WHITE)
            text(draw, tx + tag_w / 2, y + 61, state, "bold", 30, (14, 14, 16), align="center", spacing=4)
        else:
            draw.rectangle([tx, y + 34, tx + tag_w, y + 86], outline=DIM, width=2)
            text(draw, tx + tag_w / 2, y + 61, state, "bold", 30, SOFT, align="center", spacing=4)
        if state == "OFFLINE":
            text(draw, x + pw / 2, y + panel_h / 2, "Offline or unreachable", "semi", 40, DIM, align="center")
            continue
        players, cap = s.get("players", 0), s.get("max", 0) or 100
        nw = text(draw, x + 30, y + 168, players, "title", 150, WHITE)
        cw = text(draw, x + 30 + nw + 14, y + 190, f"/ {cap} players", "semi", 38, SOFT)
        text(draw, x + pw - 30, y + 190, (s.get("note") or "").upper(), "semi", 30, WHITE if active else DIM,
             align="right", spacing=2, max_width=pw - 60 - nw - 14 - cw - 26, shrink_to=20)
        _bar(draw, x + 30, y + 238, pw - 60, 12, players / cap)
        fy = y + 300
        for label, value in (("MAP", s.get("map")), ("MODE", s.get("mode")), ("WEATHER", s.get("weather"))):
            text(draw, x + 30, fy, label, "semi", 26, DIM, spacing=3)
            text(draw, x + 190, fy, value or "Unknown", "semi", 34, (222, 220, 216), max_width=pw - 220, shrink_to=24)
            fy += 48
        draw.line([(x + 30, fy - 8), (x + pw - 30, fy - 8)], fill=LINE, width=2)
        scores = (s.get("scores") or [])[:3]
        best = max([p for _, p, _ in scores] + [1])
        lead = max([p for _, p, _ in scores] + [0])
        for j, (faction, pts, on) in enumerate(scores):
            sy = fy + 28 + j * 50
            first = lead > 0 and pts == lead
            text(draw, x + 30, sy, faction, "bold" if first else "semi", 34, WHITE if first else SOFT)
            text(draw, x + pw - 30, sy, f"{on} on", "text", 28, DIM, align="right")
            text(draw, x + pw - 130, sy, pts, "bold", 36, WHITE if first else SOFT, align="right")
            _bar(draw, x + 230, sy - 5, pw - 230 - 230, 10, pts / best if lead else 0,
                 WHITE if first else (120, 120, 126))
    _footer(draw, y + panel_h + 20, footer_left, footer_right)
    return _png(canvas)


def status_one(s, footer_left="", footer_right=""):
    """Live status of one server as its own picture, so everything on it is larger.
    s: dict with name, state (LIVE, SEEDING, EMPTY or OFFLINE), note, players, max,
    map, mode, weather, scores [(faction, points, players on)]."""
    state = str(s.get("state") or "OFFLINE").upper()
    active = state in ("LIVE", "SEEDING")
    top_h, score_h = 330, 84
    scores = (s.get("scores") or [])[:3]
    body_h = 26 + top_h + (26 + score_h * len(scores) if scores and state != "OFFLINE" else 0) + 22
    canvas, draw = _canvas(HEADER_H + body_h + FOOT_H, s.get("name") or "Server", "Live server status",
                           (s.get("note") or "") if state != "OFFLINE" else "")
    x0, x1, y = PAD, W - PAD, HEADER_H + 26
    draw.rectangle([x0, y, x1, y + body_h - 48], fill=ROW_A)
    draw.rectangle([x0, y, x1, y + 5], fill=RED if active else (70, 70, 76))
    if state == "OFFLINE":
        text(draw, W / 2, y + top_h / 2, "OFFLINE OR UNREACHABLE", "title", 80, SOFT, align="center")
        _footer(draw, HEADER_H + body_h, footer_left, footer_right)
        return _png(canvas)

    # Left: the player count, as large as it can be, with the state written out beside it.
    players, cap = s.get("players", 0), s.get("max", 0) or 100
    nw = text(draw, x0 + 36, y + 138, players, "title", 250, WHITE)
    text(draw, x0 + 36 + nw + 20, y + 176, f"/ {cap}", "semi", 56, SOFT)
    text(draw, x0 + 36 + nw + 20, y + 108, "PLAYERS", "semi", 30, DIM, spacing=4)
    tag_w = width(state, "bold", 40, 5) + 60
    split = x0 + (x1 - x0) * 0.47
    # The state tag sits in the top right corner, clear of the count even at 100 players.
    tx = x1 - 36 - tag_w
    if active:
        draw.rectangle([tx, y + 36, tx + tag_w, y + 104], fill=WHITE)
        text(draw, tx + tag_w / 2, y + 71, state, "bold", 40, (14, 14, 16), align="center", spacing=5)
    else:
        draw.rectangle([tx, y + 36, tx + tag_w, y + 104], outline=DIM, width=3)
        text(draw, tx + tag_w / 2, y + 71, state, "bold", 40, SOFT, align="center", spacing=5)
    _bar(draw, x0 + 36, y + 262, split - 40 - (x0 + 36), 18, players / cap)

    # Right: map, mode and weather.
    draw.line([(split, y + 40), (split, y + top_h - 30)], fill=LINE, width=2)
    fy = y + 78
    for label, value in (("MAP", s.get("map")), ("MODE", s.get("mode")), ("WEATHER", s.get("weather"))):
        text(draw, split + 40, fy - 30, label, "semi", 24, DIM, spacing=4)
        room = x1 - 36 - (split + 40) - (tag_w + 24 if label == "MAP" else 0)
        text(draw, split + 40, fy + 8, value or "Unknown", "semi", 44, (232, 230, 226),
             max_width=room, shrink_to=28)
        fy += 96

    # Faction scores across the full width.
    sy = y + top_h
    draw.line([(x0 + 36, sy), (x1 - 36, sy)], fill=LINE, width=2)
    best = max([p for _, p, _ in scores] + [1])
    lead = max([p for _, p, _ in scores] + [0])
    for j, (faction, pts, on) in enumerate(scores):
        cy = sy + 26 + j * score_h + score_h / 2 - 8
        first = lead > 0 and pts == lead
        text(draw, x0 + 36, cy, faction, "bold" if first else "semi", 46, WHITE if first else SOFT)
        text(draw, x1 - 36, cy, f"{on} on", "text", 34, DIM, align="right")
        text(draw, x1 - 180, cy, pts, "bold", 50, WHITE if first else SOFT, align="right")
        _bar(draw, x0 + 300, cy - 8, (x1 - 300) - (x0 + 300), 16, pts / best if lead else 0,
             WHITE if first else (120, 120, 126))
    _footer(draw, HEADER_H + body_h, footer_left, footer_right)
    return _png(canvas)


def seederboard_one(heading, subtitle, rows, right="", footer_left="", footer_right=""):
    """One seeding board as its own picture (for example just the last 30 days).
    rows: dicts with name, seconds, days."""
    n = max(1, len(rows))
    canvas, draw = _canvas(HEADER_H + COLS_H + ROW_H * n + FOOT_H, f"Seederboard · {heading}", subtitle, right)
    x0, x1 = PAD, W - PAD
    days_x, time_x = x1 - 24, x1 - (250 if COMPACT else 300)
    name_x = PAD + (96 if COMPACT else 110)
    y = HEADER_H + COLS_H / 2 + 2
    text(draw, PAD + 34, y, "#", "semi", 28, DIM, align="center")
    text(draw, name_x, y, "PLAYER", "semi", 28, DIM, spacing=3)
    text(draw, time_x, y, "SEEDING TIME", "semi", 28, DIM, align="right", spacing=3)
    text(draw, days_x, y, "DAYS", "semi", 28, DIM, align="right", spacing=3)
    top = HEADER_H + COLS_H
    draw.line([(x0, top - 1), (x1, top - 1)], fill=LINE, width=2)
    for i, r in enumerate(rows):
        y0 = top + i * ROW_H
        mid = y0 + ROW_H / 2
        rank = i + 1
        podium = rank <= 3
        draw.rectangle([x0, y0, x1, y0 + ROW_H - 3], fill=ROW_A if i % 2 == 0 else ROW_B)
        if podium:
            draw.rectangle([x0, y0, x0 + 6, y0 + ROW_H - 3], fill=MEDAL[rank - 1])
        _rank(draw, PAD + 34, mid, rank)
        days = r.get("days", 0)
        text(draw, days_x, mid + 1, f"{days} day{'' if days == 1 else 's'}", "text", 34, SOFT, align="right")
        tw = text(draw, time_x, mid + 1, _clock(r.get("seconds", 0)), "bold", 40, WHITE, align="right")
        text(draw, name_x, mid + 1, r.get("name") or "unknown", "bold" if podium else "semi", 38,
             WHITE if podium else (222, 220, 216), max_width=time_x - tw - 40 - name_x, shrink_to=29)
    if not rows:
        text(draw, W / 2, top + ROW_H / 2, "No seeding time recorded yet", "semi", 32, DIM, align="center")
    _footer(draw, top + ROW_H * n, footer_left, footer_right)
    return _png(canvas)


def awards_part(title, subtitle, categories, right="", footer_left="", footer_right=""):
    """A few award categories in one row, drawn large. Used to split the weekly
    awards across more than one picture. categories: (heading, [(name, value)])."""
    cats = categories[:3] or [("", [])]
    wide = len(cats) <= 2
    row_h, name_size, head_size = (118, 50, 72) if wide else (112, 42, 60)
    gap = 24
    panel_h = 118 + 3 * (row_h + 10) + 14
    canvas, draw = _canvas(HEADER_H + 26 + panel_h + 26 + FOOT_H, title, subtitle, right)
    y = HEADER_H + 26
    pw = (W - 2 * PAD - gap * (len(cats) - 1)) // len(cats)
    for i, (heading, winners) in enumerate(cats):
        x = PAD + i * (pw + gap)
        draw.rectangle([x, y, x + pw, y + panel_h], fill=ROW_A)
        draw.rectangle([x, y, x + pw, y + 5], fill=RED)
        text(draw, x + 28, y + 66, heading.upper(), "title", head_size, WHITE, max_width=pw - 56, shrink_to=44)
        for j in range(3):
            ry = y + 118 + j * (row_h + 10)
            draw.rectangle([x + 16, ry, x + pw - 16, ry + row_h], fill=ROW_B)
            if j >= len(winners):
                continue
            name, value = winners[j]
            if j == 0:
                draw.rectangle([x + 16, ry, x + 22, ry + row_h], fill=MEDAL[0])
            _rank(draw, x + 66, ry + row_h / 2, j + 1, r=30, size=42)
            vw = text(draw, x + pw - 38, ry + row_h / 2 + 2, value, "bold" if j == 0 else "semi", name_size,
                      WHITE if j == 0 else SOFT, align="right")
            text(draw, x + 116, ry + row_h / 2 + 2, name, "bold" if j == 0 else "semi", name_size,
                 WHITE if j == 0 else (222, 220, 216), max_width=pw - 116 - 38 - vw - 22, shrink_to=30)
        if not winners:
            text(draw, x + pw / 2, y + 118 + row_h * 1.5, "Nobody qualified this week", "semi", 32, DIM, align="center")
    _footer(draw, y + panel_h + 26, footer_left, footer_right)
    return _png(canvas)


def awards_one(heading, subtitle, winners, right="", footer_left="", footer_right=""):
    """One weekly award as its own picture: the top three for a single category.
    winners: [(name, value)]. Names are never cut off; a very long one is drawn smaller."""
    row_h, gap = 150, 12
    rows = 3
    canvas, draw = _canvas(HEADER_H + 28 + rows * (row_h + gap) - gap + 28 + FOOT_H,
                           f"Weekly awards · {heading}", subtitle, right)
    x0, x1 = PAD, W - PAD
    y = HEADER_H + 28
    for j in range(rows):
        ry = y + j * (row_h + gap)
        mid = ry + row_h / 2
        first = j == 0
        draw.rectangle([x0, ry, x1, ry + row_h], fill=ROW_A if first else ROW_B)
        if j >= len(winners):
            continue
        name, value = winners[j]
        draw.rectangle([x0, ry, x0 + 8, ry + row_h], fill=MEDAL[j])
        _rank(draw, x0 + 92, mid, j + 1, r=42, size=58)
        vw = text(draw, x1 - 44, mid + 2, value, "bold", 92 if first else 76,
                  WHITE if first else SOFT, align="right")
        text(draw, x0 + 176, mid + 2, name, "bold" if first else "semi", 72 if first else 62,
             WHITE if first else (222, 220, 216), max_width=x1 - 44 - vw - 48 - (x0 + 176), shrink_to=30)
    if not winners:
        text(draw, W / 2, y + row_h * 1.5 + gap, "Nobody qualified this week", "semi", 40, DIM, align="center")
    _footer(draw, y + rows * (row_h + gap) - gap + 28, footer_left, footer_right)
    return _png(canvas)


def numbers_week(heading, subtitle, stats, right="", footer_left="", footer_right=""):
    """Last week's headline numbers as one picture: one row per number.
    stats: [(label, value, small note)]."""
    row_h, gap = 112, 10
    n = max(1, len(stats))
    canvas, draw = _canvas(HEADER_H + 28 + n * (row_h + gap) - gap + 28 + FOOT_H,
                           f"Weekly numbers · {heading}", subtitle, right)
    x0, x1 = PAD, W - PAD
    y = HEADER_H + 28
    for j, (label, value, note) in enumerate(stats):
        ry = y + j * (row_h + gap)
        mid = ry + row_h / 2
        draw.rectangle([x0, ry, x1, ry + row_h], fill=ROW_A if j % 2 == 0 else ROW_B)
        draw.rectangle([x0, ry, x0 + 6, ry + row_h], fill=RED)
        vw = text(draw, x1 - 40, mid + 2, value, "bold", 78, WHITE, align="right")
        if note:
            vw += 24 + text(draw, x1 - 40 - vw - 24, mid + 6, note, "text", 34, DIM, align="right")
        text(draw, x0 + 44, mid + 2, label, "semi", 54, (222, 220, 216),
             max_width=x1 - 40 - vw - 40 - (x0 + 44), shrink_to=34)
    _footer(draw, y + n * (row_h + gap) - gap + 28, footer_left, footer_right)
    return _png(canvas)


def numbers_trend(heading, subtitle, rows, right="", footer_left="", footer_right=""):
    """The last four weeks beside the four before, one row per measure.
    rows: [(measure, value now, value before, change in words such as 'Up 12%')]."""
    row_h = 84
    n = max(1, len(rows))
    canvas, draw = _canvas(HEADER_H + COLS_H + row_h * n + FOOT_H, f"Weekly numbers · {heading}", subtitle, right)
    x0, x1 = PAD, W - PAD
    change_x, before_x, now_x = x1 - 28, x1 - 300, x1 - 540
    y = HEADER_H + COLS_H / 2 + 2
    text(draw, x0 + 30, y, "MEASURE", "semi", 28, DIM, spacing=3)
    text(draw, now_x, y, "LAST 4 WEEKS", "semi", 28, DIM, align="right", spacing=3)
    text(draw, before_x, y, "4 BEFORE", "semi", 28, DIM, align="right", spacing=3)
    text(draw, change_x, y, "CHANGE", "semi", 28, DIM, align="right", spacing=3)
    top = HEADER_H + COLS_H
    draw.line([(x0, top - 1), (x1, top - 1)], fill=LINE, width=2)
    for i, (measure, now, before, change) in enumerate(rows):
        y0 = top + i * row_h
        mid = y0 + row_h / 2
        draw.rectangle([x0, y0, x1, y0 + row_h - 3], fill=ROW_A if i % 2 == 0 else ROW_B)
        text(draw, change_x, mid + 1, change, "semi", 36, (222, 220, 216), align="right")
        text(draw, before_x, mid + 1, before, "text", 40, SOFT, align="right")
        nw = text(draw, now_x, mid + 1, now, "bold", 50, WHITE, align="right")
        text(draw, x0 + 30, mid + 1, measure, "semi", 42, (222, 220, 216),
             max_width=now_x - nw - 36 - (x0 + 30), shrink_to=30)
    _footer(draw, top + row_h * n, footer_left, footer_right)
    return _png(canvas)
