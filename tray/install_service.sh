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

mkdir -p "$TARGET_DIR"
cp "$SERVICE_FILE" "$TARGET_FILE"

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
