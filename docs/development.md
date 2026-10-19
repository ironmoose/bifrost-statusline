# Development

## Tests

```bash
python3 -m unittest -v
```

The desktop strip uses optional PySide6. To inspect it manually, install
PySide6 in the active Python environment and run
`bifrost desktop --session <session-uuid>` with a live Codex session.
`--rollout <path>` opens the same window for a specific fixture or saved
rollout.

## Regenerating assets

Regenerating `assets/` needs Pillow and a monospace font with block glyphs (Liberation or DejaVu):

```bash
python3 -m venv .render-venv
.render-venv/bin/pip install Pillow
.render-venv/bin/python tools/render.py
```
