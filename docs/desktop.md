# Floating desktop strip

`bifrost desktop` opens a small, frameless window that stays above other
windows. The wide horizontal strip sits beside the macOS Dock, with Claude Code
on the left and Codex on the right. With no arguments it shows both tools:

- **Claude Code**, read from `~/.claude/bifrost/claude-status.json`
- **Codex**, read from the rollout under `CODEX_HOME/sessions/**` (or
  `~/.codex/sessions/**`) that was written most recently

Each panel names the session whose context is displayed, alongside context usage
and the five-hour and weekly windows with their reset times. Codex names come
from its local `session_index.jsonl`, matched to the rollout's session UUID.
Claude names come from the matching session transcript's `custom-title` records.
If a title is unavailable, the label uses the project directory plus a short
session ID. Long names shorten with an ellipsis; hover over the name for the
full title, model, session ID, and working directory. Title reads are cached
and limited to the last 512 KiB of metadata; a title outside that range may
initially use the fallback. Context and quota reads continue if names are missing.

The label identifies the session supplying telemetry, rather than the foreground
chat: Codex follows the most recently written rollout, while Claude follows the
session last prompted. Five-hour and weekly quotas remain account-wide. Bars color by position on a smooth cyan → orange → magenta ramp. Low fill
stays cool; high fill reaches magenta. Percentage numbers use the same ramp
according to their current value, with unknown values shown in a neutral color.

A bar hides while its tool is not running (no process named `claude` or
`codex`), and comes back within two seconds of the tool starting. The Claude
desktop app alone doesn't count: its Code sessions run `claude` processes. A
source named with `--claude-status`, `--session` or `--rollout` always shows.

## Platforms

macOS and Linux. Windows is not supported yet: the `bifrost` launcher is a
shell script, and the Claude Code mod finds the home folder through `$HOME`
and finishes each write with `mv`. The Codex bar and the window itself use
only Python and PySide6.

## Install

```bash
python3 -m pip install PySide6
~/.local/bin/bifrost desktop
```

PySide6 must be installed in the Python environment the `bifrost` launcher
runs. The normal status line never imports it.

## The Claude Code bar

Claude Code pipes its status data only to a `statusLine` command, and the
Claude desktop app does not run that command at all. The mod in
`claude-mod/bifrost-status` reads the same figures from inside each session
and writes them to `~/.claude/bifrost/claude-status.json`, in the shape a
`statusLine` command receives on stdin. It also shows a compact
`ctx · 5h · 7d` line in the session itself.

Install it once:

```bash
mkdir -p ~/.claude/mods
cp -R claude-mod/bifrost-status ~/.claude/mods/
```

Then add it to the `env` block of `~/.claude/settings.json`:

```json
"CLAUDE_CODE_PLUGIN_DIRS": "~/.claude/mods/bifrost-status"
```

Sessions started after that load it, in the terminal and in the desktop app.

Several sessions can run at once, and the file holds one of them:

- The session you last sent a prompt to owns the file and writes its model
  and context.
- Every other session refreshes only the five-hour and weekly windows, which
  are account-wide.
- Switching to another session without typing leaves the bar on the previous
  one until you send a prompt.

The mod writes after each turn, when usage moves, and every 30 seconds, and
writes through a temporary file so the strip never reads half a file.

## The Codex bar

With no arguments the Codex bar follows the newest rollout, so it moves to
whichever Codex session is working. To pin it to one session or file instead,
open a single-source window:

```bash
~/.local/bin/bifrost desktop --session 123e4567-e89b-12d3-a456-426614174000 --title "My Codex chat"
~/.local/bin/bifrost desktop --rollout ~/.codex/sessions/2026/10/rollout-...jsonl
```

Session lookup, model recovery and platform notes: [codex.md](codex.md).

## Single-source windows

| Flag | Shows |
| --- | --- |
| `--claude-status [PATH]` | Claude Code only, from the default file or `PATH` |
| `--session UUID` | one Codex session, by UUID |
| `--rollout PATH` | one Codex rollout file |

`--title` renames the bar in a single-source window.

## Refresh and staleness

The strip re-reads every source every two seconds. A bar whose source has not
changed for 120 seconds is labeled stale and keeps its last figures. A bar
with no source yet (no status file, no rollout) waits and keeps checking.

## Window controls

Drag the BIFROST grip on the left to move the window. The pin button toggles always-on-top and
the × closes it. Hover over a source for its full status details. The angular frame and background
grid are static. It reopens where you last moved it, saved with Qt's
`QSettings` as `bifrost`/`desktop-strip` (on macOS,
`~/Library/Preferences/com.bifrost.desktop-strip.plist`). The horizontal layout saves its own `horizontal-position`, separate from the old
vertical layout. The first time, or when that spot is no longer on any screen,
it opens at the bottom-right of the primary screen, using the full screen so it
can occupy the band beside the Dock. Drag it to whichever side is free on your
desktop. Its height is fixed at 96 logical pixels, including room for emoji session names;
width follows the visible sources and quota columns. It stays within the screen
when those columns return.
