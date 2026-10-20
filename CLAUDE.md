# Bifrost statusline

Bifrost renders Claude Code and Codex usage, with an optional PySide6 desktop strip.

## Patterns

- Keep PySide6 optional; `bifrost.py` imports `desktop` only for the desktop command.
- Use `codex_status.py` for rollout parsing and `desktop_status.py` for strip data.
- `claude-mod/bifrost-status` writes `~/.claude/bifrost/claude-status.json` in the statusLine stdin shape; `desktop_status.present_status` reads that shape for both sources.

## Dependencies

- The normal CLI uses the Python standard library and local modules.
- `desktop.py` requires optional PySide6.
