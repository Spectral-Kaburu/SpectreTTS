#!/usr/bin/env bash
#
# SpectreTTS - Hotkey Registration
# ----------------------------------
# Registers Ctrl+Alt+R as a GNOME custom keybinding that calls
# hotkey_trigger.py. Works under both X11 and Wayland sessions
# since GNOME's compositor owns the global shortcut, not our script.
#
# Safe to re-run — checks for existing SpectreTTS binding first.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TRIGGER_SCRIPT="$SCRIPT_DIR/hotkey_trigger.py"
PYTHON_BIN="$HOME/.python311/bin/python3.11"

# Fall back to venv python if .python311 doesn't exist at this path
if [ ! -x "$PYTHON_BIN" ]; then
    PYTHON_BIN="$(dirname "$SCRIPT_DIR")/.venv/bin/python3"
fi

if [ ! -x "$PYTHON_BIN" ]; then
    echo "ERROR: Could not find a Python interpreter to bind."
    echo "Edit PYTHON_BIN in this script to point at your venv's python3."
    exit 1
fi

COMMAND="$PYTHON_BIN $TRIGGER_SCRIPT"
KEYBINDING="<Ctrl><Alt>r"
NAME="SpectreTTS Read Selection"

BASE_PATH="org.gnome.settings-daemon.plugins.media-keys"
CUSTOM_BASE="$BASE_PATH.custom-keybinding"
CUSTOM_PATH_PREFIX="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom"

# Get existing custom keybindings list
EXISTING=$(gsettings get "$BASE_PATH" custom-keybindings)

# Check if our binding already exists
if echo "$EXISTING" | grep -q "spectretts"; then
    echo "SpectreTTS keybinding already registered. Skipping."
    exit 0
fi

# Find next available custom slot index
SLOT_PATH="${CUSTOM_PATH_PREFIX}spectretts/"
FULL_PATH="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/customspectretts/"

# Build the new list (append our entry)
if [ "$EXISTING" = "@as []" ]; then
    NEW_LIST="['$FULL_PATH']"
else
    # Strip trailing ']' and append
    NEW_LIST=$(echo "$EXISTING" | sed "s/]$/, '$FULL_PATH']/")
fi

echo "Registering keybinding..."
gsettings set "$BASE_PATH" custom-keybindings "$NEW_LIST"

gsettings set "$CUSTOM_BASE:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/customspectretts/" name "$NAME"
gsettings set "$CUSTOM_BASE:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/customspectretts/" command "$COMMAND"
gsettings set "$CUSTOM_BASE:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/customspectretts/" binding "$KEYBINDING"

echo "Done. Ctrl+Alt+R is now bound to: $COMMAND"
echo ""
echo "Verify in GNOME Settings > Keyboard > View and Customize Shortcuts > Custom Shortcuts"
