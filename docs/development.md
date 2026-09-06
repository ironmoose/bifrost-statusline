# Development

## Tests

```bash
python3 -m unittest -v
```

## Regenerating assets

Regenerating `assets/` needs Pillow and a monospace font with block glyphs (Liberation or DejaVu):

```bash
python3 -m venv .render-venv
.render-venv/bin/pip install Pillow
.render-venv/bin/python tools/render.py
```
