#!/usr/bin/env python3
"""
Render the README graphics for bifrost-statusline.

Colors and bar geometry are imported straight from ../statusline.py, so the
screenshots can never drift from what the script actually prints. Requires
Pillow (only for generating assets; the status line itself is pure stdlib).

    python3 tools/render.py
"""

import importlib.util
import os
import sys

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ASSETS = os.path.join(ROOT, "assets")

# --- pull the single source of truth from the status line itself ----------
spec = importlib.util.spec_from_file_location("bifrost", os.path.join(ROOT, "statusline.py"))
sl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sl)

BAR_WIDTH = sl.BAR_WIDTH
TRACK_RGB = sl.TRACK_RGB
ramp = sl._ramp
cell_pct = sl._cell_pct

# --- palette / type --------------------------------------------------------
BG = (13, 9, 23)
PANEL = (18, 12, 32)
PANEL_EDGE = (44, 26, 77)
INK = (236, 230, 251)
MUTED = (154, 134, 196)
DIM = (107, 90, 138)
DIVIDER = (61, 44, 99)
CYAN = (0, 229, 255)
MAGENTA = (255, 46, 151)

# Candidate (regular, bold) monospace font pairs, in priority order. All of
# these ship the block-element glyphs (e.g. FULL_CELL) the renderer needs.
FONT_CANDIDATES = [
    ("/usr/share/fonts/liberation-mono-fonts/LiberationMono-Regular.ttf",
     "/usr/share/fonts/liberation-mono-fonts/LiberationMono-Bold.ttf"),
    ("/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
     "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf"),
    ("/usr/share/fonts/dejavu-sans-mono-fonts/DejaVuSansMono.ttf",
     "/usr/share/fonts/dejavu-sans-mono-fonts/DejaVuSansMono-Bold.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"),
    ("/System/Library/Fonts/Menlo.ttc", "/System/Library/Fonts/Menlo.ttc"),
]


def _resolve_font_paths():
    for regular, bold in FONT_CANDIDATES:
        if os.path.exists(regular) and os.path.exists(bold):
            return regular, bold
    print(
        "warning: no monospace font with block glyphs found (need Liberation "
        "or DejaVu Mono); looked in " + ", ".join(r for r, _ in FONT_CANDIDATES)
        + "; falling back to a default font that may render bars incorrectly",
        file=sys.stderr,
    )
    return None, None


MONO, MONO_BOLD = _resolve_font_paths()

SS = 3  # supersample factor for crisp text, downscaled at save


def font(size, bold=False):
    path = MONO_BOLD if bold else MONO
    if path is None:
        try:
            return ImageFont.load_default(size * SS)
        except TypeError:
            return ImageFont.load_default()
    return ImageFont.truetype(path, size * SS)


def _len(draw, s, f):
    return draw.textlength(s, font=f)


def run(draw, x, y, s, f, fill):
    draw.text((x, y), s, font=f, fill=fill)
    return x + _len(draw, s, f)


def draw_bar(draw, x, y, pct, f, line_h):
    """Draw a BAR_WIDTH gauge as seamless cells; returns the new x."""
    cw = _len(draw, "█", f)
    full = sl.filled_cells(pct)
    pad = int(line_h * 0.16)
    top, bot = y + pad, y + line_h - pad
    for i in range(BAR_WIDTH):
        cx0 = x + i * cw
        cx1 = x + (i + 1) * cw
        col = ramp(cell_pct(i)) if i < full else TRACK_RGB
        # 1px seam removal by rounding outward
        draw.rectangle([round(cx0), top, round(cx1), bot], fill=col)
    return x + BAR_WIDTH * cw


def measure_line(draw, state, f, fb, line_h):
    """Total pixel width of a rendered status line (for centering / sizing)."""
    return _draw_line(draw, 0, 0, state, f, fb, line_h, measure=True)


def _seg(draw, x, y, pct, label, tail, f, line_h, measure):
    if not measure:
        run(draw, x, y, "●", f, ramp(pct))
    x += _len(draw, "● ", f)
    if not measure:
        run(draw, x, y, label, f, DIM)
    x += _len(draw, label + " ", f)
    x = draw_bar(draw if not measure else draw, x, y, pct, f, line_h) if not measure else x + _len(draw, "█" * BAR_WIDTH, f)
    x += _len(draw, " ", f)
    pct_s = f"{pct:.0f}%"
    if not measure:
        run(draw, x, y, pct_s, f, INK)
    x += _len(draw, pct_s, f)
    if tail:
        x += _len(draw, " ", f)
        if not measure:
            run(draw, x, y, tail, f, DIM)
        x += _len(draw, tail, f)
    return x


def _draw_line(draw, x, y, state, f, fb, line_h, measure=False):
    x0 = x
    name = state.get("model", "Opus 4.8")
    if not measure:
        run(draw, x, y, name, fb, INK)
    x += _len(draw, name, fb)
    badge = state.get("badge", "1M")
    if badge:
        x += _len(draw, " ", f)
        if not measure:
            run(draw, x, y, badge, f, DIM)
        x += _len(draw, badge, f)

    def divider():
        nonlocal x
        x += _len(draw, "  ", f)
        if not measure:
            run(draw, x, y, "│", f, DIVIDER)
        x += _len(draw, "│  ", f)

    if "ctx" in state:
        divider()
        x = _seg(draw, x, y, state["ctx"], "ctx", state.get("ctxt", ""), f, line_h, measure)
    for key, label in (("h5", "5h"), ("wk", "7d")):
        if key in state:
            divider()
            x = _seg(draw, x, y, state[key], label, state.get(key + "t", ""), f, line_h, measure)
    if state.get("git"):
        divider()
        if not measure:
            xx = run(draw, x, y, "git ", f, DIM)
            xx = run(draw, xx, y, state["git"], f, MUTED)
            if state.get("dirty"):
                run(draw, xx, y, "*", f, MAGENTA)
        x += _len(draw, "git " + state["git"] + ("*" if state.get("dirty") else ""), f)
    return x - x0


def window_card(rows, title, size, pad, row_h, top_chrome):
    """A dark 'terminal window' card containing pre-rendered rows."""
    W, H = size
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    # panel
    d.rounded_rectangle([pad, pad, W - pad, H - pad], radius=14 * SS,
                        fill=PANEL, outline=PANEL_EDGE, width=SS)
    # neon top edge
    d.rounded_rectangle([pad, pad, W - pad, pad + 6 * SS], radius=6 * SS, fill=(40, 22, 60))
    # traffic lights + title
    fchrome = font(11)
    cy = pad + int(top_chrome * 0.5)
    for i, col in enumerate([(255, 95, 109), (255, 189, 68), (95, 226, 122)]):
        cx = pad + (22 + i * 26) * SS
        r = 7 * SS
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col)
    d.text((pad + (120) * SS, cy - 8 * SS), title, font=fchrome, fill=DIM)
    return img, d


def render_states():
    f = font(19)
    fb = font(19, bold=True)
    probe = Image.new("RGB", (10, 10))
    pd = ImageDraw.Draw(probe)
    asc, desc = f.getmetrics()
    line_h = asc + desc

    rows = [
        ("fresh",    {"ctx": 6,  "ctxt": "58k/1M",  "h5": 7,  "h5t": "4h12m", "wk": 17, "wkt": "5d2h"}),
        ("cruising", {"ctx": 34, "ctxt": "340k/1M", "h5": 41, "h5t": "2h48m", "wk": 33, "wkt": "4d6h"}),
        ("deep",     {"ctx": 61, "ctxt": "610k/1M", "h5": 73, "h5t": "1h05m", "wk": 45, "wkt": "3d5h"}),
        ("watch it", {"ctx": 88, "ctxt": "880k/1M", "h5": 96, "h5t": "18m",   "wk": 66, "wkt": "5d18h", "git": "main"}),
        ("ragnarok", {"ctx": 97, "ctxt": "970k/1M", "h5": 99, "h5t": "6m",    "wk": 81, "wkt": "2d1h",  "git": "feat/gauges", "dirty": True}),
    ]
    widths = [measure_line(pd, s, f, fb, line_h) for _, s in rows]
    tag_w = int(max(_len(pd, t, f) for t, _ in rows)) + 24 * SS
    content_w = int(max(widths))
    pad = 20 * SS
    inner_pad = 26 * SS
    top_chrome = 46 * SS
    row_gap = 16 * SS
    W = pad * 2 + inner_pad * 2 + tag_w + content_w
    H = pad * 2 + top_chrome + inner_pad + len(rows) * (line_h + row_gap)

    img, d = window_card(rows, "bifrost-statusline", (W, H), pad, line_h, top_chrome)
    y = pad + top_chrome + int(inner_pad * 0.5)
    x0 = pad + inner_pad
    for (tag, state), w in zip(rows, widths):
        d.text((x0, y), tag, font=f, fill=(120, 100, 160))
        _draw_line(d, x0 + tag_w, y, state, f, fb, line_h)
        y += line_h + row_gap

    out = img.resize((W // SS, H // SS), Image.LANCZOS)
    os.makedirs(ASSETS, exist_ok=True)
    out.save(os.path.join(ASSETS, "states.png"))
    print("wrote assets/states.png", out.size)


def render_hero_gif():
    f = font(22)
    fb = font(22, bold=True)
    asc, desc = f.getmetrics()
    line_h = asc + desc
    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))

    def state_at(pct):
        return {"ctx": pct, "ctxt": f"{int(pct*10)}k/1M",
                "h5": min(100, pct + 6), "h5t": "1h05m",
                "wk": 17, "wkt": "5d2h"}

    wmax = max(measure_line(probe, state_at(p), f, fb, line_h) for p in (0, 100))
    pad = 20 * SS
    inner = 30 * SS
    top_chrome = 50 * SS
    W = pad * 2 + inner * 2 + int(wmax)
    H = pad * 2 + top_chrome + inner + line_h

    frames = []
    seq = list(range(0, 101, 2)) + [100] * 14 + list(range(100, -1, -3)) + [0] * 8
    for p in seq:
        img, d = window_card([], "bifrost-statusline  ~  session watch", (W, H), pad, line_h, top_chrome)
        y = pad + top_chrome + int(inner * 0.4)
        _draw_line(d, pad + inner, y, state_at(p), f, fb, line_h)
        frames.append(img.resize((W // SS, H // SS), Image.LANCZOS).convert("P", palette=Image.ADAPTIVE, colors=96))
    os.makedirs(ASSETS, exist_ok=True)
    # Full opaque frames + optimize=False: every frame is complete, so the
    # static parts (name, labels, other gauges) never blink out mid-play.
    frames[0].save(os.path.join(ASSETS, "hero.gif"), save_all=True, append_images=frames[1:],
                   duration=60, loop=0, disposal=1, optimize=False)
    print("wrote assets/hero.gif", frames[0].size, len(frames), "frames")


if __name__ == "__main__":
    render_states()
    render_hero_gif()
