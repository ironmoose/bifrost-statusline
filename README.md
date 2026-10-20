# bifrost-statusline

A synthwave status line for Claude Code and Codex CLI: model, context, and usage windows in one line.

![bifrost-statusline](assets/hero.gif)

## What it shows

- `ctx`, context window used
- `5h`, rolling five-hour usage window
- `7d`, rolling weekly usage window
- `git`, current branch, `*` if dirty

![states](assets/states.png)

## Install

### Claude Code

**Auto:**

```bash
curl -fsSL https://raw.githubusercontent.com/ironmoose/bifrost-statusline/main/install.sh | bash
```

**Manual:** copy `statusline.py` to `~/.claude/bifrost-statusline.py` and add:

```json
"statusLine": {
  "type": "command",
  "command": "python3 ~/.claude/bifrost-statusline.py",
  "refreshInterval": 30
}
```

Needs Python 3.8+.

### Codex CLI

> [!IMPORTANT]
> Not a native Codex integration. Codex's footer can't run an external renderer, so Bifrost launches Codex inside a private tmux server and draws the line in tmux's status bar instead. Plain `codex` won't show it.

Needs Python 3.11+ and `tmux`.

```bash
python3 install-codex.py
```

```bash
~/.local/bin/bifrost codex
~/.local/bin/bifrost codex resume
~/.local/bin/bifrost codex --model gpt-5-codex
```

### Floating desktop strip

The optional desktop strip shows context, five-hour and weekly usage, reset
times, and the current model in a small always-on-top window. It needs PySide6
in addition to the normal Bifrost install:

```bash
python3 -m pip install PySide6
~/.local/bin/bifrost desktop --session 123e4567-e89b-12d3-a456-426614174000 --title "My Codex chat"
```

Use the UUID of the Codex session to bind the strip to that exact rollout.
You can also point it directly at a rollout with `--rollout /path/to/file.jsonl`.
The strip reads status only; it does not change Codex configuration or start a
Codex process. See [docs/codex.md](docs/codex.md) for session lookup and
platform notes.

Run `~/.local/bin/bifrost desktop` with no source to watch both tools in one
window: a Claude Code bar and a Codex bar, the Codex one following whichever
session wrote its rollout most recently.

For Claude Code alone, run `~/.local/bin/bifrost desktop --claude-status`. It reads
`~/.claude/bifrost/claude-status.json`, which a Claude Code session writes in the
same shape it pipes to a `statusLine` command. The session you last sent a prompt
to owns the file, so the strip follows the session you are working in; the 5h and
weekly windows are account-wide and stay current from any session. Pass a path to
read a different file.

The file comes from the Claude Code mod in `claude-mod/bifrost-status`, which also
shows a compact usage line in the session itself. Copy that folder to
`~/.claude/mods/bifrost-status` and add
`"CLAUDE_CODE_PLUGIN_DIRS": "~/.claude/mods/bifrost-status"` to the `env` block of
`~/.claude/settings.json`; sessions started after that load it.

## Configuration

Layout is controlled by constants at the top of `statusline.py`: segment order, gradient style vs. flat fill, an optional frame, and the color ramp. Refresh cadence and how `ctx` differs from Codex's own percentage: [docs/telemetry.md](docs/telemetry.md).

## Docs

- [docs/codex.md](docs/codex.md): Codex CLI setup and internals
- [docs/telemetry.md](docs/telemetry.md): how the numbers work
- [docs/development.md](docs/development.md): tests and regenerating assets

## License

MIT, see [LICENSE](LICENSE).
