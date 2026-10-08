#!/bin/sh
set -eu

if [ "$(id -u)" -eq 0 ]; then
  echo "Run this setup as your regular macOS user; it does not need administrator access." >&2
  exit 1
fi

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ -f "$HERE/MaviRuntime/portable/requirements.txt" ]; then
  REQUIREMENTS="$HERE/MaviRuntime/portable/requirements.txt"
elif [ -f "$HERE/../portable/requirements.txt" ]; then
  REQUIREMENTS="$HERE/../portable/requirements.txt"
else
  echo "Could not find portable/requirements.txt. Rebuild or download the complete Mavi app." >&2
  exit 1
fi

DATA="$HOME/Library/Application Support/Mavi"
RUNTIME="$DATA/runtime"
PYENV="$RUNTIME/bin/python3"
mkdir -p "$DATA"
chmod 700 "$DATA"

if [ -n "${MAVI_PYTHON3:-}" ]; then
  PYTHON=$MAVI_PYTHON3
else
  PYTHON=
  for candidate in /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11 \
                   /usr/local/bin/python3.12 /usr/local/bin/python3.11 \
                   "$(command -v python3 2>/dev/null || true)"; do
    [ -n "$candidate" ] && [ -x "$candidate" ] || continue
    if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)' >/dev/null 2>&1; then
      PYTHON=$candidate
      break
    fi
  done
fi

if [ -z "${PYTHON:-}" ] || [ ! -x "$PYTHON" ] || ! "$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)' >/dev/null 2>&1; then
  echo "Install Python 3.11 or newer from https://www.python.org/downloads/macos/ and run this setup again." >&2
  echo "You can select a specific executable with MAVI_PYTHON3=/path/to/python3." >&2
  exit 1
fi

if [ ! -x "$PYENV" ]; then
  mkdir -p "$RUNTIME"
  "$PYTHON" -m venv "$RUNTIME"
fi
if ! "$PYENV" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)' >/dev/null 2>&1; then
  echo "The existing Mavi runtime is not Python 3.11+. Choose a supported interpreter and repair the runtime before launching Mavi." >&2
  exit 1
fi
"$PYENV" -m pip install -r "$REQUIREMENTS"
chmod -R go-rwx "$RUNTIME" 2>/dev/null || true
echo "Mavi's local Python runtime is ready at: $PYENV"
echo "Open Mavi.app to start its local service. No model weights were downloaded."
