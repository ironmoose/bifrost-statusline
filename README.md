# bifrost-statusline

A synthwave session HUD for Claude Code: model, context, and usage windows in one line.

![bifrost-statusline](assets/hero.gif)

The name comes from the Bifrost, the rainbow bridge of Norse myth. The gradient
bar is the bridge: cyan when you're fresh, arcing through purple and magenta,
and burning neon orange as you close in on a limit.

## What it shows

One line, printed once per invocation: the model name and a compact context
badge (like `1M`), then three gauges with a leading color dot, plus an
optional git segment.

- `ctx`, how much of the context window is used
- `5h`, the rolling five-hour usage window
- `7d`, the rolling weekly (7-day) usage window
- `git`, current branch, with a `*` if the working tree is dirty

Each gauge is a 10-cell bar filled with a synthwave gradient (cyan at low
usage, through neon purple and hot magenta, to neon orange near the limit),
so a hotter color always means closer to the limit. Cells snap to whole
blocks, and any nonzero usage lights at least one cell so 1-5% never reads
as a dead, empty bar.

![states](assets/states.png)

## Install

One line, from the public repo (branch `main`):

```
curl -fsSL https://raw.githubusercontent.com/ironmoose/bifrost-statusline/main/install.sh | bash
```

That downloads `statusline.py` to `~/.claude/bifrost-statusline.py` and adds
the `statusLine` entry to `~/.claude/settings.json`, without touching
anything else already in that file.

Manual install, if you'd rather do it by hand:

```
git clone https://github.com/ironmoose/bifrost-statusline.git
cd bifrost-statusline
./install.sh
```

or copy `statusline.py` to `~/.claude/bifrost-statusline.py` yourself and add
this block to `~/.claude/settings.json`:

```json
"statusLine": {
  "type": "command",
  "command": "python3 ~/.claude/bifrost-statusline.py",
  "refreshInterval": 30
}
```

Requirements: python3 (3.8+) and `git` on your `PATH`. Nothing else, no
packages to install.

## How it works

Claude Code pipes a JSON blob on stdin describing the current session
(model, context window, rate limits, working directory) and expects one
printed line of ANSI back. See Claude Code's own docs for the full schema:
https://code.claude.com/docs/en/statusline. A sanitized example payload
lives at `tools/sample-payload.json`.

Claude Code debounces status updates by about 300ms and cancels an
in-flight script if a newer update arrives before it finishes, so the
script has to be fast and reliable under that pressure. That's why
`statusline.py` is pure standard library: no imports to resolve, no
interpreter startup tax beyond python3 itself. `refreshInterval` (minimum
1 second) re-runs the command on a timer too, so the countdowns keep
ticking while you're idle.

## A note on the usage windows

The `5h` and `7d` percentages come straight from Claude Code and only
refresh after an API response, so they can lag a live session by a turn or
two. Claude Code is also supposed to drop a usage window once its reset
timestamp passes, but it sometimes ships a stale window with a reset
already in the past instead. When that happens, `bifrost-statusline` shows
the percentage with no countdown rather than a confident, wrong one.

## Configuration

The constants at the top of `statusline.py` control the layout:

- `SEGMENTS`, the list and order of segments to render (drop `"git"` to skip it, for example)
- `GRADIENT_FILL`, whether each bar cell heats up individually or the whole fill is one flat color
- `FRAME`, an optional neon capsule housing around each bar
- `GIT_LABEL` / `GIT_DIRTY`, the git segment's label and dirty marker
- `SYNTH_STOPS`, the color ramp itself, a list of `(position, (r, g, b))` stops

## Development

Regenerate the README graphics (requires Pillow, only for asset generation).
The renderer also needs a monospace font with block glyphs installed
(Liberation or DejaVu), e.g. `sudo dnf install liberation-fonts` or
`sudo apt install fonts-liberation`:

```
python3 -m venv .render-venv
.render-venv/bin/pip install Pillow
.render-venv/bin/python tools/render.py
```

Run the tests (stdlib only, no venv needed):

```
python3 -m unittest -v
```

## Example output

```
Opus 4.8 1M │ ● ctx ██████░░░░ 61% 610k/1M │ ● 5h ███████░░░ 73% 1h5m │ ● 7d █████░░░░░ 45% 3d5h
```

```
Opus 4.8 1M │ ● ctx █░░░░░░░░░ 6% 58k/1M │ ● 5h █░░░░░░░░░ 7% 4h12m │ ● 7d ██░░░░░░░░ 17% 5d2h │ git main
```

## License

MIT, see `LICENSE`.
