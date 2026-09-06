# bifrost-statusline

A synthwave session HUD for Claude Code, plus a tmux workaround for Codex
CLI: model, context, and usage windows in one line.

![bifrost-statusline](assets/hero.gif)

Bifrost is the rainbow bridge of Norse myth; the gradient bar is the bridge,
cyan when fresh, arcing through purple and magenta, burning neon orange near
a limit.

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue.svg)](#install)

## What it shows

One line, printed once per invocation: model name and a compact context
badge (like `1M`), then three gauges with a leading color dot, plus an
optional git segment.

- `ctx`, how much of the context window is used
- `5h`, the rolling five-hour usage window
- `7d`, the rolling weekly (7-day) usage window
- `git`, current branch, with a `*` if the working tree is dirty

Each gauge is a 10-cell bar: cyan at low usage, through purple and magenta,
to neon orange near the limit. Cells snap to whole blocks, and any nonzero
usage lights at least one cell so 1-5% never reads as empty.

![states](assets/states.png)

## Install

### Claude Code

```bash
curl -fsSL https://raw.githubusercontent.com/ironmoose/bifrost-statusline/main/install.sh | bash
```

Downloads `statusline.py` to `~/.claude/bifrost-statusline.py` and adds a
`statusLine` entry to `~/.claude/settings.json`, without touching anything
else already in that file (existing settings are backed up to `.bak` first).

Manual install:

```bash
git clone https://github.com/ironmoose/bifrost-statusline.git
cd bifrost-statusline
./install.sh
```

Or copy `statusline.py` to `~/.claude/bifrost-statusline.py` yourself and add:

```json
"statusLine": {
  "type": "command",
  "command": "python3 ~/.claude/bifrost-statusline.py",
  "refreshInterval": 30
}
```

Requires python3 (3.8+), no packages. `git` is optional: the `git` segment
is omitted when `git` is not on `PATH`.

### Codex CLI

> [!IMPORTANT]
> Not a native Codex integration. Codex's footer can't run an external
> renderer, so Bifrost launches Codex inside a private tmux server and draws
> the line in tmux's status bar. Plain `codex` will not show it. Full detail
> in [docs/codex.md](docs/codex.md).

Requires Python 3.11+ and `tmux`.

```bash
python3 install-codex.py
```

Launch or resume through the installed launcher:

```bash
~/.local/bin/bifrost codex
~/.local/bin/bifrost codex resume
~/.local/bin/bifrost codex --model gpt-5-codex
```

- Detach with `Ctrl-b d`; the launcher prints the reconnect command.
- Nested inside another tmux session: `Ctrl-b Ctrl-b [` scrolls, `Ctrl-b Ctrl-b d` detaches the inner session.
- Prefer Codex's own footer, no tmux: `python3 install-codex.py --native-footer` (updates `config.toml`).

See [docs/codex.md](docs/codex.md) for the install layout, the tmux server's
isolation guarantees, the hook-trust caveat, and scrollback controls.

## Configuration

The constants at the top of `statusline.py` control the layout:

- `SEGMENTS`, the list and order of segments to render (drop `"git"` to skip it)
- `GRADIENT_FILL`, whether each bar cell heats up individually or the fill is one flat color
- `FRAME`, an optional neon capsule around each bar
- `GIT_LABEL` / `GIT_DIRTY`, the git segment's label and dirty marker
- `SYNTH_STOPS`, the color ramp, a list of `(position, (r, g, b))` stops

Numbers refresh once per response for both Claude and Codex; see
[docs/telemetry.md](docs/telemetry.md) for refresh cadence, lag, and how
`ctx` differs from Codex's own percentage.

## Development

```bash
python3 -m unittest -v
```

Regenerating `assets/` needs Pillow and a monospace font with block glyphs
(Liberation or DejaVu):

```bash
python3 -m venv .render-venv
.render-venv/bin/pip install Pillow
.render-venv/bin/python tools/render.py
```

## Example output

```
Opus 4.8 1M │ ● ctx ██████░░░░ 61% 610k/1M │ ● 5h ███████░░░ 73% 1h5m │ ● 7d █████░░░░░ 45% 3d5h
```

```
Opus 4.8 1M │ ● ctx █░░░░░░░░░ 6% 58k/1M │ ● 5h █░░░░░░░░░ 7% 4h12m │ ● 7d ██░░░░░░░░ 17% 5d2h │ git main
```

## License

MIT, see [LICENSE](LICENSE).
