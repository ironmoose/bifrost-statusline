#!/usr/bin/env bash
# Installs bifrost-statusline into ~/.claude and registers it in
# ~/.claude/settings.json. Safe to re-run: it merges the statusLine block
# without touching any other settings, and backs up the file first.
set -eu

REPO_RAW_BASE="https://raw.githubusercontent.com/ironmoose/bifrost-statusline/main"
CLAUDE_DIR="${HOME}/.claude"
DEST="${CLAUDE_DIR}/bifrost-statusline.py"
SETTINGS="${CLAUDE_DIR}/settings.json"

mkdir -p "${CLAUDE_DIR}"

# When run from a local clone (or ./install.sh next to statusline.py), copy
# the local file. Otherwise (curl | bash) there is no script file on disk to
# look next to, so download from the raw GitHub URL instead.
LOCAL_SOURCE=""
if [ -n "${BASH_SOURCE:-}" ]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
  if [ -f "${SCRIPT_DIR}/statusline.py" ]; then
    LOCAL_SOURCE="${SCRIPT_DIR}/statusline.py"
  fi
fi

if [ -n "${LOCAL_SOURCE}" ]; then
  cp "${LOCAL_SOURCE}" "${DEST}"
  echo "Installed statusline.py from ${LOCAL_SOURCE}"
else
  if ! command -v curl >/dev/null 2>&1; then
    echo "error: curl is required to download statusline.py (no local copy found)" >&2
    exit 1
  fi
  curl -fsSL "${REPO_RAW_BASE}/statusline.py" -o "${DEST}"
  echo "Downloaded statusline.py from ${REPO_RAW_BASE}/statusline.py"
fi

chmod +x "${DEST}"
echo "Installed to ${DEST}"

if ! command -v python3 >/dev/null 2>&1; then
  echo "error: python3 is required to update ${SETTINGS}" >&2
  exit 1
fi

python3 - "${SETTINGS}" <<'PYEOF'
import json
import os
import sys

path = sys.argv[1]
data = {}

if os.path.exists(path):
    with open(path, "r") as f:
        raw = f.read()
    if raw.strip():
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            sys.stderr.write(
                "error: %s is not valid JSON, aborting so nothing gets clobbered: %s\n" % (path, e)
            )
            sys.exit(1)
    backup = path + ".bak"
    with open(backup, "w") as f:
        f.write(raw)
    print("Backed up existing settings to %s" % backup)

if not isinstance(data, dict):
    sys.stderr.write("error: %s does not contain a JSON object, aborting\n" % path)
    sys.exit(1)

data["statusLine"] = {
    "type": "command",
    "command": "python3 ~/.claude/bifrost-statusline.py",
    "refreshInterval": 30,
}

with open(path, "w") as f:
    json.dump(data, f, indent=2)
    f.write("\n")

print("Updated %s with the statusLine block" % path)
PYEOF

echo ""
echo "Done. The status line will start showing up within about 300ms of your"
echo "next Claude Code update (restart Claude Code if it doesn't appear)."
