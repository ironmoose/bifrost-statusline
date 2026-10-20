# Development

## Tests

```bash
python3 -m unittest -v
```

The desktop strip uses optional PySide6. To inspect it manually, install
PySide6 in the active Python environment and run `bifrost desktop` with a
live Claude Code session and a live Codex session. `--session <session-uuid>`,
`--rollout <path>` and `--claude-status <path>` open a single-source window,
for example on a fixture or saved file. The desktop UI tests run only when
PySide6 is installed.

The Claude Code mod has its own tests:

```bash
claude plugin validate claude-mod/bifrost-status
claude plugin test claude-mod/bifrost-status
```

## Regenerating assets

Regenerating `assets/` needs Pillow and a monospace font with block glyphs (Liberation or DejaVu):

```bash
python3 -m venv .render-venv
.render-venv/bin/pip install Pillow
.render-venv/bin/python tools/render.py
```
