#!/usr/bin/env python3
"""
A single-file, zero-dependency status line for Claude Code.

Reads the session JSON that Claude Code pipes on stdin and prints one
line: the model, a smooth-gradient context-usage bar, and both usage
windows (5-hour and weekly) with a live countdown to each reset.

No compile step, no third-party packages: drop this file anywhere,
point settings.json at it, done. Works on macOS and Linux.

Docs for the input schema: https://code.claude.com/docs/en/statusline
"""

import json
import os
import re
import subprocess
import sys
import time

# ---------------------------------------------------------------------------
# Configuration. Reorder or trim SEGMENTS to taste; each name maps to a
# render_* function below. Everything degrades gracefully when a field is
# missing, so removing a segment never breaks the others.
# ---------------------------------------------------------------------------
SEGMENTS = ["model", "context", "five_hour", "seven_day", "git"]

BAR_WIDTH = 10          # cells in each progress bar
USE_TRUECOLOR = True    # 24-bit gradient; set False for a 3-bucket fallback
GRADIENT_FILL = True    # color each fill cell by its position, so the bar's
                        # tip heats up as it fills (vs one flat color)
FRAME = False           # optional neon capsule "housing" around each bar
SEP = "  "              # gap between segments (a dim divider is added too)

FULL_CELL = "█"         # a filled gauge cell
TRACK = "░"             # unfilled portion of the bar (a dim neon channel)
TRACK_RGB = (92, 70, 130)

# Git segment markers. Kept to portable ASCII so the line renders the same in
# any terminal font (branch-fork glyphs like ⎇ are tofu in many monospaces).
GIT_LABEL = "git"       # dim leading label for the branch
GIT_DIRTY = "*"         # marks uncommitted changes

# Optional capsule caps (FRAME=True). ▐ / ▌ are half-blocks whose solid edge
# hugs the fill, so ▐████░░▌ reads as an enclosed gauge housing.
FRAME_L, FRAME_R = "▐", "▌"
FRAME_RGB = (162, 110, 255)   # neon purple housing

# Synthwave "sunset" ramp, low usage -> high usage:
# cyan (chill) -> neon purple -> hot magenta -> neon orange (hot / near limit).
# Hot still means danger, so the semantics survive the restyle.
SYNTH_STOPS = [
    (0.00, (0, 229, 255)),     # neon cyan
    (0.35, (162, 94, 255)),    # neon purple
    (0.65, (255, 46, 151)),    # hot magenta
    (0.85, (255, 108, 58)),    # neon orange
    (1.00, (255, 58, 92)),     # hot red-orange
]


# ---------------------------------------------------------------------------
# Color helpers
# ---------------------------------------------------------------------------
def _supports_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("CLICOLOR_FORCE"):
        return True
    return True  # Claude Code renders ANSI even off-tty (isatty() is not the gate)


_COLOR = _supports_color()


def _fg(r: int, g: int, b: int) -> str:
    if not _COLOR:
        return ""
    if USE_TRUECOLOR:
        return f"\033[38;2;{r};{g};{b}m"
    # 3-bucket fallback keyed off the green channel dominance
    if g > 150 and r < 150:
        return "\033[32m"
    if r > 180 and g > 120:
        return "\033[33m"
    return "\033[31m"


def _dim(s: str) -> str:
    return f"\033[2m{s}\033[0m" if _COLOR else s


def _reset() -> str:
    return "\033[0m" if _COLOR else ""


def _ramp(pct: float) -> tuple:
    """Synthwave sunset color for a 0..100 percentage via multi-stop lerp."""
    p = max(0.0, min(100.0, pct)) / 100.0
    for i in range(len(SYNTH_STOPS) - 1):
        p0, c0 = SYNTH_STOPS[i]
        p1, c1 = SYNTH_STOPS[i + 1]
        if p <= p1:
            t = 0.0 if p1 == p0 else (p - p0) / (p1 - p0)
            return tuple(int(c0[j] + t * (c1[j] - c0[j])) for j in range(3))
    return SYNTH_STOPS[-1][1]


def _dot(pct: float) -> str:
    """Leading status indicator whose color tracks the same ramp."""
    return f"{_fg(*_ramp(pct))}●{_reset()}"


def _cell_pct(index: int) -> float:
    """Ramp position for a fill cell at `index`, so the tip heats up as the
    bar fills (a nearly-full bar ends hot; a low bar stays cyan)."""
    return (index / (BAR_WIDTH - 1)) * 100 if BAR_WIDTH > 1 else 0.0


def filled_cells(pct: float) -> int:
    """How many whole cells to light for `pct`.

    Snaps to the nearest cell (partial half-blocks show terminal background
    on their empty side, reading as a confusing black sliver at the tip).
    Any nonzero usage lights at least one cell, so 1-5% never looks like a
    dead 0% gauge; only a true zero stays empty.
    """
    p = max(0.0, min(100.0, pct)) / 100.0
    n = int(p * BAR_WIDTH + 0.5)
    if pct > 0 and n == 0:
        n = 1
    return max(0, min(BAR_WIDTH, n))


def bar(pct: float) -> str:
    """A synthwave gauge of BAR_WIDTH solid cells."""
    full = filled_cells(pct)

    out = []
    if GRADIENT_FILL:
        for i in range(full):
            out.append(f"{_fg(*_ramp(_cell_pct(i)))}{FULL_CELL}{_reset()}")
    elif full:
        out.append(f"{_fg(*_ramp(pct))}{FULL_CELL * full}{_reset()}")

    track = TRACK * (BAR_WIDTH - full)
    if track:
        out.append(f"{_fg(*TRACK_RGB)}{track}{_reset()}")

    body = "".join(out)
    if FRAME and _COLOR:
        cap = _fg(*FRAME_RGB)
        return f"{cap}{FRAME_L}{_reset()}{body}{cap}{FRAME_R}{_reset()}"
    if FRAME:
        return f"{FRAME_L}{body}{FRAME_R}"
    return body


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------
def human_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return str(int(n))


def until(resets_at: int) -> str:
    """Compact 'time remaining' from a unix-epoch reset timestamp.

    Returns "" for an already-passed timestamp. Claude Code is supposed to
    drop a usage window once its reset passes, but it sometimes ships a stale
    window with a past reset instead; showing no countdown there beats
    printing a confident but wrong one.
    """
    secs = int(resets_at) - int(time.time())
    if secs <= 0:
        return ""
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m, _ = divmod(rem, 60)
    if d:
        return f"{d}d{h}h"
    if h:
        return f"{h}h{m}m"
    return f"{m}m"


# ---------------------------------------------------------------------------
# Segment renderers. Each returns a string, or "" to be skipped.
# ---------------------------------------------------------------------------
def render_model(data: dict) -> str:
    m = data.get("model", {})
    name = m.get("display_name") or m.get("id") or "?"
    # Pull a "(1M context)" / "(200k context)" suffix into a compact,
    # normal-size dim badge. Superscript glyphs render too small to read.
    badge = ""
    match = re.search(r"\s*\((\d+[MmKk]?) context\)", name)
    if match:
        badge = " " + _dim(match.group(1).upper())
        name = name[: match.start()].strip()
    bold = f"\033[1m{name}{_reset()}" if _COLOR else name
    return bold + badge


def render_context(data: dict) -> str:
    ctx = data.get("context_window") or {}
    pct = ctx.get("used_percentage")
    if pct is None:
        return f"{_dim('ctx')} n/a"
    used = (ctx.get("total_input_tokens") or 0) + (ctx.get("total_output_tokens") or 0)
    size = ctx.get("context_window_size") or 0
    tail = f" {_dim(human_tokens(used) + '/' + human_tokens(size))}" if size else ""
    return f"{_dot(pct)} {_dim('ctx')} {bar(pct)} {pct:.0f}%{tail}"


def _window(data: dict, key: str, label: str) -> str:
    rl = (data.get("rate_limits") or {}).get(key)
    if not rl or rl.get("used_percentage") is None:
        return ""
    pct = rl["used_percentage"]
    tail = ""
    if rl.get("stale"):
        # A stale window's resets_at is untrustworthy; annotate instead of
        # counting down to a deadline that may already be wrong.
        tail = f" {_dim('stale')}"
    elif rl.get("resets_at"):
        u = until(rl["resets_at"])
        if u:
            tail = f" {_dim(u)}"
    return f"{_dot(pct)} {_dim(label)} {bar(pct)} {pct:.0f}%{tail}"


def render_five_hour(data: dict) -> str:
    return _window(data, "five_hour", "5h")


def render_seven_day(data: dict) -> str:
    return _window(data, "seven_day", "7d")


def render_git(data: dict) -> str:
    cwd = (data.get("workspace") or {}).get("current_dir") or data.get("cwd")
    if not cwd or not os.path.isdir(cwd):
        return ""
    try:
        branch = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=0.3,
        )
        if branch.returncode != 0:
            return ""
        name = branch.stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", cwd, "status", "--porcelain"],
            capture_output=True, text=True, timeout=0.3,
        )
        flag = f"{_fg(*FRAME_RGB)}{GIT_DIRTY}{_reset()}" if dirty.stdout.strip() else ""
        return f"{_dim(GIT_LABEL)} {name}{flag}"
    except (OSError, subprocess.SubprocessError):
        return ""


RENDERERS = {
    "model": render_model,
    "context": render_context,
    "five_hour": render_five_hour,
    "seven_day": render_seven_day,
    "git": render_git,
}


def render(data: dict) -> str:
    """Render one statusline from a Claude Code (or normalized Codex) payload."""
    parts = []
    for seg in SEGMENTS:
        fn = RENDERERS.get(seg)
        if not fn:
            continue
        try:
            out = fn(data)
        except Exception:
            out = ""
        if out:
            parts.append(out)

    divider = _dim("│")
    return f"{SEP}{divider}{SEP}".join(parts)


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        data = {}

    print(render(data))


if __name__ == "__main__":
    main()
