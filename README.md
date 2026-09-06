# bifrost-statusline

A synthwave session HUD for Claude Code, plus a tmux-based workaround for
Codex CLI: model, context, and usage windows in one line.

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

### Codex CLI

> [!IMPORTANT]
> Bifrost's Codex support is **not a native Codex status-line integration**.
> Codex's footer only supports its built-in fields and cannot run an external
> renderer. Bifrost works around that limitation by launching the unmodified
> Codex CLI inside an isolated tmux server and drawing the Bifrost line in
> tmux's status bar. Running `codex` directly will not show Bifrost.

Requires Python 3.11+ and `tmux`. Tested against Codex CLI 0.153.4 and tmux
3.7c. Default install does not touch Codex's own `config.toml`; it lays
down a self-contained launcher instead:

```bash
python3 install-codex.py
```

That installs `~/.local/bin/bifrost` plus a `share/bifrost/` copy of
`bifrost.py`, `codex_status.py`, and `statusline.py`. Use `--prefix
/path/to/prefix` to install elsewhere. Repeat installs are idempotent;
changed files are backed up first, and a byte-identical repeat install
touches nothing.

Launch or resume Codex through the installed launcher instead of `codex`
directly. Every argument after `codex` reaches the real Codex CLI exactly
as typed -- flags first, a subcommand, a literal prompt, `--help`,
`--version`, or Codex's own `--` delimiter all pass through untouched:

```bash
~/.local/bin/bifrost codex
~/.local/bin/bifrost codex resume
~/.local/bin/bifrost codex --model gpt-5-codex
~/.local/bin/bifrost codex --help
```

To make plain `codex` use the workaround by default in Zsh, add this to
`~/.zshrc`, then start a new shell (or run `source ~/.zshrc`):

```zsh
codex() {
  command bifrost codex "$@"
}
```

This only changes interactive Zsh command resolution; it does not turn
Bifrost into a native Codex feature. Use `command codex` when you need to
bypass Bifrost and run the underlying CLI directly.

That opens Codex inside a dedicated tmux server (`-L`, isolated from any
tmux servers or `~/.tmux.conf` you already have) with a bottom status line
rendering the real Bifrost gauges: the same gradient bars the Claude
renderer draws, not Codex's own footer fields. Codex's UI has no way to
host an external command's output in place of its live footer, so tmux's
own status line is what carries it instead. The launch passes
`--dangerously-bypass-hook-trust`, scoped to that one invocation. That flag
is not scoped to Bifrost's own hook -- it lets **every** hook configured
for that invocation run without Codex's usual trust prompt, including
anything already in your own `config.toml`, not only the SessionStart hook
Bifrost injects (which itself only ever records the session id,
transcript path, model, and cwd, nothing else). No permanent Codex config
is touched.

Detach as usual (`Ctrl-b d`); the session and its state keep running, and
the launcher prints the exact `tmux -L ... attach` command to reconnect.
Exiting Codex normally -- in the foreground, or later after a detach --
tears down that session's isolated tmux server and its temporary state
directory. The mouse wheel scrolls tmux's pane history instead of cycling
through Codex's input history; press `q` to leave tmux copy mode after
scrolling. Hold `Shift` while dragging if your terminal uses that modifier
to bypass tmux for native text selection. Bifrost keeps up to 50,000 lines
of scrollback in its own isolated server.

If Bifrost is launched from inside an existing tmux client, it still creates
a separate server and does not change the outer server, its sessions, or its
configuration. The outer client gets first chance to handle mouse and prefix
events, though, so it may capture the wheel. With tmux's default prefix, use
`Ctrl-b Ctrl-b [` to enter Bifrost's inner scrollback and `Ctrl-b Ctrl-b d`
to detach the inner Bifrost session while leaving the outer one attached.

Before the first prompt creates a Codex thread, the status line reads
"waiting for Codex session...": Codex only fires SessionStart once a
thread exists, not at idle startup. The usage snapshot (token counts and
rate-limit percentages) refreshes once per response, same as Claude Code,
so those numbers hold steady between responses; the reset countdowns keep
ticking on Bifrost's five-second refresh, since they're computed live from
the last known reset time rather than frozen with the snapshot. A rate-limit
window Codex's own reply omits (5h or weekly) is shown as absent rather
than a fabricated 0%, and a window whose reset timestamp has already
passed is marked `stale` instead of counting down to a deadline that's
already wrong. The tmux renderer checks for updates every five seconds;
the countdown display itself is minute-granular.

The `ctx` gauge is raw last-turn occupancy
(`last_token_usage.total_tokens / model_context_window`), not Codex's own
native percentage, which subtracts a version-specific baseline before
displaying a number; the two will disagree, and raw occupancy was chosen
here to avoid hardcoding a baseline that shifts between Codex releases.
Codex's own native footer (see below) cannot be reconfigured to show this
math in place; it always renders its own baseline-adjusted number.

#### Alternative: native Codex footer preset (not Bifrost)

Codex also supports a handful of built-in footer fields directly in
`config.toml`, without tmux. It's a lighter touch if you'd rather not run
Codex inside tmux, at the cost of Bifrost's ANSI gradient bars,
context-size badge, and dirty-tree marker, none of which this preset can
render:

```bash
python3 install-codex.py --native-footer
```

This backs up and updates `$CODEX_HOME/config.toml` (default
`~/.codex/config.toml`), preserving unrelated settings. Restart Codex
afterward. Use `--config /path/to/config.toml` to target another file.

The preset selects model with reasoning, context used, five-hour limit,
weekly limit, and Git branch, in that order. Tested against Codex CLI
0.153.4. Codex's native usage fields have their own formatting and
availability; they do not reproduce Claude's used-percentage gauges or
countdown layout. See the [Codex configuration
reference](https://learn.chatgpt.com/docs/config-file/config-reference#tui-status_line).

### Claude Code

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
