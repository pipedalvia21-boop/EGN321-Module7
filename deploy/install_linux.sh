#!/usr/bin/env bash
# Install the monitor as a systemd service that starts at boot and restarts automatically.
#   sudo bash deploy/install_linux.sh            # install + start
#   sudo bash deploy/install_linux.sh --remove   # stop + uninstall
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT=/etc/systemd/system/sensor-monitor.service
if [[ "${1:-}" == "--remove" ]]; then
  systemctl disable --now sensor-monitor.service || true
  rm -f "$UNIT"; systemctl daemon-reload; echo "Removed."; exit 0
fi
PYTHON="$PROJECT_DIR/.venv/bin/python"
[[ -x "$PYTHON" ]] || PYTHON="$(command -v python3)"
RUN_USER="${SUDO_USER:-$(id -un)}"
sed -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" -e "s|__PYTHON__|$PYTHON|g" -e "s|__USER__|$RUN_USER|g" \
    "$PROJECT_DIR/deploy/sensor-monitor.service" > "$UNIT"
systemctl daemon-reload
systemctl enable --now sensor-monitor.service
systemctl --no-pager status sensor-monitor.service | head -5
echo "Dashboard: http://127.0.0.1:5000   Logs: $PROJECT_DIR/logs/   journalctl -u sensor-monitor -f"
