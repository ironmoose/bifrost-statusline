# bifrost-statusline

A synthwave session HUD for Claude Code, plus a tmux workaround for Codex CLI. Model, context, and usage windows, one line.

![bifrost-statusline](assets/hero.gif)

Named for the Norse rainbow bridge. The gradient bar is the bridge: cyan when fresh, orange near a limit.

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

## What it shows

Model name, a compact context badge (`1M`), three gauges with a color dot, an optional git segment.

- `ctx`, context window used
- `5h`, rolling five-hour usage window
- `7d`, rolling weekly usage window
- `git`, current branch, `*` if dirty

Each gauge is a 10-cell bar, cyan to purple to neon orange. Cells snap to whole blocks; any nonzero usage lights at least one, so 1-5% never reads as empty.

![states](assets/states.png)

## Install

### Claude Code

```bash
curl -fsSL https://raw.githubusercontent.com/ironmoose/bifrost-statusline/main/install.sh | bash
```

Downloads `statusline.py` to `~/.claude/bifrost-statusline.py`, adds a `statusLine` entry to `~/.claude/settings.json`. Existing settings are backed up to `.bak` first; nothing else in the file is touched.

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

Needs Python 3.8+, no packages.

### Codex CLI

> [!IMPORTANT]
> Not a native Codex integration. Codex's footer can't run an external renderer, so Bifrost launches Codex inside a private tmux server and draws the line in tmux's status bar. Plain `codex` won't show it. Full detail in [docs/codex.md](docs/codex.md).

Needs Python 3.11+ and `tmux`.

```bash
python3 install-codex.py
```

```bash
~/.local/bin/bifrost codex
~/.local/bin/bifrost codex resume
~/.local/bin/bifrost codex --model gpt-5-codex
```

- Detach with `Ctrl-b d`. The launcher prints the reconnect command.
- Nested in another tmux session: `Ctrl-b Ctrl-b [` scrolls, `Ctrl-b Ctrl-b d` detaches the inner one.
- Prefer Codex's own footer, no tmux: `python3 install-codex.py --native-footer` (updates `config.toml`).

More in [docs/codex.md](docs/codex.md): install layout, the tmux server's isolation guarantees, the hook-trust caveat, scrollback controls.

## Configuration

Constants at the top of `statusline.py` control the layout:

- `SEGMENTS`, order of segments to render (drop `"git"` to skip it)
- `GRADIENT_FILL`, per-cell heat vs. one flat fill color
- `FRAME`, an optional neon capsule around each bar
- `GIT_LABEL` / `GIT_DIRTY`, the git segment's label and dirty marker
- `SYNTH_STOPS`, the color ramp: a list of `(position, (r, g, b))` stops

Numbers refresh once per response, for both Claude and Codex. Cadence, lag, and how `ctx` differs from Codex's own percentage: [docs/telemetry.md](docs/telemetry.md).

## Development

```bash
python3 -m unittest -v
```

Regenerating `assets/` needs Pillow and a monospace font with block glyphs (Liberation or DejaVu):

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
