# Codex CLI: how the tmux workaround works

Codex's status footer only renders its own built-in fields; it has no hook for
an external command's output. Bifrost works around that by running the
unmodified Codex CLI inside a private tmux server and drawing the Bifrost line
in tmux's own status bar instead of Codex's footer. This is not a native
Codex feature, and plain `codex` will not show it.

## Install layout

```bash
python3 install-codex.py
```

This installs `~/.local/bin/bifrost`, a `/bin/sh` shim that execs
`../share/bifrost/bifrost.py`, plus a `share/bifrost/` copy of `bifrost.py`,
`codex_status.py`, and `statusline.py`. Use `--prefix /path/to/prefix` to
install somewhere other than `~/.local`.

Repeat installs are safe to re-run: any file whose contents changed is
backed up first, and re-installing unchanged content creates no backups.
Codex's own `config.toml` is not modified by this default install.

## Launching and resuming

Use the installed launcher in place of `codex`:

```bash
~/.local/bin/bifrost codex
~/.local/bin/bifrost codex resume
~/.local/bin/bifrost codex --model gpt-5-codex
~/.local/bin/bifrost codex --help
```

Every argument after `codex` reaches the real Codex CLI exactly as typed.
`bifrost` requires `tmux` and `codex` on `PATH` and exits with a clear message
if either is missing. Tested against Codex CLI 0.153.4 and tmux 3.7c.

To make plain `codex` resolve to the workaround in Zsh, add this to
`~/.zshrc`, then start a new shell:

```zsh
codex() {
  command bifrost codex "$@"
}
```

This changes interactive Zsh command resolution only; it does not make
Bifrost a native Codex feature. Use `command codex` to bypass it.

## The isolated tmux server

Each launch starts its own tmux server: `tmux -L bifrost-{pid}-{uuid8} -f
/dev/null`. It never reads `~/.tmux.conf` and never touches your default
tmux server, its sessions, or its config.

Options set on that private server: `history-limit 50000`,
`status-position bottom`, `mouse on`, `status-interval 5`,
`status-format[0]` calling back into `bifrost status --tmux`,
`default-terminal tmux-256color`, and truecolor `terminal-overrides`.
`exit-empty` is off so a session-closed hook can run cleanup before the
server exits.

Detach with `Ctrl-b d`; the launcher prints the exact reconnect command,
`tmux -L <server> attach -t codex`. Exiting Codex normally, in the
foreground or after a detach, tears down that session's isolated server and
removes its temporary state directory. Cleanup only ever deletes paths
directly under that temp directory with the `bifrost-codex-` prefix.

## Hook trust

Codex is launched with `--dangerously-bypass-hook-trust` and
`-c tui.status_line=[]` and a `SessionStart` hook binding. The bypass flag is
scoped to that one invocation, but within it, it lets **every enabled** hook
run without Codex's usual trust prompt, including anything already in your own
`config.toml`, not only the `SessionStart` hook Bifrost injects. Explicitly
disabled hooks stay disabled, and Codex prints a startup warning while the flag
is active. See Codex's own [CLI
reference](https://developers.openai.com/codex/cli/reference).

Bifrost's own `SessionStart` hook records exactly four fields: session id,
transcript path, model, and cwd. No permanent Codex config is touched.

Before the first prompt creates a Codex thread, the status line reads
"waiting for Codex session...", since Codex only fires `SessionStart` once a
thread exists, not at idle startup.

## Scrollback and mouse

The mouse wheel scrolls tmux's pane history, up to 50000 lines, instead of
cycling Codex's own input history. Press `q` to leave tmux copy mode after
scrolling. Hold `Shift` while dragging if your terminal uses that modifier to
bypass tmux for native text selection.

## Nested tmux

If Bifrost is launched from inside an existing tmux client, it still creates
a separate server and does not change the outer server, its sessions, or its
config. The outer client gets first chance at mouse and prefix events, so it
may capture the wheel. With tmux's default prefix:

- `Ctrl-b Ctrl-b [` enters Bifrost's inner scrollback
- `Ctrl-b Ctrl-b d` detaches the inner Bifrost session, leaving the outer one attached

The launcher pops `TMUX` and `TMUX_PANE` from the child environment when
nested, so the inner Codex process doesn't mistake itself for a client of the
outer server.

## Alternative: native Codex footer preset

Codex also supports a handful of built-in footer fields directly in
`config.toml`, no tmux required. This is lighter weight, at the cost of
Bifrost's gradient bars, context badge, and dirty-tree marker, none of which
this preset can render:

```bash
python3 install-codex.py --native-footer
```

This backs up and updates `$CODEX_HOME/config.toml` (default
`~/.codex/config.toml`), preserving unrelated settings. Restart Codex
afterward. Use `--config /path/to/config.toml` to target another file.

The preset sets, in order: model with reasoning, context used, five-hour
limit, weekly limit, and git branch. See the [Codex configuration
reference](https://learn.chatgpt.com/docs/config-file/config-reference#tui-status_line).
