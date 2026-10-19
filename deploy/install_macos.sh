#!/usr/bin/env bash
# Install the monitor as a macOS LaunchAgent (starts at login, restarted automatically).
#   bash deploy/install_macos.sh            # install + start
#   bash deploy/install_macos.sh --remove   # stop + uninstall
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL=com.egn321.sensormonitor
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
if [[ "${1:-}" == "--remove" ]]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"; echo "Removed."; exit 0
fi
PYTHON="$PROJECT_DIR/.venv/bin/python"
[[ -x "$PYTHON" ]] || PYTHON="$(command -v python3)"
mkdir -p "$HOME/Library/LaunchAgents" "$PROJECT_DIR/logs"
sed -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" -e "s|__PYTHON__|$PYTHON|g" \
    "$PROJECT_DIR/deploy/$LABEL.plist" > "$PLIST"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Installed. Dashboard: http://127.0.0.1:5000   Logs: $PROJECT_DIR/logs/"
