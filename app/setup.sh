#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")"
if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  print -u2 "Mavi currently requires Apple Silicon macOS."
  exit 1
fi
version="${MAVI_VERSION:-2.0.1}"
MAVI_VERSION="$version" ./build.sh
app="${MAVI_BUILD_DIR:-${TMPDIR:-/tmp}/mavi-build}/Mavi.app"
install_dir="$HOME/Applications"
mkdir -p "$install_dir"
if [[ -e "$install_dir/Mavi.app" ]]; then
  mv "$install_dir/Mavi.app" "$install_dir/Mavi.app.backup.$(date +%Y%m%d%H%M%S)"
fi
cp -R "$app" "$install_dir/Mavi.app"
print "Installed $install_dir/Mavi.app"
if command -v ollama >/dev/null 2>&1; then
  print "Ollama is installed. Mavi uses local models; pull qwen3:4b if you want a small starter chat model."
  read "answer?Download qwen3:4b now? (y/N) "
  if [[ "$answer" == [yY] ]]; then ollama pull qwen3:4b; fi
else
  print "Install Ollama separately, then optionally run: ollama pull qwen3:4b"
fi
open "$install_dir/Mavi.app"
