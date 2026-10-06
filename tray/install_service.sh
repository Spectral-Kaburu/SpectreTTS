#!/usr/bin/env bash
#
# SpectreTTS - Systemd Service Installer
# -----------------------------------------
# Installs spectretts.service as a systemd --user unit and enables it
# to autostart at login. Safe to re-run.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_FILE="$SCRIPT_DIR/spectretts.service"
TARGET_DIR="$HOME/.config/systemd/user"
TARGET_FILE="$TARGET_DIR/spectretts.service"

if [ ! -f "$SERVICE_FILE" ]; then
    echo "ERROR: spectretts.service not found at $SERVICE_FILE"
    exit 1
fi

PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_PYTHON="$PROJECT_DIR/.venv/bin/python"
DAEMON_SCRIPT="$SCRIPT_DIR/daemon.py"

if [ ! -x "$VENV_PYTHON" ]; then
    echo "ERROR: Could not find Python at $VENV_PYTHON"
    echo "Please create a virtual environment at .venv first."
    exit 1
fi

mkdir -p "$TARGET_DIR"
sed -e "s|{{VENV_PYTHON}}|$VENV_PYTHON|g" \
    -e "s|{{DAEMON_SCRIPT}}|$DAEMON_SCRIPT|g" \
    -e "s|{{PROJECT_DIR}}|$PROJECT_DIR|g" \
    "$SERVICE_FILE" > "$TARGET_FILE"

echo "Reloading systemd user daemon..."
systemctl --user daemon-reload

echo "Enabling SpectreTTS to start at login..."
systemctl --user enable spectretts.service

echo "Starting SpectreTTS now..."
systemctl --user start spectretts.service

sleep 1
echo ""
echo "=== Status ==="
systemctl --user status spectretts.service --no-pager -l || true

echo ""
echo "Done. Useful commands:"
echo "  systemctl --user status spectretts     # check if running"
echo "  systemctl --user restart spectretts    # restart (e.g. after code changes)"
echo "  systemctl --user stop spectretts       # stop"
echo "  journalctl --user -u spectretts -f     # follow logs live"
