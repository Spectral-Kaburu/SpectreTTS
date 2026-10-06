#!/usr/bin/env bash
# SpectreTTS - Full Setup Script
# Run this from the root of the project to set up everything automatically.

set -e

# Always run from the repository root directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== SpectreTTS Setup ==="
echo "Repository root: $SCRIPT_DIR"

# 1. Check for system dependencies
echo "Checking system dependencies..."
REQUIRED_PKGS="espeak-ng libportaudio2 xclip xdotool python3-gi python3-gi-cairo gir1.2-gtk-3.0 libayatana-appindicator3-dev gir1.2-ayatanaappindicator3-0.1 libgirepository-2.0-dev libcairo2-dev pkg-config python3-dev gcc"
MISSING=""
for pkg in $REQUIRED_PKGS; do
    if ! dpkg -s "$pkg" >/dev/null 2>&1; then
        MISSING="$MISSING $pkg"
    fi
done

if [ -n "$MISSING" ]; then
    echo "Missing system packages:$MISSING"
    echo "Please install them via:"
    echo "  sudo apt install -y$MISSING"
    exit 1
fi
echo "All system dependencies found."

# 2. Virtual Environment Setup
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
else
    echo "Virtual environment already exists."
fi

# 3. Install Python Dependencies
echo "Installing Python dependencies..."
./.venv/bin/pip install -r requirements.txt

# 4. Generate Default .env if missing
if [ ! -f ".env" ]; then
    echo "Generating default .env file..."
    cat << 'EOF' > .env
# SpectreTTS — environment configuration
# Loaded automatically by engine/configs.py via python-dotenv.

# ── TTS Backend ─────────────────────────────────────────────────────────────
# Which TTS engine to use. Options: "pocket", "kokoro", "piper"
SPECTRETTS_BACKEND=piper

# ── Pocket-TTS options (only used when SPECTRETTS_BACKEND=pocket) ────────────
# Language model to load. Options: english, french, german, portuguese, italian, spanish
# Append "_24l" for the larger/higher-quality variant (e.g. "english_24l")
SPECTRETTS_POCKET_LANGUAGE=english

# ── Piper-TTS options (only used when SPECTRETTS_BACKEND=piper) ──────────────
# Voice to load. See engine/backends/piper_backend.py for options.
# Default: en_US-lessac-medium
# SPECTRETTS_PIPER_VOICE=en_US-lessac-medium

# ── Socket path ──────────────────────────────────────────────────────────────
SPECTRETTS_SOCKET_PATH=/tmp/spectretts.sock
EOF
fi

# 5. Make bash scripts executable
chmod +x tray/register_hotkey.sh tray/install_service.sh tray/hotkey_trigger.py

# 6. Install systemd service
echo "Installing background service..."
./tray/install_service.sh

# 7. Register Global Hotkey
echo "Registering global hotkey..."
./tray/register_hotkey.sh

echo "=== Setup Complete! ==="
echo "You can now select text anywhere and press Ctrl+Alt+R to read it."
